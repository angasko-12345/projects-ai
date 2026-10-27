"""HeadroomGate (ai-token-tracker ``headroom --json`` v1) and the opt-in
``usage_limit`` failure category with a restart-proof cooldown."""

from __future__ import annotations

import asyncio
import json
import subprocess
import unittest
import warnings
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from agentops.config import (
    AgentConfig,
    AppConfig,
    HeadroomConfig,
    UsageLimitConfig,
    load_config,
)
from agentops.events import EventType
from agentops.failure import (
    DEFAULT_USAGE_LIMIT_PATTERNS,
    USAGE_LIMIT_TAIL_CHARS,
    Failure,
    FailureCategory,
    FailureClassifier,
    FailureSeverity,
    FailureSource,
    RepairAction,
    RetryPolicy,
    match_usage_limit,
    new_failure_id,
    parse_retry_after_seconds,
)
from agentops.headroom import (
    DEPRIORITIZE,
    REJECT,
    GateVerdict,
    HeadroomBinding,
    HeadroomGate,
    HeadroomPolicy,
    evaluate,
    merge_verdicts,
    parse_snapshot,
)
from agentops.headroom_source import HeadroomCommandReader, HeadroomFileReader, build_headroom_gate
from agentops.registry import AgentRegistry, DetectedAgent
from agentops.routing import AgentCapabilityResolver, AgentRouter
from agentops.runner import RunResult
from agentops.state import StateStore
from agentops.tasks import Task, TaskStatus
from agentops.workflow import WorkflowEngine


def _epoch(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


NOW_ISO = "2026-10-26T02:00:00Z"
NOW = _epoch(NOW_ISO)


def _window(
    window="5h",
    *,
    used_percent=10.0,
    state="ok",
    freshness="fresh",
    resets_at="2026-10-26T04:00:00Z",
    limit_id="codex",
    minutes=300,
    reached=False,
):
    """One window exactly as token_tracker.headroom.make_window emits it."""
    return {
        "source": "codex-rollout",
        "window": window,
        "limit_id": limit_id,
        "window_minutes": minutes,
        "unit": "percent",
        "used": used_percent,
        "limit": 100.0,
        "remaining": None if used_percent is None else max(100.0 - used_percent, 0.0),
        "used_percent": used_percent,
        "resets_at": resets_at,
        "resets_at_basis": "reported" if resets_at else None,
        "reset_interval": None,
        "as_of": "2026-10-26T01:58:00Z",
        "age_seconds": 120,
        "freshness": freshness,
        "state": state,
        "reached": reached,
        "elapsed_percent": 50.0,
        "pace": 0.2,
        "plan_type": "plus",
    }


def _provider(name, status="ok", windows=(), detail=None):
    return {
        "provider": name,
        "source": f"{name}-src",
        "status": status,
        "detail": detail,
        "location": None,
        "windows": list(windows),
        "warnings": [],
    }


def _document(*providers, generated_at=NOW_ISO, schema="ai-token-tracker/headroom", version=1):
    if not providers:
        providers = (
            _provider("codex", windows=[_window()]),
            _provider("claude", "not_configured", detail="status line tee not installed"),
            _provider("openrouter", "not_configured"),
        )
    return {
        "schema": schema,
        "schema_version": version,
        "generated_at": generated_at,
        "thresholds": {"warn_percent": 80.0, "critical_percent": 95.0},
        "providers": list(providers),
    }


def _snapshot(document, now=NOW, max_age=900.0):
    return parse_snapshot(json.dumps(document), now, max_age)


def _gate(document, bindings=None, policy=None):
    bindings = bindings or {"codex": HeadroomBinding("codex")}
    return HeadroomGate(lambda now: _snapshot(document, now), bindings, policy or HeadroomPolicy())


def _agent_config(name, roles=("implementation",)):
    return AgentConfig(name=name, command=name, args=("{prompt}",), roles=roles)


def _profiles(*names):
    resolver = AgentCapabilityResolver()
    return tuple(
        resolver.profile(DetectedAgent(_agent_config(name), True, f"/bin/{name}", "1.0"))
        for name in names
    )


class TrackerContractTests(unittest.TestCase):
    """The fixture above must match what ai-token-tracker on main emits."""

    def test_fixture_matches_tracker_make_window_keys_when_tracker_is_present(self):
        tracker = Path(__file__).resolve().parents[2] / "ai-token-tracker"
        source = tracker / "token_tracker" / "headroom.py"
        if not source.is_file():
            self.skipTest("ai-token-tracker not in this checkout")
        text = source.read_text(encoding="utf-8")
        # The contract is the JSON document, not the Python package: check the
        # constants and every window key by text, without importing the tracker.
        self.assertIn('SCHEMA = "ai-token-tracker/headroom"', text)
        self.assertIn("SCHEMA_VERSION = 1", text)
        for key in _window():
            self.assertIn(f'"{key}"', text, key)


class SnapshotParsingTests(unittest.TestCase):
    def test_valid_document_is_usable(self):
        snapshot = _snapshot(_document())
        self.assertTrue(snapshot.usable, snapshot.detail)

    def test_missing_file_is_a_no_op(self):
        with TemporaryDirectory() as directory:
            reader = HeadroomFileReader(Path(directory) / "absent.json", 900)
            snapshot = reader(NOW)
        self.assertFalse(snapshot.usable)
        self.assertEqual(snapshot.status, "missing")
        gate = HeadroomGate(reader, {"codex": HeadroomBinding("codex")})
        verdicts, _ = gate.check(NOW)
        self.assertEqual(verdicts, {})

    def test_schema_mismatch_is_a_no_op(self):
        for document in (
            _document(version=2),
            _document(schema="someone-else/headroom"),
            {**_document(), "schema_version": True},
        ):
            with self.subTest(document={k: document[k] for k in ("schema", "schema_version")}):
                snapshot = _snapshot(document)
                self.assertEqual(snapshot.status, "schema_mismatch")
                self.assertEqual(
                    evaluate(snapshot, {"codex": HeadroomBinding("codex")}, HeadroomPolicy()), {}
                )

    def test_stale_snapshot_is_a_no_op(self):
        exhausted = _document(
            _provider("codex", windows=[_window(used_percent=100.0, state="exhausted")])
        )
        snapshot = _snapshot(exhausted, now=NOW + 901, max_age=900)
        self.assertEqual(snapshot.status, "stale")
        self.assertEqual(_gate(exhausted).check(NOW + 901)[0], {})
        # one second inside the bound the same document rejects
        self.assertIn("codex", _gate(exhausted).check(NOW + 899)[0])

    def test_unreadable_and_malformed_documents_are_no_ops(self):
        cases = (
            "{not json",
            "[]",
            json.dumps({**_document(), "providers": None}),
            json.dumps({**_document(), "generated_at": "yesterday"}),
        )
        for text in cases:
            with self.subTest(text=text[:30]):
                snapshot = parse_snapshot(text, NOW, 900)
                self.assertEqual(snapshot.status, "invalid")
                self.assertFalse(snapshot.usable)

    def test_reader_exception_fails_open(self):
        def broken(now):
            raise RuntimeError("disk on fire")

        gate = HeadroomGate(broken, {"codex": HeadroomBinding("codex")})
        verdicts, snapshot = gate.check(NOW)
        self.assertEqual(verdicts, {})
        self.assertEqual(snapshot.status, "unreadable")
        self.assertIn("routing unchanged", HeadroomGate.note(snapshot))


class EvaluationTests(unittest.TestCase):
    def _verdict(self, *windows, policy=None, binding=None, status="ok"):
        document = _document(_provider("codex", status, windows))
        verdicts = evaluate(
            _snapshot(document),
            {"codex": binding or HeadroomBinding("codex")},
            policy or HeadroomPolicy(),
        )
        return verdicts.get("codex")

    def test_exhausted_window_rejects_with_explicit_reason(self):
        verdict = self._verdict(
            _window(),
            _window(
                "weekly",
                used_percent=100.0,
                state="exhausted",
                resets_at="2026-10-27T05:40:00Z",
                minutes=10080,
            ),
        )
        self.assertEqual(verdict.action, REJECT)
        self.assertEqual(
            verdict.reasons,
            ("headroom: codex weekly 100% used (exhausted), resets 2026-10-27 05:40 UTC",),
        )

    def test_reached_flag_rejects_below_100_percent(self):
        verdict = self._verdict(_window(used_percent=91.0, state="exhausted", reached=True))
        self.assertEqual(verdict.action, REJECT)

    def test_threshold_rejects_and_deprioritizes(self):
        self.assertEqual(self._verdict(_window(used_percent=96.0, state="critical")).action, REJECT)
        low = self._verdict(_window(used_percent=85.0, state="warn"))
        self.assertEqual(low.action, DEPRIORITIZE)
        self.assertIn("85% used (warn)", low.reasons[0])
        self.assertIsNone(self._verdict(_window(used_percent=40.0)))
        custom = HeadroomPolicy(reject_at_percent=50.0, deprioritize_at_percent=None)
        self.assertEqual(
            self._verdict(_window(used_percent=60.0, state="ok"), policy=custom).action, REJECT
        )
        self.assertIsNone(self._verdict(_window(used_percent=40.0), policy=custom))

    def test_unknown_provider_status_applies_unknown_policy(self):
        for policy, expected in (
            ("deprioritize", DEPRIORITIZE),
            ("reject", REJECT),
            ("allow", None),
        ):
            with self.subTest(policy=policy):
                verdict = self._verdict(
                    status="not_configured", policy=HeadroomPolicy(unknown_policy=policy)
                )
                self.assertEqual(getattr(verdict, "action", None), expected)
        reason = self._verdict(status="no_data").reasons[0]
        self.assertIn("codex has no reading (status no_data)", reason)
        self.assertIn("deprioritized by unknown_policy", reason)

    def test_stale_window_keeps_a_high_reading_but_not_a_low_one(self):
        # usage only rises inside a window: a stale 100% is still exhausted
        self.assertEqual(
            self._verdict(_window(used_percent=100.0, state="exhausted", freshness="stale")).action,
            REJECT,
        )
        stale_low = self._verdict(_window(used_percent=20.0, freshness="stale"))
        self.assertEqual(stale_low.action, DEPRIORITIZE)
        self.assertIn("reading is stale", stale_low.reasons[0])

    def test_expired_window_is_unknown_never_zero(self):
        verdict = self._verdict(
            _window(
                used_percent=100.0,
                state="unknown",
                freshness="expired",
                resets_at="2026-10-26T01:00:00Z",
            )
        )
        self.assertEqual(verdict.action, DEPRIORITIZE)
        self.assertIn("reading is expired", verdict.reasons[0])

    def test_binding_narrows_windows(self):
        windows = (
            _window(used_percent=10.0),
            _window("weekly", used_percent=100.0, state="exhausted", minutes=10080),
        )
        self.assertIsNone(
            self._verdict(*windows, binding=HeadroomBinding("codex", windows=("5h",)))
        )
        self.assertEqual(self._verdict(*windows, binding=HeadroomBinding("codex")).action, REJECT)

    def test_unbound_agents_get_no_verdict(self):
        document = _document(
            _provider("codex", windows=[_window(used_percent=100.0, state="exhausted")])
        )
        verdicts = evaluate(_snapshot(document), {}, HeadroomPolicy())
        self.assertEqual(verdicts, {})

    def test_merge_keeps_strongest_action(self):
        merged = merge_verdicts(
            {"a": GateVerdict(DEPRIORITIZE, ("x",))}, {"a": GateVerdict(REJECT, ("y",))}
        )
        self.assertEqual(merged["a"].action, REJECT)
        self.assertEqual(merged["a"].reasons, ("x", "y"))


class RouterGateTests(unittest.TestCase):
    def _route(self, **kwargs):
        return AgentRouter().route(
            role="implementation",
            task_description="Implement a feature",
            available_agents=_profiles("codex", "claude"),
            user_preferences=("codex", "claude"),
            **kwargs,
        )

    def test_no_verdicts_leaves_the_decision_unchanged(self):
        self.assertEqual(
            self._route().to_dict(), self._route(gate_verdicts={}, gate_notes=()).to_dict()
        )

    def test_rejection_reasons_are_recorded(self):
        reason = "headroom: codex weekly 100% used (exhausted), resets 2026-10-27 05:40 UTC"
        decision = self._route(
            gate_verdicts={"codex": GateVerdict(REJECT, (reason,))},
            gate_notes=("headroom: headroom document generated 2026-10-26 02:00 UTC",),
        )
        self.assertEqual(decision.selected_identifier, "claude")
        rejected = {item.identifier: item.reasons for item in decision.rejected_candidates}
        self.assertEqual(rejected["codex"], (reason,))
        self.assertIn(
            "headroom: headroom document generated 2026-10-26 02:00 UTC", decision.constraints
        )

    def test_deprioritized_preferred_agent_ranks_below_a_clean_one(self):
        decision = self._route(
            gate_verdicts={"codex": GateVerdict(DEPRIORITIZE, ("headroom: low",))}
        )
        self.assertEqual(decision.selected_identifier, "claude")
        alternative = decision.alternatives[0]
        self.assertEqual(alternative.identifier, "codex")
        self.assertIn("headroom: low", alternative.reasons)

    def test_deprioritized_agent_still_runs_when_it_is_the_only_one(self):
        decision = AgentRouter().route(
            role="implementation",
            task_description="x",
            available_agents=_profiles("codex"),
            gate_verdicts={"codex": GateVerdict(DEPRIORITIZE, ("headroom: stale",))},
        )
        self.assertEqual(decision.selected_identifier, "codex")

    def test_rejected_agent_is_not_used_by_capability_fallback(self):
        decision = AgentRouter().route(
            role="implementation",
            task_description="x",
            available_agents=_profiles("codex"),
            required_capabilities=("security review",),
            allow_capability_fallback=True,
            gate_verdicts={"codex": GateVerdict(REJECT, ("headroom: exhausted",))},
        )
        self.assertIsNone(decision.selected_agent)


class CommandReaderTests(unittest.TestCase):
    def test_command_output_is_parsed_and_cached(self):
        calls = []

        def runner(argv, timeout):
            calls.append((tuple(argv), timeout))
            return subprocess.CompletedProcess(argv, 0, json.dumps(_document()), "")

        reader = HeadroomCommandReader(
            ("python", "-m", "token_tracker", "headroom", "--json"),
            900,
            timeout_seconds=7,
            cache_seconds=60,
            runner=runner,
        )
        self.assertTrue(reader(NOW).usable)
        self.assertTrue(reader(NOW + 30).usable)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], 7)
        reader(NOW + 61)
        self.assertEqual(len(calls), 2)

    def test_command_failures_fail_open(self):
        def nonzero(argv, timeout):
            return subprocess.CompletedProcess(argv, 2, "", "usage: ...")

        def slow(argv, timeout):
            raise subprocess.TimeoutExpired(argv, timeout)

        def absent(argv, timeout):
            raise FileNotFoundError(argv[0])

        for runner, needle in (
            (nonzero, "exited 2"),
            (slow, "timed out"),
            (absent, "could not start"),
        ):
            with self.subTest(needle=needle):
                snapshot = HeadroomCommandReader(("tracker",), 900, runner=runner)(NOW)
                self.assertEqual(snapshot.status, "unreadable")
                self.assertIn(needle, snapshot.detail)


class ConfigTests(unittest.TestCase):
    BASE = {
        "agents": {
            "codex": {"command": "codex", "args": ["{prompt}"]},
            "claude": {"command": "claude", "args": ["{prompt}"]},
        }
    }

    def _load(self, extra):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "agents.yaml"
            path.write_text(json.dumps({**self.BASE, **extra}), encoding="utf-8")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return load_config(path), Path(directory)

    def test_bundled_and_minimal_configs_have_no_gate(self):
        bundled = load_config()
        self.assertIsNone(bundled.headroom)
        self.assertIsNone(bundled.usage_limits)
        config, _ = self._load({})
        self.assertIsNone(config.headroom)
        self.assertIsNone(build_headroom_gate(config.headroom))

    def test_headroom_section_parses(self):
        config, directory = self._load(
            {
                "headroom": {
                    "file": "headroom.json",
                    "max_age_seconds": 600,
                    "unknown_policy": "reject",
                    "agents": {
                        "codex": "codex",
                        "claude": {"provider": "claude", "windows": ["5h"]},
                    },
                }
            }
        )
        headroom = config.headroom
        self.assertEqual(headroom.file, directory / "headroom.json")
        self.assertEqual(headroom.agents["codex"], ("codex", (), ()))
        self.assertEqual(headroom.agents["claude"], ("claude", ("5h",), ()))
        self.assertEqual(headroom.unknown_policy, "reject")
        self.assertIsNotNone(build_headroom_gate(headroom))

    def test_headroom_section_rejects_bad_input(self):
        bad = (
            {"agents": {"codex": "codex"}},
            {"file": "a.json", "command": ["x"], "agents": {"codex": "codex"}},
            {"file": "a.json", "agents": {"ghost": "codex"}},
            {"file": "a.json", "agents": {"codex": "codex"}, "unknown_policy": "maybe"},
            {"file": "a.json", "agents": {"codex": "codex"}, "deprioritize_at_percent": 99},
        )
        for section in bad:
            with self.subTest(section=section), self.assertRaises(ValueError):
                self._load({"headroom": section})

    def test_usage_limits_section_parses(self):
        config, _ = self._load(
            {
                "usage_limits": {
                    "default_cooldown_seconds": 600,
                    "agents": {"claude": ["Claude AI usage limit reached"]},
                }
            }
        )
        limits = config.usage_limits
        self.assertEqual(limits.default_cooldown_seconds, 600)
        self.assertIn("claude ai usage limit reached", limits.patterns_for("claude"))
        self.assertNotIn("claude ai usage limit reached", limits.patterns_for("codex"))
        self.assertIn("usage limit", limits.patterns_for("codex"))
        off, _ = self._load({"usage_limits": {"enabled": False}})
        self.assertIsNone(off.usage_limits)
        with self.assertRaises(ValueError):
            self._load({"usage_limits": {"agents": {"ghost": ["x"]}}})


class UsageLimitClassificationTests(unittest.TestCase):
    MESSAGE = "ERROR: You've hit your usage limit. Try again in 20 minutes."

    def test_off_by_default(self):
        result = FailureClassifier.classify(
            source=FailureSource.AGENT, exit_code=1, error=self.MESSAGE
        )
        self.assertEqual(result.category, FailureCategory.AGENT_ERROR)

    def test_pattern_match_is_usage_limit(self):
        result = FailureClassifier.classify(
            source=FailureSource.AGENT,
            exit_code=1,
            error=self.MESSAGE,
            usage_limit_patterns=DEFAULT_USAGE_LIMIT_PATTERNS,
        )
        self.assertEqual(result.category, FailureCategory.USAGE_LIMIT)
        self.assertEqual(result.recommended_action, RepairAction.RETRY_DIFFERENT_AGENT)
        self.assertTrue(result.retryable)

    def test_recorded_cli_messages_match_defaults(self):
        messages = (
            "You've hit your usage limit. Upgrade to Pro or try again in 3 hours 12 minutes.",
            "Claude AI usage limit reached|1761445200",
            "Error: 429 Too Many Requests",
            "RESOURCE_EXHAUSTED: Quota exceeded for quota metric 'Generate Content API requests'",
            "Rate limit reached for gpt-5 in organization org-x on tokens per min",
            "insufficient_quota: You exceeded your current quota",
        )
        for message in messages:
            with self.subTest(message=message):
                self.assertIsNotNone(match_usage_limit(message, DEFAULT_USAGE_LIMIT_PATTERNS))

    def test_only_the_tail_is_matched(self):
        early = (
            "implemented the rate limit middleware\n"
            + "x" * USAGE_LIMIT_TAIL_CHARS
            + "\nAssertionError"
        )
        self.assertIsNone(match_usage_limit(early, DEFAULT_USAGE_LIMIT_PATTERNS))

    def test_timeout_and_cancellation_stay_authoritative(self):
        for flags, category in (
            ({"timed_out": True}, FailureCategory.TIMEOUT),
            ({"cancelled": True}, FailureCategory.CANCELLATION),
        ):
            with self.subTest(flags=flags):
                result = FailureClassifier.classify(
                    error=self.MESSAGE, usage_limit_patterns=("usage limit",), **flags
                )
                self.assertEqual(result.category, category)

    def test_retry_after_parsing(self):
        self.assertEqual(parse_retry_after_seconds(self.MESSAGE), 1200)
        self.assertEqual(parse_retry_after_seconds("Retry-After: 30"), 30)
        self.assertEqual(parse_retry_after_seconds("limit resets in 2h"), 7200)
        self.assertIsNone(parse_retry_after_seconds("resets at 5pm"))

    def test_plan_repair_switches_agents_or_stops(self):
        failure = Failure(
            new_failure_id(),
            "wf",
            "t",
            None,
            FailureSource.AGENT,
            FailureCategory.USAGE_LIMIT,
            FailureSeverity.MEDIUM,
            True,
            False,
        )
        policy = RetryPolicy(max_attempts=2)
        plan = FailureClassifier.plan_repair(
            failure, attempt=1, repair_cycle=0, policy=policy, alternate_agent_available=True
        )
        self.assertEqual(plan.action, RepairAction.RETRY_DIFFERENT_AGENT)
        alone = FailureClassifier.plan_repair(failure, attempt=1, repair_cycle=0, policy=policy)
        self.assertEqual(alone.action, RepairAction.STOP)


class _EngineHarness:
    """Real registry and router, a fake runner, and an in-file state store."""

    LIMIT = ("Error: You've hit your usage limit. Try again in 30 minutes.", 1)

    def __init__(
        self,
        database,
        *,
        usage_limits=None,
        headroom=None,
        outputs=None,
        clock=None,
        headroom_gate=None,
    ):
        agents = {"codex": _agent_config("codex"), "claude": _agent_config("claude")}
        self.config = AppConfig(
            agents,
            {"implementation": ("codex", "claude")},
            (("test",),),
            max_attempts=2,
            concurrency=1,
            backoff_base_seconds=0.0,
            headroom=headroom,
            usage_limits=usage_limits,
        )
        self.state = StateStore(database)
        self.registry = AgentRegistry(self.config)
        self.outputs = outputs or {}
        self.calls: list[str] = []
        runner = MagicMock()

        async def run_agent(agent, prompt, directory, task_id, cancel_event=None):
            name = agent.config.name
            self.calls.append(name)
            stderr, code = self.outputs.get(name, ("", 0))
            return RunResult(
                name,
                (name,),
                code,
                "done" if code == 0 else "",
                stderr,
                0.01,
                False,
                Path(f"{task_id}.log"),
            )

        runner.run_agent = run_agent
        self.engine = WorkflowEngine(
            self.config,
            self.state,
            self.registry,
            runner,
            MagicMock(),
            clock=clock or (lambda: NOW),
            headroom_gate=headroom_gate,
        )

    def run_task(self, directory):
        workflow_id = self.state.create_workflow("limit failover")
        task = self.state.add_task(
            Task("Implement it", "implementation", workflow_id, max_attempts=2)
        )
        for _ in range(2):
            asyncio.run(self.engine._execute_task(task, directory))
            task = self.state.get_task(task.id)
            if task.status is not TaskStatus.PENDING:
                break
        return task


@patch("agentops.registry.subprocess.run", return_value=MagicMock(stdout="1.0\n", stderr=""))
@patch("agentops.registry.shutil.which", side_effect=lambda command: f"/bin/{command}")
class WorkflowGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.database = str(self.root / "state.sqlite")

    def _headroom_config(self, path):
        return HeadroomConfig(
            agents={"codex": ("codex", (), ()), "claude": ("claude", (), ())}, file=path
        )

    def test_exhausted_agent_is_skipped_with_logged_reason(self, which, run):
        path = self.root / "headroom.json"
        path.write_text(
            json.dumps(
                _document(
                    _provider(
                        "codex",
                        windows=[
                            _window(
                                "weekly",
                                used_percent=100.0,
                                state="exhausted",
                                resets_at="2026-10-27T05:40:00Z",
                                minutes=10080,
                            )
                        ],
                    ),
                    _provider("claude", windows=[_window(used_percent=20.0, limit_id="five_hour")]),
                    _provider("openrouter", "not_configured"),
                )
            ),
            encoding="utf-8",
        )
        harness = _EngineHarness(self.database, headroom=self._headroom_config(path))
        self.addCleanup(harness.state.close)
        task = harness.run_task(self.work)
        self.assertEqual(task.status, TaskStatus.PASSED)
        self.assertEqual(harness.calls, ["claude"])
        payload = harness.state.query_events(event_type=EventType.ROUTING_DECISION)[0].payload
        rejected = {item["identifier"]: item["reasons"] for item in payload["rejected_candidates"]}
        self.assertEqual(
            rejected["codex"],
            ["headroom: codex weekly 100% used (exhausted), resets 2026-10-27 05:40 UTC"],
        )
        self.assertTrue(
            any(
                c.startswith("headroom: headroom document generated")
                for c in payload["constraints"]
            )
        )

    def test_missing_file_changes_nothing_and_is_logged_once(self, which, run):
        plain = _EngineHarness(str(self.root / "plain.sqlite"))
        gated = _EngineHarness(
            self.database, headroom=self._headroom_config(self.root / "absent.json")
        )
        self.addCleanup(plain.state.close)
        self.addCleanup(gated.state.close)
        for harness in (plain, gated):
            self.assertEqual(harness.run_task(self.work).status, TaskStatus.PASSED)
            harness.run_task(self.work)
        self.assertEqual(plain.calls, gated.calls)
        self.assertEqual(gated.calls, ["codex", "codex"])
        plain_payload = plain.state.query_events(event_type=EventType.ROUTING_DECISION)[0].payload
        gated_payload = gated.state.query_events(event_type=EventType.ROUTING_DECISION)[0].payload
        self.assertEqual(plain_payload["selected_agent"], gated_payload["selected_agent"])
        self.assertEqual(plain_payload["rejected_candidates"], gated_payload["rejected_candidates"])
        self.assertTrue(any("unavailable (missing" in c for c in gated_payload["constraints"]))
        notes = [
            event
            for workflow in gated.state.list_workflows()
            for event in gated.state.list_events(workflow.id)
            if event["kind"] == "headroom-unavailable"
        ]
        self.assertEqual(len(notes), 1)

    def test_every_candidate_rejected_fails_with_reasons(self, which, run):
        rejecting = HeadroomGate(
            lambda now: _snapshot(
                _document(
                    _provider("codex", windows=[_window(used_percent=100.0, state="exhausted")]),
                    _provider("claude", "no_data"),
                    _provider("openrouter", "not_configured"),
                ),
                now,
            ),
            {"codex": HeadroomBinding("codex"), "claude": HeadroomBinding("claude")},
            HeadroomPolicy(unknown_policy="reject"),
        )
        harness = _EngineHarness(self.database, headroom_gate=rejecting)
        self.addCleanup(harness.state.close)
        task = harness.run_task(self.work)
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(harness.calls, [])
        self.assertIn("routing-gate rejections", task.result)
        self.assertIn("codex: headroom: codex 5h 100% used (exhausted)", task.result)
        failure = harness.state.list_failures(task.workflow_id, task.id)[-1]
        self.assertEqual(failure.category, FailureCategory.ENVIRONMENT_FAILURE)

    def test_fake_limit_message_fails_over_and_cooldown_survives_restart(self, which, run):
        limits = UsageLimitConfig()
        first = _EngineHarness(
            self.database, usage_limits=limits, outputs={"codex": _EngineHarness.LIMIT}
        )
        task = first.run_task(self.work)
        self.assertEqual(task.status, TaskStatus.PASSED)
        self.assertEqual(task.assigned_agent, "claude")
        self.assertEqual(first.calls, ["codex", "claude"])
        failures = first.state.list_failures(task.workflow_id, task.id)
        self.assertEqual([f.category for f in failures], [FailureCategory.USAGE_LIMIT])
        cooldowns = first.state.active_agent_cooldowns(NOW)
        self.assertEqual(set(cooldowns), {"codex"})
        self.assertAlmostEqual(cooldowns["codex"]["until_epoch"], NOW + 1800, delta=1)
        first.state.close()

        # A fresh process: new store on the same file, new engine, first attempt.
        second = _EngineHarness(self.database, usage_limits=limits, clock=lambda: NOW + 600)
        self.addCleanup(second.state.close)
        task = second.run_task(self.work)
        self.assertEqual(second.calls, ["claude"])
        payload = second.state.query_events(event_type=EventType.ROUTING_DECISION)[-1].payload
        rejected = {item["identifier"]: item["reasons"] for item in payload["rejected_candidates"]}
        self.assertTrue(
            rejected["codex"][0].startswith("usage limit cooldown until 2026-10-26 02:30 UTC")
        )

        # After the cooldown the preferred agent is eligible again.
        later = _EngineHarness(self.database, usage_limits=limits, clock=lambda: NOW + 1801)
        self.addCleanup(later.state.close)
        later.run_task(self.work)
        self.assertEqual(later.calls, ["codex"])

    def test_without_usage_limits_config_the_limit_is_an_agent_error(self, which, run):
        harness = _EngineHarness(self.database, outputs={"codex": _EngineHarness.LIMIT})
        self.addCleanup(harness.state.close)
        task = harness.run_task(self.work)
        failures = harness.state.list_failures(task.workflow_id, task.id)
        self.assertEqual(failures[0].category, FailureCategory.AGENT_ERROR)
        self.assertEqual(harness.state.active_agent_cooldowns(NOW), {})


if __name__ == "__main__":
    unittest.main()
