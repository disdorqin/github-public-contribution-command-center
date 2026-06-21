"""GitHub Public Contribution Command Center.

Safe Mode v1 orchestrator. Scans own public repos, scouts external public
issues, generates patch drafts via mini-swe-agent, writes daily reports,
updates a fenced block in a designated GitHub Profile README. Never publishes
external PRs, never publishes external comments, never pushes to external
repos, never touches private/internal repos.

See README_CONTRIB_CENTER.md for the safety contract and config docs.
"""

__version__ = "0.1.0"
