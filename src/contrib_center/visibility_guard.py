"""Public-only visibility guard.

This is the security keystone of the contribution center. EVERY GitHub
operation (read, clone, issue, PR, push) MUST go through
``guard_repo_operation(full_name, operation)`` first.

The guard performs three checks, in order:

1. The repo must be in the public-only allowlist loaded from
   ``config/public_repos.yml``. Otherwise we abort.
2. The repo's GitHub metadata must report ``private == false`` and
   ``visibility == "public"``. Otherwise we abort.
3. We never silently proceed when ``gh``/API is unavailable. Unknown
   visibility is treated as NOT public (fail-closed).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from . import logger
from .policy import Policy


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
            "gh_api_failed", full_name=full_name, rc=rc, stderr=err.strip()
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


def guard_repo_operation(
    full_name: str,
    operation: str,
    policy: Policy | None = None,
) -> RepoVisibility:
    """All GitHub operations MUST go through this function.

    - Reject if repo is not in the public-only allowlist.
    - Reject if repo is not provably public.
    - Log every decision.
    - Return RepoVisibility on success.
    """
    if policy is None:
        policy = Policy.load()
    assert_repo_in_allowlist(full_name, policy.allowlist())
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
    logger.log_action(
        "guard_pass",
        repo=full_name,
        operation=operation,
        source=vis.source,
    )
    return vis
