Build a first-class Verification Engine for AgentOps.

The engine must make verification deterministic and independent from the coding agent's claim that work is complete.

Model verification checks as explicit objects containing:

* id
* name
* command
* working directory
* timeout
* environment policy
* allowlist classification
* required/optional
* status
* exit code
* start/end time
* duration
* stdout/stderr references
* parsed result
* failure reason

Support check types:

* tests
* lint
* formatting
* type checking
* build
* custom allowlisted commands

Verification commands must continue to use the existing safety restrictions.

A task should only be considered verified when all required checks pass.

Implement:

* verification profiles
* per-project configuration
* sequential checks
* parallel independent checks
* fail-fast option
* continue-on-failure option
* verification summaries

Produce a machine-readable VerificationReport.

The report should contain:

* total checks
* passed
* failed
* skipped
* duration
* required failures
* overall status

Integrate the report with AgentRun and workflow state.

Add CLI inspection and GUI visibility.

Add comprehensive tests.

Do not weaken the current command allowlist/security model.
Run all tests.
