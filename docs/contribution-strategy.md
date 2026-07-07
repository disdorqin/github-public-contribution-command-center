# Open Source Contribution Strategy

This document outlines a principled approach to open source contribution. It is intended for developers who want to participate in open source communities in a way that respects project maintainers, follows community norms, and builds genuine skill.

## Principles

1. **Contribute value, not noise.** Every contribution should improve the project for its users or maintainers.
2. **Start small, learn first.** Understand a project's conventions before proposing changes.
3. **Respect maintainers' time.** Well-documented, focused contributions are more likely to be accepted.
4. **Build reputation over time.** Consistent, quality contributions matter more than volume.
5. **Follow the project's guidelines.** Each project has its own contributing guide, code of conduct, and PR template.

## Finding Good First Contributions

### On GitHub

- **`good first issue` label**: Many projects tag beginner-friendly issues. Search:
  ```
  https://github.com/search?q=label%3A%22good+first+issue%22&type=Issues
  ```
- **`help wanted` label**: Issues where maintainers explicitly ask for help.
- **Documentation improvements**: Almost every project accepts doc fixes. These are often the easiest way to start.
- **Test coverage**: Adding or improving tests is universally valuable and rarely controversial.
- **Examples and tutorials**: Many projects lack up-to-date examples.

### Evaluate Before Contributing

Before investing time, check:

1. Is the project **active**? (commits within the last 3 months)
2. Are issues being **responded to**?
3. Is there a **CONTRIBUTING.md** or similar guide?
4. Are recent PRs being **reviewed and merged**?
5. Is the **license** compatible with your use case?

## Types of Contributions (Prioritized)

### High Value, Low Risk

| Type | Examples | Why It Helps |
|------|----------|-------------|
| Documentation | Fix typos, clarify README, update examples | Low maintenance cost, always welcome |
| Test improvements | Add unit tests, edge cases, fixtures | Increases project reliability |
| Bug fixes | Small, well-scoped issues with reproduction steps | Directly helps users |
| Issue triage | Reproduce bugs, add missing context | Reduces maintainer burden |

### Medium Effort

| Type | Examples | Notes |
|------|----------|-------|
| Feature implementation | New functionality with clear spec | Requires coordination with maintainers |
| Performance optimization | Benchmarks, profiling results | Needs to be validated |
| Dependency updates | Version bumps, compatibility fixes | Risk of breaking changes |

### Avoid

- ❌ Spam PRs (whitespace changes, renaming variables without reason)
- ❌ Mass "fix typo" campaigns across unrelated repos
- ❌ Automated tool submissions without manual review
- ❌ PRs that don't follow the project's template or style
- ❌ Submitting features without prior discussion (always open an issue first)

## Submitting Quality PRs

### Before You Open a PR

1. Read the project's `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md`
2. Check existing issues and PRs for related work
3. For non-trivial changes, open an issue first to discuss the approach

### PR Checklist

- [ ] Single, focused change per PR
- [ ] Clear, descriptive title and description
- [ ] References the related issue (e.g., "Fixes #123")
- [ ] Follows project code style and conventions
- [ ] Includes or updates tests
- [ ] Updates relevant documentation
- [ ] No unrelated changes

### After Submitting

- Respond to reviewer feedback promptly
- Be open to suggestions — maintainers know their project best
- If a PR is inactive for a while, a polite nudge after 2-4 weeks is acceptable

## Tracking Your Contributions

Keep a personal log of:

- Projects you've contributed to
- Types of contributions (docs, tests, features)
- What you learned from each experience

This helps you identify patterns, track growth, and build a portfolio for future opportunities.

## Working with Forked Repositories

This repository (`github-public-contribution-command-center`) is a fork of an upstream project (mini-swe-agent). When working with forks:

1. **Sync regularly**: Keep your fork up to date with the upstream
2. **Contribute upstream**: If you fix a bug in a fork, submit the fix to the upstream project too
3. **Be transparent**: Mention in PRs that the change originated in a fork
4. **Respect upstream license**: Forked code retains the original license terms

## Resources

- [GitHub's Open Source Guide](https://opensource.guide/how-to-contribute/)
- [First Timers Only](https://www.firsttimersonly.com/) — beginner-friendly contributions
- [Up For Grabs](https://up-for-grabs.net/) — curated list of beginner-friendly projects
