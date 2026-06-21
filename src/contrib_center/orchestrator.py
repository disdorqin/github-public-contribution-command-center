"""Daily pipeline orchestrator.

Implements the 14-step flow from the project spec.
"""

from __future__ import annotations

from typing import Any

from . import github_client, logger, state, visibility_guard
from .adapters import mini_swe_adapter
from .bots import (
    issue_scout,
    notification_bot,
    own_repo_bot,
    pr_agent,
    profile_bot,
    report_bot,
)
from .policy import Policy


def _step(msg: str) -> None:
    print(f"[contrib-center] {msg}")


def run(mode: str = "safe") -> dict[str, Any]:
    _step("loading config")
    policy = Policy.load()
    if policy.mode != mode:
        _step(
            f"warning: config mode={policy.mode} but CLI mode={mode}; "
            f"using CLI mode={mode}"
        )

    _step("checking gh auth status")
    auth = github_client.auth_status()
    logger.log_action("gh_auth_status", **auth)
    if not auth["logged_in"]:
        _step(
            "warning: gh is not logged in. The guard will fall back to "
            "fail-closed (everything treated as not public). "
            "Run `gh auth login`."
        )

    _step(
        f"loaded public repo whitelist ({len(policy.public_repos)} repos)"
    )
    for r in policy.public_repos:
        _step(f"  - {r.full_name}  purpose={r.purpose}  allow={r.allow}")

    counters: dict[str, int] = {
        "public_repos_scanned": 0,
        "repos_skipped": 0,
        "private_repos_touched": 0,
        "patches_generated": 0,
    }
    rejected: list[str] = []

    # Step 4-6: visibility guard on every whitelist entry, then scan
    # own public repos. Anything not public is skipped with a
    # [SECURITY_SKIP] log.
    _step("running visibility guard on whitelist")
    own_results: list[dict] = []
    for repo in policy.public_repos:
        try:
            visibility_guard.guard_repo_operation(
                repo.full_name, "scan_own", policy
            )
        except visibility_guard.PermissionError_ as e:
            counters["repos_skipped"] += 1
            rejected.append(f"skip: {repo.full_name} -> {e}")
            continue
        counters["public_repos_scanned"] += 1
        own_results.append(own_repo_bot.scan_repo(repo, policy).to_dict())

    # Step 7: own-repo suggestions (in Safe Mode we just collect them).
    _step(f"own-repo scan complete ({len(own_results)} scanned)")

    # Step 8-9: external public issue scout + score.
    _step("scouting external public issues")
    candidates = issue_scout.scout(policy)
    candidate_dicts = [c.to_dict() for c in candidates if not c.skipped]

    # Step 10: high-score candidates -> patch draft via mini-swe-agent.
    _step("generating patch drafts for high-score candidates")
    patch_results: list[dict] = []
    pr_drafts: list[dict] = []
    actions: list[str] = []
    rules = policy.rules
    min_patch = float(
        rules.get("quality_gate", {}).get("min_score_for_patch", 7)
    )
    for cand in candidates:
        if cand.skipped or cand.score.denied:
            continue
        if cand.score.total < min_patch:
            continue
        try:
            res = mini_swe_adapter.generate_patch(
                issue_url=cand.issue_url,
                repo_full_name=cand.repo,
                score=cand.score.total,
                policy=policy,
            )
        except Exception as e:  # noqa: BLE001
            logger.log_reject(
                "mini_swe_crashed", error=str(e), issue=cand.issue_url
            )
            continue
        patch_results.append(res.to_dict())
        if res.patch_generated and not res.error:
            counters["patches_generated"] += 1
            actions.append(
                f"Patch draft for {res.repo} from {res.issue_url} "
                f"(score {res.score:.2f})"
            )

    # Step 11: PR drafts (NOT published in Safe Mode).
    _step("compiling PR drafts (safe mode = no publish)")
    drafts = pr_agent.build_drafts(candidates, policy)
    pr_drafts = [d.to_dict() for d in drafts]
    state.increment("external_pr_drafts", len(pr_drafts))
    state.increment("external_patches", counters["patches_generated"])

    # Step 12: daily report.
    report = {
        "mode": mode,
        "counters": counters,
        "own_repos": own_results,
        "candidates": candidate_dicts,
        "patch_results": patch_results,
        "pr_drafts": pr_drafts,
        "rejected": rejected,
        "actions": actions,
    }
    report_path = report_bot.write(report)
    _step(f"daily report written: {report_path}")

    # Step 13: profile README update.
    _step("updating profile README block")
    profile_status = profile_bot.update_profile(policy, report)
    _step(f"profile update: {profile_status}")
    logger.log_action("profile_update", **profile_status)

    # Step 14: log summary.
    notification_bot.notify(report, str(report_path))
    logger.log_action("daily_run_done", **counters, candidates=len(candidate_dicts))

    return {
        "report": report,
        "report_path": str(report_path),
        "profile_status": profile_status,
    }


def scan_own() -> list[dict]:
    policy = Policy.load()
    return [s.to_dict() for s in own_repo_bot.scan_all(policy)]


def scout() -> list[dict]:
    policy = Policy.load()
    return [c.to_dict() for c in issue_scout.scout(policy)]


def update_profile() -> dict:
    policy = Policy.load()
    return profile_bot.update_profile(policy, report={})


def regenerate_report() -> str:
    policy = Policy.load()
    candidates = issue_scout.scout(policy)
    own = [s.to_dict() for s in own_repo_bot.scan_all(policy)]
    report = {
        "mode": policy.mode,
        "counters": {
            "public_repos_scanned": len(own),
            "repos_skipped": 0,
            "patches_generated": 0,
        },
        "own_repos": own,
        "candidates": [c.to_dict() for c in candidates if not c.skipped],
        "patch_results": [],
        "pr_drafts": [],
        "rejected": [],
        "actions": [],
    }
    return str(report_bot.write(report))
