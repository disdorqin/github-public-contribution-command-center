"""External public-issue scout.

Searches ``gh search issues`` with the queries from
``config/external_search.yml``, scores each result, and produces a list
of candidates. In Safe Mode v1 we never post comments or open PRs — the
candidates are saved to ``data/candidates.jsonl`` and surfaced in the
daily report.

Every external issue's repo is verified public via
``guard_external_public_repo_read`` before any patch work.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import github_client, logger, state
from ..policy import Policy
from ..scoring import Score, action_for_score, score_issue
from .. import visibility_guard


@dataclass
class Candidate:
    repo: str
    issue_url: str
    title: str
    score: Score
    action: str
    skipped: bool = False
    skip_reason: str | None = None
    external_public_verified: bool = False
    write_allowed: bool = False
    deny_keywords: list[str] = field(default_factory=list)
    fields: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "repo": self.repo,
            "issue_url": self.issue_url,
            "title": self.title,
            "score": self.score.to_dict(),
            "action": self.action,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "external_public_verified": self.external_public_verified,
            "write_allowed": self.write_allowed,
            "deny_keywords": self.deny_keywords,
            "fields": self.fields,
        }
        return d


def _extract_repo(issue: dict) -> str:
    repo = issue.get("repository") or {}
    if isinstance(repo, dict):
        # Try different field names for owner
        owner = repo.get("owner") or repo.get("ownerLogin") or ""
        name = repo.get("name") or ""
        
        # Try nameWithOwner format (e.g., "owner/repo")
        name_with_owner = repo.get("nameWithOwner") or ""
        if "/" in name_with_owner:
            return name_with_owner
        
        if owner and name:
            return f"{owner}/{name}"
    return ""


def _extract_labels(issue: dict) -> list[str]:
    labels = issue.get("labels") or []
    out: list[str] = []
    for lab in labels:
        if isinstance(lab, dict):
            out.append(lab.get("name", ""))
        else:
            out.append(str(lab))
    return out


def _has_deny_keywords(title: str, body: str) -> list[str]:
    """Return matched high-risk keywords (uses visibility_guard helper)."""
    return visibility_guard.issue_body_has_deny_keywords(title, body)


def scout(policy: Policy, limit_per_query: int = 20) -> list[Candidate]:
    queries = policy.external_search.get("queries", [])
    min_score = float(
        policy.rules.get("quality_gate", {}).get("min_score_for_report", 6)
    )
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for q in queries:
        logger.log_action("scout_query", query=q)
        items = github_client.search_issues(q, limit=limit_per_query)
        for it in items:
            url = it.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            title = it.get("title", "")
            
            # Step 0 — try to get body from search result, if empty, fetch it
            body = it.get("body") or ""
            if not body:
                logger.log_action("issue_body_fetching", issue_url=url)
                body = github_client.get_issue_body(url)
                if not body:
                    logger.log_action("issue_body_fetch_failed", issue_url=url)
                    # Continue with empty body, but will have lower confidence
            
            labels = _extract_labels(it)
            repo = _extract_repo(it)

            # Step 1 — guard: prove repo is public (no allowlist required)
            try:
                visibility_guard.guard_external_public_repo_read(
                    repo, "inspect_issue", policy
                )
                public_verified = True
            except visibility_guard.PermissionError_ as e:
                logger.log_reject(
                    "scout_repo_denied",
                    repo=repo,
                    issue=url,
                    error=str(e),
                )
                continue

            # Step 2 — deny-keyword check (checks BOTH title and body)
            deny_hits = _has_deny_keywords(title, body)

            # Step 3 — score (uses title + body)
            sc = score_issue(title, body, labels, rules=policy.rules)
            action = action_for_score(sc.total) if not sc.denied else "drop"

            # Step 4 — determine write_allowed (always False in safe mode)
            write_allowed = False
            if policy.mode != "safe":
                # Future: check config/publishers.yml allowlist
                write_allowed = False

            skipped = sc.denied or sc.total < min_score
            skip_reason = (
                ";".join(sc.deny_reasons)
                if sc.denied
                else (
                    f"score<{min_score}"
                    if sc.total < min_score
                    else None
                )
            )

            cand = Candidate(
                repo=repo,
                issue_url=url,
                title=title,
                score=sc,
                action=action,
                skipped=skipped,
                skip_reason=skip_reason,
                external_public_verified=public_verified,
                write_allowed=write_allowed,
                deny_keywords=deny_hits,
            )
            candidates.append(cand)

            if not cand.skipped:
                logger.log_candidate(
                    score=sc.total,
                    repo=repo,
                    issue_url=url,
                    action=action,
                    title=title,
                )
                state.remember_issue(url, sc.total, repo)
            else:
                logger.log_reject(
                    "issue_below_threshold",
                    url=url,
                    repo=repo,
                    score=sc.total,
                    skip_reason=cand.skip_reason,
                )
    logger.log_action("scout_done", total=len(candidates))
    return candidates
