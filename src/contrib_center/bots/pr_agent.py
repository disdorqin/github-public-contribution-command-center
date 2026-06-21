"""PR agent (Safe Mode = no publish).

In Safe Mode v1 this module produces a PR draft (title, body) for each
candidate with action == "patch_draft" or "autopilot_eligible", but it
DOES NOT call ``gh pr create``. The drafts are written to
``data/patch_drafts.jsonl`` and the daily report so a human can review.

When the project is upgraded to Assisted Mode, this module will gain a
``publish_pr()`` method that is gated by:
  - policy.mode == "assisted"
  - explicit --confirm-publish CLI flag
  - per-day cap (rules.limits.daily_external_prs)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .. import logger
from ..adapters import pr_agent_adapter
from ..policy import Policy


@dataclass
class PrDraft:
    repo: str
    issue_url: str
    title: str
    body: str
    mode: str
    published: bool = False

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def build_drafts(candidates, policy: Policy) -> list[PrDraft]:
    drafts: list[PrDraft] = []
    for cand in candidates:
        if cand.action not in ("patch_draft", "autopilot_eligible"):
            continue
        if cand.skipped:
            continue
        title = f"[contrib-center] {cand.title}"
        body = pr_agent_adapter.describe(
            title,
            body=(
                f"Auto-generated draft from the public contribution center.\n\n"
                f"- Issue: {cand.issue_url}\n"
                f"- Score: {cand.score.total:.2f}\n"
                f"- Mode: {policy.mode}\n\n"
                f"NOTE: Safe Mode did NOT publish this PR.\n"
            ),
        )
        draft = PrDraft(
            repo=cand.repo,
            issue_url=cand.issue_url,
            title=title,
            body=body,
            mode=policy.mode,
            published=False,
        )
        drafts.append(draft)
        logger.log_action(
            "pr_draft_created", repo=cand.repo, issue=cand.issue_url
        )

    out_path = Path("data/patch_drafts.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for d in drafts:
            f.write(json.dumps(d.to_dict(), ensure_ascii=False) + "\n")
    return drafts
