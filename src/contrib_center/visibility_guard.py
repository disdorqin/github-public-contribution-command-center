"""Public-only visibility guard.

This is the security keystone of the contribution center. EVERY GitHub
operation (read, clone, issue, PR, push) MUST go through
``guard_repo_operation(full_name, operation)`` first.

Two guard tiers:
  1. ``guard_own_repo_operation``  — for YOUR repos in public_repos.yml.
     Requires BOTH allowlist membership AND proven public visibility.
  2. ``guard_external_public_repo_read`` — for EXTERNAL public repos.
     Does NOT require allowlist, but enforces read-only operations.
     Safe Mode blocks all write operations (push/pr/comment) even if public.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

from . import logger
from .policy import Policy

ALLOWED_READ_OPERATIONS = frozenset(
    {"read", "clone", "inspect_issue", "generate_patch", "search", "read_metadata"}
)
DENIED_OPERATIONS_SAFE_MODE = frozenset(
    {"push", "create_pr", "create_issue", "comment", "publish"}
)


class PermissionError_(Exception):
    """Raised when a repo operation is denied by the public-only guard."""


@dataclass
class RepoVisibility:
    full_name: str
    private: bool | None
    visibility: str | None
    public: bool
    source: str  # "gh", "api", "cache", "default-deny"

    def to_dict(self) -> dict[str, Any]:
        return {
            "full_name": self.full_name,
            "private": self.private,
            "visibility": self.visibility,
            "public": self.public,
            "source": self.source,
        }


def _run_gh(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    if shutil.which("gh") is None:
        return (127, "", "gh CLI not installed")
    try:
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        return (124, "", "gh CLI timeout")
    return (proc.returncode, proc.stdout, proc.stderr)


def fetch_visibility(full_name: str) -> RepoVisibility:
    """Best-effort fetch of repo visibility via ``gh`` CLI.

    Fails closed: any error / unknown / private / internal => public=False.
    """
    rc, out, err = _run_gh(
        [
            "api",
            f"repos/{full_name}",
            "--jq",
            "{private: .private, visibility: .visibility, fork: .fork}",
        ]
    )
    if rc != 0:
        logger.log_action(
            "gh_api_failed",
            full_name=full_name,
            rc=rc,
            stderr=err.strip()[:300],
        )
        return RepoVisibility(full_name, None, None, False, "default-deny")
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        logger.log_action("gh_api_parse_failed", full_name=full_name, raw=out[:200])
        return RepoVisibility(full_name, None, None, False, "default-deny")
    private = bool(data.get("private", False))
    visibility = (data.get("visibility") or "").lower()
    public = (not private) and visibility == "public"
    return RepoVisibility(
        full_name=full_name,
        private=private,
        visibility=visibility or None,
        public=public,
        source="gh",
    )


def is_public_repo(full_name: str) -> bool:
    """Return True iff the repo is provably public.

    private / internal / unknown => False (fail-closed).
    """
    vis = fetch_visibility(full_name)
    return vis.public


def assert_public_repo(full_name: str) -> None:
    if not is_public_repo(full_name):
        vis = fetch_visibility(full_name)
        logger.log_reject(
            "not_public",
            full_name=full_name,
            private=vis.private,
            visibility=vis.visibility,
            source=vis.source,
        )
        raise PermissionError_(
            f"[SECURITY_SKIP] repo={full_name} reason=not_public "
            f"(private={vis.private}, visibility={vis.visibility})"
        )


def assert_repo_in_allowlist(full_name: str, allowlist: list[str]) -> None:
    if full_name not in allowlist:
        logger.log_reject("not_in_allowlist", full_name=full_name)
        raise PermissionError_(
            f"[SECURITY_SKIP] repo={full_name} reason=not_in_allowlist"
        )


# ---------------------------------------------------------------------------
# Tier 1 — own repos (require allowlist + proven public)
# ---------------------------------------------------------------------------

def guard_own_repo_operation(
    full_name: str,
    operation: str,
    policy: Policy | None = None,
) -> RepoVisibility:
    """Guard for YOUR repos listed in config/public_repos.yml.

    Requirements:
      1. Repo MUST be in the public_repos.yml allowlist.
      2. GitHub API MUST prove it is public (visibility=="public", private==false).
      3. private / internal / unknown => raise PermissionError_.

    Use this for: list_own_repo_issues, get_readme, repo_metadata,
    update_profile, create_issue on own repos, push to own repos.
    """
    if policy is None:
        policy = Policy.load()
    # Step 1 — allowlist check
    assert_repo_in_allowlist(full_name, policy.allowlist())
    # Step 2 — visibility proof
    vis = fetch_visibility(full_name)
    if not vis.public:
        logger.log_reject(
            "not_public",
            full_name=full_name,
            operation=operation,
            private=vis.private,
            visibility=vis.visibility,
            source=vis.source,
        )
        logger.log_action(
            "security_skip",
            repo=full_name,
            reason="not_public",
            operation=operation,
        )
        raise PermissionError_(
            f"[SECURITY_SKIP] repo={full_name} reason=not_public operation={operation}"
        )
    # Step 3 — Safe Mode write restriction for own repos
    if policy.mode == "safe" and operation in DENIED_OPERATIONS_SAFE_MODE:
        logger.log_reject(
            "safe_mode_write_denied",
            full_name=full_name,
            operation=operation,
        )
        raise PermissionError_(
            f"[SAFE_MODE] repo={full_name} operation={operation} "
            f"denied in safe mode"
        )
    logger.log_action(
        "guard_own_pass",
        repo=full_name,
        operation=operation,
        source=vis.source,
    )
    return vis


# ---------------------------------------------------------------------------
# Tier 2 — external public repos (NO allowlist required, read-only)
# ---------------------------------------------------------------------------

def guard_external_public_repo_read(
    full_name: str,
    operation: str,
    policy: Policy | None = None,
) -> RepoVisibility:
    """Guard for EXTERNAL public repos NOT in your allowlist.

    Requirements:
      1. GitHub API MUST prove it is public.
      2. operation MUST be a read-only operation:
         {"read", "clone", "inspect_issue", "generate_patch", "search"}
      3. private / internal / unknown => raise PermissionError_.
      4. Safe Mode: ALL write operations denied.
      5. Assisted/AutoPilot Mode: push/pr/comment still require
         explicit per-repo allowlist or config flag.

    Use this for: external issue patch drafts (clone + generate diff).
    """
    if policy is None:
        policy = Policy.load()
    # Step 1 — visibility proof (NO allowlist check)
    vis = fetch_visibility(full_name)
    if not vis.public:
        logger.log_reject(
            "external_repo_not_public",
            full_name=full_name,
            operation=operation,
            private=vis.private,
            visibility=vis.visibility,
            source=vis.source,
        )
        raise PermissionError_(
            f"[SECURITY_SKIP] external repo={full_name} "
            f"reason=not_public operation={operation}"
        )
    # Step 2 — operation whitelist
    if operation not in ALLOWED_READ_OPERATIONS:
        logger.log_reject(
            "external_operation_not_allowed",
            full_name=full_name,
            operation=operation,
        )
        raise PermissionError_(
            f"[SECURITY_SKIP] external repo={full_name} "
            f"operation={operation} not in allowed read operations"
        )
    # Step 3 — Safe Mode: deny ALL writes
    if policy.mode == "safe" and operation in DENIED_OPERATIONS_SAFE_MODE:
        logger.log_reject(
            "safe_mode_external_write_denied",
            full_name=full_name,
            operation=operation,
        )
        raise PermissionError_(
            f"[SAFE_MODE] external repo={full_name} operation={operation} "
            f"denied in safe mode"
        )
    logger.log_action(
        "guard_external_read_pass",
        repo=full_name,
        operation=operation,
        source=vis.source,
    )
    return vis


# ---------------------------------------------------------------------------
# Legacy wrapper — defaults to own-repo guard for backwards compat
# ---------------------------------------------------------------------------

def guard_repo_operation(
    full_name: str,
    operation: str,
    policy: Policy | None = None,
) -> RepoVisibility:
    """Legacy wrapper — delegates to ``guard_own_repo_operation``.

    Kept for backwards compatibility with existing callers.
    New code should call the explicit guard function directly.
    """
    return guard_own_repo_operation(full_name, operation, policy)


# ---------------------------------------------------------------------------
# High-risk keyword filter for external issues
# ---------------------------------------------------------------------------

# Comprehensive list of high-risk keywords that should trigger denial
# These keywords indicate issues that are too sensitive/risky to automate
DENY_KEYWORDS_RE = re.compile(
    r"(?i)"
    r"(security|vulnerability|authentication|auth[^eo]|payment"
    r"|production.outage|breaking.change|crypto.wallet"
    r"|private.key|credential|secret(?!.)|access.token"
    r"|password|api.key|token.leak|production.breach)"
)

# Alternative: explicit list for clearer matching
DENY_KEYWORDS_EXPLICIT = [
    "security",
    "vulnerability", 
    "authentication",
    "auth",  # Catches auth, but not "author"
    "payment",
    "production outage",
    "breaking change",
    "crypto wallet",
    "private key",
    "credential",
    "secret",
    "access token",
    "api key",
    "password",
    "token leak",
    "production breach",
]


def issue_body_has_deny_keywords(title: str, body: str) -> list[str]:
    """Return a list of matched deny-keywords found in title+body.
    
    Uses explicit keyword list for more accurate matching.
    """
    text = f"{title}\n{body or ''}"
    text_lower = text.lower()
    found = set()
    
    # Use explicit list for clearer, more maintainable matching
    for kw in DENY_KEYWORDS_EXPLICIT:
        # Use word boundary matching for single words, substring for phrases
        if " " in kw:
            # Multi-word phrase - check if it appears as substring
            if kw.lower() in text_lower:
                found.add(kw.lower())
        else:
            # Single word - use word boundary to avoid partial matches
            if re.search(rf"\b{re.escape(kw.lower())}\b", text_lower):
                found.add(kw.lower())
    
    return sorted(found)
