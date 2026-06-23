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
  7. Persist the patch to data/patches/YYYY-MM-DD/ for later use.

LLM Routing Support:
  - Reads LLM provider config from environment variables:
    - OPENAI_API_KEY (or LLM_PRIMARY_API_KEY)
    - OPENAI_BASE_URL (or LLM_PRIMARY_BASE_URL)
    - MSWEA_MODEL (or LLM_PRIMARY_MODEL)
  - Falls back to llm_router module if available.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
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
    patch_file: str | None = None
    patch_workdir: str | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _git_diff(workdir: Path) -> tuple[str, list[str]]:
    # First, find untracked files (new files not yet staged)
    untracked_proc = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )
    untracked_files = [f for f in untracked_proc.stdout.splitlines() if f.strip()]

    # Use git add -N to make git diff show new file content
    if untracked_files:
        subprocess.run(
            ["git", "add", "-N", *untracked_files],
            cwd=workdir,
            capture_output=True,
            text=True,
            check=False,
        )

    # Generate diff (now includes untracked files)
    proc = subprocess.run(
        ["git", "diff", "--no-color", "--unified=3"],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )
    diff = proc.stdout
    
    # Get changed files (including untracked)
    files_proc = subprocess.run(
        ["git", "diff", "--name-only"],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )
    files = [f for f in files_proc.stdout.splitlines() if f.strip()]
    
    # Also add untracked files to changed_files if not already there
    for f in untracked_files:
        if f not in files:
            files.append(f)
    
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


def _build_llm_env() -> dict[str, str]:
    """Build subprocess environment with LLM provider config.

    Returns an environment dict with LLM provider settings injected.
    """
    env = {**os.environ}
    
    # Suppress mini-swe-agent startup message to avoid Unicode errors on Windows
    env["MSWEA_SILENT_STARTUP"] = "1"

    # Try to get LLM config from llm_router if available
    try:
        from ..llm_router import load_llm_routes, build_provider_configs
        
        routes = load_llm_routes()
        configs = build_provider_configs(routes)
        
        if configs:
            # Use the first configured provider
            provider = configs[0]
            api_key = provider.get_api_key()
            base_url = provider.get_base_url()
            model = provider.get_model()
            
            if api_key:
                env["OPENAI_API_KEY"] = api_key
            if base_url:
                env["OPENAI_BASE_URL"] = base_url
            if model:
                env["MSWEA_MODEL"] = model
    except ImportError:
        # Fall back to environment variables
        pass

    # Ensure minimum required env vars are set
    if "OPENAI_API_KEY" not in env:
        env["OPENAI_API_KEY"] = env.get("LLM_PRIMARY_API_KEY", "")
    if "OPENAI_BASE_URL" not in env:
        env["OPENAI_BASE_URL"] = env.get("LLM_PRIMARY_BASE_URL", "")
    if "MSWEA_MODEL" not in env:
        env["MSWEA_MODEL"] = env.get("LLM_PRIMARY_MODEL", "")

    return env


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

        # LLM Routing: inject provider config into subprocess environment
        subprocess_env = _build_llm_env()

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
            env=subprocess_env,
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

        # Persist the diff for the daily report and later PR publishing.
        # Save to data/patches/YYYY-MM-DD/ for persistence
        today = datetime.now().strftime("%Y-%m-%d")
        repo_slug = repo_full_name.replace("/", "-")
        
        # Extract issue number from URL
        issue_number = "unknown"
        if "/issues/" in issue_url:
            try:
                issue_number = issue_url.split("/issues/")[-1].strip("/")
            except Exception:
                pass
        
        # Generate short hash from issue_url
        short_hash = hashlib.sha256(issue_url.encode()).hexdigest()[:8]
        
        # Create patches directory
        patches_dir = Path("data/patches") / today
        patches_dir.mkdir(parents=True, exist_ok=True)
        
        # Define patch file and metadata file paths
        patch_filename = f"{repo_slug}-issue-{issue_number}-{short_hash}.diff"
        metadata_filename = f"{repo_slug}-issue-{issue_number}-{short_hash}.json"
        
        patch_file_path = patches_dir / patch_filename
        metadata_file_path = patches_dir / metadata_filename
        
        # Save metadata JSON (common fields)
        metadata = {
            "issue_url": issue_url,
            "repo": repo_full_name,
            "score": score,
            "changed_files": files,
            "diff_lines": diff_lines,
            "tests_passed": result.tests_passed,
            "patch_generated": result.patch_generated,  # Add patch_generated field
            "pr_title": result.pr_title,
            "pr_body": result.pr_body,
            "generated_at": datetime.now().isoformat(),
        }
        
        # Only write patch file and metadata if patch was generated
        if result.patch_generated:
            # Save diff to file
            patch_file_path.write_text(diff, encoding="utf-8")
            
            # Update result with patch file paths
            result.patch_file = str(patch_file_path)
            result.patch_workdir = str(patches_dir)
            
            # Add patch_file to metadata
            metadata["patch_file"] = str(patch_file_path)
            metadata["publishable"] = True
            
            # Save metadata JSON
            metadata_file_path.write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            
            logger.log_action(
                "patch_persisted",
                repo=repo_full_name,
                issue=issue_url,
                files=len(files),
                diff_lines=diff_lines,
                patch_file=str(patch_file_path),
            )
        else:
            # Patch not generated (empty diff)
            # Write a failure record to patch_drafts.jsonl, but NOT to data/patches/YYYY-MM-DD/
            metadata["patch_generated"] = False
            metadata["publishable"] = False
            metadata["skip_reason"] = "empty_patch"
            
            logger.log_action(
                "patch_not_generated",
                repo=repo_full_name,
                issue=issue_url,
                reason="empty_patch",
            )
        
        # Always log to patch_drafts.jsonl (for reporting)
        patch_drafts_path = Path("data/patch_drafts.jsonl")
        with open(patch_drafts_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(metadata, ensure_ascii=False) + "\n")
        
        # Also save to temp output dir for backward compatibility (only if patch generated)
        if result.patch_generated:
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
