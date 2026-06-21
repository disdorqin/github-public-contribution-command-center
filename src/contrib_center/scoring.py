"""External-issue scoring.

Each external issue is scored 0..10 across six dimensions. The weighted
sum becomes the final score, and the threshold table (from the project
spec) decides what we do with it:

  score < 6                 : drop
  6  <= score < 7           : daily report only
  7  <= score < 8           : analysis report (not generated in v1 stub)
  8  <= score < 9           : generate patch draft via mini-swe-agent
  score >= 9                : would be autopilot-eligible, but v1 still
                              does not auto-publish anything externally
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DIMENSIONS = (
    "clarity",
    "scope",
    "testability",
    "community_risk",
    "domain_match",
    "patchability",
)

# Highest weight to scope + patchability — we want small, fixable issues.
WEIGHTS = {
    "clarity": 1.0,
    "scope": 1.5,
    "testability": 1.2,
    "community_risk": 1.0,
    "domain_match": 0.8,
    "patchability": 1.5,
}

DENY_KEYWORDS_DEFAULT = {
    "security",
    "vulnerability",
    "authentication",
    "auth",
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
}

PREFERRED_DOMAINS_DEFAULT = {
    "ai",
    "ml",
    "python",
    "typescript",
    "power systems",
    "energy",
    "documentation",
    "i18n",
}


@dataclass
class Score:
    total: float
    breakdown: dict[str, float]
    denied: bool
    deny_reasons: list[str]

    def to_dict(self) -> dict:
        return {
            "total": round(self.total, 2),
            "breakdown": {k: round(v, 2) for k, v in self.breakdown.items()},
            "denied": self.denied,
            "deny_reasons": self.deny_reasons,
        }


def _keyword_count(text: str, keywords: set[str]) -> int:
    t = text.lower()
    return sum(1 for kw in keywords if kw.lower() in t)


def score_issue(
    title: str,
    body: str,
    labels: list[str] | None = None,
    rules: dict | None = None,
) -> Score:
    """Score a candidate issue.

    Inputs are the issue title, body, and label names. ``rules`` is the
    parsed config/rules.yml dict; if omitted, default deny keywords and
    preferred domains from this module are used.
    """
    text = f"{title}\n{body or ''}"
    labels = [str(l).lower() for l in (labels or [])]
    rules = rules or {}
    deny_kw = set(rules.get("deny_keywords", [])) or DENY_KEYWORDS_DEFAULT
    pref_domains = (
        set(rules.get("preferred_domains", [])) or PREFERRED_DOMAINS_DEFAULT
    )

    # Hard deny: any deny keyword anywhere in the issue.
    deny_reasons: list[str] = []
    lowered = text.lower()
    for kw in deny_kw:
        if kw.lower() in lowered:
            deny_reasons.append(f"deny_keyword:{kw}")

    # Dimension heuristics -----------------------------------------------------

    # clarity: longer, well-formed text scores higher. <40 chars => low.
    clarity = 7.0
    if len(text) < 80:
        clarity = 4.0
    elif len(text) < 200:
        clarity = 6.0
    elif len(text) > 1200:
        clarity = 8.5
    else:
        clarity = 8.0
    if not body or len(body.strip()) < 40:
        clarity = min(clarity, 5.0)

    # scope: short titles with one or two verbs (fix/update/typo) => small.
    title_l = title.lower()
    if any(k in title_l for k in ("typo", "doc", "readme", "comment", "spelling")):
        scope = 9.0
    elif any(k in title_l for k in ("fix", "patch", "update", "add")):
        scope = 7.5
    elif any(k in title_l for k in ("refactor", "redesign", "rewrite")):
        scope = 4.5
    else:
        scope = 6.0

    # testability: presence of test/repro signals.
    testability = 6.0
    if re.search(r"\b(test|repro|reproduce|steps to reproduce)\b", lowered):
        testability = 9.0
    elif re.search(r"\bexample\b|\bsnippet\b", lowered):
        testability = 7.0

    # community_risk: low risk for typo / doc issues, higher for prod / auth.
    community_risk = 8.0
    if any(k in lowered for k in ("production", "outage", "down", "broken")):
        community_risk = 4.0
    if any(l in {"good first issue", "documentation", "typo", "help wanted"} for l in labels):
        community_risk = max(community_risk, 9.0)

    # domain_match: hit any preferred domain keyword?
    domain_match = 5.0
    for d in pref_domains:
        if d.lower() in lowered:
            domain_match = 9.0
            break

    # patchability: small files + has file path or symbol => easier.
    patchability = 6.0
    if re.search(r"\b[\w/]+\.py\b|\b[\w/]+\.md\b|\b[\w/]+\.ts\b", text):
        patchability = 8.5
    if re.search(r"\btypo\b|\bspelling\b", lowered):
        patchability = 9.5

    breakdown = {
        "clarity": clarity,
        "scope": scope,
        "testability": testability,
        "community_risk": community_risk,
        "domain_match": domain_match,
        "patchability": patchability,
    }

    total = sum(breakdown[k] * WEIGHTS[k] for k in DIMENSIONS) / sum(WEIGHTS.values())

    if deny_reasons:
        return Score(total=0.0, breakdown=breakdown, denied=True, deny_reasons=deny_reasons)

    return Score(total=total, breakdown=breakdown, denied=False, deny_reasons=[])


def action_for_score(score: float) -> str:
    if score < 6:
        return "drop"
    if score < 7:
        return "report"
    if score < 8:
        return "analyze"
    if score < 9:
        return "patch_draft"
    return "autopilot_eligible"
