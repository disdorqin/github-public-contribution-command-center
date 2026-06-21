"""External public-issue scout.

Searches ``gh search issues`` with the queries from
``config/external_search.yml``, scores each result, and produces a list
of candidates. In Safe Mode v1 we never post comments or open PRs — the
candidates are saved to ``data/candidates.jsonl`` and surfaced in the
daily report.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import github_client, logger, state
from ..policy import Policy
from ..scoring import Score, action_for_score, score_issue


@dataclass
class Candidate:
    repo: str
    issue_url: str
    title: str
    score: Score
    action: str
    skipped: bool = False
    skip_reason: str | None = None
    fields: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "repo": self.repo,
            "issue_url": self.issue_url,
            "title": self.title,
            "score": self.score.to_dict(),
            "action": self.action,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "fields": self.fields,
        }


def _extract_repo(issue: dict) -> str:
    repo = issue.get("repository") or {}
    if isinstance(repo, dict):
        owner = repo.get("owner") or repo.get("ownerLogin") or ""
        name = repo.get("name") or ""
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
            body = it.get("body") or ""
            labels = _extract_labels(it)
            repo = _extract_repo(it)
            sc = score_issue(title, body, labels, rules=policy.rules)
            action = action_for_score(sc.total) if not sc.denied else "drop"
            cand = Candidate(
                repo=repo,
                issue_url=url,
                title=title,
                score=sc,
                action=action,
                skipped=sc.denied or sc.total < min_score,
                skip_reason=(
                    ";".join(sc.deny_reasons)
                    if sc.denied
                    else (
                        f"score<{min_score}"
                        if sc.total < min_score
                        else None
                    )
                ),
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
                    reason=cand.skip_reason,
                )
    logger.log_action("scout_done", total=len(candidates))
    return candidates
