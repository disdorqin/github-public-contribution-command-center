"""Profile README bot.

Updates the GitHub Profile README in the repo designated by
``config/public_repos.yml`` as ``purpose: GitHub Profile README``. Only
the fenced block delimited by ``<!-- DAILY-BOT:START --> ... <!--
DAILY-BOT:END -->`` is replaced; the rest of the README is preserved.
"""

from __future__ import annotations

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


def _clone_profile_repo(full_name: str, workdir: Path) -> bool:
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


def _replace_block(text: str, new_block: str) -> str:
    if START_MARK in text and END_MARK in text:
        start = text.index(START_MARK)
        end = text.index(END_MARK) + len(END_MARK)
        return text[:start] + new_block + text[end:]
    # First run — append at the end.
    return text.rstrip() + "\n\n" + new_block + "\n"


def _build_block(report: dict) -> str:
    today = date.today().isoformat()
    metrics = metrics_adapter.render()
    snake = snake_adapter.render()
    stats = readme_stats_adapter.render()
    counters = report.get("counters", {})
    candidates = report.get("candidates", [])
    actions = report.get("actions", [])
    block = f"""{START_MARK}
## ⚡ GitHub Public Contribution Command Center

> Status: SAFE MODE
> Scope: PUBLIC REPOS ONLY
> Private repo access: BLOCKED
> Last update: {today}

### 今日作战状态

| 指标 | 数量 |
|---|---:|
| 扫描 public 仓库 | {counters.get('public_repos_scanned', 0)} |
| 跳过 private/internal 仓库 | {counters.get('repos_skipped', 0)} |
| 外部 issue 候选 | {len(candidates)} |
| 生成 patch 草稿 | {counters.get('patches_generated', 0)} |
| 创建公开 PR | 0 |
| 外部评论 | 0 |

### 今日推荐行动
"""
    if actions:
        for a in actions[:5]:
            block += f"- {a}\n"
    else:
        block += f"- (今日无新增推荐行动 — 见 reports/{today}.md)\n"
    if metrics:
        block += f"\n### Public Metrics\n{metrics}\n"
    if stats:
        block += f"\n### Readme Stats\n{stats}\n"
    if snake:
        block += f"\n### Contribution Snake\n{snake}\n"
    block += END_MARK
    return block


def update_profile(policy: Policy, report: dict) -> dict:
    """Update the profile repo's README block. Returns a status dict."""
    profile = policy.profile_repo()
    if profile is None:
        logger.log_action("profile_repo_missing")
        return {"ok": False, "reason": "no_profile_repo_in_allowlist"}

    if not profile.allow.get("update_readme", False):
        logger.log_action("profile_readme_disabled", repo=profile.full_name)
        return {"ok": False, "reason": "update_readme_disabled"}

    try:
        visibility_guard.guard_repo_operation(
            profile.full_name, "update_readme", policy
        )
    except Exception as e:  # noqa: BLE001
        logger.log_reject("profile_guard_failed", repo=profile.full_name, error=str(e))
        return {"ok": False, "reason": f"guard_failed: {e}"}

    workdir = Path("data") / "profile_workdir"
    if workdir.exists():
        shutil.rmtree(workdir)
    if not _clone_profile_repo(profile.full_name, workdir):
        return {"ok": False, "reason": "clone_failed"}

    readme_path = workdir / "README.md"
    if not readme_path.exists():
        readme_path.write_text("", encoding="utf-8")
    text = readme_path.read_text(encoding="utf-8")
    new_text = _replace_block(text, _build_block(report))
    readme_path.write_text(new_text, encoding="utf-8")
    logger.log_action("profile_readme_block_written", repo=profile.full_name)
    return {
        "ok": True,
        "repo": profile.full_name,
        "readme_path": str(readme_path),
    }
