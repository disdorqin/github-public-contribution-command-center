"""Structured JSONL logger for the contribution center.

Two log streams:
  - data/action_log.jsonl  : every guard decision, every API call, every file write
  - data/rejected.jsonl    : every skip / deny reason (private repo, not in allowlist, low score, ...)

Logs are append-only. We never truncate.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.environ.get("CONTRIB_CENTER_DATA_DIR", "data"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_action(event: str, **fields: Any) -> None:
    record = {"ts": _now(), "event": event, **fields}
    _append(DATA_DIR / "action_log.jsonl", record)


def log_reject(reason: str, **fields: Any) -> None:
    record = {"ts": _now(), "reason": reason, **fields}
    _append(DATA_DIR / "rejected.jsonl", record)


def log_candidate(score: float, repo: str, issue_url: str, **fields: Any) -> None:
    record = {
        "ts": _now(),
        "score": score,
        "repo": repo,
        "issue_url": issue_url,
        **fields,
    }
    _append(DATA_DIR / "candidates.jsonl", record)
