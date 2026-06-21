"""Tests for profile_bot — _replace_block, _build_block, and
update_profile() with full filesystem mocking."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from contrib_center.bots import profile_bot
from contrib_center.policy import Policy, Repository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_policy(profile_full_name: str = "disdorqin/disdorqin") -> Policy:
    repos = [
        Repository(
            name="disdorqin",
            full_name=profile_full_name,
            purpose="GitHub Profile README",
            allow={"update_readme": True, "push_commit": True},
        )
    ]
    return Policy(mode="safe", public_repos=repos)


# ---------------------------------------------------------------------------
# _replace_block
# ---------------------------------------------------------------------------

class TestReplaceBlock:
    def test_block_exists_replaces(self):
        original = "# Hello\n<!-- DAILY-BOT:START -->old<!-- DAILY-BOT:END -->\nfooter"
        new_block = "<!-- DAILY-BOT:START -->new<!-- DAILY-BOT:END -->"
        result, changed = profile_bot._replace_block(original, new_block)
        assert changed is True
        assert "new" in result
        assert "old" not in result

    def test_no_block_appends(self):
        original = "# Hello\nfooter"
        new_block = "<!-- DAILY-BOT:START -->new<!-- DAILY-BOT:END -->"
        result, changed = profile_bot._replace_block(original, new_block)
        assert changed is True
        assert result.strip().endswith("<!-- DAILY-BOT:END -->")

    def test_block_identical_no_change(self):
        block = "<!-- DAILY-BOT:START -->same<!-- DAILY-BOT:END -->"
        original = f"# Hello\n{block}\nfooter"
        result, changed = profile_bot._replace_block(original, block)
        assert changed is False


# ---------------------------------------------------------------------------
# _build_block
# ---------------------------------------------------------------------------

class TestBuildBlock:
    def test_contains_markers(self):
        report = {"counters": {}, "candidates": [], "actions": []}
        block = profile_bot._build_block(report)
        assert "<!-- DAILY-BOT:START -->" in block
        assert "<!-- DAILY-BOT:END -->" in block

    def test_contains_safe_mode_badge(self):
        report = {"counters": {}, "candidates": [], "actions": []}
        block = profile_bot._build_block(report)
        assert "SAFE MODE" in block


# ---------------------------------------------------------------------------
# update_profile — full mock of filesystem + git
# ---------------------------------------------------------------------------

class TestUpdateProfile:
    @patch("contrib_center.bots.profile_bot.visibility_guard", autospec=True)
    def test_no_profile_repo_returns_false(self, mock_vg):
        policy = Policy(mode="safe", public_repos=[])
        result = profile_bot.update_profile(policy, {})
        assert result["ok"] is False
        assert "no_profile" in result["reason"]

    @patch("contrib_center.bots.profile_bot.visibility_guard", autospec=True)
    @patch("contrib_center.bots.profile_bot._clone_profile_repo", return_value=True)
    @patch("contrib_center.bots.profile_bot._commit_and_push", return_value=(True, "ok"))
    @patch("contrib_center.bots.profile_bot.Path")
    def test_changed_pushes(self, mock_path_class, mock_push, mock_clone, mock_vg):
        # Mock Path("data") / "profile_workdir" chain
        mock_workdir = MagicMock()
        mock_readme = MagicMock()
        mock_readme.exists.return_value = True
        mock_readme.read_text.return_value = "# Old\n<!-- DAILY-BOT:START -->old<!-- DAILY-BOT:END -->"
        mock_workdir.__truediv__ = lambda self, name: (
            mock_readme if name == "README.md" else MagicMock()
        )
        mock_readme.write_text = MagicMock()
        # Make Path("data") / "profile_workdir" return mock_workdir
        mock_path_class.return_value.__truediv__ = lambda self, name: (
            mock_workdir if name == "profile_workdir" else MagicMock()
        )
        # Simplify: just mock readme_path.read_text directly via patch.object
        with patch.object(Path, "read_text", return_value="# Old\n<!-- DAILY-BOT:START -->old<!-- DAILY-BOT:END -->"):
            with patch.object(Path, "write_text", return_value=None):
                with patch.object(Path, "exists", return_value=True):
                    policy = _make_policy()
                    report = {"counters": {"public_repos_scanned": 1}, "candidates": [], "actions": []}
                    result = profile_bot.update_profile(policy, report)
        assert result["ok"] is True
        assert result["changed"] is True
        assert result["pushed"] is True

    @patch("contrib_center.bots.profile_bot.visibility_guard", autospec=True)
    @patch("contrib_center.bots.profile_bot._clone_profile_repo", return_value=True)
    @patch("contrib_center.bots.profile_bot._commit_and_push", return_value=(True, "ok"))
    @patch("contrib_center.bots.profile_bot.Path")
    def test_no_change_skips_commit(self, mock_path_class, mock_push, mock_clone, mock_vg):
        block = "<!-- DAILY-BOT:START -->same<!-- DAILY-BOT:END -->"
        with patch.object(Path, "read_text", return_value=f"# Hello\n{block}\n"):
            with patch.object(Path, "write_text", return_value=None):
                with patch.object(Path, "exists", return_value=True):
                    policy = _make_policy()
                    report = {"counters": {}, "candidates": [], "actions": []}
                    result = profile_bot.update_profile(policy, report)
        assert result["changed"] is False
        assert result["committed"] is False
        assert result["pushed"] is False

    @patch("contrib_center.bots.profile_bot.visibility_guard", autospec=True)
    def test_guard_fail_aborts(self, mock_vg):
        mock_vg.guard_own_repo_operation.side_effect = (
            profile_bot.visibility_guard.PermissionError_("denied")
        )
        policy = _make_policy()
        result = profile_bot.update_profile(policy, {})
        assert result["ok"] is False
        assert "guard" in result["reason"]
