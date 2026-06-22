"""Tests for main.py CLI commands."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.contrib_center.main import _cmd_assisted_pr, _cmd_autopilot_publish


class TestAssistedPrCLI:
    """Test assisted-pr command."""

    @patch("src.contrib_center.main.Policy")
    def test_safe_mode_rejected(self, mock_policy):
        """Test that safe mode is rejected."""
        mock_policy_instance = MagicMock()
        mock_policy_instance.mode = "safe"
        mock_policy.return_value = mock_policy_instance
        mock_policy.load.return_value = mock_policy_instance

        result = _cmd_assisted_pr(
            target_issue_url="https://github.com/owner/repo/issues/1",
            mode="safe",
            confirm_publish=False,
            dry_run=True,
        )

        assert result == 1

    def test_patch_file_missing(self):
        """Test that missing patch_file is rejected."""
        # Create a temporary non-existent file path
        result = _cmd_assisted_pr(
            target_issue_url="https://github.com/owner/repo/issues/1",
            mode="assisted",
            confirm_publish=True,
            dry_run=False,
            patch_file="/tmp/nonexistent.diff",
        )

        assert result == 1

    def test_patch_file_not_diff(self):
        """Test that non-.diff patch_file is rejected."""
        # Create a temporary file with .txt extension
        result = _cmd_assisted_pr(
            target_issue_url="https://github.com/owner/repo/issues/1",
            mode="assisted",
            confirm_publish=True,
            dry_run=False,
            patch_file="/tmp/patch.txt",
        )

        assert result == 1

    @patch("src.contrib_center.external_pr_publisher.dry_run_external_pr")
    def test_dry_run_no_publish(self, mock_dry_run):
        """Test that dry-run doesn't publish."""
        mock_dry_run.return_value = MagicMock(
            ok=True,
            upstream_repo="owner/repo",
            fork_repo="disdorqin/repo",
            skipped_reason="dry_run_only",
        )

        result = _cmd_assisted_pr(
            target_issue_url="https://github.com/owner/repo/issues/1",
            mode="assisted",
            confirm_publish=False,
            dry_run=True,
            patch_file="/tmp/patch.diff",
        )

        assert result == 0
        mock_dry_run.assert_called_once()


class TestAutopilotPublishCLI:
    """Test autopilot-publish command."""

    @patch("src.contrib_center.main.Policy")
    def test_safe_mode_rejected(self, mock_policy):
        """Test that safe mode is rejected."""
        mock_policy_instance = MagicMock()
        mock_policy_instance.mode = "safe"
        mock_policy.return_value = mock_policy_instance
        mock_policy.load.return_value = mock_policy_instance

        result = _cmd_autopilot_publish(mode="safe", dry_run=True)

        assert result == 1

    @patch("src.contrib_center.autopilot_publisher.autopilot_publish_one")
    @patch("src.contrib_center.main.Policy")
    def test_dry_run_no_publish(self, mock_policy, mock_autopilot):
        """Test that dry-run doesn't publish."""
        mock_policy_instance = MagicMock()
        mock_policy_instance.mode = "autopilot"
        mock_policy.return_value = mock_policy_instance
        mock_policy.load.return_value = mock_policy_instance

        result = _cmd_autopilot_publish(mode="autopilot", dry_run=True)

        assert result == 0
        mock_autopilot.assert_not_called()

    @patch("src.contrib_center.autopilot_publisher.autopilot_publish_one")
    @patch("src.contrib_center.main.Policy")
    def test_autopilot_mode_publishes(self, mock_policy, mock_autopilot):
        """Test that autopilot mode publishes."""
        mock_policy_instance = MagicMock()
        mock_policy_instance.mode = "autopilot"
        mock_policy.return_value = mock_policy_instance
        mock_policy.load.return_value = mock_policy_instance

        mock_autopilot.return_value = {
            "ok": True,
            "published": True,
            "repo": "owner/repo",
            "pr_url": "https://github.com/owner/repo/pull/123",
        }

        result = _cmd_autopilot_publish(mode="autopilot", dry_run=False)

        assert result == 0
        mock_autopilot.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
