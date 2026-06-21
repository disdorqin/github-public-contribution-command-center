# GitHub Public Contribution Command Center

A safe-mode daily automation built on top of `SWE-agent/mini-swe-agent`.
Reuses mini-swe-agent as a **patch engine**; adds a brand-new
`src/contrib_center/` orchestrator that scans whitelisted public repos,
scouts external public issues, scores them, generates patch drafts,
writes a daily Markdown report, and updates a fenced block in your
GitHub Profile README.

> Status: **Safe Mode v1** — never publishes external PRs, never
> publishes external comments, never pushes to external repos, never
> touches private/internal repos.

## Files added

```
src/contrib_center/                 # all new code
  __init__.py
  main.py                           # CLI entry point
  orchestrator.py                   # 14-step daily pipeline
  visibility_guard.py               # public-only security keystone
  github_client.py                  # gh CLI wrapper
  policy.py                         # config loader
  state.py                          # sqlite counters
  scoring.py                        # external issue scoring
  logger.py                         # jsonl logger
  bots/
    profile_bot.py                  # Profile README block
    own_repo_bot.py                 # scans public whitelisted repos
    issue_scout.py                  # external public issue search
    pr_agent.py                     # PR drafts (safe = no publish)
    report_bot.py                   # writes reports/YYYY-MM-DD.md
    notification_bot.py             # stdout summary stub
  adapters/
    mini_swe_adapter.py             # wraps mini-swe-agent subprocess
    metrics_adapter.py              # stub for lowlighter/metrics
    snake_adapter.py                # stub for Platane/snk
    readme_stats_adapter.py         # stub for github-readme-stats
    pr_agent_adapter.py             # stub for PR-Agent

config/
  public_repos.yml                  # public-only allowlist
  rules.yml                         # limits, quality gate, deny keywords
  external_search.yml               # gh search queries
  profile.yml                       # profile block markers
  prompts.yml                       # prompt templates

data/                               # local state (committable in v1)
  state.sqlite                      # daily counters
  action_log.jsonl                  # every guard decision / API call
  candidates.jsonl                  # issues that scored >= min_score_for_report
  rejected.jsonl                    # every skip / deny reason
  patch_drafts.jsonl                # PR drafts (not published)

reports/                            # one Markdown file per day

.github/workflows/
  daily-contribution-center.yml     # 09:00 Asia/Singapore daily run

tests/
  test_visibility_guard.py
  test_scoring.py
```

No files inside `src/minisweagent/` are modified.

## How to configure the token

1. Go to https://github.com/settings/tokens (or use a fine-grained PAT).
2. Create a new token named `PUBLIC_GITHUB_TOKEN` with **only** the
   `public_repo` scope. **Do NOT** grant `repo`, `admin:org`, or
   `delete_repo` — the entire point of this automation is that even if
   the token leaks, it cannot reach private repos.
3. Rotate any older token that was previously pasted into chat.
4. Add the token as a secret in this repo: Settings → Secrets and
   variables → Actions → New repository secret:
   - Name: `PUBLIC_GITHUB_TOKEN`
   - Value: the new token
5. (Optional) Add `OPENAI_API_KEY` if you want mini-swe-agent to
   actually call an LLM during patch generation.

The same `PUBLIC_GITHUB_TOKEN` is also used by `gh auth login` in the
workflow, and is the only token the contrib center ever sees.

## How to run locally

```bash
# from the repo root
pip install -e .
gh auth login                                    # one-time, on your machine
python -m contrib_center.main check-visibility disdorqin/DARIS
python -m contrib_center.main run --mode safe
```

Outputs:

- `reports/YYYY-MM-DD.md` — daily report
- `data/action_log.jsonl` — every guard decision and API call
- `data/candidates.jsonl` — issues that scored >= the reporting
  threshold
- `data/rejected.jsonl` — every skip / deny reason
- `data/state.sqlite` — daily counters
- The fenced block `<!-- DAILY-BOT:START --> ... <!-- DAILY-BOT:END -->`
  is updated in the README of the profile repo (currently
  `disdorqin/dis_daily_agent` or `disdorqin/disdorqin`, whichever is
  marked `purpose: GitHub Profile README` in `config/public_repos.yml`).

## How to run in GitHub Actions

The workflow `.github/workflows/daily-contribution-center.yml` is
already wired to:

- run on `workflow_dispatch` and on cron `0 1 * * *` (01:00 UTC ==
  09:00 Asia/Singapore)
- install the package (`pip install -e .`)
- run `python -m contrib_center.main run --mode safe`
- commit and push `reports/` and `data/`

To enable it:

1. Make sure the `PUBLIC_GITHUB_TOKEN` and `OPENAI_API_KEY` secrets are
   set (see "How to configure the token" above).
2. The default `GITHUB_TOKEN` is used only for the workflow's own
   checkout/commit/push. It does **not** perform any external API
   call.
3. Trigger it manually from the Actions tab to verify the run.

## Acceptance criteria

After running:

```bash
python -m contrib_center.main check-visibility disdorqin/DARIS
python -m contrib_center.main run --mode safe
```

the run MUST show:

- GitHub login status (via `gh auth status`).
- `public_repos.yml` whitelist loaded.
- private-repo guard enabled.
- which public repos were scanned, which were skipped and why.
- external issue candidate count.
- path to the generated daily report.
- profile README block update result.
- **0** external PRs published.
- **0** external comments published.
- **0** private-repo touches.

## Safe Mode → Assisted Mode upgrade path

1. Add `mode: assisted` to `config/rules.yml`.
2. Add a `config/publishers.yml` allowlist naming the external repos
   you authorize the bot to publish to.
3. Bump `rules.limits.daily_external_prs` from 0 to a small number
   (e.g. 1) and `daily_external_comments` similarly.
4. Add a `--confirm-publish` CLI flag and a `workflow_dispatch` input
   `confirm_publish` that must be set to `true` to enable any external
   write.
5. In `bots/pr_agent.py`, implement `publish_pr()` that calls
   `gh pr create` only after the guard has approved the target repo
   AND the per-day cap has not been exceeded.
6. In `bots/issue_scout.py`, add a `comment_on_issue()` path that
   follows the same gating rules.

Until every step above is complete, the bot will keep refusing any
external write and will continue to operate in Safe Mode.
