# Issue-driven workflow

## Opening an issue

1. **Clarify.** If the request is ambiguous, ask before proceeding.
2. **Check for an active issue.** If another issue is currently being solved (its branch/PR is open, not yet merged), decide whether this new request fits inside that issue's existing scope:
   - **In bounds** — file it as a sub-issue of the active one: `gh issue create` as usual, then link it with GitHub's native sub-issue relationship (`gh api graphql` mutation `addSubIssue`, parent identified by its issue node id from `gh issue view <parent#> --json id`). It's typically solved on the same branch/PR as the parent.
   - **Out of bounds** — file it as its own independent issue, unrelated to the active one.
   - No issue currently in flight — this step is a no-op.
3. **Issue.** Construct it into a GitHub issue (`gh issue create`) — title, scope, motivation, acceptance criteria where known.

## Solving an issue

4. **Branch + PR.** Create a feature branch and open a PR (as a draft) referencing the issue, before starting implementation.
5. **Repo-specific step, if any.** Some repos define an extra step here in their own `CLAUDE.md` (e.g. updating a domain model or spec before code). Follow it if present; otherwise go straight to Validate.
6. **Validate.** With any repo-specific step done, ask for review; wait for explicit sign-off. Do not start implementation before this.
7. **Code.** Implement on the same branch/PR, verify (tests/build/run), commit, push, mark the PR ready for review, reference the issue (`Resolves #N`).

## Scope exemption

Issues that are pure tooling, docs, CI, or process — nothing touching the product's actual runtime behavior — skip Branch+PR-early and the repo-specific step entirely: go straight from Issue to Validate (confirm the issue itself) → Code (branch, implement, PR).
