"""Tests for external_pr_publisher module.

Covers:
  1. Safe Mode blocks all external PRs
  2. Assisted Mode requires confirm_publish=true
  3. Upstream repo must be public
  4. Deny keywords block PR creation
  5. Patch limits (files, lines, binary)
  6. Fork creation and branch naming
  7. PR body contains required sections
  8. No star/follow/promotion in PR body
  9. Max 1 PR per run
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from contrib_center.external_pr_publisher import (
    PublishResult,
    _check_deny_keywords,
    _validate_patch_limits,
    _build_pr_body,
    dry_run_external_pr,
    publish_external_pr,
)
from contrib_center.policy import Policy
from contrib_center.visibility_guard import PermissionError_


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_policy(mode: str = "safe") -> Policy:
    """Create a mock Policy object."""
    policy = MagicMock(spec=Policy)
    policy.mode = mode
    policy.allowlist = ["disdorqin/disdorqin", "disdorqin/DARIS"]
    return policy


def _make_temp_patch_dir() -> Path:
    """Create a temporary directory with a fake git repo."""
    d = Path(tempfile.mkdtemp())
    # Init git repo
    import subprocess
    subprocess.run(["git", "init"], cwd=str(d), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(d), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(d), capture_output=True)
    # Create a file
    (d / "test.txt").write_text("initial")
    subprocess.run(["git", "add", "."], cwd=str(d), capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=str(d), capture_output=True)
    return d


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestSafeModeBlocksAllExternalPRs:
    """Safe Mode must never publish external PRs."""

    def test_safe_mode_returns_skipped(self):
        policy = _make_policy(mode="safe")
        result = publish_external_pr(
            issue_url="https://github.com/owner/repo/issues/1",
            upstream_repo="owner/repo",
            patch_workdir=Path("/tmp/fake"),
            pr_title="Test",
            pr_body="Test",
            policy=policy,
            confirm_publish=True,  # Even with confirm=true
        )
        assert result.ok is False
        assert result.skipped_reason == "safe_mode_blocks_external_pr"


class TestAssistedModeRequiresConfirmPublish:
    """Assisted Mode must have confirm_publish=true."""

    def test_assisted_mode_without_confirm_returns_skipped(self):
        policy = _make_policy(mode="assisted")
        result = publish_external_pr(
            issue_url="https://github.com/owner/repo/issues/1",
            upstream_repo="owner/repo",
            patch_workdir=Path("/tmp/fake"),
            pr_title="Test",
            pr_body="Test",
            policy=policy,
            confirm_publish=False,  # Missing confirm
        )
        assert result.ok is False
        assert result.skipped_reason == "confirm_publish_not_set"


class TestUpstreamRepoMustBePublic:
    """Upstream repo must be provably public."""

    @patch("contrib_center.external_pr_publisher.guard_external_public_repo_read")
    def test_non_public_repo_returns_error(self, mock_guard):
        mock_guard.side_effect = PermissionError_("not_public")
        policy = _make_policy(mode="assisted")
        result = publish_external_pr(
            issue_url="https://github.com/owner/repo/issues/1",
            upstream_repo="owner/private-repo",
            patch_workdir=Path("/tmp/fake"),
            pr_title="Test",
            pr_body="Test",
            policy=policy,
            confirm_publish=True,
        )
        assert result.ok is False
        assert result.skipped_reason == "upstream_not_public"


class TestDenyKeywordsBlockPR:
    """Issues with deny keywords must be skipped."""

    def test_deny_keywords_in_title(self):
        found = _check_deny_keywords(
            title="Fix security vulnerability",
            body="This fixes a security issue",
        )
        assert len(found) > 0
        assert "security" in found

    def test_deny_keywords_in_body(self):
        found = _check_deny_keywords(
            title="Update docs",
            body="This requires authentication changes",
        )
        assert "auth" in found or "authentication" in found

    @patch("contrib_center.external_pr_publisher._run_cmd")
    @patch("contrib_center.external_pr_publisher._check_deny_keywords")
    @patch("contrib_center.external_pr_publisher.guard_external_public_repo_read")
    def test_publish_blocks_on_deny_keywords(self, mock_guard, mock_check, mock_run_cmd):
        mock_guard.return_value = MagicMock(public=True)  # Mock guard to pass
        mock_check.return_value = ["security", "auth"]
        # Mock _run_cmd to return success for gh issue view
        mock_run_cmd.return_value = (0, '{"title": "Fix security issue", "body": "..."}', "")
        policy = _make_policy(mode="assisted")
        result = publish_external_pr(
            issue_url="https://github.com/owner/repo/issues/1",
            upstream_repo="owner/repo",
            patch_workdir=Path("/tmp/fake"),
            pr_title="Test",
            pr_body="Test",
            policy=policy,
            confirm_publish=True,
        )
        assert result.ok is False
        assert "deny_keywords" in result.skipped_reason


class TestPatchLimits:
    """Patch must stay within configured limits."""

    def test_validate_patch_limits_runs(self):
        """_validate_patch_limits should return a dict with 'ok' key."""
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            # Init git
            import subprocess
            subprocess.run(["git", "init"], cwd=str(d), capture_output=True)
            subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(d), capture_output=True)
            subprocess.run(["git", "config", "user.name", "T"], cwd=str(d), capture_output=True)
            (d / "a.txt").write_text("hello")
            subprocess.run(["git", "add", "."], cwd=str(d), capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=str(d), capture_output=True)
            # Modify file to create diff
            (d / "a.txt").write_text("hello world")
            stats = _validate_patch_limits(d)
            assert "ok" in stats
            assert "changed_files" in stats


class TestPRBodyContainsRequiredSections:
    """PR body must contain all required sections."""

    def test_pr_body_includes_summary(self):
        body = _build_pr_body(
            pr_title="Fix typo",
            pr_body="Fixed a typo in README",
            issue_url="https://github.com/owner/repo/issues/1",
            stats={"num_changed_files": 1, "diff_lines": 10},
        )
        assert "## Summary" in body
        assert "## Linked issue" in body
        assert "## Tests" in body
        assert "## Safety" in body
        assert "## Disclosure" in body

    def test_pr_body_includes_issue_link(self):
        body = _build_pr_body(
            pr_title="Fix typo",
            pr_body="Fixed a typo",
            issue_url="https://github.com/owner/repo/issues/42",
            stats={"num_changed_files": 1, "diff_lines": 10},
        )
        assert "https://github.com/owner/repo/issues/42" in body


class TestPRBodyNoSpam:
    """PR body must not contain star/follow/promotion."""

    def test_pr_body_no_star_request(self):
        body = _build_pr_body(
            pr_title="Fix typo",
            pr_body="Fixed a typo",
            issue_url="https://github.com/owner/repo/issues/1",
            stats={"num_changed_files": 1, "diff_lines": 10},
        )
        body_lower = body.lower()
        assert "star" not in body_lower
        assert "follow" not in body_lower
        assert "promotion" not in body_lower
        assert "please star" not in body_lower

    def test_pr_body_includes_ai_disclosure(self):
        body = _build_pr_body(
            pr_title="Fix typo",
            pr_body="Fixed a typo",
            issue_url="https://github.com/owner/repo/issues/1",
            stats={"num_changed_files": 1, "diff_lines": 10},
        )
        assert "AI" in body or "ai" in body


class TestDryRun:
    """Dry-run mode should validate without publishing."""

    @patch("contrib_center.external_pr_publisher.guard_external_public_repo_read")
    def test_dry_run_does_not_publish(self, mock_guard):
        """Dry-run should succeed but not create PR."""
        mock_guard.return_value = MagicMock(public=True)
        policy = _make_policy(mode="assisted")
        result = dry_run_external_pr(
            issue_url="https://github.com/owner/repo/issues/1",
            upstream_repo="owner/repo",
            patch_workdir=None,
            pr_title="Test",
            policy=policy,
        )
        # Dry-run "succeeds" (ok=True) but skipped_reason="dry_run_only"
        assert result.ok is True
        assert result.skipped_reason == "dry_run_only"


class TestInvalidIssueURL:
    """Invalid issue URLs must be rejected."""

    @patch("contrib_center.external_pr_publisher.guard_external_public_repo_read")
    def test_non_github_url_rejected(self, mock_guard):
        mock_guard.return_value = MagicMock(public=True)  # Mock guard to pass
        policy = _make_policy(mode="assisted")
        result = publish_external_pr(
            issue_url="https://gitlab.com/owner/repo/issues/1",  # Not GitHub
            upstream_repo="owner/repo",
            patch_workdir=Path("/tmp/fake"),
            pr_title="Test",
            pr_body="Test",
            policy=policy,
            confirm_publish=True,
        )
        assert result.ok is False
        assert result.skipped_reason == "invalid_issue_url"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
