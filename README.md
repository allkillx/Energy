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

# Live check (no fallback to sample data): listing -> download -> parse -> field validation
python -m nos_monitor diagnose --save-fixture tests/fixtures/real --import
python -m nos_monitor diagnose --dispatch            # same check for DispatchIS

# Manual operations
python -m nos_monitor refresh                       # check upstream once, now
python -m nos_monitor fetch-latest                  # just download the newest PUBLIC_NETWORK zip to var/downloads/
python -m nos_monitor inspect var/downloads/PUBLIC_NETWORK_xxx.zip   # show actual tables, versions, columns, row counts
python -m nos_monitor validate var/downloads/PUBLIC_NETWORK_xxx.zip  # can outage/status/constraint-set fields be read?
python -m nos_monitor import PUBLIC_NETWORK_xxx.zip [more files...]   # import manually downloaded zip/CSV files
python -m nos_monitor import --dispatch PUBLIC_DISPATCHIS_xxx.zip    # import market data
python -m nos_monitor import-genconset GENCONSET.csv                  # import constraint set -> constraint membership
python -m nos_monitor locations-template --out unmapped.csv           # list substations that still lack coordinates
python -m nos_monitor locations-suggest GA_substations.geojson --out candidates.csv   # rank GA candidates for review
python -m nos_monitor locations-apply candidates.csv --dataset "<dataset name, version, download date>"

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

## Verification status (2026-10-14)

Network access to NEMweb and NEOpoint became available on 2026-10-14, and everything below was run against live data.

| Item | Verified with real data? | Evidence |
|---|---|---|
| Finding the newest `PUBLIC_NETWORK_*.zip` in the NEMweb listing | **Yes** | Live run; picked `PUBLIC_NETWORK_20261014123018_…zip` |
| Downloading and parsing it | **Yes** | Saved as `tests/fixtures/real/`; `RealFileTests` runs on it |
| `NETWORK_OUTAGEDETAIL` (v4) and `NETWORK_OUTAGECONSTRAINTSET` (v1) | **Yes** | 12,383 rows / 5,695 outages; 946 constraint-set rows, all joined to an outage |
| Regions, status descriptions, equipment descriptions | **Yes, via AEMO's monthly archive** | Not in the live file (see below); 516 of 559 outage substation IDs resolve to a region, and all 12 status codes have descriptions |
| Constraint set → constraint membership (GENCONSET) | **Partly** | Last 12 monthly archives give 4,770 sets; sets not changed in that period stay "Cannot be confirmed" |
| DispatchIS market observations | **Yes** | Live run: 27 binding constraints, region prices, interconnector flows (including `NSW1-SA1`) |
| NEOpoint NOS live fetch | **Yes** (NSW1) | Live response saved as `tests/fixtures/neopoint/neopoint_nos_outages_NSW1_2026-10-14T1239.csv`; same 20 columns |
| Page with live AEMO data | **Yes** | Checked in Chromium: list, filters, search, details, map notice; no JS errors |
| Other NEOpoint feeds, coordinates | **No** | No samples; coordinate tables still empty |

### What the real AEMO data showed

- **The live `PUBLIC_NETWORK` file carries only two tables**: `NETWORK_OUTAGEDETAIL` and `NETWORK_OUTAGECONSTRAINTSET`.
  - Substation regions, status descriptions and equipment descriptions are not in it.
  - The tool fetches them from AEMO's MMS Data Model monthly archive (`Data_Archive/Wholesale_Electricity/MMSDM/<year>/MMSDM_<year>_<month>/…/DATA/`). The newest month is found from the directory listing.
  - The tables are cached under `var/reference/` and refreshed weekly (`python -m nos_monitor refresh-reference`). The current snapshot's diagnostics name the archive month used.
- **Timestamps** in the live file and the archive carry milliseconds (`2011/01/28 10:29:12.250`).
- **Status codes seen**: `SUBMIT`, `WDRAWN` (Withdrawn), `MTLTP`, `COMPLETE`, `RESUBMIT`, `PTP`, `INFO`, `STLTP`, `WD REQ` (Withdraw Request), `PDLTP`, `PTR`. Only `WDRAWN` counts as cancelled; `WD REQ` is a request, not a withdrawal.
- **Unresolved substation IDs**: the 43 IDs with no region are mostly line-section codes (`275KV_813`, `6C`, `N/A`) plus sites newer than the archive month. They show as region "Unknown".
- **Placeholder dates**: withdrawn records can carry dates in year 2100 or later.
- **Shared constraint sets**: one constraint set is often linked to several outages. Binding status is therefore only evaluated for an outage that is active in the dispatch interval; for any other outage it shows "Not evaluated".

The live check that failed on 2026-10-13 (proxy 403) is recorded in git history.

## NEOpoint data source

The page can take outages from NEOpoint instead of NEMweb (`serve --source neopoint`). NEOpoint needs an API key.

### Status (2026-10-14)

| Feed | NEOpoint report | Status |
|---|---|---|
| Outages | 106 Flows and Constraints\NOS Planned Outages by Region | **Mapped and verified** against a user export (NSW1, 2026-10-12) and a live fetch (NSW1, 2026-10-14): `tests/fixtures/neopoint/` |
| Constraints | Constraints and sets | Not available: no real sample or link yet, so no mapping. Dispatch interval / marginal value / violation degree unconfirmed |
| Interconnectors | Interconnectors | Not available: no real sample or link yet |
| DUID prices | Local Price by DUID (GULLRWF1, GULLRWF2) | Not available: no real sample or link yet |
| Region prices | Local Price by Region (NSW1, QLD1, VIC1) | Not available: no real sample or link yet |

Live fetching from NEOpoint was verified on 2026-10-14 for NSW1. The response had the same 20 columns as the first export the user downloaded.

### What the real NOS export contains

Columns (exact; the converter fails loudly if any is renamed or dropped):

`actual_starttime, actual_endtime, outageid, REGIONID, ownerid, substationid, substation_DESCRIPTION, equipmenttype, equipmentid, equipment_DESCRIPTION, genconsetid, setDESCRIPTION, proposes_start_time, proposes_end_time, submitteddate, outagestatuscode, outage_status_DESCRIPTION, recall_minutes_day, recall_minutes_night, companyrefcode`

Findings from the sample:

- **Shape**: one row per outage × substation × equipment × constraint set. 51 rows → 22 outages, 24 distinct constraint sets. Exact duplicate rows occur and are dropped.
- **Status**: code and description are both included. Only `PTP` (Permission to Proceed) and `COMPLETE` (Completed) appeared.
- **Times**:
  - `proposes_start_time` / `proposes_end_time` are used as the outage's start and end.
  - `actual_starttime` / `actual_endtime` differ between constraint-set rows of one outage (998519: 06:30 vs 06:51 for `I-QN_500`), and for 1004313 they lie months outside the proposed window. So they are shown per constraint set, marked "meaning unconfirmed", and **not** used as outage actual times.
  - Time zone is not stated; times are treated as NEM time (AEMO convention).
- **Data window**: the export held outages whose `actual_*` window overlaps the requested day. Nothing started after 10-12 14:20, so upcoming outages need a later `from` date. Whether NEOpoint returns future days for this report is not yet verified.
- **Short-notice / unplanned outages**: there is no planned/unplanned column. 7 outages were submitted after they started (e.g. 1006835: submitted 15:17, started 14:20), so short-notice entries are included, but completeness for unplanned outages cannot be confirmed.
- **Not provided** (shown on the page as "Not provided by source"): reason, secondary flag, last changed, resubmission, outage actual times, voltage (only inside description text), constraint-set intervals, planned/unplanned type.
- **Status/time mismatch**: 1006835 is `COMPLETE` with a planned end of 10-16. It is tagged "Status/time mismatch" and not interpreted.
- **Update time**: the report has no publish time. The page shows "Latest submitteddate" as the source data time, plus the local fetch time. The update frequency has not been observed.

### Setup and commands

1. `cp .env.example .env` (`.env` is git-ignored).
2. Set `NEOPOINT_API_KEY`.
3. Set each `NEOPOINT_URL_*` to the report link, with the key value replaced by `{key}`.

`.env.example` already holds the NOS link: `instances={instances}` with `NEOPOINT_INSTANCES_NOS_OUTAGES=NSW1,QLD1,VIC1,SA1,TAS1`, one request per region. A refresh stores data only if **every** region succeeds, because partial data would show as false "Removed" changes. Identical data is not stored twice.

Rules for the key:

- It is read only by the Python backend and is never sent to the browser.
- It is scrubbed from every URL, error and log line (`nos_monitor/neopoint.py:redact`).

```bash
python -m nos_monitor serve --source neopoint                        # live NEOpoint NOS (needs network access to neopoint.com.au)
python -m nos_monitor import-neopoint nsw.csv qld.csv vic.csv        # import saved CSVs as one snapshot
python -m nos_monitor serve --no-fetch                               # view imported data
python -m nos_monitor neopoint-status                                # which feeds are configured (no key printed)
python -m nos_monitor neopoint-inspect --feed nos_outages --instance NSW1 --save tests/fixtures/neopoint
python -m nos_monitor neopoint-inspect --file sample.csv             # real columns, rows, time range of a CSV
```

Stale rules for NEOpoint data: no successful NEOpoint check for 30 minutes, the latest check failed, or the data came from a manual import.

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

- **Upstream update frequency**: NOS files are expected roughly every 30 minutes. This figure comes from AEMO's description of the NOS data, not from observation: no listing could be read here. Once NEMweb is reachable, `diagnose` prints the latest file's name timestamp and listing time, and the "Saved snapshots" table on the page shows the actual spacing between published files.
- **Local check frequency**: the server checks every 5 minutes by default (`--poll-seconds`). The page's "Auto check" dropdown (Off/1/2/5/10/15/30 min) controls how often the page asks the server to check; "Refresh now" checks immediately. The server enforces at least 60 seconds between non-forced checks. The page states explicitly that this is only the page's check frequency, not the upstream update frequency.
- **Times shown**: the page shows "Upstream file published" (from the file name timestamp; hover for the directory listing time and CSV report time) separately from "Local fetch time", each with its age. All times are NEM time (AEST, UTC+10, no daylight saving).
- **New file detection**: the same source file is never stored twice; when there is no new file, only the check is logged.
- **Failure handling**: when a fetch or parse fails, the last successful data is kept, and the page shows a red "Fetch failed · showing old data" badge. Every failure records:
  - the URL;
  - the HTTP status (including a proxy's CONNECT refusal);
  - the stage (`listing`, `download`, `parse` or `validate`);
  - the error text.

  These appear on the page and in the fetch log. With no successful data yet, the page shows nothing rather than demo data.
- **Validation**: every new file goes through field validation (`nos_monitor/validate.py`). A file that fails, for example because `NETWORK_OUTAGEDETAIL` is missing or empty or the outage/status/constraint-set key columns are missing, is rejected and saved as `var/raw/REJECTED_<name>` for inspection, so it never overwrites existing data. The validation report of the current snapshot is shown under "Data sources and diagnostics".
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

### Current map coverage

| Mapping | Entries | Status |
|---|---|---|
| Substation coordinates | 0 | Empty |
| Line geometries | 0 | Empty |
| Interconnector terminal substations | 0 | Empty |

So with real data **every outage is currently shown as "Location unmapped"**. No coordinates were added because no trusted source (Geoscience Australia, data.gov.au, OpenStreetMap) could be reached from the development environment. Coordinates from memory would be guesses, so none were used.

### Filling the mappings

Recommended route, with a human review step:

1. Get real outage data in first (`diagnose --import`), so the tool knows which AEMO SUBSTATIONIDs need coordinates.
2. Download Geoscience Australia's *Electricity Transmission Substations* dataset (Digital Atlas of Australia) as GeoJSON or CSV.
3. Run `python -m nos_monitor locations-suggest <ga file> --out candidates.csv`. For each unmapped substation it lists up to 3 GA candidates in the same state, ranked by name similarity. Nothing is accepted automatically.
4. Review `candidates.csv` and set `confirmed=yes` on correct matches only (at most one per substation).
5. Run `python -m nos_monitor locations-apply candidates.csv --dataset "Geoscience Australia Electricity Transmission Substations, downloaded <date>"`. Confirmed rows are appended to `data/substation_locations.csv`, with the dataset label as `source` and the GA feature id as `source_ref`. Existing rows are never overwritten.
6. Lines and interconnector terminals are edited by hand in `data/line_locations.geojson` and `data/interconnector_substations.csv`, each with a `source`.

`python -m nos_monitor locations-template` lists unmapped substations, for when you would rather fill coordinates by hand. The page picks up changes automatically; `POST /api/reload-locations` reloads immediately.

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
  nemweb.py     directory listing parsing, dynamic latest-file selection, download, FetchError (URL/status/stage)
  validate.py   field-level validation of outage/status/constraint-set data
  reference.py  AEMO MMSDM monthly archive: regions, status/equipment descriptions, GENCONSET membership
  ga_import.py  Geoscience Australia candidate suggestions + applying human-confirmed rows
  neopoint.py   NEOpoint: .env loading, server-side fetch, key redaction, CSV inspection
  neopoint_nos.py  NEOpoint "NOS Planned Outages by Region" CSV -> NETWORK_* shapes (verified columns)
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
tests/          unittest; tests/fixtures/real/ holds real AEMO files (currently empty)
```

## Known limitations

- Constraint set membership covers only sets that changed in the last 12 monthly archives; the rest show "Cannot be confirmed". See "Verification status" above.
- Directory listing parsing follows the common NEMweb (IIS) listing format. The test fixture `tests/fixtures/network_listing.html` was hand-written in that format, not copied from the real page.
- The coordinate tables are empty, so with real data every outage shows "Location unmapped" for now.
- Interconnector impact is shown in only two ways: the hand-maintained terminal substation mapping, and region adjacency. The tool never infers causality from constraint names or price moves.
