# Lessons — Cline sessions

Agent-scoped lessons. The canonical dated lessons live in
`.agents/memory/lessons.md`; those win on conflict. These are the ones worth
carrying forward when working from a Cline session, mostly about process and
tooling rather than about product code.

### 2026-10-01 - A green suite is not evidence a defect is fixed

- **Symptom:** The historical audit claimed the review-gate defect was
  "mutation-proven invisible to the suite": replacing the CLI readiness
  calculation with `ready = True` left the whole suite passing. That is
  simultaneously a warning and an invitation — it means the suite had no test
  standing on that line at all.
- **Root cause:** The tested path and the broken path were different paths. The
  standard flow was covered; the CLI custom-DAG path was not, because exercising
  it needed real worktree and git setup that no test did.
- **Solution:** Reproduce the defect in isolation first, then write tests that
  fail against the unfixed code, then fix. For path-divergence defects, add a
  cheap structural assertion (e.g. asserting the CLI module no longer contains
  the old formula) *in addition to* behavioural tests — the structural one
  cannot be satisfied by accident through an unrelated code path.
- **Remember:** Before trusting a passing suite on a reported bug, check whether
  any test would notice if the fix were deleted. Deleting the fix is the test.
  A regression suite that has never been seen failing is an assumption.

### 2026-10-01 - "Do not assume the description is still accurate" is doing real work

- **Symptom:** The task supplied four "known leads to verify", each stated
  confidently enough to be acted on directly, and warned specifically not to
  reintroduce four UGA fixes from earlier audits.
- **Root cause:** Audit reports describe a codebase at a point in time; this is
  a shared, actively-edited repository. In this case all four AgentOps leads
  turned out to be genuinely live in current source, and the four warned-about
  UGA items were out of scope and untouched — but that was only knowable by
  looking.
- **Solution:** Reproduce each lead with a small throwaway script before writing
  any production code. Three of the four repros needed fixes first (a wrong
  `create_workflow` signature, a wrong enum member, a foreign-key constraint
  from fake IDs) — which is itself the argument: the reproduction is where you
  find out that your mental model of the API is wrong.
- **Remember:** Treat a bug report as a hypothesis with a file:line, not a
  ticket. Reproducing first also prevents the specific failure mode of "fixing"
  code that was already correct and thereby breaking it.

### 2026-10-01 - Report what you rejected, not just what you did

- **Symptom:** The task asked for an explicit list of historical findings
  rejected as stale, and named four UGA fixes that must not be blindly
  reintroduced.
- **Root cause:** Silent scope decisions are indistinguishable from silent
  omissions to the next session. A future agent who cannot tell whether ROOT-015
  was checked-and-fine or simply forgotten will re-investigate it or, worse,
  trust it.
- **Solution:** Record rejected findings by name with the reason, and record
  deliberate non-changes in the code and in memory, not just in chat.
- **Remember:** "I did not touch X" is a finding. Write it where it will be
  read. This also protects against the opposite failure — a future session
  "fixing" something that was intentionally left alone, like `retry_merge`
  gating, which is now tracked as D9 in the roadmap.

### 2026-10-01 - Leave the dirty tree alone, and back it up first

- **Symptom:** The repo had pre-existing uncommitted changes (a deleted
  tokenizer file, an untracked HTML file) before any work began. Mid-task,
  another session began modifying five `universal-game-agent/` files.
- **Root cause:** This is a shared working tree under a single-writer rule that
  is easy to state and easy to violate. A convenient `git add -A` would have
  swept someone else's half-finished work into a commit.
- **Solution:** Back up `git diff` plus untracked files to `%TEMP%` before
  starting; stage by explicit path list; verify `git show --stat` before
  considering a commit complete.
- **Remember:** The single-writer rule protects against concurrency damage, but
  staging discipline is what actually enforces it. When you find unexplained
  modifications in `git status`, leave them and say so — that is a signal worth
  surfacing, not noise to clean up.