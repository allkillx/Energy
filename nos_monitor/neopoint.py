"""NEOpoint data source: server-side fetching with the API key kept out of every output.

Format-independent on purpose. Column names, layout (long vs wide), time
ranges and update times must be confirmed from real NEOpoint responses
(`python -m nos_monitor neopoint-inspect`) before any field mapping is written.

Configuration (environment variables, or a git-ignored .env in the repo root):

    NEOPOINT_API_KEY=<secret>
    NEOPOINT_URL_NOS_OUTAGES=<report link, with the key replaced by {key}>
    NEOPOINT_URL_CONSTRAINTS=<...{key}...>
    NEOPOINT_URL_INTERCONNECTORS=<...{key}...>
    NEOPOINT_URL_PRICE_DUID=<...{key}...>
    NEOPOINT_URL_PRICE_REGION=<...{key}...>

The `{key}` placeholder marks where the key goes in the link, so nothing about
NEOpoint's parameter names is assumed. Links may also contain {from} / {to}
placeholders, filled with NEM-time dates at fetch time, and {instances}, which
is filled from NEOPOINT_INSTANCES_<FEED> (comma-separated, one request each).

Link structure seen in a user-supplied (key-redacted) link, 2026-10-13:
    https://neopoint.com.au/Service/Csv?f=106%20Flows%20and%20Constraints%5CNOS%20Planned%20Outages%20by%20Region
        &from=2026-10-12%2000%3A00&period=Daily&instances=NSW1&section=-1&key=<redacted>
i.e. f=<category>\<report>, from, period, instances (one region), section, key.
"""

from __future__ import annotations

import csv
import io
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from .nemweb import USER_AGENT, FetchError
from .timeutil import NEM_TZ, now_nem, parse_nem

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# feed id -> (env var, NEOpoint report name as given by the user)
FEEDS = {
    "nos_outages": ("NEOPOINT_URL_NOS_OUTAGES", "NOS Planned Outages by Region"),
    "constraints": ("NEOPOINT_URL_CONSTRAINTS", "Constraints and sets"),
    "interconnectors": ("NEOPOINT_URL_INTERCONNECTORS", "Interconnectors"),
    "price_duid": ("NEOPOINT_URL_PRICE_DUID", "Local Price by DUID (GULLRWF1, GULLRWF2)"),
    "price_region": ("NEOPOINT_URL_PRICE_REGION", "Local Price by Region (NSW1, QLD1, VIC1)"),
}
KEY_ENV = "NEOPOINT_API_KEY"
_KEYISH = re.compile(r"(?i)((?:api[_-]?)?key|token|apikey|password|secret)=([^&\s\"']+)")


def load_dotenv(path: str | None = None) -> None:
    """Minimal .env loader. Existing environment variables win; values are never printed."""
    path = path or os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip().removeprefix("export ").strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            os.environ.setdefault(k, v)


def redact(text, key: str | None = None) -> str:
    """Remove the API key (and any key=/token= query value) from text destined for logs, errors or the UI."""
    text = str(text)
    key = key if key is not None else os.environ.get(KEY_ENV, "")
    if key:
        text = text.replace(key, "***")
        text = text.replace(urllib.parse.quote(key, safe=""), "***")
    return _KEYISH.sub(lambda m: f"{m.group(1)}=***", text)


def configured_feeds() -> dict[str, dict]:
    out = {}
    for feed, (env, report) in FEEDS.items():
        tpl = os.environ.get(env, "")
        out[feed] = {"feed": feed, "report": report, "env": env, "configured": bool(tpl),
                     "has_key_placeholder": "{key}" in tpl, "instances": instances(feed)}
    return out


def instances(feed: str) -> list[str | None]:
    """Instances (e.g. regions) to request for a feed; [None] when the link has no {instances} placeholder."""
    env, _ = FEEDS[feed]
    if "{instances}" not in os.environ.get(env, ""):
        return [None]
    raw = os.environ.get(env.replace("_URL_", "_INSTANCES_"), "")
    return [i.strip() for i in raw.split(",") if i.strip()]


def build_url(template: str, key: str, instance: str | None = None, days_back: int = 1, days_forward: int = 0) -> str:
    if "{key}" not in template:
        raise ValueError("report link has no {key} placeholder; replace the key value in the link with {key}")
    if "{instances}" in template and not instance:
        raise ValueError("report link has {instances} but no instance was given (set NEOPOINT_INSTANCES_<FEED>)")
    today = now_nem().date()
    url = (template.replace("{key}", urllib.parse.quote(key, safe=""))
           .replace("{from}", str(today - timedelta(days=days_back)))
           .replace("{to}", str(today + timedelta(days=days_forward))))
    return url.replace("{instances}", urllib.parse.quote(instance, safe="")) if instance else url


def fetch(feed: str, instance: str | None = None, timeout: float = 60.0, opener=None) -> tuple[bytes, str]:
    """Fetch a configured feed server-side. Returns (body, redacted_url). Errors never contain the key."""
    env, _ = FEEDS[feed]
    template, key = os.environ.get(env, ""), os.environ.get(KEY_ENV, "")
    safe_tpl = redact(template, key) or f"<{env} not set>"
    if not template:
        raise FetchError("config", safe_tpl, f"{env} is not set")
    if not key:
        raise FetchError("config", safe_tpl, f"{KEY_ENV} is not set")
    try:
        url = build_url(template, key, instance)
    except ValueError as exc:
        raise FetchError("config", safe_tpl, str(exc)) from None
    safe_url = redact(url, key)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as resp:
            return resp.read(), safe_url
    except urllib.error.HTTPError as exc:
        raise FetchError("download", safe_url, redact(exc.reason or "HTTP error", key), status=exc.code) from None
    except urllib.error.URLError as exc:
        reason = redact(exc.reason, key)
        m = re.search(r"Tunnel connection failed:\s*(\d{3})", reason)
        raise FetchError("download", safe_url, reason, status=int(m.group(1)) if m else None, via_proxy=bool(m)) from None
    except (TimeoutError, OSError) as exc:
        raise FetchError("download", safe_url, redact(f"{type(exc).__name__}: {exc}", key)) from None


_EXTRA_TIME_FORMATS = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def _parse_time(value: str):
    """Recognise date-like cells for inspection only (format of NEOpoint dates not yet confirmed)."""
    t = parse_nem(value)
    if t or not value:
        return t
    for fmt in _EXTRA_TIME_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=NEM_TZ)
        except ValueError:
            continue
    return None


def inspect_csv(data: bytes, max_rows_shown: int = 3) -> dict:
    """Describe a CSV exactly as received: header, row count, sample rows, and time range of date-like columns.

    Makes no assumption about NEOpoint's layout; it reports what is there so a mapping can be written from facts.
    """
    text = data.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return {"rows": 0, "columns": [], "note": "empty response"}
    header, body = rows[0], rows[1:]
    info = {"columns": header, "rows": len(body), "sample": body[:max_rows_shown], "time_columns": {}}
    if text.lstrip()[:1] == "<":
        info["note"] = "response looks like HTML, not CSV (login page or error?)"
    for i, col in enumerate(header):
        times = [t for t in (_parse_time(r[i]) for r in body if i < len(r)) if t]
        if body and len(times) >= max(1, len(body) // 2):
            info["time_columns"][col] = {"min": min(times).isoformat(), "max": max(times).isoformat(),
                                         "parsed": len(times)}
    info["ragged_rows"] = sum(1 for r in body if len(r) != len(header))
    return info

