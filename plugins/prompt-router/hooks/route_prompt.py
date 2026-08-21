#!/usr/bin/env python3
"""
UserPromptSubmit hook: classifies the incoming prompt as a "question", an
"issue", or a "pr" via a headless `claude -p` call to a cheap/fast model,
then injects additionalContext nudging the session toward the matching lane
of the git-workflow pipeline (or a direct answer, for questions). It never
performs the GitHub actions itself — git-workflow's own pipeline still owns
opening issues/PRs, this just tells it which lane applies.

Recursion guard: the classification call is itself a `claude -p` invocation,
which would otherwise re-trigger this same UserPromptSubmit hook. The
ROUTE_PROMPT_ACTIVE env var is set on the child process and checked first
thing on entry, so the nested call skips straight through.

Fails soft: any error (missing `claude` on PATH, timeout, malformed model
output, ...) is logged to stderr and results in no additionalContext being
injected — the prompt just passes through unclassified rather than blocking.
"""
import json
import os
import re
import subprocess
import sys

CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"
CLASSIFIER_TIMEOUT = 15

CLASSIFY_PROMPT_TEMPLATE = """You are a fast prompt classifier for a coding assistant harness. Classify the user's message below into exactly one category:

- "question": can be answered directly, with no code change or GitHub issue needed.
- "issue": reports a bug, requests a feature, or describes a problem that should be tracked as a GitHub issue before any code changes happen.
- "pr": asks to implement or fix something where the work should happen on a branch culminating in a pull request (including continuing already-scoped implementation work).

Respond with ONLY a compact JSON object, no other text, no markdown fences: {{"category": "question|issue|pr"}}

User message:
{prompt}"""

CONTEXT_BY_CATEGORY = {
    "question": (
        "[prompt-router] This prompt looks like a question. Answer it directly — "
        "no GitHub issue or PR is needed unless the answer itself reveals one is warranted."
    ),
    "issue": (
        "[prompt-router] This prompt looks like a new bug report or feature request. "
        "Follow the issue-driven workflow: clarify if needed, then open a GitHub issue "
        "capturing it before making any code changes."
    ),
    "pr": (
        "[prompt-router] This prompt looks like a request to implement or fix something. "
        "Follow the issue-driven workflow: make sure an issue exists (open one first if not), "
        "then branch, implement, and open a PR referencing it."
    ),
}


def eprint(*args):
    print(*args, file=sys.stderr)


def classify(prompt, cwd):
    env = dict(os.environ)
    env["ROUTE_PROMPT_ACTIVE"] = "1"
    res = subprocess.run(
        [
            "claude", "-p", CLASSIFY_PROMPT_TEMPLATE.format(prompt=prompt),
            "--model", CLASSIFIER_MODEL,
            "--output-format", "json",
        ],
        cwd=cwd, env=env, capture_output=True, text=True,
        timeout=CLASSIFIER_TIMEOUT, stdin=subprocess.DEVNULL,
    )
    if res.returncode != 0:
        raise RuntimeError(f"classifier exited {res.returncode}: {res.stderr[:300]}")

    outer = json.loads(res.stdout)
    result_text = outer.get("result", "")
    # Strip markdown fences if the model wrapped its JSON in one anyway.
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", result_text, re.DOTALL)
    inner_text = fence_match.group(1) if fence_match else result_text
    inner = json.loads(inner_text)

    category = inner.get("category")
    if category not in CONTEXT_BY_CATEGORY:
        raise ValueError(f"unexpected category: {category!r}")
    return category


def main():
    if os.environ.get("ROUTE_PROMPT_ACTIVE"):
        return  # nested classification call — skip, avoid recursion

    payload = json.load(sys.stdin)
    prompt = (payload.get("prompt") or "").strip()
    cwd = payload.get("cwd") or "."
    if not prompt:
        return

    category = classify(prompt, cwd)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": CONTEXT_BY_CATEGORY[category],
        },
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        eprint(f"route_prompt: unexpected error: {e}")
        sys.exit(0)
