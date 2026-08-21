# git-workflow

A Claude Code plugin that applies a generic actionable-request pipeline — clarify, open a GitHub issue (or a sub-issue if one fits inside an issue already being solved), branch, wait for sign-off, implement, PR — to every Claude Code session, independent of which repo it's running in.

## Status

Implemented. A `SessionStart` hook (`hooks/inject_pipeline.py`) injects the pipeline text in `hooks/git-workflow.md` into context at the start of every session — the same role a project's own `CLAUDE.md` can play locally, but generalized so it applies everywhere without per-repo setup. Individual repos can still extend it with their own domain-specific steps in their own `CLAUDE.md` (e.g. a model/spec step between branch and validate) — `git-workflow.md` explicitly makes room for that.

## Requirements

- [GitHub CLI](https://cli.github.com/) (`gh`), authenticated.
- Claude Code running inside a git repository whose issues/PRs live on GitHub.
