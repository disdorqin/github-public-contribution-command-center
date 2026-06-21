"""Own-repo scanner.

For each public whitelisted repo, this bot emits a list of "suggestions":
- README improvements (length, missing install/usage sections)
- TODO / FIXME scan
- Open issues snapshot
- Whether CI/workflows appear to be configured

In Safe Mode v1 this bot does NOT push, does NOT open issues, and does
NOT create PRs. It only emits suggestions for the daily report and for
human review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import github_client, logger
from ..policy import Policy, Repository


@dataclass
class OwnRepoSuggestion:
    full_name: str
    purpose: str
    readme_present: bool = False
    readme_too_short: bool = False
    missing_install_section: bool = False
    missing_usage_section: bool = False
    todo_count: int = 0
    open_issues: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _check_readme(text: str) -> dict[str, bool]:
    return {
        "readme_present": bool(text.strip()),
        "readme_too_short": len(text.strip()) < 400,
        "missing_install_section": not re.search(r"##\s*install", text, re.I),
        "missing_usage_section": not re.search(r"##\s*usage", text, re.I),
    }


def _scan_local_todos(workdir: Path) -> int:
    if not workdir.exists():
        return 0
    n = 0
    for p in workdir.rglob("*.py"):
        try:
            n += sum(
                1
                for line in p.read_text(encoding="utf-8", errors="ignore").splitlines()
                if re.search(r"\b(TODO|FIXME)\b", line)
            )
        except OSError:
            continue
    return n


def scan_repo(
    repo: Repository, policy: Policy, workdir: Path | None = None
) -> OwnRepoSuggestion:
    """Scan one whitelisted public repo. Network calls go through the guard."""
    sug = OwnRepoSuggestion(full_name=repo.full_name, purpose=repo.purpose)
    try:
        readme = github_client.get_readme(repo.full_name, policy)
    except Exception as e:  # noqa: BLE001
        sug.notes.append(f"readme_fetch_error: {e}")
        readme = ""

    flags = _check_readme(readme)
    sug.readme_present = flags["readme_present"]
    sug.readme_too_short = flags["readme_too_short"]
    sug.missing_install_section = flags["missing_install_section"]
    sug.missing_usage_section = flags["missing_usage_section"]

    if workdir is not None:
        sug.todo_count = _scan_local_todos(workdir)

    try:
        issues = github_client.list_own_repo_issues(repo.full_name, policy)
        sug.open_issues = [i.get("title", "") for i in issues][:10]
    except Exception as e:  # noqa: BLE001
        sug.notes.append(f"issues_fetch_error: {e}")

    logger.log_action(
        "own_repo_scanned",
        repo=repo.full_name,
        readme_present=sug.readme_present,
        todo_count=sug.todo_count,
        open_issues=len(sug.open_issues),
    )
    return sug


def scan_all(
    policy: Policy, workdir_map: dict[str, Path] | None = None
) -> list[OwnRepoSuggestion]:
    workdir_map = workdir_map or {}
    out: list[OwnRepoSuggestion] = []
    for r in policy.public_repos:
        out.append(scan_repo(r, policy, workdir=workdir_map.get(r.full_name)))
    return out
