"""NEM Network Outage Schedule (NOS) monitor.

Reads AEMO's public PUBLIC_NETWORK_*.zip reports from NEMweb, keeps
snapshots in SQLite, diffs consecutive snapshots and serves a trader-facing
HTML page. Standard library only.
"""

__version__ = "0.1.0"
