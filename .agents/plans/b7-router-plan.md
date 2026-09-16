# Plan — B7 Capability Resolver and Deterministic Router

> Owner: pi (sole writer); copilot (test review); opencode (architecture review).
> Status: implementation and both reviews complete; full suite 336 OK (4 skips).

## Objective

Implement the task-header B7 upgrade without breaking existing constructors,
role gates, preference order, fallback behavior, or persisted-data contracts.

## Architecture decisions

- New `agentops/routing.py` owns profiles, capability resolution, scoring, and
  explainable decisions. It remains free of SQLite and process execution.
- `Capability` gains the ten requested task-level values. Existing A2 values
  and unknown-string passthrough behavior are unchanged.
- `AgentConfig` gains additive profile metadata:
  `display_name`, `priority`, `metadata`, `version_command`, structured-output,
  cancellation, timeout, and interactive/non-interactive support fields.
- `DetectedAgent` gains an optional best-effort `version`.
- `AgentRegistry` exposes `profiles()`, `profile(name)`, and
  `detect_profiles()`; direct `select()` behavior remains unchanged.
- Explicit router requirements are hard constraints. Capabilities inferred
  from role, task wording, and repository hints are scoring signals and do not
  disqualify otherwise-eligible agents.
- Workflow-integrated routing passes no hard capability filter, preserving the
  legacy role/preference/fallback path while adding capability-aware scoring.
- Routing decisions are persisted as `routing.decision` typed events. Selected
  profile metadata is cleared from the event payload.
- `runtime.routing_enabled` (or `runtime.routing.enabled`) preserves the old
  static preference/fallback path when false.

## Work completed

1. Audited registry selection, adapters, workflow selection, events, config,
   and existing tests.
2. Added profiles, resolver, deterministic router, and 20 routing tests.
3. Integrated workflow selection, historical success rates, exclusions, event
   persistence, disabled-routing coverage, README documentation, and package
   exports.
4. Ran focused and full suites green.
5. Completed copilot read-only snapshot review; fixed scalar-string selector
   compatibility with a regression test.
6. Requested opencode read-only architecture review asynchronously.

## Verification

- `tests/test_routing.py`: 20 tests.
- Full suite: 309 passing, 3 environment skips.
- `git diff --check`: clean.
- Copilot review: one valid finding fixed; two snapshot-packaging observations
  correctly rejected as review-harness artifacts.
- OpenCode architecture review: all eight findings adjudicated. Fixed version
  decoding, role-derived selector capabilities, legacy-capability mapping,
  executed-agent/event agreement, and single-profile input. Rejected two stale
  observations and retained full-vocabulary empty-role profiles by design.
