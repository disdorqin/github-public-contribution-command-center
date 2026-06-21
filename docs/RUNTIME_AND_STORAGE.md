# Runtime and Storage Documentation

## Overview

The GitHub Public Contribution Command Center is designed to run **fully in the cloud** via GitHub Actions. No local machine is required for daily operation.

---

## 1. Do I need to keep my computer on?

**No.** GitHub Actions runs in the cloud. Once configured, the system will:

- Run automatically at 09:00 Asia/Singapore (01:00 UTC) daily
- Can be triggered manually via `workflow_dispatch`
- All processing happens on GitHub's runners

---

## 2. Where is the cloud code stored?

- **Source code**: Stored in your GitHub repository (`disdorqin/github-public-contribution-command-center`)
- **Runtime**: GitHub Actions runner temporary directory (ephemeral)
- **Cloned repos**: Temporary directories (`tempfile.TemporaryDirectory`), deleted after run
- **Generated data**: Committed back to the repository (`data/`, `reports/`)

---

## 3. Where are external repos cloned?

External public repositories are cloned to **temporary directories** that are:

- Created using `tempfile.TemporaryDirectory(prefix="contrib_center_")`
- Located in the system temp directory (not in the repository)
- **Automatically deleted** when the workflow ends
- **Never committed** to the repository

Example temp path: `/tmp/contrib_center_abc123/` (Linux runner)

---

## 4. Recommended local development directory

If you want to run locally for testing:

```
D:\AI-GitHub-Bots\github-public-contribution-command-center
```

Setup:

```bash
cd D:\AI-GitHub-Bots\github-public-contribution-command-center
python -m pip install -e ".[dev]"
python -m contrib_center.main llm-check
python -m contrib_center.main run --mode safe
```

---

## 5. Required GitHub Secrets

Configure these in your repository settings (`Settings` → `Secrets and variables` → `Actions`):

### Required for GitHub Operations

| Secret Name | Description | Example |
|------------|-------------|---------|
| `PUBLIC_GITHUB_TOKEN` | GitHub PAT with `public_repo` scope | `ghp_xxxxxxxx` |

### Required for LLM Operations

| Secret Name | Description | Example |
|------------|-------------|---------|
| `LLM_PRIMARY_API_KEY` | Primary LLM provider API key | DeepSeek / DS key |
| `LLM_PRIMARY_BASE_URL` | Primary LLM base URL | `https://api.deepseek.com/v1` |
| `LLM_PRIMARY_MODEL` | Primary LLM model name | `deepseek-chat` |
| `LLM_BACKUP_1_API_KEY` | Backup 1 API key | SenseNova key |
| `LLM_BACKUP_1_BASE_URL` | Backup 1 base URL | `https://token.sensenova.cn/v1` |
| `LLM_BACKUP_1_MODEL` | Backup 1 model name | `sensenova-6.7-flash-lite` |
| `LLM_BACKUP_2_API_KEY` | Backup 2 API key | Agnes key |
| `LLM_BACKUP_2_BASE_URL` | Backup 2 base URL | `https://apihub.agnes-ai.com/v1` |
| `LLM_BACKUP_2_MODEL` | Backup 2 model name | `agnes-2.0-flash` |

---

## 6. How to test manually

### Test LLM Provider Configuration

```bash
python -m contrib_center.main llm-check
```

Expected output (when configured):

```json
{
  "deepseek_or_ds": {
    "configured": true,
    "ok": true,
    "model": "deepseek-chat",
    "base_url_host": "api.deepseek.com",
    "error": null
  },
  "sensenova": {
    "configured": true,
    "ok": true,
    "model": "sensenova-6.7-flash-lite",
    "base_url_host": "token.sensenova.cn",
    "error": null
  }
}
```

### Run Daily Pipeline Locally

```bash
python -m contrib_center.main run --mode safe
```

### Check Specific Repo Visibility

```bash
python -m contrib_center.main check-visibility disdorqin/DARIS
```

---

## 7. Common Errors

### Error: `PUBLIC_GITHUB_TOKEN not configured`

**Cause**: The GitHub token is not set in Actions secrets.

**Fix**: Add `PUBLIC_GITHUB_TOKEN` to repository secrets.

---

### Error: `LLM key invalid` / `401 Unauthorized`

**Cause**: The LLM API key is invalid or expired.

**Fix**: 
1. Check the key in repository secrets
2. Verify the key has not expired
3. Try rotating to a new key

---

### Error: `base_url not compatible`

**Cause**: The `base_url` is not OpenAI-compatible.

**Fix**:
1. Verify the `base_url` ends with `/v1` (OpenAI compatibility)
2. Check the provider's API documentation

---

### Error: `model name error`

**Cause**: The model name is incorrect for the provider.

**Fix**:
1. Check the provider's model list
2. Verify the model name spelling

---

### Error: `GitHub API rate limit`

**Cause**: Too many GitHub API calls in a short time.

**Fix**:
1. Wait for rate limit to reset (usually 1 hour)
2. Use a token with higher rate limit

---

### Error: `external repo clone failed`

**Cause**: Network issue or repo no longer public.

**Fix**:
1. Check if the repo is still public
2. Retry the workflow

---

### Error: `issue body fetch failed`

**Cause**: `gh` CLI failed to fetch issue body.

**Effect**: The issue is still processed, but with lower confidence (body is empty).

**Fix**:
1. Check `gh` auth status
2. Verify the issue still exists

---

## 8. Security Boundaries

### ✅ Allowed

- **Read public repos**: Safe Mode + Assisted Mode
- **Clone public repos** to temp directory: Safe Mode + Assisted Mode
- **Generate patch drafts**: Safe Mode + Assisted Mode
- **Run tests** on cloned repos: Safe Mode + Assisted Mode
- **Update profile README**: Safe Mode + Assisted Mode
- **Generate reports**: Safe Mode + Assisted Mode
- **Publish external PR** (Assisted Mode ONLY, with manual confirmation)

### ❌ Forbidden (All Modes)

- **Access private/internal repos**: Always forbidden
- **Publish external PR** without confirmation: Forbidden
- **Comment on external issues**: Forbidden
- **Auto-star repositories**: Forbidden
- **Store API keys in code**: Forbidden
- **Log full API keys**: Forbidden

---

## 9. Safe Mode vs Assisted Mode

### Safe Mode (Default)

- ✅ Scans public issues
- ✅ Clones public repos (temp directory)
- ✅ Generates patch drafts
- ✅ Runs tests
- ✅ Updates profile README
- ✅ Generates daily reports
- ❌ Does NOT publish external PRs
- ❌ Does NOT comment on external issues

### Assisted Mode (Manual Trigger)

- All Safe Mode features +
- ✅ Can publish **one** external PR per run (with confirmation)
- ✅ Requires `confirm_publish=true` in workflow dispatch
- ✅ Strict security gates (max 5 files, max 200 diff lines, tests must pass)

---

## 10. Data Storage

### Repository Files

| Path | Description | Committed? |
|------|-------------|------------|
| `data/action_log.jsonl` | All actions log | ✅ Yes |
| `data/rejected.jsonl` | Rejected issues log | ✅ Yes |
| `data/state.sqlite` | State database | ✅ Yes |
| `data/patch_drafts.jsonl` | Patch drafts | ✅ Yes |
| `reports/YYYY-MM-DD.md` | Daily reports | ✅ Yes |

### Temporary Files (Not Committed)

| Path | Description |
|------|-------------|
| `/tmp/contrib_center_*` | Cloned external repos |
| GitHub Actions runner temp | Workflow execution files |

---

## 11. Monitoring

### Check Workflow Runs

1. Go to your repository on GitHub
2. Click "Actions" tab
3. Select "Daily Public Contribution Center" workflow
4. View run logs

### Check Generated Reports

```bash
cat reports/$(date +%Y-%m-%d).md
```

### Check Rejected Issues

```bash
cat data/rejected.jsonl | jq .
```

---

## 12. Troubleshooting

### Workflow not running

- Check if the schedule cron is correct (09:00 SGT = 01:00 UTC)
- Verify the workflow file is on `main` branch
- Try manual trigger via `workflow_dispatch`

### LLM requests failing

```bash
python -m contrib_center.main llm-check
```

Check which provider is failing.

### Patch generation failing

- Check if `mini-swe-agent` is installed
- Check if external repo is still public
- Check logs in `data/action_log.jsonl`

---

## 13. Advanced Configuration

### Change LLM Provider Order

Edit `config/llm_routes.yml`:

```yaml
default_provider_order:
  - backup_1  # Try SenseNova first
  - primary    # Fall back to DeepSeek
  - backup_2  # Last resort
```

### Adjust Scoring Weights

Edit `config/rules.yml`:

```yaml
scoring:
  weights:
    clarity: 0.20
    impact: 0.25
    ...
```

### Add More Allowed Repos

Edit `config/public_repos.yml`:

```yaml
repositories:
  - name: disdorqin
    full_name: disdorqin/disdorqin
    purpose: GitHub Profile README
    allow:
      update_readme: true
```

---

## 14. FAQ

**Q: Can I run multiple PRs per day?**  
A: No. Assisted Mode limits to 1 external PR per run.

**Q: Can I auto-star repos?**  
A: No. Auto-star is forbidden.

**Q: Where is my API key stored?**  
A: Only in GitHub Actions secrets. Never in code or logs.

**Q: Can I use this for private repos?**  
A: No. This tool is for public repos only.

**Q: What happens if all LLM providers fail?**  
A: The pipeline skips patch generation and writes to the report.

---

**Last updated**: 2026-06-21  
**Maintainer**: disdorqin
