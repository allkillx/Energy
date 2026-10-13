"""Parser for AEMO MMS "CSV Data Interchange" files (the format inside NEMweb zips).

Row types (first field):
  C  comment / file header, e.g. C,NEMP.WORLD,PUBLIC_NETWORK,AEMO,PUBLIC,2026/10/12,12:30:05,...
  I  column header for the following D rows: I,<REPORT>,<SUBTYPE>,<VERSION>,<COL1>,<COL2>,...
  D  data row matching the most recent I row for that report/subtype.

The parser does not assume a fixed column list; it uses whatever the I rows
declare, so new MMS Data Model versions (extra columns) keep working.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass, field


@dataclass
class MmsTable:
    name: str  # e.g. NETWORK_OUTAGEDETAIL
    version: str
    columns: list[str]
    rows: list[dict[str, str]] = field(default_factory=list)


@dataclass
class MmsFile:
    tables: dict[str, MmsTable] = field(default_factory=dict)
    header: list[str] = field(default_factory=list)  # first C row
    footer: list[str] = field(default_factory=list)  # last C row (END OF REPORT)
    warnings: list[str] = field(default_factory=list)

    @property
    def report_date(self) -> str | None:
        """Date/time from the C header row ('YYYY/MM/DD HH:MM:SS'), if present."""
        if len(self.header) >= 7 and self.header[5] and self.header[6]:
            return f"{self.header[5]} {self.header[6]}"
        return None

    def table(self, name: str) -> MmsTable | None:
        return self.tables.get(name.upper())

    def rows(self, name: str) -> list[dict[str, str]]:
        t = self.table(name)
        return t.rows if t else []


def parse_text(text: str) -> MmsFile:
    result = MmsFile()
    current: MmsTable | None = None
    for lineno, row in enumerate(csv.reader(io.StringIO(text)), start=1):
        if not row:
            continue
        kind = row[0].strip().upper()
        if kind == "C":
            if not result.header:
                result.header = row
            else:
                result.footer = row
        elif kind == "I":
            if len(row) < 5:
                result.warnings.append(f"line {lineno}: short I row")
                current = None
                continue
            name = f"{row[1].strip()}_{row[2].strip()}".upper()
            columns = [c.strip().upper() for c in row[4:]]
            existing = result.tables.get(name)
            if existing and existing.columns == columns:
                current = existing
            else:
                if existing:
                    result.warnings.append(
                        f"line {lineno}: {name} redeclared with different columns; later rows win"
                    )
                current = MmsTable(name=name, version=row[3].strip(), columns=columns)
                result.tables[name] = current
        elif kind == "D":
            if current is None:
                result.warnings.append(f"line {lineno}: D row before any I row")
                continue
            name = f"{row[1].strip()}_{row[2].strip()}".upper()
            if name != current.name:
                result.warnings.append(f"line {lineno}: D row for {name} under I row {current.name}")
                continue
            values = row[4:]
            if len(values) != len(current.columns):
                result.warnings.append(
                    f"line {lineno}: {name} has {len(values)} values for {len(current.columns)} columns"
                )
            current.rows.append(
                {col: (values[i].strip() if i < len(values) else "") for i, col in enumerate(current.columns)}
            )
        else:
            result.warnings.append(f"line {lineno}: unknown row type {kind!r}")
    return result


def parse_bytes(data: bytes) -> MmsFile:
    return parse_text(data.decode("utf-8-sig", errors="replace"))


def parse_zip_bytes(data: bytes) -> MmsFile:
    """Parse every CSV inside a zip into one MmsFile (NEMweb zips usually hold one CSV)."""
    merged = MmsFile()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise ValueError("zip contains no .csv file")
        for n in names:
            part = parse_bytes(zf.read(n))
            if not merged.header:
                merged.header = part.header
            merged.footer = part.footer or merged.footer
            merged.warnings.extend(f"{n}: {w}" for w in part.warnings)
            for tname, table in part.tables.items():
                if tname in merged.tables and merged.tables[tname].columns == table.columns:
                    merged.tables[tname].rows.extend(table.rows)
                else:
                    merged.tables[tname] = table
    return merged


def parse_file(path: str) -> MmsFile:
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:2] == b"PK":
        return parse_zip_bytes(data)
    return parse_bytes(data)


def describe(mms: MmsFile) -> str:
    """Human-readable summary: tables, versions, columns, row counts (used by `inspect`)."""
    lines = [f"header: {','.join(mms.header)}"]
    for t in mms.tables.values():
        lines.append(f"{t.name} v{t.version}: {len(t.rows)} rows")
        lines.append("  columns: " + ", ".join(t.columns))
        if t.rows:
            lines.append("  first row: " + repr(t.rows[0]))
    if mms.warnings:
        lines.append(f"warnings ({len(mms.warnings)}):")
        lines.extend("  " + w for w in mms.warnings[:20])
    return "\n".join(lines)
