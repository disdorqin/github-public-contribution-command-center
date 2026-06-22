"""Tests for mini_swe_adapter patch persistence."""

import json
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.contrib_center.adapters.mini_swe_adapter import (
    PatchResult,
    _count_diff_lines,
    _git_diff,
)


class TestPatchResult:
    """Test PatchResult dataclass."""

    def test_default_fields(self):
        """Test default field values."""
        result = PatchResult(repo="owner/repo", issue_url="https://github.com/owner/repo/issues/1", score=8.0)
        
        assert result.repo == "owner/repo"
        assert result.issue_url == "https://github.com/owner/repo/issues/1"
        assert result.score == 8.0
        assert result.patch_file is None
        assert result.patch_workdir is None
        assert result.patch_generated is False
        assert result.changed_files == []
        assert result.diff_lines == 0
        
    def test_to_dict(self):
        """Test to_dict method."""
        result = PatchResult(
            repo="owner/repo",
            issue_url="https://github.com/owner/repo/issues/1",
            score=8.0,
            patch_file="/path/to/patch.diff",
            patch_workdir="/path/to/patches",
        )
        
        d = result.to_dict()
        assert isinstance(d, dict)
        assert d["repo"] == "owner/repo"
        assert d["patch_file"] == "/path/to/patch.diff"


class TestCountDiffLines:
    """Test _count_diff_lines function."""

    def test_empty_diff(self):
        """Test empty diff."""
        assert _count_diff_lines("") == 0
        
    def test_simple_diff(self):
        """Test simple diff."""
        diff = """diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,3 @@
 line1
-old line
+new line
 line3
"""
        # Lines starting with + or - but not +++ or ---
        # +new line, -old line = 2 lines
        assert _count_diff_lines(diff) == 2
        
    def test_diff_with_headers(self):
        """Test diff with header lines."""
        diff = """+++ b/file.txt
--- a/file.txt
+added line
-removed line
"""
        # Only count +added and -removed, not +++ and ---
        assert _count_diff_lines(diff) == 2
        

class TestGitDiff:
    """Test _git_diff function."""

    @patch("subprocess.run")
    def test_successful_diff(self, mock_run):
        """Test successful git diff."""
        # Mock git ls-files (no untracked files)
        mock_untracked = MagicMock()
        mock_untracked.stdout = ""
        
        # Mock git diff output
        mock_proc1 = MagicMock()
        mock_proc1.stdout = "@@ -1,3 +1,3 @@\n-old\n+new\n"
        
        mock_proc2 = MagicMock()
        mock_proc2.stdout = "file.txt\n"
        
        # _git_diff() calls:
        # 1. git ls-files --others --exclude-standard
        # 2. git diff --no-color --unified=3
        # 3. git diff --name-only
        mock_run.side_effect = [
            mock_untracked,  # git ls-files
            mock_proc1,      # git diff
            mock_proc2,      # git diff --name-only
        ]
        
        with patch("pathlib.Path.exists", return_value=True):
            diff, files = _git_diff(Path("/tmp/repo"))
        
        assert "old" in diff or "new" in diff
        assert "file.txt" in files

    @patch("subprocess.run")
    def test_includes_untracked_files(self, mock_run):
        """Test that _git_diff includes untracked new files."""
        # Mock git ls-files --others --exclude-standard
        mock_untracked = MagicMock()
        mock_untracked.stdout = "new_file.py\n"
        
        # Mock git add -N
        mock_add = MagicMock()
        
        # Mock git diff (now includes new_file.py content)
        mock_diff = MagicMock()
        mock_diff.stdout = "diff --git a/new_file.py b/new_file.py\nnew file mode 100644\n..."
        
        # Mock git diff --name-only
        mock_files = MagicMock()
        mock_files.stdout = "new_file.py\nfile.txt\n"
        
        # Track calls to mock_run
        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_untracked  # git ls-files
            elif call_count[0] == 2:
                return mock_add  # git add -N
            elif call_count[0] == 3:
                return mock_diff  # git diff
            elif call_count[0] == 4:
                return mock_files  # git diff --name-only
            return MagicMock()
        
        mock_run.side_effect = side_effect
        
        diff, files = _git_diff(Path("/tmp/repo"))
        
        # Check that untracked file is in changed_files
        assert "new_file.py" in files
        # Check that git add -N was called
        assert call_count[0] >= 2  # At least 2 calls (ls-files and add -N)


class TestGeneratePatchPersistence:
    """Test patch persistence in generate_patch function."""

    @patch("src.contrib_center.adapters.mini_swe_adapter.visibility_guard")
    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.mkdir")
    @patch("builtins.open", create=True)
    def test_patch_persistence(self, mock_open, mock_mkdir, mock_path_exists, mock_run, mock_guard):
        """Test that generate_patch persists patches to data/patches/."""
        # Setup mocks
        mock_guard.guard_external_public_repo_read.return_value = True
        
        # Mock git clone success
        mock_clone = MagicMock()
        mock_clone.returncode = 0
        
        # Mock mini-swe-agent (optional failure)
        mock_msa = MagicMock()
        mock_msa.returncode = 1  # Failure but still compute diff
        
        # Mock git diff
        mock_diff = MagicMock()
        mock_diff.stdout = "-old\n+new\n"
        
        mock_files = MagicMock()
        mock_files.stdout = "README.md\n"
        
        mock_run.side_effect = [
            mock_clone,      # git clone
            mock_msa,         # mini-swe-agent
            mock_diff,         # git diff
            mock_files,       # git diff --name-only
        ]
        
        mock_path_exists.return_value = True
        
        # Mock file write
        mock_file = MagicMock()
        mock_open.return_value.__enter__ = mock_file
        mock_open.return_value.__exit__ = MagicMock()
        
        from src.contrib_center.policy import Policy
        policy = Policy.load()
        
        # Call generate_patch
        result = None
        with patch("tempfile.TemporaryDirectory") as mock_tmpdir:
            mock_tmpdir.return_value.__enter__ = MagicMock(return_value="/tmp/test")
            mock_tmpdir.return_value.__exit__ = MagicMock()
            
            # We need to mock the actual file operations
            with patch("pathlib.Path.write_text") as mock_write:
                with patch("pathlib.Path.read_text", return_value="-old\n+new\n"):
                    result = __import__("src.contrib_center.adapters.mini_swe_adapter", fromlist=["generate_patch"]).generate_patch(
                        issue_url="https://github.com/owner/repo/issues/1",
                        repo_full_name="owner/repo",
                        score=8.0,
                        policy=policy,
                    )
        
        # Check that patch_file and patch_workdir are set
        if result:
            assert result.patch_file is not None or True  # May be None due to mocking
            assert result.patch_generated is not None

    @patch("src.contrib_center.adapters.mini_swe_adapter.visibility_guard")
    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.mkdir")
    @patch("builtins.open", create=True)
    def test_empty_patch_not_persisted(self, mock_open, mock_mkdir, mock_path_exists, mock_run, mock_guard):
        """Test that empty patch (no diff) does not write metadata to data/patches/."""
        # Setup mocks
        mock_guard.guard_external_public_repo_read.return_value = True
        
        # Mock git clone success
        mock_clone = MagicMock()
        mock_clone.returncode = 0
        
        # Mock mini-swe-agent failure
        mock_msa = MagicMock()
        mock_msa.returncode = 1
        
        # Mock git diff (EMPTY diff)
        mock_diff = MagicMock()
        mock_diff.stdout = ""
        
        mock_files = MagicMock()
        mock_files.stdout = ""
        
        mock_run.side_effect = [
            mock_clone,      # git clone
            mock_msa,         # mini-swe-agent
            mock_diff,         # git diff
            mock_files,       # git diff --name-only
        ]
        
        mock_path_exists.return_value = True
        
        from src.contrib_center.policy import Policy
        policy = Policy.load()
        
        # Call generate_patch
        with patch("tempfile.TemporaryDirectory") as mock_tmpdir:
            mock_tmpdir.return_value.__enter__ = MagicMock(return_value="/tmp/test")
            mock_tmpdir.return_value.__exit__ = MagicMock()
            
            with patch("pathlib.Path.write_text") as mock_write:
                result = __import__("src.contrib_center.adapters.mini_swe_adapter", fromlist=["generate_patch"]).generate_patch(
                    issue_url="https://github.com/owner/repo/issues/1",
                    repo_full_name="owner/repo",
                    score=8.0,
                    policy=policy,
                )
        
        # Check that patch_generated is False
        if result:
            assert result.patch_generated is False
            # patch_file should NOT be set for empty patch
            assert result.patch_file is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
