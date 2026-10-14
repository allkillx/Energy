# Real AEMO fixtures

Real files downloaded from NEMweb on 2026-10-14 with `python -m nos_monitor diagnose --save-fixture tests/fixtures/real`:

- `PUBLIC_NETWORK_20261014123018_*.zip`: live NOS file. It contains only NETWORK_OUTAGEDETAIL (v4) and NETWORK_OUTAGECONSTRAINTSET (v1).
- `reference/`: AEMO MMS Data Model monthly archive tables for 2026-09 (NETWORK_SUBSTATIONDETAIL, NETWORK_OUTAGESTATUSCODE, GENCONSET), used to resolve regions and status descriptions. NETWORK_EQUIPMENTDETAIL (720 KB) is not kept here.

`tests/test_validate.py::RealFileTests` validates every `PUBLIC_NETWORK_*.zip` in this directory.
