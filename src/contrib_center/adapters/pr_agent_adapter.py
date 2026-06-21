"""Stub for PR-Agent integration.

PR-Agent runs review/description generation on PRs. In Safe Mode v1 we do
NOT open any external PRs, so this adapter only returns the body
unchanged.

When Assisted Mode is enabled (config/rules.yml -> mode: assisted) this
adapter will be wired up to call PR-Agent's CLI before opening a PR.
"""
from __future__ import annotations


def describe(title: str, body: str) -> str:  # pragma: no cover - stub
    return body
