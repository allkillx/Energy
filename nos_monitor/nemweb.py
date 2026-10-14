"""Discover and download the newest files from NEMweb directory listings.

File names are never hard-coded: the listing HTML is parsed and the newest
file matching a prefix (e.g. PUBLIC_NETWORK_) is chosen by the timestamp
embedded in the file name, falling back to the listing date.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

from .timeutil import NEM_TZ

NETWORK_URL = "https://nemweb.com.au/Reports/Current/Network/"
DISPATCHIS_URL = "https://nemweb.com.au/Reports/Current/DispatchIS_Reports/"
USER_AGENT = "nos-monitor/0.1 (+https://github.com/allkillx/Energy)"

# IIS listing line, e.g.
#  Monday, October 12, 2026 12:31 PM   123456 <A HREF="/Reports/Current/Network/PUBLIC_NETWORK_202610121230_0000000123.zip">PUBLIC_NETWORK_...zip</A><br>
_LINK_RE = re.compile(r'<a\s+href="([^"]+)"\s*>([^<]+)</a>', re.IGNORECASE)
_LISTING_DATE_RE = re.compile(
    r"(\w+day,\s+\w+\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2}\s*[AP]M)\s+(\d+|&lt;dir&gt;)?\s*$",
    re.IGNORECASE,
)
_NAME_TS_RE = re.compile(r"_(\d{12}|\d{14})(?:_|\.)")


@dataclass
class ListingEntry:
    name: str
    url: str
    listing_time: datetime | None  # NEM time, from the listing's date column
    size: int | None

    @property
    def name_time(self) -> datetime | None:
        """Timestamp embedded in the file name (NEM time)."""
        return timestamp_from_name(self.name)

    @property
    def sort_key(self):
        t = self.name_time or self.listing_time
        return (t or datetime.min.replace(tzinfo=NEM_TZ), self.name)


def timestamp_from_name(name: str) -> datetime | None:
    m = _NAME_TS_RE.search(name)
    if not m:
        return None
    s = m.group(1)
    fmt = "%Y%m%d%H%M%S" if len(s) == 14 else "%Y%m%d%H%M"
    try:
        return datetime.strptime(s, fmt).replace(tzinfo=NEM_TZ)
    except ValueError:
        return None


def parse_listing(html: str, base_url: str) -> list[ListingEntry]:
    entries: list[ListingEntry] = []
    # IIS puts each entry on a line ending in <br>; split on <br> to pair date with link.
    for chunk in re.split(r"<br\s*/?>", html, flags=re.IGNORECASE):
        link = _LINK_RE.search(chunk)
        if not link:
            continue
        href, text = link.group(1), link.group(2).strip()
        if text.startswith("[") or href.endswith("/"):
            continue  # parent directory / subdirectories
        before = chunk[: link.start()].strip()
        listing_time, size = None, None
        m = _LISTING_DATE_RE.search(before)
        if m:
            try:
                listing_time = datetime.strptime(
                    re.sub(r"\s+", " ", m.group(1)), "%A, %B %d, %Y %I:%M %p"
                ).replace(tzinfo=NEM_TZ)
            except ValueError:
                listing_time = None
            if m.group(2) and m.group(2).isdigit():
                size = int(m.group(2))
        entries.append(ListingEntry(text, urljoin(base_url, href), listing_time, size))
    return entries


def latest(entries: list[ListingEntry], prefix: str, suffix: str = ".zip") -> ListingEntry | None:
    candidates = [
        e for e in entries if e.name.upper().startswith(prefix.upper()) and e.name.lower().endswith(suffix)
    ]
    return max(candidates, key=lambda e: e.sort_key) if candidates else None


class FetchError(Exception):
    """A fetch/parse failure with enough context to diagnose it: URL, HTTP status, stage."""

    def __init__(self, stage: str, url: str, detail: str, status: int | None = None, via_proxy: bool = False):
        super().__init__(detail)
        self.stage, self.url, self.detail, self.status, self.via_proxy = stage, url, detail, status, via_proxy

    def __str__(self) -> str:
        where = f"HTTP {self.status}{' from proxy CONNECT' if self.via_proxy else ''}" if self.status else "no HTTP status"
        return f"[{self.stage}] {self.url} — {where} — {self.detail}"

    def as_dict(self) -> dict:
        return {"stage": self.stage, "url": self.url, "http_status": self.status,
                "via_proxy": self.via_proxy, "detail": self.detail}


_TUNNEL_RE = re.compile(r"Tunnel connection failed:\s*(\d{3})")


def http_get(url: str, timeout: float = 30.0, stage: str = "download") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise FetchError(stage, url, exc.reason or "HTTP error", status=exc.code) from exc
    except urllib.error.URLError as exc:
        reason = str(exc.reason)
        m = _TUNNEL_RE.search(reason)  # an HTTPS proxy refused the CONNECT (e.g. egress policy)
        raise FetchError(stage, url, reason, status=int(m.group(1)) if m else None, via_proxy=bool(m)) from exc
    except (TimeoutError, OSError) as exc:
        raise FetchError(stage, url, f"{type(exc).__name__}: {exc}") from exc


def _get(getter, url: str, stage: str) -> bytes:
    try:
        return getter(url)
    except FetchError as exc:
        exc.stage = stage
        raise
    except Exception as exc:  # custom getters (tests) may raise anything
        raise FetchError(stage, url, f"{type(exc).__name__}: {exc}") from exc


def find_latest(base_url: str, prefix: str, getter=http_get) -> ListingEntry:
    html = _get(getter, base_url, "listing").decode("utf-8", errors="replace")
    entries = parse_listing(html, base_url)
    entry = latest(entries, prefix)
    if entry is None:
        raise FetchError("listing", base_url, f"no {prefix}*.zip found in listing ({len(entries)} entries parsed)")
    return entry


def download(entry: ListingEntry, getter=http_get) -> bytes:
    return _get(getter, entry.url, "download")
