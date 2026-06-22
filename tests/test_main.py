"""
Tests for main.py CLI commands.

Covers:
  1. assisted-pr command parsing and execution
  2. subprocess.run() unpacking fix
  3. --mode parameter handling
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestAssistedPrCommand:
    """Test assisted-pr command in main.py."""

    @patch("contrib_center.external_pr_publisher.dry_run_external_pr")
    @patch("contrib_center.main.Policy")
    def test_dry_run_no_subprocess_unpack_error(self, mock_policy_class, mock_dry_run):
        """Dry-run should not raise TypeError from subprocess.run unpacking."""
        from contrib_center.main import _cmd_assisted_pr

        # Setup mock policy
        mock_policy = MagicMock()
        mock_policy.mode = "assisted"
        mock_policy_class.load.return_value = mock_policy

        # Mock dry_run_external_pr to return success
        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.upstream_repo = "owner/repo"
        mock_result.fork_repo = "disdorqin/repo"
        mock_result.branch = "contrib-center/repo/1-abc"
        mock_result.pr_url = None
        mock_result.error = None
        mock_result.skipped_reason = None
        mock_result.patch_stats = {}
        mock_dry_run.return_value = mock_result

        # Should not raise TypeError
        try:
            exit_code = _cmd_assisted_pr(
                target_issue_url="https://github.com/owner/repo/issues/1",
                mode="assisted",
                confirm_publish=False,
                dry_run=True,
                patch_workdir=None,
            )
            # If we get here, no TypeError was raised
            assert exit_code == 0
        except TypeError as e:
            pytest.fail(f"subprocess.run() unpacking error: {e}")

    @patch("contrib_center.main.Policy")
    def test_mode_safe_rejected(self, mock_policy_class, capsys):
        """--mode safe should be rejected."""
        from contrib_center.main import _cmd_assisted_pr

        # Setup mock policy
        mock_policy = MagicMock()
        mock_policy.mode = "safe"  # This will be overridden by mode argument
        mock_policy_class.load.return_value = mock_policy

        exit_code = _cmd_assisted_pr(
            target_issue_url="https://github.com/owner/repo/issues/1",
            mode="safe",
            confirm_publish=False,
            dry_run=True,
            patch_workdir=None,
        )

        assert exit_code == 1
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is False
        assert "requires --mode assisted" in output["error"]

    @patch("contrib_center.main.Policy")
    def test_mode_assisted_without_patch_workdir_rejected(self, mock_policy_class, capsys):
        """--mode assisted --confirm-publish true without --patch-workdir should be rejected."""
        from contrib_center.main import _cmd_assisted_pr

        # Setup mock policy
        mock_policy = MagicMock()
        mock_policy.mode = "assisted"
        mock_policy_class.load.return_value = mock_policy

        exit_code = _cmd_assisted_pr(
            target_issue_url="https://github.com/owner/repo/issues/1",
            mode="assisted",
            confirm_publish=True,
            dry_run=False,
            patch_workdir=None,  # No patch_workdir
        )

        assert exit_code == 1
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is False
        assert "patch_workdir" in output.get("error", "").lower() or "--patch-workdir" in output.get("error", "")

    @patch("contrib_center.external_pr_publisher.publish_external_pr")
    @patch("contrib_center.main.Policy")
    def test_mode_assisted_with_empty_patch_rejected(self, mock_policy_class, mock_publish, capsys):
        """--mode assisted --confirm-publish true with empty patch should return empty_patch."""
        from contrib_center.main import _cmd_assisted_pr

        # Setup mock policy
        mock_policy = MagicMock()
        mock_policy.mode = "assisted"
        mock_policy_class.load.return_value = mock_policy

        # Mock publish_external_pr to return empty_patch result
        mock_result = MagicMock()
        mock_result.ok = False
        mock_result.skipped_reason = "empty_patch"
        mock_result.error = "No diff found in patch_workdir"
        mock_publish.return_value = mock_result

        # Create a temporary directory as patch_workdir
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            exit_code = _cmd_assisted_pr(
                target_issue_url="https://github.com/owner/repo/issues/1",
                mode="assisted",
                confirm_publish=True,
                dry_run=False,
                patch_workdir=tmpdir,
            )

        assert exit_code == 1
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is False
        assert output.get("skipped_reason") == "empty_patch"

    @patch("contrib_center.external_pr_publisher.dry_run_external_pr")
    @patch("contrib_center.main.Policy")
    def test_dry_run_no_fork_or_push(self, mock_policy_class, mock_dry_run):
        """Dry-run should call dry_run_external_pr, not publish_external_pr."""
        from contrib_center.main import _cmd_assisted_pr

        # Setup mock policy
        mock_policy = MagicMock()
        mock_policy.mode = "assisted"
        mock_policy_class.load.return_value = mock_policy

        # Track which function was called
        called_func = None
        def track_call(*args, **kwargs):
            nonlocal called_func
            called_func = "dry_run"
            mock_result = MagicMock()
            mock_result.ok = True
            return mock_result

        mock_dry_run.side_effect = track_call

        exit_code = _cmd_assisted_pr(
            target_issue_url="https://github.com/owner/repo/issues/1",
            mode="assisted",
            confirm_publish=False,
            dry_run=True,
            patch_workdir=None,
        )

        assert exit_code == 0
        assert called_func == "dry_run"


class TestSubprocessRunUnpacking:
    """Test that subprocess.run() is not incorrectly unpacked as tuple."""

    def test_subprocess_run_returns_completedprocess(self):
        """Verify subprocess.run() returns CompletedProcess, not tuple."""
        import subprocess

        proc = subprocess.run(
            ["echo", "hello"],
            capture_output=True,
            text=True,
            check=False,
        )

        # Should be CompletedProcess, not tuple
        assert hasattr(proc, "returncode")
        assert hasattr(proc, "stdout")
        assert hasattr(proc, "stderr")

        # Should NOT be unpackable as tuple (this would raise TypeError)
        with pytest.raises(TypeError):
            rc, out, err = proc  # This should raise TypeError


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
