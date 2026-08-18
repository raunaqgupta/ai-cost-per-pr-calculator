#!/usr/bin/env python3
"""
Stop hook: when a turn's tool calls included `gh pr ready` (not --undo),
non-draft `gh pr create`, or `git push` (not a branch delete), update a PR
comment with the token usage and approximate USD cost attributable to the
current branch's work so far, broken down per commit.

Attribution: per-branch state lives in .git/claude-pr-cost/<branch>.json.
Usage is tracked per assistant message (deduped by message id) into a
"pending" bucket; each successful `git commit` on the branch closes that
bucket into a commit segment (keyed by the resulting short sha) and starts
a fresh one. Work not yet committed shows as its own "(not yet committed)"
row rather than being silently dropped. The first time a branch is seen,
tracking starts at zero from that point forward — usage from *before* the
branch existed (other work earlier in the session, or on other branches)
is never backfilled or double-counted.

The same state file remembers the posted comment's id, so a later trigger
(e.g. a follow-up push after the PR is already open) edits that comment in
place with the updated breakdown instead of spamming a new one each time.

Fails soft: any error prints a warning to stderr and exits 0, so a broken
hook never blocks Claude from stopping.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PRICING_URL = "https://platform.claude.com/docs/en/about-claude/pricing.md"

# Fallback rates (USD per million tokens), used only if the live pricing
# fetch fails. Keyed by a lowercase substring matched against the model id
# reported in transcript entries. Keep roughly in sync with the pricing
# page; the live fetch is the source of truth when it succeeds.
FALLBACK_RATES = {
    "sonnet-5": {"input": 2.0, "cache_1h": 4.0, "cache_5m": 2.50, "cache_read": 0.20, "output": 10.0},
    "opus-5": {"input": 5.0, "cache_1h": 10.0, "cache_5m": 6.25, "cache_read": 0.50, "output": 25.0},
    "haiku-4-5": {"input": 1.0, "cache_1h": 2.0, "cache_5m": 1.25, "cache_read": 0.10, "output": 5.0},
}

MODEL_ROW_PATTERNS = [
    # (substring to match in the model id, regex for the pricing table row label)
    ("sonnet-5", r"Claude Sonnet 5 \[through"),
    ("opus-5", r"Claude Opus 5\b"),
    ("haiku-4-5", r"Claude Haiku 4\.5\b"),
]

# First line of `git commit`'s own stdout on success, e.g.
# "[my-branch abc1234] Fix the thing". Absence of a match means the commit
# didn't actually happen (nothing to commit, a failed pre-commit hook, ...).
COMMIT_RESULT_RE = re.compile(r"^\[\S+(?:\s+\(root-commit\))?\s+([0-9a-f]{7,40})\]\s*(.*)", re.MULTILINE)


def zero_usage():
    return {"input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0, "cache_1h": 0, "cache_5m": 0}


def add_usage(bucket, model_key, usage, cache_creation):
    entry = bucket.setdefault(model_key, zero_usage())
    entry["input_tokens"] += usage.get("input_tokens", 0) or 0
    entry["cache_read_input_tokens"] += usage.get("cache_read_input_tokens", 0) or 0
    entry["output_tokens"] += usage.get("output_tokens", 0) or 0
    entry["cache_1h"] += cache_creation.get("ephemeral_1h_input_tokens", 0) or 0
    entry["cache_5m"] += cache_creation.get("ephemeral_5m_input_tokens", 0) or 0


def bucket_has_usage(bucket):
    return any(any(v for v in per_model.values()) for per_model in bucket.values())


def run(cmd, cwd=None, timeout=30):
    # stdin=DEVNULL: never let a subprocess (gh, git, ...) block this hook
    # waiting on an interactive prompt it'll never get an answer to.
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL
    )


def eprint(*args):
    print(*args, file=sys.stderr)


def fetch_live_rates():
    """Best-effort fetch + parse of the pricing table. Returns {} on failure."""
    try:
        # urllib's default User-Agent is blocked by this host; curl works.
        res = subprocess.run(
            ["curl", "-sL", "--max-time", "10", PRICING_URL],
            capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL,
        )
        if res.returncode != 0 or not res.stdout:
            raise RuntimeError(f"curl exit {res.returncode}: {res.stderr[:200]}")
        text = res.stdout
    except Exception as e:
        eprint(f"post_pr_cost: pricing fetch failed ({e}), using fallback rates")
        return {}

    rates = {}
    for key, row_pat in MODEL_ROW_PATTERNS:
        m = re.search(row_pat + r".*", text)
        if not m:
            continue
        line = m.group(0)
        nums = re.findall(r"\$([0-9.]+)\s*/\s*MTok", line)
        if len(nums) >= 5:
            rates[key] = {
                "input": float(nums[0]),
                "cache_5m": float(nums[1]),
                "cache_1h": float(nums[2]),
                "cache_read": float(nums[3]),
                "output": float(nums[4]),
            }
    return rates


def model_key(model_id):
    model_id = (model_id or "").lower()
    for key, _ in MODEL_ROW_PATTERNS:
        if key in model_id:
            return key
    return None


def fresh_state():
    return {
        "seen_message_ids": [],
        "handled_tool_use_ids": [],
        "commits": [],  # [{sha, subject, by_model}, ...] in commit order
        "pending_by_model": {},  # usage since the last commit (or ever, if none yet)
        "posted_comment_id": None,
        "posted_pr_number": None,
    }


def load_state(state_path):
    if not state_path.exists():
        return fresh_state()
    try:
        state = json.loads(state_path.read_text())
    except Exception:
        return fresh_state()

    if "by_model" in state and "commits" not in state:
        # Migrate from the pre-per-commit format: nothing was lost, it just
        # wasn't attributed to a commit yet — carry it forward as pending.
        state = {
            "seen_message_ids": state.get("seen_message_ids", []),
            "handled_tool_use_ids": state.get("handled_tool_use_ids", []),
            "commits": [],
            "pending_by_model": state.get("by_model", {}),
            "posted_comment_id": state.get("posted_comment_id"),
            "posted_pr_number": state.get("posted_pr_number"),
        }

    defaults = fresh_state()
    for key, default in defaults.items():
        state.setdefault(key, default)
    return state


def save_state(state_path, state):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))


def is_trigger(cmd):
    if re.search(r"\bgh\s+pr\s+ready\b", cmd) and "--undo" not in cmd:
        return True
    if re.search(r"\bgh\s+pr\s+create\b", cmd) and "--draft" not in cmd:
        return True
    if re.search(r"\bgit\s+push\b", cmd) and not re.search(r"(^|\s)(--delete|-d)(\s|$)", cmd):
        return True
    return False


def is_commit_command(cmd):
    return bool(re.search(r"\bgit\s+commit\b", cmd))


def cost_for_bucket(bucket, live_rates):
    """Returns (rows, total_tokens, total_cost) for one usage bucket (by model)."""
    rows = []
    total_tokens = 0
    total_cost = 0.0
    for mk, t in bucket.items():
        rates = live_rates.get(mk) or FALLBACK_RATES.get(mk)
        tokens = t["input_tokens"] + t["cache_read_input_tokens"] + t["output_tokens"] + t["cache_1h"] + t["cache_5m"]
        total_tokens += tokens
        if not rates:
            rows.append((mk, t, tokens, None))
            continue
        cost = (
            t["input_tokens"] * rates["input"]
            + t["cache_1h"] * rates["cache_1h"]
            + t["cache_5m"] * rates["cache_5m"]
            + t["cache_read_input_tokens"] * rates["cache_read"]
            + t["output_tokens"] * rates["output"]
        ) / 1_000_000
        total_cost += cost if cost is not None else 0
        rows.append((mk, t, tokens, cost))
    return rows, total_tokens, total_cost


def main():
    payload = json.load(sys.stdin)
    transcript_path = payload.get("transcript_path")
    cwd = payload.get("cwd") or "."
    if not transcript_path or not Path(transcript_path).exists():
        return

    branch_res = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    if branch_res.returncode != 0:
        return
    branch = branch_res.stdout.strip()
    if branch in ("HEAD", ""):
        return

    default_branch = "main"
    dr = run(["gh", "repo", "view", "--json", "defaultBranchRef", "-q", ".defaultBranchRef.name"], cwd=cwd)
    if dr.returncode == 0 and dr.stdout.strip():
        default_branch = dr.stdout.strip()
    if branch in (default_branch, "master", "main"):
        return

    repo_root_res = run(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    if repo_root_res.returncode != 0:
        return
    repo_root = Path(repo_root_res.stdout.strip())

    # Commits actually ahead of the base branch — the true membership test
    # for "is this commit part of this PR". A long-running session that
    # touches several branches in turn (e.g. #75 then #77 then #79 then
    # #81) replays its whole transcript when a *new* branch's state file is
    # first created, which without this check would sweep in commits made
    # earlier in the session on those other, already-merged branches (#83).
    branch_shas = None  # None = couldn't determine; skip filtering rather than wrongly drop rows
    base_ref = default_branch
    base_res = run(["git", "rev-parse", "--verify", base_ref], cwd=cwd)
    if base_res.returncode != 0:
        base_ref = f"origin/{default_branch}"
        base_res = run(["git", "rev-parse", "--verify", base_ref], cwd=cwd)
    if base_res.returncode == 0:
        mb_res = run(["git", "merge-base", base_ref, "HEAD"], cwd=cwd)
        if mb_res.returncode == 0 and mb_res.stdout.strip():
            log_res = run(["git", "log", "--format=%H", f"{mb_res.stdout.strip()}..HEAD"], cwd=cwd)
            if log_res.returncode == 0:
                branch_shas = set(log_res.stdout.split())
    if branch_shas is None:
        eprint("post_pr_cost: could not resolve commits ahead of base branch, skipping commit-membership filter")

    def in_branch(short_sha):
        return branch_shas is None or any(full.startswith(short_sha) for full in branch_shas)

    safe_branch = re.sub(r"[^A-Za-z0-9._-]", "__", branch)
    state_path = repo_root / ".git" / "claude-pr-cost" / f"{safe_branch}.json"
    state = load_state(state_path)
    seen = set(state["seen_message_ids"])
    already_seen = set(seen)  # snapshot: which messages were already counted before this run
    handled_tool_use = set(state["handled_tool_use_ids"])
    # Drop any commits recorded before this filter existed (or from a stale
    # state file) that aren't actually ahead of the base branch.
    commits = [c for c in state["commits"] if in_branch(c["sha"])]
    pending = state["pending_by_model"]

    tool_use_commands = {}  # tool_use_id -> command string
    tool_results = {}  # tool_use_id -> result text
    trigger_tool_use_ids = []  # Bash tool_use ids in this batch matching a trigger command
    new_messages = []  # [{mid, model, usage, cache_creation, commit_tids: [...]}] in file order, new only

    with open(transcript_path) as f:
        lines = f.readlines()

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue

        etype = entry.get("type")
        msg = entry.get("message", {}) if isinstance(entry.get("message"), dict) else {}

        if etype == "assistant":
            mid = msg.get("id")
            content = msg.get("content", [])
            commit_tids_this_message = []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash":
                        cmd = (block.get("input") or {}).get("command", "")
                        tid = block.get("id")
                        if tid:
                            tool_use_commands[tid] = cmd
                            if tid not in handled_tool_use and is_trigger(cmd):
                                trigger_tool_use_ids.append(tid)
                            if is_commit_command(cmd):
                                commit_tids_this_message.append(tid)

            if not mid or mid in already_seen:
                continue
            usage = msg.get("usage", {}) or {}
            new_messages.append({
                "mid": mid,
                "model": model_key(msg.get("model", "unknown")) or msg.get("model", "unknown"),
                "usage": usage,
                "cache_creation": usage.get("cache_creation", {}) or {},
                "commit_tids": commit_tids_this_message,
            })
            seen.add(mid)

        elif etype == "user":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tid = block.get("tool_use_id")
                        c = block.get("content")
                        if isinstance(c, list):
                            texts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
                            tool_results[tid] = "\n".join(texts)
                        elif isinstance(c, str):
                            tool_results[tid] = c

    # Second pass: now that tool_results is fully populated, replay the new
    # messages in order, accumulating into `pending` and closing it into a
    # commit segment wherever a `git commit` in that message actually
    # succeeded (a failed/no-op commit attempt never closes the bucket).
    for m in new_messages:
        add_usage(pending, m["model"], m["usage"], m["cache_creation"])
        for tid in m["commit_tids"]:
            result_text = tool_results.get(tid, "")
            cm = COMMIT_RESULT_RE.search(result_text)
            if not cm:
                continue
            sha, subject = cm.group(1)[:7], cm.group(2).strip()
            if in_branch(sha):
                commits.append({"sha": sha, "subject": subject, "by_model": pending})
            pending = {}

    state["seen_message_ids"] = list(seen)
    state["commits"] = commits
    state["pending_by_model"] = pending

    if not trigger_tool_use_ids:
        save_state(state_path, state)
        return

    # Resolve the PR number from the triggering command / its output.
    pr_number = None
    trigger_id = trigger_tool_use_ids[-1]
    cmd = tool_use_commands.get(trigger_id, "")
    tm = re.search(r"gh\s+pr\s+ready\s+(\d+)", cmd)
    if tm:
        pr_number = tm.group(1)
    else:
        result_text = tool_results.get(trigger_id, "")
        tm = re.search(r"/pull/(\d+)", result_text)
        if tm:
            pr_number = tm.group(1)
    if not pr_number:
        pv = run(["gh", "pr", "view", "--json", "number", "-q", ".number"], cwd=cwd)
        if pv.returncode == 0 and pv.stdout.strip():
            pr_number = pv.stdout.strip()

    if not pr_number:
        # Don't mark these tool_use ids handled — e.g. a push before any PR
        # exists yet legitimately has nothing to resolve. Leaving them
        # unhandled means the next Stop event retries resolution instead of
        # silently giving up forever.
        save_state(state_path, state)
        eprint("post_pr_cost: could not resolve PR number, skipping comment")
        return

    state["handled_tool_use_ids"] = list(handled_tool_use | set(trigger_tool_use_ids))
    save_state(state_path, state)

    live_rates = fetch_live_rates()

    lines_out = [
        "## Token usage and cost for this PR's work, by commit",
        "",
        "Auto-updated by a Stop hook (`.claude/hooks/post_pr_cost.py`) each time this branch is pushed or the PR is marked ready — tracks usage since the branch was first seen, not the whole session if other work happened elsewhere. Rates from " + PRICING_URL + (" (live)" if live_rates else " (fallback, live fetch failed)") + ".",
        "",
        "| Commit | Model | Input | Cache write (1h/5m) | Cache read | Output | Tokens | Cost |",
        "|---|---|---|---|---|---|---|---|",
    ]

    grand_tokens = 0
    grand_cost = 0.0
    any_unknown_rate = False

    def emit_rows(label, bucket):
        nonlocal grand_tokens, grand_cost, any_unknown_rate
        rows, tokens, cost = cost_for_bucket(bucket, live_rates)
        grand_tokens += tokens
        grand_cost += cost
        for mk, t, row_tokens, row_cost in rows:
            cost_str = f"${row_cost:.4f}" if row_cost is not None else "n/a"
            if row_cost is None:
                any_unknown_rate = True
            lines_out.append(
                f"| {label} | {mk} | {t['input_tokens']:,} | {t['cache_1h']:,}/{t['cache_5m']:,} | "
                f"{t['cache_read_input_tokens']:,} | {t['output_tokens']:,} | {row_tokens:,} | {cost_str} |"
            )

    for c in commits:
        subject = c["subject"][:60] + ("…" if len(c["subject"]) > 60 else "")
        emit_rows(f"`{c['sha']}` {subject}", c["by_model"])

    if bucket_has_usage(pending):
        emit_rows("_(not yet committed)_", pending)

    lines_out.append("")
    total_note = " (some rows above have unknown per-model rates, excluded from this total)" if any_unknown_rate else ""
    lines_out.append(f"**Total: {grand_tokens:,} tokens, ≈${grand_cost:.4f}**{total_note}")
    body = "\n".join(lines_out)

    existing_comment_id = state.get("posted_comment_id")
    same_pr = state.get("posted_pr_number") == pr_number
    dry_run = os.environ.get("POST_PR_COST_DRY_RUN")

    if existing_comment_id and same_pr:
        if dry_run:
            eprint(f"post_pr_cost: [dry run] would edit comment {existing_comment_id} on PR #{pr_number}:\n{body}")
            print(json.dumps({"systemMessage": f"[dry run] would update ~${grand_cost:.2f} on PR #{pr_number}."}))
            return
        er = subprocess.run(
            ["gh", "api", "-X", "PATCH", f"repos/{{owner}}/{{repo}}/issues/comments/{existing_comment_id}", "-F", "body=@-"],
            cwd=cwd, input=body, capture_output=True, text=True, timeout=30,
        )
        if er.returncode == 0:
            print(json.dumps({"systemMessage": f"Updated token usage + cost (~${grand_cost:.2f}) on PR #{pr_number}."}))
            return
        eprint(f"post_pr_cost: comment edit failed ({er.stderr}), falling back to a new comment")

    if dry_run:
        eprint(f"post_pr_cost: [dry run] would post new comment to PR #{pr_number}:\n{body}")
        print(json.dumps({"systemMessage": f"[dry run] would post ~${grand_cost:.2f} to PR #{pr_number}."}))
        return

    cr = run(["gh", "pr", "comment", pr_number, "--body", body], cwd=cwd)
    if cr.returncode != 0:
        eprint(f"post_pr_cost: gh pr comment failed: {cr.stderr}")
        return

    cm = re.search(r"#issuecomment-(\d+)", cr.stdout)
    state["posted_comment_id"] = cm.group(1) if cm else None
    state["posted_pr_number"] = pr_number
    save_state(state_path, state)

    print(json.dumps({"systemMessage": f"Posted token usage + cost (~${grand_cost:.2f}) to PR #{pr_number}."}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        eprint(f"post_pr_cost: unexpected error: {e}")
        sys.exit(0)
