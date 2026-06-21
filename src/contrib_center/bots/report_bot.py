"""Daily report writer.

Renders ``reports/YYYY-MM-DD.md`` from the orchestrator's report dict.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .. import logger

REPORT_DIR = Path("reports")


def write(report: dict) -> Path:
    today = date.today().isoformat()
    out = REPORT_DIR / f"{today}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    counters = report.get("counters", {})
    candidates = report.get("candidates", [])
    own = report.get("own_repos", [])
    rejected = report.get("rejected", [])
    actions = report.get("actions", [])
    pr_drafts = report.get("pr_drafts", [])
    patch_results = report.get("patch_results", [])

    lines: list[str] = []
    lines.append(f"# GitHub Daily Public Contribution Report - {today}")
    lines.append("")
    lines.append("## 安全摘要")
    lines.append(f"- Mode: {report.get('mode', 'safe')}")
    lines.append("- Public-only guard: enabled")
    lines.append(
        f"- Private repositories touched: {counters.get('private_repos_touched', 0)}"
    )
    lines.append("- External PRs published: 0")
    lines.append("- External comments published: 0")
    lines.append("")
    lines.append("## 自有 public 仓库扫描")
    if own:
        lines.append("")
        lines.append("| Repo | README | TODOs | Open issues | Notes |")
        lines.append("|---|---|---:|---|---|")
        for s in own:
            notes = "; ".join(s.get("notes", [])) or "—"
            lines.append(
                f"| {s['full_name']} | "
                f"{'present' if s['readme_present'] else 'missing'} | "
                f"{s.get('todo_count', 0)} | "
                f"{len(s.get('open_issues', []))} | {notes} |"
            )
    else:
        lines.append("")
        lines.append("_(no whitelisted public repos scanned)_")
    lines.append("")
    lines.append("## 外部 issue 候选")
    if candidates:
        lines.append("")
        lines.append("| Score | Repo | Title | Action |")
        lines.append("|---:|---|---|---|")
        for c in candidates:
            lines.append(
                f"| {c['score']['total']:.2f} | {c['repo']} | "
                f"{c['title'][:60]} | {c['action']} |"
            )
    else:
        lines.append("")
        lines.append("_(no candidates met the reporting threshold today)_")
    lines.append("")
    lines.append("## Patch 草稿")
    if patch_results:
        lines.append("")
        lines.append("| Repo | Issue | Files | Diff lines | Tests |")
        lines.append("|---|---|---:|---:|---|")
        for p in patch_results:
            lines.append(
                f"| {p['repo']} | {p['issue_url']} | "
                f"{len(p.get('changed_files', []))} | "
                f"{p.get('diff_lines', 0)} | "
                f"{p.get('tests_passed')} |"
            )
    else:
        lines.append("")
        lines.append("_(no patch drafts generated today)_")
    if pr_drafts:
        lines.append("")
        lines.append("### PR drafts (not published in Safe Mode)")
        for d in pr_drafts:
            lines.append(f"- {d['repo']} :: {d['title']}")
    lines.append("")
    lines.append("## 被拒绝 / 跳过的项目")
    if rejected:
        lines.append("")
        for r in rejected[:50]:
            lines.append(f"- {r}")
    else:
        lines.append("")
        lines.append("_(none)_")
    lines.append("")
    lines.append("## 明日建议")
    if actions:
        for a in actions:
            lines.append(f"- {a}")
    else:
        lines.append("- Keep monitoring; today's actions are conservative.")
    lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    logger.log_action("report_written", path=str(out))
    return out
