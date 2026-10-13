# NEM Network Outage Schedule (NOS) Monitor

A local web page that helps traders quickly review NEM transmission network outages. It answers these questions:

- Which outages are active now, and which are coming up;
- Which equipment, substations and regions are involved, and what the start/end times, status and linked constraint sets are;
- What changed between recent snapshots;
- Where each event is on a map, and which interconnectors border the affected region;
- Which file the data came from, when it was published, when it was fetched, and whether it is stale.

NSW1, QLD1 and VIC1 are the default focus regions; SA1 and TAS1 can be turned on in the filters.

## Running

Requires Python ≥ 3.10 and **no third-party packages**. Leaflet 1.9.4 is vendored in `web/vendor/leaflet/`, so no CDN is needed at runtime.

```bash
# Normal mode: checks NEMweb every 300 s; open http://127.0.0.1:8765/
python -m nos_monitor serve

# Common options
python -m nos_monitor serve --port 8080 --poll-seconds 300 --no-market   # skip DispatchIS
python -m nos_monitor serve --no-fetch                                     # offline: only show imported data

# Manual operations
python -m nos_monitor refresh                       # check upstream once, now
python -m nos_monitor fetch-latest                  # just download the newest PUBLIC_NETWORK zip to var/downloads/
python -m nos_monitor inspect var/downloads/PUBLIC_NETWORK_xxx.zip   # show actual tables, versions, columns, row counts
python -m nos_monitor import PUBLIC_NETWORK_xxx.zip [more files...]   # import manually downloaded zip/CSV files
python -m nos_monitor import --dispatch PUBLIC_DISPATCHIS_xxx.zip    # import market data
python -m nos_monitor import-genconset GENCONSET.csv                  # import constraint set -> constraint membership
python -m nos_monitor locations-template --out unmapped.csv           # list substations that still lack coordinates

# Demo: loads synthetic samples (two snapshots, to show change comparison); no network needed
python -m nos_monitor demo        # http://127.0.0.1:8765/
```

All runtime data lives in `var/` (git-ignored):

- `var/nos.sqlite`: snapshots, changes, fetch log, market data;
- `var/raw/`: the 20 most recent raw zips;
- `var/demo/`: demo mode only, kept separate from real data.

Tests:

```bash
python -m unittest discover -s tests -v
```

## Data sources

| Data | Source | Used for |
|---|---|---|
| `PUBLIC_NETWORK_*.zip` | <https://nemweb.com.au/Reports/Current/Network/> | NOS outage data |
| `PUBLIC_DISPATCHIS_*.zip` | <https://nemweb.com.au/Reports/Current/DispatchIS_Reports/> | Market observations: binding constraints, regional RRP, interconnector flows |
| MMS Data Model reference | <https://nemweb.com.au/Reports/Current/MMSDataModelReport/Electricity/> | Table and column definitions |
| AEMO Network Outages page | <https://www.aemo.com.au/energy-systems/electricity/national-electricity-market-nem/nem-events-and-reports/network-outages> | Background |

All data comes from public files. The tool does not use the NOS login page and needs no account.

**Finding the latest file**: the tool parses the NEMweb directory listing and picks the `PUBLIC_NETWORK_*.zip` with the newest timestamp in its file name, falling back to the listing date when there is none. No date or file name is hard-coded (see `nos_monitor/nemweb.py`).

**Parsing**: AEMO MMS CSV files have three row types: `C` is the file header, `I` is a column header, `D` is a data row. The parser reads each block of data rows using the columns declared by its `I` row, rather than a fixed column list, so it keeps working when the Data Model adds columns or bumps versions. Tables used:

- `NETWORK_OUTAGEDETAIL`: main outage table. Key is OUTAGEID + SUBSTATIONID + EQUIPMENTTYPE + EQUIPMENTID + STARTTIME. The page merges all equipment for one OUTAGEID into a single record;
- `NETWORK_OUTAGECONSTRAINTSET`: constraint sets linked to each outage;
- `NETWORK_OUTAGESTATUSCODE`: status code descriptions, taken from the data rather than hard-coded;
- `NETWORK_SUBSTATIONDETAIL`: substation name and region, using the version currently valid per VALIDFROM/VALIDTO;
- `NETWORK_EQUIPMENTDETAIL`: equipment description and voltage.

> ⚠️ **Not yet verified against a real file**: the development environment's outbound network policy blocked `nemweb.com.au` and `www.aemo.com.au`, so no real archive could be downloaded to check the column layout. Column names currently follow the MMS Data Model table definitions, and reads are tolerant: missing columns show as "—". The first time you run on a machine with network access, run:
> ```bash
> python -m nos_monitor fetch-latest && python -m nos_monitor inspect var/downloads/PUBLIC_NETWORK_*.zip
> ```
> and check the table and column names in the output. If they differ from the names used in `nos_monitor/outages.py`, only that file needs changing. The "Data sources and diagnostics" section at the bottom of the page also lists the tables, versions, columns and parse warnings found in each snapshot.

## Refresh behaviour

- **Upstream update frequency**: NOS files are published roughly every 30 minutes.
- **Local check frequency**: the server checks every 5 minutes by default (`--poll-seconds`). The page's "Auto check" dropdown (Off/1/2/5/10/15/30 min) controls how often the page asks the server to check; "Refresh now" checks immediately. The server enforces at least 60 seconds between non-forced checks. The page states explicitly that this is only the page's check frequency, not the upstream update frequency.
- **Times shown**: the page shows "Upstream file published" (from the file name timestamp; hover for the directory listing time and CSV report time) separately from "Local fetch time", each with its age. All times are NEM time (AEST, UTC+10, no daylight saving).
- **New file detection**: the same source file is never stored twice; when there is no new file, only the check is logged.
- **Failure handling**: when a fetch or parse fails, the last successful data is kept and the page shows a red "Fetch failed · showing old data" badge with the error. If a new file's `NETWORK_OUTAGEDETAIL` is missing or empty, it is rejected as suspect, so empty data never overwrites existing data.
- **Stale rules**: data is marked stale if any of these hold: the upstream file is older than 90 minutes (3 publishing cycles); the latest fetch failed; the current data came from a manual import or sample. Market data older than 15 minutes is also marked stale.

### Change history

Each new snapshot is compared with the previous one, and changes are recorded by type:

- Added;
- Removed;
- Time change (planned and actual start/end);
- Status change;
- Constraint set change;
- Equipment change.

Changes are stored in SQLite, for up to the 300 most recent snapshots. The page's "Recent data changes" section groups them by snapshot and can be filtered by type and region; the details panel also lists the change history for the selected outage.

### Page categories

| Category | Rule |
|---|---|
| Cancelled/withdrawn | Status code or description contains CANCEL / WITHDRAW / REJECT / DELET / RECALLED |
| Ended | Actual end ≤ now; without actual times, planned end ≤ now |
| Active | Actual start ≤ now (tagged "Actually started"); without actual times, now falls inside the planned window (tagged "In planned window") |
| Upcoming | Planned start is after now |
| **Needs attention** | Either: ① the outage is active or starts within 24 h, **and** has a linked constraint set or touches a mapped interconnector substation; or ② it changed in the latest snapshot comparison and has not ended or been cancelled |

Outages that ended more than 2 days ago are hidden.

## Three kinds of information

The details panel is split into three blocks:

1. **NOS record**: straight from the source: original outage ID, equipment, substation, region, equipment type, planned and actual start/end, status, reason, secondary flag, recall times, etc. The raw rows can be expanded.
2. **Expected constraints**: from `NETWORK_OUTAGECONSTRAINTSET`, i.e. the constraint sets AEMO expects to invoke for the outage.
3. **Market observation**: from the latest DispatchIS file, including:
   - whether member constraints of the linked constraint sets are binding (MARGINALVALUE ≠ 0, using the INTERVENTION=0 run);
   - the region's RRP and spreads to neighbouring regions;
   - flows and limits on adjacent interconnectors.

   These are **observations from the same dispatch interval**. The page states clearly that they do not establish that the outage caused any effect.

**Timeliness of constraint data**: DispatchIS is published publicly every 5 minutes and shows which constraints (constraint IDs) are binding. Outages, however, are linked to constraint set IDs, and mapping a set to its constraints needs the GENCONSET membership data. As far as I know AEMO publishes that mapping in the monthly MMS Data Model archive, not in `Reports/Current`, so same-day data is usually not available. Because of the network restriction this could **not be verified**. Unless a mapping is imported with `import-genconset`, constraint set binding status is always shown as **"Cannot be confirmed"**. The tool does not guess membership from similar names.

## Map and coordinate sources

The map uses Leaflet with OpenStreetMap tiles. Tiles need network access; without it there is no basemap but markers still show.

**No coordinates are ever guessed.** Coordinates come only from explicitly maintained, sourced files under `data/`:

| File | Contents |
|---|---|
| `data/substation_locations.csv` | `substation_id,name,latitude,longitude,source,source_ref,notes`. Rows without a `source`, or with coordinates outside Australia, are ignored and listed under "Mapping table problems" on the page |
| `data/line_locations.geojson` | Transmission line LineStrings. Properties must include `substation_id`, `equipment_id` (matching NETWORK_OUTAGEDETAIL) and `source` |
| `data/interconnector_substations.csv` | Interconnector terminal substations (`interconnector_id,substation_id,source,notes`), used to flag "Touches mapped interconnector substation" |

All three files are currently **empty**, because no trusted source could be downloaded during development. Suggested steps to fill them:

1. Run `python -m nos_monitor locations-template --out unmapped.csv` to get the SUBSTATIONIDs in the current data that lack coordinates, with their names and regions;
2. Fill in coordinates one by one from a citable dataset, such as Geoscience Australia's *Electricity Transmission Substations* and *Electricity Transmission Lines* (Digital Atlas of Australia) or TNSP publications, recording the source in `source`;
3. The page picks up changes automatically; `POST /api/reload-locations` reloads immediately.

**Map display rules:**

- Outages without coordinates are not plotted; they are marked "Location unmapped" in the list and counted in the legend. Clicking one shows a "location unmapped" notice.
- Clicking a map marker opens the outage details (when one substation has several outages, choose in the popup). Clicking a list row pans the map to the matching marker.
- Legend: red = active, blue = planned, orange ring = needs attention, grey = location unmapped (count only).
- The "Regions and interconnectors" schematic under the map is **not geographic**. It shows:
  - outage counts and RRP per region;
  - dispatch flows on interconnectors;
  - for the selected outage, its mapped interconnectors (solid) and interconnectors that merely border its region (dashed, adjacency only, not a confirmed impact).

Demo mode uses synthetic coordinates from `samples/demo_*.{csv,geojson}`. They are loaded only by the `demo` command, and the page shows a prominent banner.

## Code layout

```
nos_monitor/
  mms_csv.py    generic MMS CSV (C/I/D rows) parser, zip support
  nemweb.py     directory listing parsing, dynamic latest-file selection, download
  outages.py    NETWORK_* tables -> outage records; page categories and "needs attention" rules
  diff.py       consecutive snapshot comparison
  market.py     DispatchIS summary and market observations (non-causal)
  locations.py  coordinate, line and interconnector mapping loaders
  store.py      SQLite persistence
  service.py    refresh flow (keeps last good data on failure) and page state
  server.py     stdlib HTTP server: static page + /api/state + POST /api/refresh + background poller
  sample.py     synthetic samples (demo and tests)
web/            index.html, app.js, styles.css, vendor/leaflet
data/           hand-maintained coordinate and interconnector mappings
samples/        synthetic demo coordinates
tests/          unittest
```

## Known limitations

- The development environment could not reach nemweb.com.au, aemo.com.au or unpkg.com (blocked by the organisation's outbound network policy), so:
  - automatic fetching and the real column layout have not been verified end to end;
  - directory listing parsing follows the common NEMweb (IIS) listing format, and the test fixture `tests/fixtures/network_listing.html` was hand-written in that format, not copied from the real page.
- The parser, page, snapshot comparison and keep-last-good logic are all tested with synthetic data. Once you have real files, check them with `inspect`, then import them offline with `import`.
- The coordinate tables are empty, so with real data every outage will show "Location unmapped" for now.
- Interconnector impact is shown in only two ways: the hand-maintained terminal substation mapping, and region adjacency. The tool never infers causality from constraint names or price moves.
