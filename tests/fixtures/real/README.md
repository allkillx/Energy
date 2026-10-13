# Real AEMO fixtures

Place real `PUBLIC_NETWORK_*.zip` files from https://nemweb.com.au/Reports/Current/Network/ here,
ideally with `python -m nos_monitor diagnose --save-fixture tests/fixtures/real`.
`tests/test_validate.py::RealFileTests` validates every file in this directory and is skipped when it is empty.

Status: empty. NEMweb could not be reached from the development environment
(the egress proxy answered `403 Forbidden` to `CONNECT nemweb.com.au:443`), so no real file has been saved yet.
