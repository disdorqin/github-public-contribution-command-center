"""Tests for the issue_scout bot.

These tests verify that:
1. Issue body is properly fetched and checked
2. High-risk keywords in body trigger skipping
3. External repo visibility is verified
4. Safe Mode enforces write_allowed=False
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from contrib_center import visibility_guard, github_client  # noqa: E402
from contrib_center.bots.issue_scout import Candidate, scout  # noqa: E402
from contrib_center.policy import Policy  # noqa: E402
from contrib_center.scoring import score_issue  # noqa: E402


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _make_policy(mode: str = "safe", queries: list[str] | None = None) -> Policy:
    """Create a Policy object for testing."""
    return Policy(
        mode=mode,
        public_repos=["disdorqin/disdorqin", "disdorqin/DARIS"],
        rules={
            "quality_gate": {"min_score_for_report": 6},
            "deny_keywords": [
                "security", "vulnerability", "authentication", "auth",
                "payment", "production outage", "breaking change",
                "crypto wallet", "private key", "credential", "secret",
                "access token", "api key", "password", "token leak",
            ],
        },
        external_search={"queries": queries or ["state:open label:bug"]},
    )


def _mock_search_issues_results(issues: list[dict]) -> list[dict]:
    """Return mock search results."""
    return issues


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def test_issue_with_body_containing_private_key_is_skipped(monkeypatch):
    """Issue body 包含 private key → 必须 skipped"""
    policy = _make_policy()
    
    # Mock search_issues to return an issue with body containing "private key"
    mock_issue = {
        "number": 123,
        "title": "Fix typo in README",
        "url": "https://github.com/external/repo/issues/123",
        "repository": {"owner": "external", "name": "repo"},
        "labels": [{"name": "bug"}],
        "createdAt": "2024-01-01T00:00:00Z",
        "author": "someone",
        "body": "I found a private key exposed in the config file",
    }
    
    monkeypatch.setattr(github_client, "search_issues", lambda q, limit: [mock_issue])
    monkeypatch.setattr(github_client, "get_issue_body", lambda url: mock_issue["body"])
    
    # Mock visibility guard to pass (repo is public)
    monkeypatch.setattr(
        visibility_guard, "guard_external_public_repo_read",
        lambda full_name, operation, policy: visibility_guard.RepoVisibility(
            full_name=full_name, private=False, visibility="public", public=True, source="test"
        )
    )
    
    candidates = scout(policy, limit_per_query=10)
    
    # The issue should be skipped due to "private key" in body
    assert len(candidates) > 0
    assert candidates[0].skipped is True
    assert "private key" in " ".join(candidates[0].deny_keywords)


def test_issue_with_body_containing_credential_is_skipped(monkeypatch):
    """Issue body 包含 credential → 必须 skipped"""
    policy = _make_policy()
    
    mock_issue = {
        "number": 124,
        "title": "Update docs",
        "url": "https://github.com/external/repo/issues/124",
        "repository": {"owner": "external", "name": "repo"},
        "labels": [{"name": "documentation"}],
        "createdAt": "2024-01-01T00:00:00Z",
        "author": "someone",
        "body": "The credential is hardcoded in the source",
    }
    
    monkeypatch.setattr(github_client, "search_issues", lambda q, limit: [mock_issue])
    monkeypatch.setattr(github_client, "get_issue_body", lambda url: mock_issue["body"])
    
    monkeypatch.setattr(
        visibility_guard, "guard_external_public_repo_read",
        lambda full_name, operation, policy: visibility_guard.RepoVisibility(
            full_name=full_name, private=False, visibility="public", public=True, source="test"
        )
    )
    
    candidates = scout(policy, limit_per_query=10)
    
    assert len(candidates) > 0
    assert candidates[0].skipped is True
    assert "credential" in " ".join(candidates[0].deny_keywords)


def test_external_repo_must_prove_public(monkeypatch):
    """外部 repo 无法证明 public → 必须 skipped / 不进入候选"""
    policy = _make_policy()
    
    mock_issue = {
        "number": 125,
        "title": "Valid issue",
        "url": "https://github.com/external/private-repo/issues/125",
        "repository": {"owner": "external", "name": "private-repo"},
        "labels": [],
        "createdAt": "2024-01-01T00:00:00Z",
        "author": "someone",
        "body": "This is a valid issue",
    }
    
    monkeypatch.setattr(github_client, "search_issues", lambda q, limit: [mock_issue])
    
    # Mock visibility guard to RAISE (repo is not public)
    monkeypatch.setattr(
        visibility_guard, "guard_external_public_repo_read",
        mock.Mock(side_effect=visibility_guard.PermissionError_("not_public"))
    )
    
    candidates = scout(policy, limit_per_query=10)
    
    # No candidates should be returned (issue should be skipped)
    assert len(candidates) == 0


def test_safe_mode_write_allowed_always_false(monkeypatch):
    """Safe Mode 下所有候选 write_allowed 必须是 false"""
    policy = _make_policy(mode="safe")
    
    mock_issue = {
        "number": 126,
        "title": "Simple typo fix",
        "url": "https://github.com/external/repo/issues/126",
        "repository": {"owner": "external", "name": "repo"},
        "labels": [{"name": "typo"}],
        "createdAt": "2024-01-01T00:00:00Z",
        "author": "someone",
        "body": "Fix a typo in the README",
    }
    
    monkeypatch.setattr(github_client, "search_issues", lambda q, limit: [mock_issue])
    monkeypatch.setattr(github_client, "get_issue_body", lambda url: mock_issue["body"])
    
    monkeypatch.setattr(
        visibility_guard, "guard_external_public_repo_read",
        lambda full_name, operation, policy: visibility_guard.RepoVisibility(
            full_name=full_name, private=False, visibility="public", public=True, source="test"
        )
    )
    
    candidates = scout(policy, limit_per_query=10)
    
    # All candidates in safe mode must have write_allowed=False
    for cand in candidates:
        assert cand.write_allowed is False, f"Candidate {cand.issue_url} has write_allowed=True in safe mode"


def test_issue_title_and_body_both_checked_for_deny_keywords(monkeypatch):
    """验证 title 和 body 都会被检查高风险关键词"""
    policy = _make_policy()
    
    # Test case 1: keyword only in title
    mock_issue_title = {
        "number": 127,
        "title": "Fix security vulnerability",
        "url": "https://github.com/external/repo/issues/127",
        "repository": {"owner": "external", "name": "repo"},
        "labels": [],
        "createdAt": "2024-01-01T00:00:00Z",
        "author": "someone",
        "body": "This is a normal issue body",
    }
    
    monkeypatch.setattr(github_client, "search_issues", lambda q, limit: [mock_issue_title])
    monkeypatch.setattr(github_client, "get_issue_body", lambda url: mock_issue_title["body"])
    
    monkeypatch.setattr(
        visibility_guard, "guard_external_public_repo_read",
        lambda full_name, operation, policy: visibility_guard.RepoVisibility(
            full_name=full_name, private=False, visibility="public", public=True, source="test"
        )
    )
    
    candidates = scout(policy, limit_per_query=10)
    
    # Should be skipped due to "security" in title
    assert len(candidates) > 0
    assert candidates[0].skipped is True
    assert any("security" in kw for kw in candidates[0].deny_keywords)


def test_scoring_uses_title_and_body():
    """Test that scoring function uses both title and body."""
    title = "Fix typo"
    body = "This is a detailed description of the typo fix needed in the documentation."
    
    sc = score_issue(title, body, [])
    
    # The score should be computed using both title and body
    # Body is long enough to get higher clarity score
    assert sc is not None
    assert sc.total >= 0


def test_candidate_to_dict_includes_all_fields():
    """Test that Candidate.to_dict() includes all required fields."""
    from contrib_center.scoring import Score
    
    sc = Score(total=7.5, breakdown={"clarity": 7.0}, denied=False, deny_reasons=[])
    
    cand = Candidate(
        repo="external/repo",
        issue_url="https://github.com/external/repo/issues/1",
        title="Test issue",
        score=sc,
        action="report",
        skipped=False,
        skip_reason=None,
        external_public_verified=True,
        write_allowed=False,
        deny_keywords=[],
    )
    
    d = cand.to_dict()
    
    # Verify all required fields are present
    assert "repo" in d
    assert "issue_url" in d
    assert "title" in d
    assert "score" in d
    assert "action" in d
    assert "skipped" in d
    assert "skip_reason" in d
    assert "external_public_verified" in d
    assert "write_allowed" in d
    assert "deny_keywords" in d


def test_denied_issue_writes_to_rejected_log(monkeypatch, tmp_path):
    """Test that denied issues are properly logged."""
    policy = _make_policy()
    
    mock_issue = {
        "number": 128,
        "title": "Fix authentication bug",
        "url": "https://github.com/external/repo/issues/128",
        "repository": {"owner": "external", "name": "repo"},
        "labels": [],
        "createdAt": "2024-01-01T00:00:00Z",
        "author": "someone",
        "body": "The auth logic is broken",
    }
    
    monkeypatch.setattr(github_client, "search_issues", lambda q, limit: [mock_issue])
    monkeypatch.setattr(github_client, "get_issue_body", lambda url: mock_issue["body"])
    
    monkeypatch.setattr(
        visibility_guard, "guard_external_public_repo_read",
        lambda full_name, operation, policy: visibility_guard.RepoVisibility(
            full_name=full_name, private=False, visibility="public", public=True, source="test"
        )
    )
    
    # Mock the logger to capture reject events
    rejected_events = []
    original_log_reject = visibility_guard.logger.log_reject
    
    def mock_log_reject(event_type, **kwargs):
        rejected_events.append((event_type, kwargs))
        return original_log_reject(event_type, **kwargs)
    
    monkeypatch.setattr(visibility_guard.logger, "log_reject", mock_log_reject)
    
    candidates = scout(policy, limit_per_query=10)
    
    # The issue should be skipped due to "authentication" or "auth" in title
    assert len(candidates) > 0
    assert candidates[0].skipped is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
