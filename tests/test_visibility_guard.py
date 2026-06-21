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
from contrib_center.policy import Policy  # noqa: E402


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
