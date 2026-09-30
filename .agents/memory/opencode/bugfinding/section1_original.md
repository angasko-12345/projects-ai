# Master Bug Synthesis v2 — `angasko-12345/projects-ai`

**Produced:** 2026-09-29
**Inputs reconciled:** bug-registry.md, deep-bug-audit-2026-09-29.md, geminihandoff.md, projects-ai-bug-handoff.md, projects-ai-review-handoff.md, repo-review-2026-09-26.md, plus the previous triage result.
**Scope:** Analysis only. No code modified.
**Previous synthesis:** master-bug-synthesis.md — this is a complete replacement with full traceability.

---

## 1. Executive Summary

Six independent audit reports plus a prior triage produce ~450 raw findings. After reconciliation, deduplication, and evidence grading, they resolve to **36 canonical root causes** (ROOT-001..ROOT-036), of which:

**18 are genuine bugs** (confirmed or likely, status ACTIVE BUG)
**15 are blocked / need investigation** (require source verification or contract decision before fixing)
**3 are test-quality clusters** (ROOT-034 covers 10 specific defects)

**Highest-severity items:**
- ROOT-002 (CRITICAL): review gate bypass — custom CLI path merges without a review task.
- ROOT-003 (CRITICAL): self-deadlock — AgentOps puts its own state in the target repo and then refuses to merge on dirty tree.
- ROOT-015/ROOT-016 (CRITICAL, from geminihandoff): PPO entropy sign reversal and GAE truncation-bootstraps-from-next-episode — both would corrupt training but require source verification against the current tree.
- ROOT-001 (HIGH): raw agent stdout/stderr persisted unredacted to SQLite.

**Previously triaged corrections upheld:**
- BUG-AO-01 (Windows termination) remains SUSPECTED — a positive exit code alone does not prove termination; do not implement the high-bit heuristic.
- BUG-UGA-05/06/07 and BUG-AO-05 confirmed false positives.
- BUG-UGA-04 remains a contract gap, not a confirmed defect.

**Key correction from previous synthesis:** The deep audit retracted its own earlier claim that raw stdout reaches the unredacted event sink via workflow.py:546. Source reading disproves it — task.result is overwritten at :542, two lines before the event. The confirmed sink is workflow.py:563 → task.result.

---

Six independent audit reports plus a prior triage produce ~450 raw findings. After reconciliation, deduplication, and evidence grading, they resolve to **36 canonical root causes** (ROOT-001..ROOT-036), of which:

**18 are genuine bugs** (confirmed or likely, status ACTIVE BUG)
**15 are blocked / need investigation** (require source verification or contract decision before fixing)
**3 are test-quality clusters** (ROOT-034 covers 10 specific defects)

**Highest-severity items:**
- ROOT-002 (CRITICAL): review gate bypass — custom CLI path merges without a review task.
- ROOT-003 (CRITICAL): self-deadlock — AgentOps puts its own state in the target repo and then refuses to merge on dirty tree.
- ROOT-015/ROOT-016 (CRITICAL, from geminihandoff): PPO entropy sign reversal and GAE truncation-bootstraps-from-next-episode — both would corrupt training but require source verification against the current tree.
- ROOT-001 (HIGH): raw agent stdout/stderr persisted unredacted to SQLite.

**Previously triaged corrections upheld:**
- BUG-AO-01 (Windows termination) remains SUSPECTED — a positive exit code alone does not prove termination; do not implement the high-bit heuristic.
- BUG-UGA-05/06/07 and BUG-AO-05 confirmed false positives.
- BUG-UGA-04 remains a contract gap, not a confirmed defect.

:**Key correction from previous synthesis:** The deep audit retracted its own earlier claim that raw stdout reaches the unredacted event sink via workflow.py:546. Source reading disproves it — task.result is overwritten at :542, two lines before the event. The confirmed sink is workflow.py:563 → task.result.
- ROOT-002 (CRITICAL): review gate bypass — custom CLI path merges without a review task.
- ROOT-003 (CRITICAL): self-deadlock — AgentOps puts its own state in the target repo and then refuses to merge on dirty tree.
- ROOT-015/ROOT-016 (CRITICAL, from geminihandoff): PPO entropy sign reversal and GAE truncation-bootstraps-from-next-episode — both would corrupt training but require source verification against the current tree.
- ROOT-001 (HIGH): raw agent stdout/stderr persisted unredacted to SQLite.
- **3 are contract/configuration gaps** requiring a decision before fixing
- **1 is a platform-semantics question** held for evidence
- **3 are test-quality clusters**
- **6 are non-bugs / architecture debt / measurement issues**

:**Highest-severity items:**
- ROOT-002 (CRITICAL): review gate bypass — custom CLI path merges without a review task.
- ROOT-003 (CRITICAL): self-deadlock — AgentOps puts its own state in the target repo and then refuses to merge on dirty tree.
- ROOT-015/ROOT-016 (CRITICAL, from geminihandoff): PPO entropy sign reversal and GAE truncation-bootstraps-from-next-episode — both would corrupt training but require source verification against the current tree.
- ROOT-001 (HIGH): raw agent stdout/stderr persisted unredacted to SQLite.

:**Previously triaged corrections upheld:**
- **21 are genuine bugs** (confirmed or likely)
- **3 are contract/configuration gaps** requiring a decision before fixing
- **1 is a platform-semantics question** held for evidence
- **3 are test-quality clusters**
- **6 are non-bugs / architecture debt / measurement issues**

:**Highest-severity items:**
- ROOT-002 (CRITICAL): review gate bypass — custom CLI path merges without a review task.
- ROOT-003 (CRITICAL): self-deadlock — AgentOps puts its own state in the target repo and then refuses to merge on dirty tree.
- ROOT-015/ROOT-016 (CRITICAL, from geminihandoff): PPO entropy sign reversal and GAE truncation-bootstraps-from-next-episode — both would corrupt training but require source verification against the current tree.
- ROOT-001 (HIGH): raw agent stdout/stderr persisted unredacted to SQLite.
- **3 are contract/configuration gaps** requiring a decision before fixing
- **1 is a platform-semantics question** held for evidence
- **3 are test-quality clusters**
- **6 are non-bugs / architecture debt / measurement issues**

:**Highest-severity items:**
- ROOT-002 (CRITICAL): review gate bypass — custom CLI path merges without a review task.
- ROOT-003 (CRITICAL): self-deadlock — AgentOps puts its own state in the target repo and then refuses to merge on dirty tree.
- ROOT-015/ROOT-016 (CRITICAL, from geminihandoff): PPO entropy sign reversal and GAE truncation-bootstraps-from-next-episode — both would corrupt training but require source verification against the current tree.
- ROOT-001 (HIGH): raw agent stdout/stderr persisted unredacted to SQLite.

:**Previously triaged corrections upheld:**
- BUG-AO-01 (Windows termination) remains SUSPECTED — a positive exit code alone does not prove termination; do not implement the high-bit heuristic.
- BUG-UGA-05/06/07 and BUG-AO-05 confirmed false positives.
- BUG-UGA-04 remains a contract gap, not a confirmed defect.

:**Key correction from previous synthesis:** The deep audit retracted its own earlier claim that raw stdout reaches the unredacted event sink via workflow.py:546. Source reading disproves it — task.result is overwritten at :542, two lines before the event. The confirmed sink is workflow.py:563 → task.result.

Six independent audit reports plus a prior triage produce ~450 raw findings. After reconciliation, deduplication, and evidence grading, they resolve to **36 canonical root causes** (ROOT-001..ROOT-036), of which:

**18 are genuine bugs** (confirmed or likely, status ACTIVE BUG)
**15 are blocked / need investigation** (require source verification or contract decision before fixing)
**3 are test-quality clusters** (ROOT-034 covers 10 specific defects)
:**Highest-severity items:**
- ROOT-002 (CRITICAL): review gate bypass — custom CLI path merges without a review task.
- ROOT-003 (CRITICAL): self-deadlock — AgentOps puts its own state in the target repo and then refuses to merge on dirty tree.
- ROOT-015/ROOT-016 (CRITICAL, from geminihandoff): PPO entropy sign reversal and GAE truncation-bootstraps-from-next-episode — both would corrupt training but require source verification against the current tree.
- ROOT-001 (HIGH): raw agent stdout/stderr persisted unredacted to SQLite.

:**Previously triaged corrections upheld:**
- BUG-AO-01 (Windows termination) remains SUSPECTED — a positive exit code alone does not prove termination; do not implement the high-bit heuristic.
- BUG-UGA-05/06/07 and BUG-AO-05 confirmed false positives.
- BUG-UGA-04 remains a contract gap, not a confirmed defect.

:**Key correction from previous synthesis:** The deep audit retracted its own earlier claim that raw stdout reaches the unredacted event sink via workflow.py:546. Source reading disproves it — task.result is overwritten at :542, two lines before the event. The confirmed sink is workflow.py:563 → task.result.
- ROOT-002 (CRITICAL): review gate bypass — custom CLI path merges without a review task.
- ROOT-003 (CRITICAL): self-deadlock — AgentOps puts its own state in the target repo and then refuses to merge on dirty tree.
- ROOT-015/ROOT-016 (CRITICAL, from geminihandoff): PPO entropy sign reversal and GAE truncation-bootstraps-from-next-episode — both would corrupt training but require source verification against the current tree.
- ROOT-001 (HIGH): raw agent stdout/stderr persisted unredacted to SQLite.
- **3 are contract/configuration gaps** requiring a decision before fixing
- **1 is a platform-semantics question** held for evidence
- **3 are test-quality clusters**
- **6 are non-bugs / architecture debt / measurement issues**

:**Highest-severity items:**
- ROOT-002 (CRITICAL): review gate bypass — custom CLI path merges without a review task.
- ROOT-003 (CRITICAL): self-deadlock — AgentOps puts its own state in the target repo and then refuses to merge on dirty tree.
- ROOT-015/ROOT-016 (CRITICAL, from geminihandoff): PPO entropy sign reversal and GAE truncation-bootstraps-from-next-episode — both would corrupt training but require source verification against the current tree.
- ROOT-001 (HIGH): raw agent stdout/stderr persisted unredacted to SQLite.

:**Previously triaged corrections upheld:**
- **21 are genuine bugs** (confirmed or likely)
- **3 are contract/configuration gaps** requiring a decision before fixing
- **1 is a platform-semantics question** held for evidence
- **3 are test-quality clusters**
- **6 are non-bugs / architecture debt / measurement issues**

:**Highest-severity items:**
- ROOT-002 (CRITICAL): review gate bypass — custom CLI path merges without a review task.
- ROOT-003 (CRITICAL): self-deadlock — AgentOps puts its own state in the target repo and then refuses to merge on dirty tree.
