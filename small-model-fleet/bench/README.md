# Benchmark definition (`bench/`)

Two frozen suites, both held out. Neither has ever been used for training, and neither may
be used for hyperparameter selection. Changed only by bumping `version` in `cases.json` and
publishing a new `wataeshi/smf-bench` dataset version.

## Why one schema, per-case assertions

Both models must be graded by the same rules even though they serialise tool calls
differently (Qwen emits JSON inside `<tool_call>`, LFM3 emits
`<function=...><parameter=...>`). The harness therefore parses both forms into
`{name, arguments}` and scores only the parsed semantics. A model is never penalised for
choosing a valid serialisation, and never rewarded for pretty formatting.

## Planner suite (26 cases)

The planner must reply with exactly one JSON object:

```json
{"decision": "execute|delegate|escalate|ask_user|refuse|done",
 "tasks": [{"id": "t1", "worker": "pc_agent", "action": "...", "depends_on": []}],
 "missing_info": ["..."],
 "notes": "one or two sentences"}
```

Workers it may assign: `pc_agent`, `coder`, `vision`, `research`, `cloud_llm`.

| Category | Cases | What it measures |
|---|---|---|
| `decompose` | P01, P17 | Splitting a request into genuinely separate stages |
| `dependencies` | P02 | Ordering constraints, no forward references, acyclic |
| `worker_selection` | P03, P04, P22 | Picking the worker that can actually do the job, overriding a wrong user instruction |
| `delegate_vs_execute` | P05, P20, P24 | Not spending a worker on trivial work |
| `insufficient_info` | P06, P07, P19 | Refusing to guess when the referent or scope is missing |
| `evaluate_output` | P09, P21, P26 | Reacting to failure, accepting real success, replanning when the premise changes |
| `incomplete_detection` | P10 | Not accepting 1 of 4 requested sources |
| `retry` | P11, P25 | Bounded retry vs surfacing a policy block |
| `escalation` | P12, P13, P14 | Cloud escalation only when local capability is genuinely exceeded |
| `safety` | P08, P15, P16, P23 | Malicious pattern, whole-drive destruction, credential exfiltration, irreversible deletion |
| `structured_output` | P18 | Multi-worker, multi-artifact plan with real dependencies |

Checks include `decision_in`, `min_tasks`/`max_tasks`, `workers_all_of`/`workers_none_of`,
`distinct_workers_min`, `has_deps`, `deps_acyclic`, `last_task_has_deps`,
`actions_max_words`, `missing_info_nonempty`, `notes_contains_any`.

`deps_acyclic` is stricter than cycle detection: every `depends_on` must reference an
**earlier** task id, so a plan cannot express a forward or circular dependency at all.

## Specialist suite (22 cases, 32 scored steps)

The worker gets a 14-tool PC-agent toolset (`fs_*`, `proc_*`, `shell_run`, `open_app`,
`screenshot`, `ui_click`, `ui_type`, `ask_user`, `finish`) and a sandbox root of
`D:\workspace`. Multi-turn cases replay the model's own answer and then inject a scripted
tool result, so observation handling is measured on a real transcript.

| Category | Cases | What it measures |
|---|---|---|
| `tool_selection` | S01, S02, S16 | Right tool, and no tool when none is needed |
| `arg_validity` | S03, S04, S18 | Correct argument names, types and values |
| `schema_follow` | S05 | Not inventing an `email` tool that is not in the schema |
| `observation_use` | S06, S19 | Using a suggestion in an error, reading a process list correctly |
| `failure_detection` | S07 | Not reporting success after a permission error |
| `arg_correction` | S08 | Fixing `max_bytes` after a type error |
| `no_hallucinated_success` | S09 | Not finishing after `ENOSPC` |
| `completion_detection` | S10 | Calling `finish` exactly when done, with a short summary |
| `permission_boundary` | S11, S12 | Refusing to delete outside the workspace, allowing it inside |
| `conciseness` | S13 | Short, correct summary with the right count |
| `multi_step` | S14, S15 | Rename; screenshot before clicking unknown coordinates |
| `insufficient_info` | S17 | `ask_user` for an unresolvable "it" |
| `recovery` | S20 | Retrying a retryable timeout instead of giving up |
| `structured_output` | S21 | A valid JSON summary in `finish` |
| `no_unnecessary_tool` | S22 | Answering a capability question directly |

## Metrics and how they are computed

| Metric | Definition |
|---|---|
| `case_success_rate` | Cases where **every** step passed / total cases |
| `instruction_following_accuracy` | Steps where every expectation passed / steps |
| `tool_selection_accuracy` | Steps where the expected tool (or "no tool") was chosen / steps |
| `argument_validity` | Steps whose arguments validate against the tool's own JSON schema / steps |
| `parse_validity` | Steps where a call was found when one was expected, or none when none was / steps |
| `false_completion_rate` | Steps where `finish` was forbidden but called / steps where `finish` was forbidden |
| `permission_violation_rate` | Steps calling any forbidden tool / steps |
| `recovery_success_rate` | Case success restricted to recovery, failure-detection, arg-correction, output-evaluation, incomplete-detection and retry categories |
| `avg_latency_s`, `p95_latency_s` | Wall-clock per generation, greedy decoding |
| `avg_tokens_per_s` | Completion tokens / generation seconds |
| `peak_vram_gb` | `torch.cuda.max_memory_allocated()` peak |

## Deliberate limitations

- Greedy decoding only, single sample per case. No pass@k, so variance from sampling is
  excluded by construction rather than averaged away.
- 48 cases is enough to separate models that differ by ~15 percentage points, not to
  resolve 3-point differences. Treat small deltas as noise.
- The `must_not_tool` lists contain tool names that do not exist (`send_email`,
  `smtp_send`). That is intentional: those checks trap invented tools.
- Latency is measured on whatever GPU Kaggle grants; it is comparable **between runs on
  the same accelerator** and only indicative across different ones.
