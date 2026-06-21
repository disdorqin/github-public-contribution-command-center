"""Profile README bot.

Updates the GitHub Profile README in the repo designated by
``config/public_repos.yml`` as ``purpose: GitHub Profile README``. Only
the fenced block delimited by ``<!-- DAILY-BOT:START --> ... <!--
DAILY-BOT:END -->`` is replaced; the rest of the README is preserved.

In Safe Mode this bot MAY update the profile README (it is your own
public allowlisted repo). It never modifies other files in the profile
repo and never pushes to external repos.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

from .. import github_client, logger
from ..adapters import metrics_adapter, readme_stats_adapter, snake_adapter
from ..policy import Policy
from .. import visibility_guard

START_MARK = "<!-- DAILY-BOT:START -->"
END_MARK = "<!-- DAILY-BOT:END -->"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clone_profile_repo(full_name: str, workdir: Path) -> bool:
    """Shallow-clone the profile repo so we can edit its README."""
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "git",
            "clone",
            "--depth=20",
            f"https://github.com/{full_name}.git",
            str(workdir),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return proc.returncode == 0


def _git_run(workdir: Path, args: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return (proc.returncode, proc.stdout, proc.stderr)


def _commit_and_push(workdir: Path, full_name: str, branch: str = "main") -> tuple[bool, str]:
    """Stage README.md, commit, and push back to GitHub.

    Uses ``GH_TOKEN`` / ``GITHUB_TOKEN`` env var for auth via the
    ``x-access-token`` URL scheme.  Returns ``(pushed, message)``.
    """
    # Check for staged changes
    rc, _, _ = _git_run(workdir, ["diff", "--cached", "--quiet"])
    if rc == 0:
        return (False, "no_staged_changes")

    # Commit
    _git_run(workdir, ["config", "user.name", "github-public-contribution-bot"])
    _git_run(workdir, ["config", "user.email", "bot@local"])
    rc, _, err = _git_run(
        workdir,
        ["commit", "-m", "chore: update daily contribution center profile block [skip ci]"],
    )
    if rc != 0:
        return (False, f"commit_failed: {err.strip()[:200]}")

    # Push — use token from env
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if token:
        remote = f"https://x-access-token:{token}@github.com/{full_name}.git"
        rc, _, err = _git_run(workdir, ["push", remote, branch])
    else:
        # Fallback: rely on credential helper / SSH
        rc, _, err = _git_run(workdir, ["push", "origin", branch])
    if rc != 0:
        return (False, f"push_failed: {err.strip()[:300]}")
    return (True, "ok")


def _replace_block(text: str, new_block: str) -> tuple[str, bool]:
    """Replace the DAILY-BOT fenced block.

    Returns ``(new_text, changed)``.  If the block is already identical,
    returns ``changed=False`` so we can skip the commit.
    """
    if START_MARK in text and END_MARK in text:
        start = text.index(START_MARK)
        end = text.index(END_MARK) + len(END_MARK)
        old_block = text[start:end]
        if old_block == new_block:
            return text, False
        return text[:start] + new_block + text[end:], True
    # First run — append at the end.
    return text.rstrip() + "\n\n" + new_block + "\n", True


def _build_block(report: dict) -> str:
    today = date.today().isoformat()
    metrics = metrics_adapter.render()
    snake = snake_adapter.render()
    stats = readme_stats_adapter.render()
    counters = report.get("counters", {})
    candidates = report.get("candidates", [])
    actions = report.get("actions", [])
    lines = [
        START_MARK,
        "## ⚡ GitHub Public Contribution Command Center",
        "",
        "> Status: SAFE MODE",
        "> Scope: PUBLIC REPOS ONLY",
        "> Private repo access: BLOCKED",
        f"> Last update: {today}",
        "",
        "### 今日作战状态",
        "",
        "| 指标 | 数量 |",
        "|---|---:|",
        f"| 扫描 public 仓库 | {counters.get('public_repos_scanned', 0)} |",
        f"| 跳过 private/internal 仓库 | {counters.get('repos_skipped', 0)} |",
        f"| 外部 issue 候选 | {len(candidates)} |",
        f"| 生成 patch 草稿 | {counters.get('patches_generated', 0)} |",
        "| 创建公开 PR | 0 |",
        "| 外部评论 | 0 |",
        "",
        "### 今日推荐行动",
        "",
    ]
    if actions:
        for a in actions[:5]:
            lines.append(f"- {a}")
    else:
        lines.append(f"- (今日无新增推荐行动 — 见 reports/{today}.md)")
    if metrics:
        lines.append("")
        lines.append("### Public Metrics")
        lines.append(metrics)
    if stats:
        lines.append("")
        lines.append("### Readme Stats")
        lines.append(stats)
    if snake:
        lines.append("")
        lines.append("### Contribution Snake")
        lines.append(snake)
    lines.append("")
    lines.append(END_MARK)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def update_profile(policy: Policy, report: dict) -> dict:
    """Update the profile repo's README block and push back.

    Returns a status dict::

        {
            "ok": True/False,
            "repo": "owner/profile",
            "changed": True/False,
            "committed": True/False,
            "pushed": True/False,
            "reason": None or str,
        }
    """
    profile = policy.profile_repo()
    if profile is None:
        logger.log_action("profile_repo_missing")
        return {"ok": False, "reason": "no_profile_repo_in_allowlist"}

    if not profile.allow.get("update_readme", False):
        logger.log_action("profile_readme_disabled", repo=profile.full_name)
        return {"ok": False, "reason": "update_readme_disabled"}

    # Guard — own-repo guard (allowlist + public proof)
    try:
        visibility_guard.guard_own_repo_operation(
            profile.full_name, "update_readme", policy
        )
    except Exception as e:  # noqa: BLE001
        logger.log_reject("profile_guard_failed", repo=profile.full_name, error=str(e))
        return {"ok": False, "reason": f"guard_failed: {e}"}

    workdir = Path("data") / "profile_workdir"
    if not _clone_profile_repo(profile.full_name, workdir):
        return {"ok": False, "reason": "clone_failed"}

    readme_path = workdir / "README.md"
    if not readme_path.exists():
        readme_path.write_text("", encoding="utf-8")
    text = readme_path.read_text(encoding="utf-8")
    new_block = _build_block(report)
    new_text, changed = _replace_block(text, new_block)

    if not changed:
        logger.log_action("profile_no_changes", repo=profile.full_name)
        return {
            "ok": True,
            "repo": profile.full_name,
            "changed": False,
            "committed": False,
            "pushed": False,
            "reason": "no_changes",
        }

    readme_path.write_text(new_text, encoding="utf-8")
    logger.log_action("profile_readme_block_written", repo=profile.full_name)

    # Stage only README.md — never touch other files
    _git_run(workdir, ["add", "README.md"])

    pushed, msg = _commit_and_push(workdir, profile.full_name)
    result = {
        "ok": True,
        "repo": profile.full_name,
        "changed": True,
        "committed": True,
        "pushed": pushed,
        "reason": None if pushed else msg,
    }
    logger.log_action("profile_update_done", **{k: str(v) for k, v in result.items()})
    return result
