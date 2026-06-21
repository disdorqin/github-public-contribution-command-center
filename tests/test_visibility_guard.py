"""Tests for the public-only visibility guard.

These tests exercise the fail-closed behaviour: any time we cannot
prove a repo is public, we MUST refuse.
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

from contrib_center import visibility_guard  # noqa: E402
from contrib_center.policy import Policy, Repository  # noqa: E402


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _make_policy(mode: str = "safe", allowlist: list[str] | None = None) -> Policy:
    """Create a Policy object with the given allowlist."""
    # Convert string list to Repository objects
    repos = []
    if allowlist:
        for full_name in allowlist:
            name = full_name.split("/")[-1]
            repos.append(Repository(
                name=name,
                full_name=full_name,
                purpose="test repo",
                allow={},
            ))
    
    return Policy(
        mode=mode,
        public_repos=repos,
        rules={},
        external_search={},
        profile={},
        prompts={},
    )


def _mock_visibility(private: bool = False, visibility: str = "public", public: bool = True):
    """Return a mock RepoVisibility object."""
    return visibility_guard.RepoVisibility(
        full_name="mock/repo",
        private=private,
        visibility=visibility,
        public=public,
        source="test",
    )


# ---------------------------------------------------------------------------
# Existing tests (preserved)
# ---------------------------------------------------------------------------

def test_private_repo_rejected(monkeypatch):
    vis = visibility_guard.RepoVisibility(
        full_name="foo/bar",
        private=True,
        visibility="private",
        public=False,
        source="gh",
    )
    monkeypatch.setattr(visibility_guard, "fetch_visibility", lambda _: vis)
    with pytest.raises(visibility_guard.PermissionError_):
        visibility_guard.assert_public_repo("foo/bar")


def test_internal_repo_rejected(monkeypatch):
    vis = visibility_guard.RepoVisibility(
        full_name="foo/bar",
        private=True,
        visibility="internal",
        public=False,
        source="gh",
    )
    monkeypatch.setattr(visibility_guard, "fetch_visibility", lambda _: vis)
    assert visibility_guard.is_public_repo("foo/bar") is False


def test_unknown_visibility_is_fail_closed(monkeypatch):
    vis = visibility_guard.RepoVisibility(
        full_name="foo/bar",
        private=None,
        visibility=None,
        public=False,
        source="default-deny",
    )
    monkeypatch.setattr(visibility_guard, "fetch_visibility", lambda _: vis)
    assert visibility_guard.is_public_repo("foo/bar") is False


def test_public_repo_passes(monkeypatch):
    vis = visibility_guard.RepoVisibility(
        full_name="foo/bar",
        private=False,
        visibility="public",
        public=True,
        source="gh",
    )
    monkeypatch.setattr(visibility_guard, "fetch_visibility", lambda _: vis)
    assert visibility_guard.is_public_repo("foo/bar") is True


def test_gh_missing_falls_back_to_deny():
    # If `gh` is not installed we must treat the repo as not public.
    with mock.patch(
        "contrib_center.visibility_guard.shutil.which", return_value=None
    ):
        assert visibility_guard.is_public_repo("foo/bar") is False


def test_guard_repo_operation_checks_allowlist_first():
    policy = Policy(
        mode="safe",
        public_repos=[],
        rules={},
    )
    with pytest.raises(visibility_guard.PermissionError_) as exc:
        visibility_guard.guard_repo_operation(
            "not/listed", "create_issue", policy
        )
    assert "not_in_allowlist" in str(exc.value)


# ---------------------------------------------------------------------------
# NEW TESTS - Task #14: Complete visibility_guard tests
# ---------------------------------------------------------------------------

# Test 1: Own public repo: allowlist + public → pass
def test_own_public_repo_in_allowlist_passes(monkeypatch):
    """自有 public repo：allowlist + public → pass"""
    policy = _make_policy(mode="safe", allowlist=["disdorqin/DARIS"])
    
    # Mock fetch_visibility to return public repo
    public_vis = _mock_visibility(private=False, visibility="public", public=True)
    monkeypatch.setattr(visibility_guard, "fetch_visibility", lambda _: public_vis)
    
    # Should NOT raise - repo is in allowlist and is public
    result = visibility_guard.guard_own_repo_operation("disdorqin/DARIS", "read", policy)
    assert result.public is True


# Test 2: Own repo not in allowlist → reject
def test_own_repo_not_in_allowlist_rejected():
    """自有 repo 不在 allowlist → reject"""
    policy = _make_policy(mode="safe", allowlist=["disdorqin/other-repo"])
    
    # Should raise PermissionError_ with "not_in_allowlist"
    with pytest.raises(visibility_guard.PermissionError_) as exc:
        visibility_guard.guard_own_repo_operation("disdorqin/not-listed", "read", policy)
    assert "not_in_allowlist" in str(exc.value)


# Test 3: Own private repo → reject (even if in allowlist)
def test_own_private_repo_rejected_even_if_in_allowlist(monkeypatch):
    """自有 private repo → reject (即使在 allowlist 里)"""
    policy = _make_policy(mode="safe", allowlist=["disdorqin/private-repo"])
    
    # Mock fetch_visibility to return private repo
    private_vis = _mock_visibility(private=True, visibility="private", public=False)
    monkeypatch.setattr(visibility_guard, "fetch_visibility", lambda _: private_vis)
    
    # Should raise PermissionError_ - private repos are always rejected
    with pytest.raises(visibility_guard.PermissionError_) as exc:
        visibility_guard.guard_own_repo_operation("disdorqin/private-repo", "read", policy)
    assert "not_public" in str(exc.value)


# Test 4: External public repo NOT in allowlist, read/clone/generate_patch → pass
def test_external_public_repo_read_operations_pass(monkeypatch):
    """外部 public repo 不在 allowlist，但 read/clone/generate_patch → pass"""
    policy = _make_policy(mode="safe", allowlist=[])
    
    # Mock fetch_visibility to return public repo
    public_vis = _mock_visibility(private=False, visibility="public", public=True)
    monkeypatch.setattr(visibility_guard, "fetch_visibility", lambda _: public_vis)
    
    # These operations should ALL pass for external public repos
    for operation in ["read", "clone", "generate_patch", "inspect_issue", "search"]:
        result = visibility_guard.guard_external_public_repo_read(
            "external/repo", operation, policy
        )
        assert result.public is True


# Test 5: External public repo but push/create_pr/comment → reject
def test_external_public_repo_write_operations_rejected(monkeypatch):
    """外部 public repo 但 push/create_pr/comment → reject"""
    policy = _make_policy(mode="safe", allowlist=[])
    
    # Mock fetch_visibility to return public repo
    public_vis = _mock_visibility(private=False, visibility="public", public=True)
    monkeypatch.setattr(visibility_guard, "fetch_visibility", lambda _: public_vis)
    
    # These operations should ALL be rejected
    for operation in ["push", "create_pr", "comment", "publish"]:
        with pytest.raises(visibility_guard.PermissionError_) as exc:
            visibility_guard.guard_external_public_repo_read(
                "external/repo", operation, policy
            )
        assert "not in allowed read operations" in str(exc.value) or "denied in safe mode" in str(exc.value)


# Test 6: External private/unknown repo → reject
def test_external_private_repo_rejected(monkeypatch):
    """外部 private/unknown repo → reject"""
    policy = _make_policy(mode="safe", allowlist=[])
    
    # Test with private repo
    private_vis = _mock_visibility(private=True, visibility="private", public=False)
    monkeypatch.setattr(visibility_guard, "fetch_visibility", lambda _: private_vis)
    
    with pytest.raises(visibility_guard.PermissionError_) as exc:
        visibility_guard.guard_external_public_repo_read("external/private-repo", "read", policy)
    assert "not_public" in str(exc.value)
    
    # Test with unknown visibility
    unknown_vis = _mock_visibility(private=None, visibility=None, public=False)
    monkeypatch.setattr(visibility_guard, "fetch_visibility", lambda _: unknown_vis)
    
    with pytest.raises(visibility_guard.PermissionError_) as exc:
        visibility_guard.guard_external_public_repo_read("external/unknown-repo", "read", policy)
    assert "not_public" in str(exc.value)


# ---------------------------------------------------------------------------
# Additional edge case tests
# ---------------------------------------------------------------------------

def test_safe_mode_denies_write_to_own_repo(monkeypatch):
    """Safe Mode should deny write operations even to own public repos."""
    policy = _make_policy(mode="safe", allowlist=["disdorqin/my-repo"])
    
    public_vis = _mock_visibility(private=False, visibility="public", public=True)
    monkeypatch.setattr(visibility_guard, "fetch_visibility", lambda _: public_vis)
    
    # Write operations should be rejected in safe mode
    for operation in ["push", "create_pr", "create_issue", "comment"]:
        with pytest.raises(visibility_guard.PermissionError_) as exc:
            visibility_guard.guard_own_repo_operation("disdorqin/my-repo", operation, policy)
        assert "safe mode" in str(exc.value).lower()


def test_deny_keywords_detection():
    """Test that high-risk keywords are properly detected in title+body."""
    # Test various high-risk keywords
    test_cases = [
        ("Fix security vulnerability", "", ["security"]),
        ("Update auth logic", "", ["auth"]),
        ("Issue with private key", "Found a private key in config", ["private key"]),
        ("", "This contains credential information", ["credential"]),
        ("", "The password is exposed", ["password"]),
        ("", "Use this access token carefully", ["access token"]),
        ("", "API key leakage detected", ["api key"]),
    ]
    
    for title, body, expected_keywords in test_cases:
        found = visibility_guard.issue_body_has_deny_keywords(title, body)
        for kw in expected_keywords:
            assert kw in found, f"Expected '{kw}' in {found} for title='{title}', body='{body}'"


def test_deny_keywords_case_insensitive():
    """Test that keyword detection is case-insensitive."""
    found = visibility_guard.issue_body_has_deny_keywords(
        "Fix SECURITY issue", 
        "This is a SECURITY vulnerability"
    )
    assert "security" in found


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
