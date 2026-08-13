# pr-cost-calculator

A Claude Code plugin that tracks token usage per commit on the current branch and posts (and keeps updated) a PR comment with the approximate USD cost of the AI work behind that PR.

## What it does

It installs a `Stop` hook (`hooks/post_pr_cost.py`). At the end of any turn whose tool calls included `gh pr ready` (not `--undo`), a non-draft `gh pr create`, or `git push` (not a branch delete), the hook:

1. Walks the session transcript for the current branch, attributing token usage to commits (usage not yet committed shows as a separate "(not yet committed)" row).
2. Prices that usage using live rates fetched from the [Claude pricing page](https://platform.claude.com/docs/en/about-claude/pricing.md) (falls back to built-in rates if the fetch fails).
3. Posts a per-commit token/cost breakdown as a comment on the PR for the current branch — or edits its own previous comment in place on subsequent pushes, rather than posting a new one each time.

Tracking is scoped to commits actually ahead of the repo's default branch, starting from when the branch was first seen — it never backfills usage from before the branch existed or from other branches.

State is kept locally per-branch in `.git/claude-pr-cost/<branch>.json` (gitignored by virtue of living inside `.git/`).

## Requirements

- [GitHub CLI](https://cli.github.com/) (`gh`), authenticated, with a `gh repo view` / `gh pr` -capable remote.
- `python3` and `curl` on `PATH`.
- Claude Code running inside a git repository whose PRs live on GitHub.

## Install

```
claude plugin marketplace add raunaqgupta/pr-cost-calculator
claude plugin install pr-cost-calculator@pr-cost-calculator
```

Or point directly at a checkout:

```
claude plugin install --plugin-dir /path/to/pr-cost-calculator
```

## Notes

- Fails soft: any internal error prints a warning to stderr and exits `0`, so a broken hook never blocks Claude from stopping.
- Set `POST_PR_COST_DRY_RUN=1` in the environment to log what would be posted/edited without actually calling `gh pr comment` / `gh api`.
