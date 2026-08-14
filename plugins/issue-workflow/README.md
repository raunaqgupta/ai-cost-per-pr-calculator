# issue-workflow

A Claude Code plugin that applies a generic actionable-request pipeline — clarify, open a GitHub issue (or a sub-issue if one fits inside an issue already being solved), branch, wait for sign-off, implement, PR — to every Claude Code session, independent of which repo it's running in.

## Status

Scaffold only. This plugin currently registers no hooks or skills; it exists to reserve its shape (manifest + marketplace entry) ahead of a follow-up that adds the actual mechanism.

## Planned design

A `SessionStart` hook will inject the repo-agnostic pipeline text into context at the start of every session — the same role a project's own `CLAUDE.md` can play locally, but generalized so it applies everywhere without per-repo setup. Individual repos can still extend it with their own domain-specific steps in their own `CLAUDE.md` (e.g. a model/spec step between branch and validate).

## Requirements (planned)

- [GitHub CLI](https://cli.github.com/) (`gh`), authenticated.
- Claude Code running inside a git repository whose issues/PRs live on GitHub.
