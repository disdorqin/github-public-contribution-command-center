"""Wrapper around mini-swe-agent used as a patch engine.

This module NEVER imports private fields or rewires mini-swe-agent's
internals. It just calls the public entry point as a subprocess and
parses the output.

In Safe Mode v1 we DO NOT call ``git push`` and we DO NOT call
``gh pr create``. We only:
  1. Verify the candidate repo is public (via visibility_guard).
  2. Shallow-clone the public repo into a temp workdir.
  3. Run `python -m minisweagent.run.mini --task "..." --output ...`.
  4. Collect a unified diff and the changed-file list.
  5. Best-effort run the project's tests if a manifest is present.
  6. Return a structured dict.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import logger, visibility_guard
from ..policy import Policy


@dataclass
class PatchResult:
    repo: str
    issue_url: str
    score: float
    patch_generated: bool = False
    tests_passed: bool | None = None
    changed_files: list[str] = field(default_factory=list)
    diff_lines: int = 0
    pr_title: str = ""
    pr_body: str = ""
    mode: str = "safe"
    published: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _git_diff(workdir: Path) -> tuple[str, list[str]]:
    proc = subprocess.run(
        ["git", "diff", "--no-color", "--unified=3"],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )
    diff = proc.stdout
    files_proc = subprocess.run(
        ["git", "diff", "--name-only"],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )
    files = [f for f in files_proc.stdout.splitlines() if f.strip()]
    return diff, files


def _count_diff_lines(diff: str) -> int:
    return sum(
        1
        for line in diff.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )


def _try_run_tests(workdir: Path) -> bool | None:
    """Best-effort: run pytest if it looks like a Python project."""
    has_py = any(workdir.rglob("*.py"))
    if not has_py:
        return None
    if not shutil.which("pytest"):
        return None
    try:
        proc = subprocess.run(
            ["pytest", "-q", "--maxfail=1"],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        return None


def generate_patch(
    issue_url: str,
    repo_full_name: str,
    score: float,
    policy: Policy,
) -> PatchResult:
    """Run mini-swe-agent in a temp workdir and capture a patch.

    Safe Mode: never push, never open a PR. The PR title/body is generated
    but only stored in the result dict for human review.
    """
    result = PatchResult(
        repo=repo_full_name, issue_url=issue_url, score=score, mode=policy.mode
    )

    try:
        # Use external-public-read guard: proves repo is public
        # without requiring allowlist membership.
        # Operation "clone" is in ALLOWED_READ_OPERATIONS.
        visibility_guard.guard_external_public_repo_read(
            repo_full_name, "clone", policy
        )
    except visibility_guard.PermissionError_ as e:
        result.error = str(e)
        logger.log_reject("mini_swe_clone_denied", repo=repo_full_name, error=str(e))
        return result

    with tempfile.TemporaryDirectory(prefix="contrib_center_") as tmp:
        workdir = Path(tmp) / repo_full_name.split("/")[-1]
        clone = subprocess.run(
            [
                "git",
                "clone",
                "--depth=50",
                f"https://github.com/{repo_full_name}.git",
                str(workdir),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if clone.returncode != 0:
            result.error = f"clone_failed: {clone.stderr.strip()[:200]}"
            logger.log_action(
                "clone_failed", repo=repo_full_name, stderr=clone.stderr.strip()[:200]
            )
            return result

        # Build the mini-swe-agent task from the issue URL.
        task = (
            f"Read the GitHub issue at {issue_url} and produce a minimal "
            f"patch in the current working directory. Do NOT push. Do NOT "
            f"open a PR. Just edit files and exit. The repo is {repo_full_name}."
        )

        out_dir = Path(tmp) / "msa_output"
        out_dir.mkdir(parents=True, exist_ok=True)

        msa = subprocess.run(
            [
                "python",
                "-m",
                "minisweagent.run.mini",
                "--task",
                task,
                "--output",
                str(out_dir / "traj.json"),
                "--yolo",  # we are already in a sandbox tempdir
            ],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
            env={**os.environ, "MSWEA_MODEL": os.environ.get("MSWEA_MODEL", "")},
        )
        if msa.returncode != 0:
            logger.log_action(
                "msa_failed",
                repo=repo_full_name,
                stderr=msa.stderr.strip()[:200],
            )
            # Fall through: still try to compute the diff; the agent may
            # have written something even on non-zero exit.

        diff, files = _git_diff(workdir)
        diff_lines = _count_diff_lines(diff)
        result.patch_generated = bool(diff.strip())
        result.changed_files = files
        result.diff_lines = diff_lines

        if result.patch_generated:
            result.tests_passed = _try_run_tests(workdir)

        # Quality gate per rules.yml.
        rules = policy.rules
        max_files = int(rules.get("quality_gate", {}).get("max_changed_files", 5))
        max_diff = int(rules.get("quality_gate", {}).get("max_diff_lines", 200))
        if result.patch_generated:
            if len(files) > max_files or diff_lines > max_diff:
                logger.log_action(
                    "patch_exceeds_quality_gate",
                    repo=repo_full_name,
                    files=len(files),
                    diff_lines=diff_lines,
                )
                result.error = (
                    f"quality_gate_failed: files={len(files)} "
                    f"diff_lines={diff_lines} (max_files={max_files}, "
                    f"max_diff_lines={max_diff})"
                )

        result.pr_title = f"[contrib-center] candidate patch for {repo_full_name}"
        result.pr_body = (
            f"Auto-generated draft from the public contribution center.\n\n"
            f"- Issue: {issue_url}\n"
            f"- Score: {score:.2f}\n"
            f"- Mode: {policy.mode}\n"
            f"- Tests passed: {result.tests_passed}\n\n"
            f"NOTE: This PR was NOT published. It is a draft for human review.\n"
        )

        # Persist the diff for the daily report.
        diff_path = out_dir / "patch.diff"
        diff_path.write_text(diff, encoding="utf-8")
        logger.log_action(
            "patch_generated",
            repo=repo_full_name,
            issue=issue_url,
            files=len(files),
            diff_lines=diff_lines,
        )

    return result
