# ai-harness

A [Claude Code plugin marketplace](https://code.claude.com/docs/en/plugins) hosting a set of small, repo-agnostic plugins:

- **[ai-cost-calculator](plugins/ai-cost-calculator/README.md)** — tracks token usage per commit on a branch and posts (and keeps updated) a PR comment with the approximate USD cost of the AI work behind it.
- **[git-workflow](plugins/git-workflow/README.md)** — applies a generic clarify → issue → branch → validate → code pipeline to every Claude Code session, regardless of which repo it's running in.

Each plugin is self-contained under `plugins/<name>/` — see its own README for what it does and how to install just that one.

## Install the whole marketplace

```
claude plugin marketplace add raunaqgupta/ai-harness
claude plugin install ai-cost-calculator@ai-harness
claude plugin install git-workflow@ai-harness
```
