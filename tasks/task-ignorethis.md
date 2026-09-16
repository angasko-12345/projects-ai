Implement a Failure Analysis subsystem for AgentOps.

When an agent run or verification step fails, classify the failure.

Create a Failure object containing:

* id
* workflow_id
* task_id
* agent_run_id
* source
* category
* severity
* retryable
* repairable
* evidence
* primary error
* related verification checks
* suggested action
* created_at

Initial categories:

AGENT_ERROR
PROCESS_ERROR
TIMEOUT
CANCELLATION
VERIFICATION_FAILURE
TEST_FAILURE
LINT_FAILURE
TYPECHECK_FAILURE
BUILD_FAILURE
ENVIRONMENT_FAILURE
DEPENDENCY_FAILURE
GIT_CONFLICT
DIRTY_WORKTREE
POLICY_VIOLATION
REVIEW_REJECTION
UNKNOWN

Implement deterministic classification first.

Then implement a repair planner.

The repair planner should decide:

* retry same agent
* retry different agent
* repair implementation
* rerun verification
* request human approval
* stop permanently

Do NOT blindly retry.

Retries must have:

* maximum attempts
* backoff
* failure-category rules
* context from previous attempts
* previous verification evidence

Repair attempts must create new AgentRuns linked to their parent attempt.

Persist all decisions.

Expose the repair chain in CLI and GUI.

Add tests for every failure category and repair decision.

Ensure failed worktrees remain inspectable according to current AgentOps behavior.
