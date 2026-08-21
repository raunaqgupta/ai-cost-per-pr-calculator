# prompt-classifier

A Claude Code plugin that classifies every prompt as a **question**, an **issue**, or a **pr**, and nudges the session toward the matching lane of the [git-workflow](../git-workflow/README.md) pipeline — without performing any GitHub actions itself.

## What it does

It installs a `UserPromptSubmit` hook (`hooks/route_prompt.py`). On every prompt, the hook:

1. Sends the prompt to a headless `claude -p` call (model: `claude-haiku-4-5-20251001`) asking for a strict classification: `question`, `issue`, or `pr`.
2. Injects `additionalContext` matching the category:
   - **question** — answer directly, no issue/PR needed.
   - **issue** — clarify if needed, then open a GitHub issue before making any code changes.
   - **pr** — make sure an issue exists (open one first if not), then branch, implement, and open a PR referencing it.

The hook only classifies and nudges — it never calls `gh` itself. Opening the actual issue/PR is still owned by whatever pipeline is in effect (e.g. `git-workflow`'s injected instructions, or a repo's own `CLAUDE.md`).

## Recursion guard

The classification call is itself a `claude -p` invocation, which would otherwise re-trigger this same `UserPromptSubmit` hook on the nested session. The hook sets `ROUTE_PROMPT_ACTIVE=1` on that child process's environment and checks it first thing on entry, so the nested call skips straight through instead of classifying itself.

## Requirements

- `claude` CLI on `PATH`, able to run headless (`claude -p`).
- `python3` on `PATH`.

## Install

```
claude plugin marketplace add raunaqgupta/ai-harness
claude plugin install prompt-classifier@ai-harness
```

Or point at a local checkout:

```
claude plugin marketplace add /path/to/ai-harness
claude plugin install prompt-classifier@ai-harness
```

## Notes

- Fails soft: any error (missing `claude` on `PATH`, timeout, malformed classifier output, ...) is logged to stderr and results in no `additionalContext` being injected — the prompt just passes through unclassified rather than blocking.
- Adds a headless model call's worth of latency and cost to every prompt. Pair with `git-workflow` for the actual issue/PR pipeline; on its own this plugin only classifies.
