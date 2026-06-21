"""Lightweight SQLite state store for daily counters and seen issues.

Stored at data/state.sqlite. Schema is small and append-friendly.

Tables:
  - daily_counters(date TEXT, action_type TEXT, count INT, PRIMARY KEY(date, action_type))
  - seen_issues(url TEXT PRIMARY KEY, first_seen TEXT, score REAL, repo TEXT)
  - published(action TEXT, repo TEXT, ts TEXT)

This module is intentionally dependency-free (only stdlib sqlite3).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(os.environ.get("CONTRIB_CENTER_DB", "data/state.sqlite"))


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_counters (
            date TEXT NOT NULL,
            action_type TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (date, action_type)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_issues (
            url TEXT PRIMARY KEY,
            first_seen TEXT NOT NULL,
            score REAL,
            repo TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS published (
            action TEXT NOT NULL,
            repo TEXT NOT NULL,
            ts TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def increment(action_type: str, by: int = 1) -> int:
    today = date.today().isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO daily_counters(date, action_type, count)
            VALUES(?, ?, ?)
            ON CONFLICT(date, action_type)
            DO UPDATE SET count = count + excluded.count
            """,
            (today, action_type, by),
        )
        cur = conn.execute(
            "SELECT count FROM daily_counters WHERE date=? AND action_type=?",
            (today, action_type),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


def get_today(action_type: str) -> int:
    today = date.today().isoformat()
    with _connect() as conn:
        cur = conn.execute(
            "SELECT count FROM daily_counters WHERE date=? AND action_type=?",
            (today, action_type),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


def remember_issue(url: str, score: float, repo: str) -> bool:
    """Returns True if this is the first time we have seen the URL."""
    with _connect() as conn:
        cur = conn.execute("SELECT 1 FROM seen_issues WHERE url=?", (url,))
        if cur.fetchone():
            return False
        conn.execute(
            "INSERT INTO seen_issues(url, first_seen, score, repo) VALUES(?, ?, ?, ?)",
            (url, "now", score, repo),
        )
        return True


def record_published(action: str, repo: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO published(action, repo, ts) VALUES(?, ?, datetime('now'))",
            (action, repo),
        )
