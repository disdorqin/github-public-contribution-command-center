"""Tests for autopilot_publisher module."""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.contrib_center.autopilot_publisher import (
    _can_publish_today,
    _check_cooldown,
    _is_safe_candidate,
    _load_patch_metadata,
    _load_published_prs,
    autopilot_publish_one,
)
from src.contrib_center.external_pr_publisher import PublishResult
from src.contrib_center.policy import Policy


class TestLoadPatchMetadata:
    """Test _load_patch_metadata function."""

    def test_no_patches_dir(self):
        """Test when patches directory doesn't exist."""
        result = _load_patch_metadata("2099-01-01")
        assert result == []

    def test_load_valid_metadata(self):
        """Test loading valid patch metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            patches_dir = Path(tmpdir) / "data" / "patches" / "2026-06-21"
            patches_dir.mkdir(parents=True)

            # Create a valid metadata JSON
            metadata = {
                "repo": "owner/repo",
                "issue_url": "https://github.com/owner/repo/issues/1",
                "score": 8.5,
                "changed_files": ["README.md"],
                "diff_lines": 10,
            }
            json_file = patches_dir / "patch_001.json"
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(metadata, f)

            # Create corresponding .diff file
            diff_file = patches_dir / "patch_001.diff"
            diff_file.write_text("diff --git a/README.md b/README.md\n")

            # Change to tmpdir so relative paths work
            old_cwd = os.getcwd()
            os.chdir(tmpdir)

            try:
                result = _load_patch_metadata("2026-06-21")
                assert len(result) == 1
                assert result[0]["repo"] == "owner/repo"
                # patch_file should be a relative path
                assert "patch_001.diff" in result[0]["patch_file"]
            finally:
                os.chdir(old_cwd)

    def test_skip_invalid_json(self):
        """Test skipping invalid JSON files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            patches_dir = Path(tmpdir) / "data" / "patches" / "2026-06-21"
            patches_dir.mkdir(parents=True)

            # Create invalid JSON
            invalid_file = patches_dir / "invalid.json"
            invalid_file.write_text("{invalid json}")

            # Create valid JSON
            valid_metadata = {"repo": "owner/repo", "score": 8.0}
            valid_file = patches_dir / "valid.json"
            with open(valid_file, "w", encoding="utf-8") as f:
                json.dump(valid_metadata, f)

            old_cwd = os.getcwd()
            os.chdir(tmpdir)

            try:
                result = _load_patch_metadata("2026-06-21")
                assert len(result) == 1
                assert result[0]["repo"] == "owner/repo"
            finally:
                os.chdir(old_cwd)


class TestLoadPublishedPRs:
    """Test _load_published_prs function."""

    def test_no_file(self):
        """Test when published_prs.jsonl doesn't exist."""
        result = _load_published_prs()
        assert result == []

    def test_load_valid_entries(self):
        """Test loading valid published PRs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            data_dir.mkdir()
            published_file = data_dir / "published_prs.jsonl"

            # Write valid entries
            entries = [
                {"date": "2026-06-21T10:00:00", "repo": "owner/repo1"},
                {"date": "2026-06-21T11:00:00", "repo": "owner/repo2"},
            ]
            with open(published_file, "w", encoding="utf-8") as f:
                for entry in entries:
                    f.write(json.dumps(entry) + "\n")

            old_cwd = os.getcwd()
            os.chdir(tmpdir)

            try:
                result = _load_published_prs()
                assert len(result) == 2
                assert result[0]["repo"] == "owner/repo1"
                assert result[1]["repo"] == "owner/repo2"
            finally:
                os.chdir(old_cwd)

    def test_skip_invalid_lines(self):
        """Test skipping invalid lines in jsonl."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            data_dir.mkdir()
            published_file = data_dir / "published_prs.jsonl"

            # Write mix of valid and invalid
            with open(published_file, "w", encoding="utf-8") as f:
                f.write(json.dumps({"date": "2026-06-21", "repo": "owner/repo"}) + "\n")
                f.write("invalid json line\n")
                f.write(json.dumps({"date": "2026-06-22", "repo": "owner/repo2"}) + "\n")

            old_cwd = os.getcwd()
            os.chdir(tmpdir)

            try:
                result = _load_published_prs()
                assert len(result) == 2
            finally:
                os.chdir(old_cwd)


class TestCanPublishToday:
    """Test _can_publish_today function."""

    def test_can_publish(self):
        """Test when under rate limit."""
        config = {"max_external_prs_per_day": 1}
        published = []
        assert _can_publish_today(published, config) is True

    def test_rate_limit_reached(self):
        """Test when rate limit reached."""
        config = {"max_external_prs_per_day": 1}
        today = datetime.now().strftime("%Y-%m-%d")
        published = [{"date": f"{today}T10:00:00"}]
        assert _can_publish_today(published, config) is False

    def test_multiple_prs_allowed(self):
        """Test when multiple PRs allowed per day."""
        config = {"max_external_prs_per_day": 3}
        today = datetime.now().strftime("%Y-%m-%d")
        published = [
            {"date": f"{today}T10:00:00"},
            {"date": f"{today}T11:00:00"},
        ]
        assert _can_publish_today(published, config) is True


class TestCheckCooldown:
    """Test _check_cooldown function."""

    def test_no_cooldown(self):
        """Test when no cooldown needed."""
        config = {}
        published = []
        assert _check_cooldown("owner/repo", published, config) is True

    def test_same_repo_cooldown(self):
        """Test cooldown for same repo."""
        config = {"cooldown": {"same_upstream_repo_hours": 72}}
        published = [{"date": datetime.now().isoformat(), "repo": "owner/repo"}]
        assert _check_cooldown("owner/repo", published, config) is False

    def test_same_owner_cooldown(self):
        """Test cooldown for same owner."""
        config = {"cooldown": {"same_owner_hours": 24}}
        published = [{"date": datetime.now().isoformat(), "repo": "owner/repo1"}]
        assert _check_cooldown("owner/repo2", published, config) is False


class TestIsSafeCandidate:
    """Test _is_safe_candidate function."""

    @patch("src.contrib_center.autopilot_publisher.guard_external_public_repo_read")
    def test_safe_candidate(self, mock_guard):
        """Test a safe candidate."""
        mock_guard.return_value = True
        config = {
            "min_score": 7.0,
            "safety": {"max_changed_files": 5, "max_diff_lines": 200},
        }
        policy = Policy.load()
        metadata = {
            "repo": "owner/repo",
            "issue_url": "https://github.com/owner/repo/issues/1",
            "score": 8.5,
            "patch_file": "/tmp/patch.diff",
            "changed_files": ["README.md"],
            "diff_lines": 10,
        }

        # Mock Path.exists()
        with patch("pathlib.Path.exists", return_value=True):
            is_safe, reason = _is_safe_candidate(metadata, config, policy)
            assert is_safe is True
            assert reason == ""

    @patch("src.contrib_center.autopilot_publisher.guard_external_public_repo_read")
    def test_score_too_low(self, mock_guard):
        """Test candidate with low score."""
        mock_guard.return_value = True
        config = {"min_score": 7.0}
        policy = Policy.load()
        metadata = {
            "repo": "owner/repo",
            "score": 5.0,
        }

        is_safe, reason = _is_safe_candidate(metadata, config, policy)
        assert is_safe is False
        assert reason == "score_too_low"

    @patch("src.contrib_center.autopilot_publisher.guard_external_public_repo_read")
    def test_patch_file_missing(self, mock_guard):
        """Test candidate with missing patch file."""
        mock_guard.return_value = True
        config = {"min_score": 7.0}
        policy = Policy.load()
        metadata = {
            "repo": "owner/repo",
            "score": 8.0,
            "patch_file": "/tmp/nonexistent.diff",
        }

        is_safe, reason = _is_safe_candidate(metadata, config, policy)
        assert is_safe is False
        assert reason == "patch_file_missing"

    @patch("src.contrib_center.autopilot_publisher.guard_external_public_repo_read")
    def test_too_many_files(self, mock_guard):
        """Test candidate with too many changed files."""
        mock_guard.return_value = True
        config = {
            "min_score": 7.0,
            "require_diff_limits": True,
            "safety": {"max_changed_files": 5, "max_diff_lines": 200},
        }
        policy = Policy.load()
        metadata = {
            "repo": "owner/repo",
            "score": 8.0,
            "patch_file": "/tmp/patch.diff",
            "changed_files": ["file1.py", "file2.py", "file3.py", "file4.py", "file5.py", "file6.py"],
            "diff_lines": 10,
        }

        with patch("pathlib.Path.exists", return_value=True):
            is_safe, reason = _is_safe_candidate(metadata, config, policy)
            assert is_safe is False
            assert reason == "too_many_files"

    @patch("src.contrib_center.autopilot_publisher.guard_external_public_repo_read")
    def test_tests_failed(self, mock_guard):
        """Test candidate with failed tests."""
        mock_guard.return_value = True
        config = {
            "min_score": 7.0,
            "require_tests_pass_or_not_available": True,
        }
        policy = Policy.load()
        metadata = {
            "repo": "owner/repo",
            "score": 8.0,
            "patch_file": "/tmp/patch.diff",
            "tests_passed": False,
        }

        with patch("pathlib.Path.exists", return_value=True):
            is_safe, reason = _is_safe_candidate(metadata, config, policy)
            assert is_safe is False
            assert reason == "tests_failed"


class TestAutopilotPublishOne:
    """Test autopilot_publish_one function."""

    @patch("src.contrib_center.autopilot_publisher.guard_external_public_repo_read")
    @patch("src.contrib_center.autopilot_publisher._load_autopilot_config")
    @patch("src.contrib_center.autopilot_publisher._load_patch_metadata")
    @patch("src.contrib_center.autopilot_publisher._load_published_prs")
    @patch("src.contrib_center.autopilot_publisher.publish_external_pr")
    def test_successful_publish(self, mock_publish, mock_load_prs, mock_load_metadata, mock_load_config, mock_guard):
        """Test successful autopilot publish."""
        # Setup mocks
        mock_guard.return_value = True
        mock_load_config.return_value = {"enabled": True, "max_external_prs_per_day": 1}

        mock_load_prs.return_value = []

        today = datetime.now().strftime("%Y-%m-%d")
        mock_load_metadata.return_value = [
            {
                "repo": "owner/repo",
                "issue_url": "https://github.com/owner/repo/issues/1",
                "score": 8.5,
                "patch_file": "/tmp/patch.diff",
                "changed_files": ["README.md"],
                "diff_lines": 10,
            }
        ]

        mock_publish.return_value = PublishResult(
            ok=True,
            upstream_repo="owner/repo",
            fork_repo="disdorqin/repo",
            branch="contrib-center/repo/1-abc123",
            pr_url="https://github.com/owner/repo/pull/123",
        )

        # Mock Path.exists()
        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", create=True):
                result = autopilot_publish_one()

        assert result["ok"] is True
        assert result["published"] is True
        assert result["repo"] == "owner/repo"
        assert "pr_url" in result

    @patch("src.contrib_center.autopilot_publisher._load_autopilot_config")
    def test_autopilot_disabled(self, mock_load_config):
        """Test when autopilot is disabled."""
        mock_load_config.return_value = {"enabled": False}

        result = autopilot_publish_one()

        assert result["ok"] is True
        assert result["published"] is False
        assert result["reason"] == "autopilot_disabled"

    @patch("src.contrib_center.autopilot_publisher._load_autopilot_config")
    @patch("src.contrib_center.autopilot_publisher._load_published_prs")
    def test_rate_limit(self, mock_load_prs, mock_load_config):
        """Test rate limit reached."""
        mock_load_config.return_value = {"enabled": True, "max_external_prs_per_day": 1}

        today = datetime.now().strftime("%Y-%m-%d")
        mock_load_prs.return_value = [{"date": f"{today}T10:00:00"}]

        result = autopilot_publish_one()

        assert result["ok"] is True
        assert result["published"] is False
        assert result["reason"] == "rate_limit_reached"

    @patch("src.contrib_center.autopilot_publisher._load_autopilot_config")
    @patch("src.contrib_center.autopilot_publisher._load_published_prs")
    @patch("src.contrib_center.autopilot_publisher._load_patch_metadata")
    def test_no_patches(self, mock_load_metadata, mock_load_prs, mock_load_config):
        """Test when no patches generated."""
        mock_load_config.return_value = {"enabled": True, "max_external_prs_per_day": 1}
        mock_load_prs.return_value = []
        mock_load_metadata.return_value = []

        result = autopilot_publish_one()

        assert result["ok"] is True
        assert result["published"] is False
        assert result["reason"] == "no_patch_generated"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
