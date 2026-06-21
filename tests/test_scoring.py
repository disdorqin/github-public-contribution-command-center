"""Tests for the external-issue scoring module."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from contrib_center import scoring  # noqa: E402


def test_deny_keyword_zeroes_score():
    sc = scoring.score_issue(
        title="Critical security vulnerability in auth flow",
        body="There is an authentication bypass.",
    )
    assert sc.denied
    assert sc.total == 0.0
    assert any("security" in r or "authentication" in r for r in sc.deny_reasons)


def test_typo_issue_high_score():
    sc = scoring.score_issue(
        title="Typo in README",
        body="Fix spelling of 'contribution' to 'contribution' in docs/index.md.",
        labels=["good first issue", "documentation"],
    )
    assert not sc.denied
    assert sc.total >= 7.0


def test_action_thresholds():
    assert scoring.action_for_score(5.9) == "drop"
    assert scoring.action_for_score(6.5) == "report"
    assert scoring.action_for_score(7.5) == "analyze"
    assert scoring.action_for_score(8.5) == "patch_draft"
    assert scoring.action_for_score(9.5) == "autopilot_eligible"


def test_payment_keyword_denied():
    sc = scoring.score_issue(
        title="Stripe payment integration broken",
        body="Production outage in payment webhook",
    )
    assert sc.denied
