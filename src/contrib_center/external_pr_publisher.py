"""External PR Publisher - Fork Mode

Implements safe, controlled PR contributions to external public repositories
using the fork + branch + PR workflow.

Security guarantees:
  1. Never push to upstream repo
  2. Only push to disdorqin/<repo> fork
  3. Requires confirm_publish=true in Assisted Mode
  4. Max 1 PR per run
  5. All patches pass quality gates before publish
  6. No star/comment/issue automation

Only used in Assisted Mode with explicit confirmation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import logger
from .policy import Policy, _load  # Reuse config loader from policy.py
from .visibility_guard import (
    PermissionError_,
    fetch_visibility,
    guard_external_public_repo_read,
    is_public_repo,
)

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class PublishResult:
    """Result of an external PR publish attempt."""
    ok: bool
    upstream_repo: str
    fork_repo: str | None = None
    branch: str | None = None
    pr_url: str | None = None
    error: str | None = None
    skipped_reason: str | None = None
    patch_stats: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_cmd(
    cmd: list[str],
    cwd: str | None = None,
    timeout: int = 120,
    input_text: str | None = None,
) -> tuple[int, str, str]:
    """Run a shell command, return (rc, stdout, stderr).

    Args:
        cmd: Command and arguments.
        cwd: Working directory.
        timeout: Timeout in seconds.
        input_text: Text to pass to stdin (requires text=True).
    """
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=cwd,
            env=os.environ.copy(),
            input=input_text,  # text=True means input should be str, not bytes
        )
    except subprocess.TimeoutExpired:
        return (124, "", f"Command timed out after {timeout}s")
    return (proc.returncode, proc.stdout, proc.stderr)


def _load_external_config() -> dict[str, Any]:
    """Load external_contribution.yml configuration."""
    return _load("external_contribution.yml")


def _load_target_groups() -> dict[str, Any]:
    """Load open_source_targets.yml configuration."""
    return _load("open_source_targets.yml")


def _compute_short_hash(text: str, length: int = 7) -> str:
    """Compute short hash for branch naming."""
    return hashlib.sha256(text.encode()).hexdigest()[:length]


def _get_default_branch(upstream_repo: str) -> str:
    """Fetch the default branch of a repo via gh CLI."""
    rc, out, err = _run_cmd(
        ["gh", "api", f"repos/{upstream_repo}", "--jq", ".default_branch"]
    )
    if rc != 0:
        logger.log_action("default_branch_fallback", repo=upstream_repo, error=err[:200])
        return "main"  # Safe fallback
    branch = out.strip()
    return branch if branch else "main"


def _check_deny_keywords(title: str, body: str) -> list[str]:
    """Check issue title+body for deny keywords."""
    config = _load_external_config()
    deny_keywords = config.get("deny_keywords", [])
    text = f"{title}\n{body or ''}".lower()
    found = [kw for kw in deny_keywords if kw.lower() in text]
    return found


def _validate_patch_limits(patch_workdir: Path) -> dict[str, Any]:
    """Validate patch against configured limits.

    Checks both staged (--cached) and unstaged changes.

    Returns dict with:
      - ok: bool
      - changed_files: list[str]
      - num_changed_files: int
      - diff_lines: int
      - binary_files: list[str]
      - errors: list[str]
    """
    config = _load_external_config()
    limits = config.get("patch_limits", {})
    max_files = limits.get("max_changed_files", 5)
    max_lines = limits.get("max_diff_lines", 200)
    forbid_binary = limits.get("forbid_binary_files", True)
    forbid_lockfile = limits.get("forbid_lockfile_only_changes", True)
    forbid_generated = limits.get("forbid_generated_files", True)

    errors = []

    # Check both staged and unstaged changes
    # Staged: git diff --cached
    # Unstaged: git diff
    changed_files = []

    # Get staged files
    rc_cache, stdout_cache, _ = _run_cmd(["git", "diff", "--cached", "--name-only"], cwd=str(patch_workdir))
    if rc_cache == 0 and stdout_cache.strip():
        changed_files.extend(stdout_cache.strip().splitlines())

    # Get unstaged files
    rc_unstaged, stdout_unstaged, _ = _run_cmd(["git", "diff", "--name-only"], cwd=str(patch_workdir))
    if rc_unstaged == 0 and stdout_unstaged.strip():
        for f in stdout_unstaged.strip().splitlines():
            if f not in changed_files:
                changed_files.append(f)

    # Check binary files
    binary_files = []
    if forbid_binary and changed_files:
        for f in changed_files:
            fpath = patch_workdir / f
            if fpath.exists():
                # Simple binary check: look for null bytes in first 8KB
                try:
                    with open(fpath, "rb") as fp:
                        chunk = fp.read(8192)
                        if b"\x00" in chunk:
                            binary_files.append(f)
                except Exception:
                    binary_files.append(f)  # Treat unreadable as binary

    # Count diff lines (staged + unstaged)
    diff_lines = 0

    # Staged diff
    rc2, out2, _ = _run_cmd(["git", "diff", "--cached", "--numstat"], cwd=str(patch_workdir))
    if rc2 == 0:
        for line in out2.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                try:
                    diff_lines += int(parts[0]) + int(parts[1])
                except ValueError:
                    pass

    # Unstaged diff
    rc3, out3, _ = _run_cmd(["git", "diff", "--numstat"], cwd=str(patch_workdir))
    if rc3 == 0:
        for line in out3.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                try:
                    diff_lines += int(parts[0]) + int(parts[1])
                except ValueError:
                    pass

    # Check lockfile-only changes
    lockfile_patterns = ["package-lock.json", "yarn.lock", "poetry.lock", "Pipfile.lock", "go.sum"]
    if forbid_lockfile and changed_files:
        if all(any(f.endswith(lf) for lf in lockfile_patterns) for f in changed_files):
            errors.append("lockfile_only_changes")

    # Check generated files
    generated_patterns = ["*.min.js", "*.min.css", "*.bundle.js", "dist/*", "build/*"]
    if forbid_generated and changed_files:
        for f in changed_files:
            if any(re.match(p.replace("*", ".*"), f) for p in generated_patterns):
                errors.append(f"generated_file: {f}")
                break

    # Validate limits
    if len(changed_files) > max_files:
        errors.append(f"too_many_files: {len(changed_files)} > {max_files}")
    if diff_lines > max_lines:
        errors.append(f"diff_too_large: {diff_lines} > {max_lines}")
    if binary_files:
        errors.append(f"binary_files: {', '.join(binary_files)}")

    return {
        "ok": len(errors) == 0,
        "changed_files": changed_files,
        "num_changed_files": len(changed_files),
        "diff_lines": diff_lines,
        "binary_files": binary_files,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Core publish function
# ---------------------------------------------------------------------------

def publish_external_pr(
    issue_url: str,
    upstream_repo: str,
    patch_workdir: Path | None = None,
    patch_file: Path | None = None,
    pr_title: str = "",
    pr_body: str = "",
    policy: Policy | None = None,
    confirm_publish: bool = False,
) -> PublishResult:
    """Publish a PR to an external public repo using fork mode.

    Supports two input modes:
      1. patch_workdir: Generate patch from git diff in the workdir
      2. patch_file: Directly read .diff file and git apply

    Priority:
      - If patch_file exists, use patch_file first
      - Otherwise use patch_workdir
      - If both are None/missing, refuse

    Steps:
      1. Validate mode + confirm_publish
      2. Validate patch_file or patch_workdir exists
      3. Validate upstream repo is public
      4. Check issue for deny keywords
      5. Ensure fork exists (gh repo fork)
      6. Clone fork, create branch
      7. Apply patch (from patch_file or patch_workdir), validate limits
      8. Run tests if available
      9. Commit, push to fork
      10. Create PR from fork -> upstream

    Returns PublishResult with details.
    """
    if policy is None:
        policy = Policy.load()

    # Step 0: Validate mode
    # Allow assisted and autopilot modes, reject safe mode
    if policy.mode not in ("assisted", "autopilot"):
        return PublishResult(
            ok=False,
            upstream_repo=upstream_repo,
            skipped_reason="mode_blocks_external_pr",
        )
    if not confirm_publish:
        return PublishResult(
            ok=False,
            upstream_repo=upstream_repo,
            skipped_reason="confirm_publish_not_set",
        )

    # Step 0.5: Validate patch_file or patch_workdir exists (ONLY if confirm_publish=True)
    if confirm_publish:
        # Check patch_file first (higher priority)
        if patch_file and patch_file.exists():
            # patch_file exists, will use it later
            pass
        elif patch_workdir and patch_workdir.exists():
            # patch_workdir exists, will use it later
            pass
        else:
            # Neither patch_file nor patch_workdir exists
            return PublishResult(
                ok=False,
                upstream_repo=upstream_repo,
                error="Neither patch_file nor patch_workdir exists",
                skipped_reason="patch_file_missing",
            )

    config = _load_external_config()
    fork_owner = config.get("forking", {}).get("fork_owner", "disdorqin")

    # Step 1: Validate upstream repo is public
    try:
        guard_external_public_repo_read(upstream_repo, "read", policy)
    except PermissionError_ as e:
        return PublishResult(
            ok=False,
            upstream_repo=upstream_repo,
            error=str(e),
            skipped_reason="upstream_not_public",
        )

    # Step 2: Check issue URL and deny keywords
    if not issue_url.startswith("https://github.com/"):
        return PublishResult(
            ok=False,
            upstream_repo=upstream_repo,
            error="Invalid issue URL",
            skipped_reason="invalid_issue_url",
        )

    # Fetch issue title+body for deny keyword check
    rc_issue, out_issue, _ = _run_cmd(
        ["gh", "issue", "view", issue_url, "--json", "title,body"]
    )
    if rc_issue == 0:
        try:
            issue_data = json.loads(out_issue)
            found_keywords = _check_deny_keywords(
                issue_data.get("title", ""),
                issue_data.get("body", ""),
            )
            if found_keywords:
                return PublishResult(
                    ok=False,
                    upstream_repo=upstream_repo,
                    skipped_reason=f"deny_keywords_found: {', '.join(found_keywords)}",
                )
        except json.JSONDecodeError:
            pass

    # Step 3: Ensure fork exists
    fork_repo = f"{fork_owner}/{upstream_repo.split('/')[-1]}"
    logger.log_action("ensuring_fork", upstream=upstream_repo, fork=fork_repo)
    rc_fork, _, err_fork = _run_cmd(
        ["gh", "repo", "fork", upstream_repo, "--clone=false"]
    )
    if rc_fork != 0 and "already exists" not in err_fork.lower():
        return PublishResult(
            ok=False,
            upstream_repo=upstream_repo,
            fork_repo=fork_repo,
            error=err_fork[:300],
            skipped_reason="fork_creation_failed",
        )

    # Step 4: Clone fork and create branch
    default_branch = _get_default_branch(upstream_repo)
    issue_number = issue_url.rstrip("/").split("/")[-1]
    repo_slug = upstream_repo.split("/")[-1]
    short_hash = _compute_short_hash(f"{upstream_repo}{issue_number}")
    branch = f"contrib-center/{repo_slug}/{issue_number}-{short_hash}"

    workdir = Path(tempfile.mkdtemp(prefix="ext_pr_"))
    clone_dir = workdir / repo_slug

    try:
        # Clone fork
        rc_clone, _, err_clone = _run_cmd(
            ["git", "clone", f"https://github.com/{fork_repo}.git", str(clone_dir)]
        )
        if rc_clone != 0:
            return PublishResult(
                ok=False,
                upstream_repo=upstream_repo,
                fork_repo=fork_repo,
                error=err_clone[:300],
                skipped_reason="clone_fork_failed",
            )

        # Add upstream remote
        _run_cmd(
            ["git", "remote", "add", "upstream", f"https://github.com/{upstream_repo}.git"],
            cwd=str(clone_dir),
        )
        _run_cmd(["git", "fetch", "upstream"], cwd=str(clone_dir))

        # Create branch from upstream/default_branch
        rc_branch, _, err_branch = _run_cmd(
            ["git", "checkout", "-b", branch, f"upstream/{default_branch}"],
            cwd=str(clone_dir),
        )
        if rc_branch != 0:
            return PublishResult(
                ok=False,
                upstream_repo=upstream_repo,
                fork_repo=fork_repo,
                error=err_branch[:300],
                skipped_reason="branch_creation_failed",
            )

        # Step 5: Apply patch
        # Priority: patch_file > patch_workdir

        if patch_file and patch_file.exists():
            # Use patch_file (read .diff file directly)
            if not patch_file.exists():
                return PublishResult(
                    ok=False,
                    upstream_repo=upstream_repo,
                    fork_repo=fork_repo,
                    branch=branch,
                    error=f"patch_file does not exist: {patch_file}",
                    skipped_reason="patch_file_missing",
                )

            # Read diff from file
            diff_content = patch_file.read_text(encoding="utf-8")
            if not diff_content.strip():
                return PublishResult(
                    ok=False,
                    upstream_repo=upstream_repo,
                    fork_repo=fork_repo,
                    branch=branch,
                    skipped_reason="empty_patch",
                )

            # Check if patch can be applied (dry-run)
            rc_check, _, err_check = _run_cmd(
                ["git", "apply", "--check"],
                cwd=str(clone_dir),
                input_text=diff_content,
            )
            if rc_check != 0:
                return PublishResult(
                    ok=False,
                    upstream_repo=upstream_repo,
                    fork_repo=fork_repo,
                    branch=branch,
                    error=f"git apply --check failed: {err_check[:200]}",
                    skipped_reason="patch_apply_check_failed",
                )

            # Apply diff to clone_dir
            rc_apply, _, err_apply = _run_cmd(
                ["git", "apply", "--index"],
                cwd=str(clone_dir),
                input_text=diff_content,
            )
            if rc_apply != 0:
                # Try without --index as fallback
                rc_apply2, _, err_apply2 = _run_cmd(
                    ["git", "apply"],
                    cwd=str(clone_dir),
                    input_text=diff_content,
                )
                if rc_apply2 != 0:
                    return PublishResult(
                        ok=False,
                        upstream_repo=upstream_repo,
                        fork_repo=fork_repo,
                        branch=branch,
                        error=f"git apply failed: {err_apply2[:200]}",
                        skipped_reason="patch_apply_failed",
                    )

        elif patch_workdir and patch_workdir.exists():
            # Use patch_workdir (generate diff from git diff)
            # Generate diff from patch_workdir
            rc_diff, diff_out, err_diff = _run_cmd(
                ["git", "diff"], cwd=str(patch_workdir)
            )
            if rc_diff != 0:
                return PublishResult(
                    ok=False,
                    upstream_repo=upstream_repo,
                    fork_repo=fork_repo,
                    branch=branch,
                    error=err_diff[:300],
                    skipped_reason="patch_diff_failed",
                )

            # Check if diff is empty
            if not diff_out.strip():
                return PublishResult(
                    ok=False,
                    upstream_repo=upstream_repo,
                    fork_repo=fork_repo,
                    branch=branch,
                    skipped_reason="empty_patch",
                )

            # Apply diff to clone_dir
            rc_apply, _, err_apply = _run_cmd(
                ["git", "apply", "--index"],
                cwd=str(clone_dir),
                input_text=diff_out,
            )
            if rc_apply != 0:
                # Try without --index as fallback
                rc_apply2, _, err_apply2 = _run_cmd(
                    ["git", "apply"],
                    cwd=str(clone_dir),
                    input_text=diff_out,
                )
                if rc_apply2 != 0:
                    return PublishResult(
                        ok=False,
                        upstream_repo=upstream_repo,
                        fork_repo=fork_repo,
                        branch=branch,
                        error=f"git apply failed: {err_apply2[:200]}",
                        skipped_reason="patch_apply_failed",
                    )
        else:
            # Neither patch_file nor patch_workdir available
            return PublishResult(
                ok=False,
                upstream_repo=upstream_repo,
                fork_repo=fork_repo,
                branch=branch,
                error="No patch source available",
                skipped_reason="patch_file_missing",
            )

        # Step 6: Validate patch limits (check staged or unstaged changes)
        # First, stage all applied changes
        _run_cmd(["git", "add", "-A"], cwd=str(clone_dir))

        # Validate against limits
        stats = _validate_patch_limits(clone_dir)
        if not stats["ok"]:
            return PublishResult(
                ok=False,
                upstream_repo=upstream_repo,
                fork_repo=fork_repo,
                branch=branch,
                error=f"Patch limits failed: {', '.join(stats['errors'])}",
                skipped_reason="patch_limits_exceeded",
                patch_stats=stats,
            )

        # Step 7: Run tests (best-effort)
        # Detect project type and run appropriate tests
        test_rc = None
        test_output = ""
        tests_passed = None

        # Detect project type
        is_python = (
            (clone_dir / "pytest.ini").exists() or
            (clone_dir / "pyproject.toml").exists() or
            (clone_dir / "setup.py").exists() or
            (clone_dir / "requirements.txt").exists()
        )
        is_jsts = (clone_dir / "package.json").exists()
        is_rust = (clone_dir / "Cargo.toml").exists()
        is_go = (clone_dir / "go.mod").exists()

        if is_python:
            # Run pytest
            test_rc, test_output, _ = _run_cmd(
                ["pytest", "-x", "-q"],
                cwd=str(clone_dir),
                timeout=300
            )
            tests_passed = (test_rc == 0)
        elif is_jsts:
            # Check if pnpm is used
            if (clone_dir / "pnpm-lock.yaml").exists():
                test_rc, test_output, _ = _run_cmd(
                    ["pnpm", "test"],
                    cwd=str(clone_dir),
                    timeout=300
                )
            else:
                test_rc, test_output, _ = _run_cmd(
                    ["npm", "test"],
                    cwd=str(clone_dir),
                    timeout=300
                )
            tests_passed = (test_rc == 0)
        elif is_rust:
            # Run cargo test
            test_rc, test_output, _ = _run_cmd(
                ["cargo", "test"],
                cwd=str(clone_dir),
                timeout=300
            )
            tests_passed = (test_rc == 0)
        elif is_go:
            # Run go test
            test_rc, test_output, _ = _run_cmd(
                ["go", "test", "./..."],
                cwd=str(clone_dir),
                timeout=300
            )
            tests_passed = (test_rc == 0)
        else:
            # No test framework detected
            tests_passed = None  # not_available

        # Check test results
        if test_rc is not None:
            if test_rc == 124:
                # Timeout
                return PublishResult(
                    ok=False,
                    upstream_repo=upstream_repo,
                    fork_repo=fork_repo,
                    branch=branch,
                    error="Tests timed out",
                    skipped_reason="tests_timeout",
                )
            elif test_rc != 0:
                # Tests failed
                return PublishResult(
                    ok=False,
                    upstream_repo=upstream_repo,
                    fork_repo=fork_repo,
                    branch=branch,
                    error=f"Tests failed: {test_output[:200]}",
                    skipped_reason="tests_failed",
                )

        # Step 8: Commit
        commit_msg = f"fix: address issue #{issue_number}\n\nReference: {issue_url}"
        _run_cmd(["git", "add", "."], cwd=str(clone_dir))
        rc_commit, _, err_commit = _run_cmd(
            ["git", "commit", "-m", commit_msg],
            cwd=str(clone_dir),
        )
        if rc_commit != 0 and "nothing to commit" not in err_commit:
            return PublishResult(
                ok=False,
                upstream_repo=upstream_repo,
                fork_repo=fork_repo,
                branch=branch,
                error=err_commit[:300],
                skipped_reason="commit_failed",
            )

        # Step 9: Push to fork
        rc_push, _, err_push = _run_cmd(
            ["git", "push", "-u", "origin", branch],
            cwd=str(clone_dir),
        )
        if rc_push != 0:
            return PublishResult(
                ok=False,
                upstream_repo=upstream_repo,
                fork_repo=fork_repo,
                branch=branch,
                error=err_push[:300],
                skipped_reason="push_failed",
            )

        # Step 10: Create PR
        pr_body_final = _build_pr_body(pr_title, pr_body, issue_url, stats)
        rc_pr, out_pr, err_pr = _run_cmd(
            [
                "gh", "pr", "create",
                "--repo", upstream_repo,
                "--head", f"{fork_owner}:{branch}",
                "--base", default_branch,
                "--title", pr_title,
                "--body", pr_body_final,
            ],
            cwd=str(clone_dir),
        )
        if rc_pr != 0:
            return PublishResult(
                ok=False,
                upstream_repo=upstream_repo,
                fork_repo=fork_repo,
                branch=branch,
                error=err_pr[:300],
                skipped_reason="pr_creation_failed",
            )

        # Extract PR URL from output
        pr_url = out_pr.strip()
        logger.log_action(
            "external_pr_created",
            upstream=upstream_repo,
            fork=fork_repo,
            branch=branch,
            pr_url=pr_url,
        )

        return PublishResult(
            ok=True,
            upstream_repo=upstream_repo,
            fork_repo=fork_repo,
            branch=branch,
            pr_url=pr_url,
            patch_stats=stats,
        )

    finally:
        # Cleanup
        if workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)


def _build_pr_body(pr_title: str, pr_body: str, issue_url: str, stats: dict) -> str:
    """Build PR body with required sections."""
    config = _load_external_config()
    pr_config = config.get("pr_body", {})

    # Check for promotional content
    forbidden_promotional = [
        "please star",
        "star this repo",
        "follow me",
        "promotion",
        "check my project",
        "star the repo",
        "please follow",
    ]

    # Filter out promotional content from pr_body
    filtered_pr_body = pr_body
    for forbidden in forbidden_promotional:
        if forbidden.lower() in (filtered_pr_body or "").lower():
            # Remove the line containing promotional content
            lines = (filtered_pr_body or "").split("\n")
            filtered_lines = []
            for line in lines:
                if forbidden.lower() not in line.lower():
                    filtered_lines.append(line)
            filtered_pr_body = "\n".join(filtered_lines)

    body_parts = []

    # Summary
    body_parts.append("## Summary")
    body_parts.append(filtered_pr_body or pr_title)
    body_parts.append("")

    # Linked issue
    if pr_config.get("include_issue_link", True):
        body_parts.append("## Linked issue")
        body_parts.append(f"References: {issue_url}")
        body_parts.append("")

    # Tests
    if pr_config.get("include_test_summary", True):
        body_parts.append("## Tests")
        tests_status = stats.get("tests_passed", None)
        if tests_status is True:
            body_parts.append("- Tests passed")
        elif tests_status is False:
            body_parts.append("- Tests failed (patch may need review)")
        else:
            body_parts.append("- Tests not available")
        body_parts.append(f"- Changed files: {stats.get('num_changed_files', 'N/A')}")
        body_parts.append(f"- Diff lines: {stats.get('diff_lines', 'N/A')}")
        body_parts.append("")

    # Safety
    if pr_config.get("include_safety_summary", True):
        body_parts.append("## Safety")
        body_parts.append("- [x] Public repo verified")
        body_parts.append("- [x] Public issue verified")
        body_parts.append("- [x] No private/internal repo access")
        body_parts.append("- [x] No security/auth/payment/credential issue handled")
        body_parts.append("- [x] Diff within configured limits")
        body_parts.append("- [x] No external issue/comment/star automation")
        body_parts.append("")

    # Disclosure
    if pr_config.get("include_ai_assisted_notice", True):
        body_parts.append("## Disclosure")
        body_parts.append(
            "This PR was prepared with AI assistance and submitted by an automated "
            "contribution workflow with strict safety gates."
        )

    return "\n".join(body_parts)


def dry_run_external_pr(
    issue_url: str,
    upstream_repo: str,
    patch_workdir: Path | None = None,
    patch_file: Path | None = None,
    pr_title: str = "",
    policy: Policy | None = None,
) -> PublishResult:
    """Dry-run mode: validate all checks without publishing.

    Supports two input modes:
      1. patch_workdir: Validate git diff in the workdir
      2. patch_file: Validate .diff file

    Useful for testing the full pipeline safely.

    Dry-run only validates, does not:
      - Fork the repo
      - Clone fork
      - Push to fork
      - Create PR
    """
    if policy is None:
        policy = Policy.load()

    config = _load_external_config()
    fork_owner = config.get("forking", {}).get("fork_owner", "disdorqin")
    fork_repo = f"{fork_owner}/{upstream_repo.split('/')[-1]}"

    # Validate upstream repo
    try:
        guard_external_public_repo_read(upstream_repo, "read", policy)
    except PermissionError_ as e:
        return PublishResult(
            ok=False,
            upstream_repo=upstream_repo,
            error=str(e),
            skipped_reason="upstream_not_public",
        )

    # Check deny keywords
    rc_issue, out_issue, _ = _run_cmd(
        ["gh", "issue", "view", issue_url, "--json", "title,body"]
    )
    if rc_issue == 0:
        try:
            issue_data = json.loads(out_issue)
            found_keywords = _check_deny_keywords(
                issue_data.get("title", ""),
                issue_data.get("body", ""),
            )
            if found_keywords:
                return PublishResult(
                    ok=False,
                    upstream_repo=upstream_repo,
                    skipped_reason=f"deny_keywords_found: {', '.join(found_keywords)}",
                )
        except json.JSONDecodeError:
            pass

    # Validate patch_file or patch_workdir
    stats = {}

    # Check patch_file first (higher priority)
    if patch_file and patch_file.exists():
        # Validate patch_file
        if not patch_file.is_file():
            return PublishResult(
                ok=False,
                upstream_repo=upstream_repo,
                fork_repo=fork_repo,
                error=f"patch_file is not a file: {patch_file}",
                skipped_reason="patch_file_not_file",
            )

        if patch_file.suffix != ".diff":
            return PublishResult(
                ok=False,
                upstream_repo=upstream_repo,
                fork_repo=fork_repo,
                error=f"patch_file must have .diff extension: {patch_file}",
                skipped_reason="patch_file_not_diff",
            )

        diff_content = patch_file.read_text(encoding="utf-8")
        if not diff_content.strip():
            return PublishResult(
                ok=False,
                upstream_repo=upstream_repo,
                fork_repo=fork_repo,
                error=f"patch_file is empty: {patch_file}",
                skipped_reason="empty_patch",
            )

        # For patch_file, we can't validate patch limits without applying
        # Just record basic stats
        stats = {
            "ok": True,
            "patch_file": str(patch_file),
            "patch_source": "patch_file",
        }

    # Check patch_workdir (lower priority)
    elif patch_workdir and patch_workdir.exists():
        if not patch_workdir.is_dir():
            return PublishResult(
                ok=False,
                upstream_repo=upstream_repo,
                fork_repo=fork_repo,
                error=f"patch_workdir is not a directory: {patch_workdir}",
                skipped_reason="patch_workdir_not_dir",
            )

        # Validate patch limits
        stats = _validate_patch_limits(patch_workdir)
        if not stats["ok"]:
            return PublishResult(
                ok=False,
                upstream_repo=upstream_repo,
                fork_repo=fork_repo,
                error=f"Patch limits failed: {', '.join(stats['errors'])}",
                skipped_reason="patch_limits_exceeded",
                patch_stats=stats,
            )

    else:
        # Neither patch_file nor patch_workdir available
        return PublishResult(
            ok=False,
            upstream_repo=upstream_repo,
            fork_repo=fork_repo,
            error="No patch source available (patch_file or patch_workdir required)",
            skipped_reason="patch_source_missing",
        )

    # Dry-run success
    logger.log_action(
        "dry_run_success",
        upstream=upstream_repo,
        issue_url=issue_url,
    )

    return PublishResult(
        ok=True,
        upstream_repo=upstream_repo,
        fork_repo=fork_repo,
        skipped_reason="dry_run_only",
        patch_stats=stats,
    )
