"""SQLite persistence: snapshots, change events, fetch log, market data, constraint-set membership."""

from __future__ import annotations

import json
import sqlite3
import threading
import zlib

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    source_url TEXT,
    source_kind TEXT NOT NULL,          -- nemweb | import | sample
    name_time TEXT,                     -- timestamp in file name (NEM)
    listing_time TEXT,                  -- NEMweb directory listing time (NEM)
    report_time TEXT,                   -- C-row report date/time inside the CSV
    fetched_at TEXT NOT NULL,           -- local fetch time
    record_count INTEGER NOT NULL,
    tables_json TEXT NOT NULL,          -- {table: {version, rows, columns}}
    warnings_json TEXT NOT NULL,
    records_z BLOB NOT NULL             -- zlib(JSON list of outage records)
);
CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    prev_snapshot_id INTEGER,
    outage_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS changes_snapshot ON changes(snapshot_id);
CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    feed TEXT NOT NULL,                 -- network | dispatch
    ok INTEGER NOT NULL,
    message TEXT,
    source_file TEXT
);
CREATE TABLE IF NOT EXISTS market (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    name_time TEXT,
    fetched_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS genconset_members (
    genconsetid TEXT NOT NULL,
    genconid TEXT NOT NULL,
    source TEXT,
    PRIMARY KEY (genconsetid, genconid)
);
"""

KEEP_SNAPSHOTS = 300
KEEP_MARKET = 20
KEEP_LOG = 2000


class Store:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self):
        self._conn.close()

    # --- snapshots -------------------------------------------------------
    def latest_snapshot(self, with_records: bool = True) -> dict | None:
        return self._snapshot("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1", (), with_records)

    def snapshot(self, snapshot_id: int, with_records: bool = True) -> dict | None:
        return self._snapshot("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,), with_records)

    def _snapshot(self, sql, args, with_records):
        with self._lock:
            row = self._conn.execute(sql, args).fetchone()
        if row is None:
            return None
        snap = {k: row[k] for k in row.keys() if k != "records_z"}
        snap["tables"] = json.loads(snap.pop("tables_json"))
        snap["warnings"] = json.loads(snap.pop("warnings_json"))
        if with_records:
            snap["records"] = json.loads(zlib.decompress(row["records_z"]).decode())
        return snap

    def has_source(self, source_file: str) -> bool:
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM snapshots WHERE source_file = ? LIMIT 1", (source_file,)
            ).fetchone() is not None

    def add_snapshot(self, meta: dict, records: list[dict], changes: list[dict], prev_id: int | None) -> int:
        blob = zlib.compress(json.dumps(records, separators=(",", ":")).encode())
        with self._lock, self._conn:
            cur = self._conn.execute(
                """INSERT INTO snapshots (source_file, source_url, source_kind, name_time, listing_time,
                   report_time, fetched_at, record_count, tables_json, warnings_json, records_z)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    meta["source_file"], meta.get("source_url"), meta["source_kind"], meta.get("name_time"),
                    meta.get("listing_time"), meta.get("report_time"), meta["fetched_at"], len(records),
                    json.dumps(meta.get("tables", {})), json.dumps(meta.get("warnings", [])[:200]), blob,
                ),
            )
            sid = cur.lastrowid
            self._conn.executemany(
                "INSERT INTO changes (snapshot_id, prev_snapshot_id, outage_id, kind, payload_json) VALUES (?,?,?,?,?)",
                [(sid, prev_id, c["outage_id"], c["kind"], json.dumps(c)) for c in changes],
            )
            self._conn.execute(
                "DELETE FROM snapshots WHERE id NOT IN (SELECT id FROM snapshots ORDER BY id DESC LIMIT ?)",
                (KEEP_SNAPSHOTS,),
            )
            self._conn.execute("DELETE FROM changes WHERE snapshot_id NOT IN (SELECT id FROM snapshots)")
        return sid

    def recent_changes(self, limit: int = 300) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT c.payload_json, c.snapshot_id, s.source_file, s.fetched_at, s.name_time
                   FROM changes c JOIN snapshots s ON s.id = c.snapshot_id
                   ORDER BY c.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            ev = json.loads(r["payload_json"])
            ev.update(snapshot_id=r["snapshot_id"], source_file=r["source_file"],
                      fetched_at=r["fetched_at"], name_time=r["name_time"])
            out.append(ev)
        return out

    def changed_ids_in_snapshot(self, snapshot_id: int) -> set[str]:
        with self._lock:
            rows = self._conn.execute("SELECT outage_id FROM changes WHERE snapshot_id = ?", (snapshot_id,)).fetchall()
        return {r[0] for r in rows}

    def snapshot_list(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT s.id, s.source_file, s.source_kind, s.name_time, s.fetched_at, s.record_count,
                   (SELECT COUNT(*) FROM changes c WHERE c.snapshot_id = s.id) AS change_count
                   FROM snapshots s ORDER BY s.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # --- fetch log -------------------------------------------------------
    def log(self, at: str, feed: str, ok: bool, message: str = "", source_file: str | None = None):
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO fetch_log (at, feed, ok, message, source_file) VALUES (?,?,?,?,?)",
                (at, feed, int(ok), message, source_file),
            )
            self._conn.execute(
                "DELETE FROM fetch_log WHERE id NOT IN (SELECT id FROM fetch_log ORDER BY id DESC LIMIT ?)", (KEEP_LOG,)
            )

    def last_log(self, feed: str, ok: bool | None = None) -> dict | None:
        sql, args = "SELECT * FROM fetch_log WHERE feed = ?", [feed]
        if ok is not None:
            sql += " AND ok = ?"
            args.append(int(ok))
        with self._lock:
            row = self._conn.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
        return dict(row) if row else None

    def recent_log(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM fetch_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # --- market ----------------------------------------------------------
    def add_market(self, source_file: str, name_time: str | None, fetched_at: str, payload: dict):
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO market (source_file, name_time, fetched_at, payload_json) VALUES (?,?,?,?)",
                (source_file, name_time, fetched_at, json.dumps(payload)),
            )
            self._conn.execute(
                "DELETE FROM market WHERE id NOT IN (SELECT id FROM market ORDER BY id DESC LIMIT ?)", (KEEP_MARKET,)
            )

    def latest_market(self) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM market ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        return {"source_file": row["source_file"], "name_time": row["name_time"],
                "fetched_at": row["fetched_at"], **json.loads(row["payload_json"])}

    def has_market_source(self, source_file: str) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM market WHERE source_file = ?", (source_file,)).fetchone() is not None

    # --- constraint set membership (optional, imported) ------------------
    def replace_genconset(self, pairs: list[tuple[str, str]], source: str):
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM genconset_members")
            self._conn.executemany(
                "INSERT OR IGNORE INTO genconset_members (genconsetid, genconid, source) VALUES (?,?,?)",
                [(a, b, source) for a, b in pairs],
            )

    def genconset_members(self) -> dict[str, list[str]]:
        with self._lock:
            rows = self._conn.execute("SELECT genconsetid, genconid FROM genconset_members").fetchall()
        out: dict[str, list[str]] = {}
        for a, b in rows:
            out.setdefault(a, []).append(b)
        return out

    def genconset_source(self) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT source FROM genconset_members LIMIT 1").fetchone()
        return row[0] if row else None
