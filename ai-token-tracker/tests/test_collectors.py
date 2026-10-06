"""Focused shot-3 tests: collector parsing, cross-source dedup, attribution,
sync status, aggregation, and legacy import compatibility.

All sources are tiny fixtures built in a temp directory; no test touches a
real installation.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from token_tracker import collectors, db
from token_tracker.model import EVENT_COLUMNS, normalize_event


# ----------------------------------------------------------- fixtures


def build_kilo_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE session (id TEXT, directory TEXT, agent TEXT)")
    con.execute(
        "CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, data TEXT)"
    )
    con.execute(
        "INSERT INTO session VALUES ('s1', 'D:/work/repo', 'plan')"
    )
    assistant = json.dumps(
        {
            "role": "assistant",
            "providerID": "kilo",
            "modelID": "stepfun/step-3.7-flash:free",
            "agent": "build",
            "cost": 0,
            "tokens": {
                "total": 167,
                "input": 100,
                "output": 20,
                "reasoning": 5,
                "cache": {"read": 40, "write": 2},
            },
        }
    )
    zero = json.dumps(
        {
            "role": "assistant",
            "providerID": "kilo",
            "modelID": "stepfun/step-3.7-flash:free",
            "tokens": {"total": 0, "input": 0, "output": 0, "reasoning": 0,
                       "cache": {"read": 0, "write": 0}},
        }
    )
    con.execute("INSERT INTO message VALUES ('m1', 's1', 1790775641047, ?)", (assistant,))
    con.execute("INSERT INTO message VALUES ('m2', 's1', 1790775642047, ?)", (zero,))
    con.execute(
        "INSERT INTO message VALUES ('m3', 's1', 1790775643047, ?)",
        (json.dumps({"role": "user", "content": "hi"}),),
    )
    con.execute(
        "INSERT INTO message VALUES ('m4', 's1', 1790775644047, ?)",
        ("{not json",),
    )
    con.commit()
    con.close()


def build_pi_dir(root: Path) -> None:
    slug = root / "slug-one"
    slug.mkdir(parents=True)
    lines = [
        json.dumps(
            {
                "type": "session",
                "version": 3,
                "id": "sess-1",
                "timestamp": "2026-09-12T10:31:50.419Z",
                "cwd": "D:/work/pi-project",
            }
        ),
        json.dumps(
            {"type": "message", "id": "msg-user", "message": {"role": "user"}}
        ),
        json.dumps(
            {
                "type": "message",
                "id": "msg-1",
                "timestamp": "2026-09-12T10:32:23.080Z",
                "message": {
                    "role": "assistant",
                    "api": "openai-completions",
                    "provider": "groq",
                    "model": "llama-3.1-8b-instant",
                    "usage": {
                        "input": 300,
                        "output": 50,
                        "cacheRead": 10,
                        "cacheWrite": 1,
                        "reasoning": 10,
                        "totalTokens": 371,
                        "cost": {"total": 0.5},
                    },
                },
            }
        ),
        json.dumps(
            {
                "type": "message",
                "id": "msg-no-usage",
                "message": {"role": "assistant", "provider": "groq"},
            }
        ),
        "{broken json line",
        json.dumps(
            {
                "type": "message",
                "id": "msg-zero",
                "timestamp": "2026-09-12T10:33:00.000Z",
                "message": {
                    "role": "assistant",
                    "provider": "groq",
                    "model": "llama-3.1-8b-instant",
                    "usage": {"input": 0, "output": 0, "cacheRead": 0,
                              "cacheWrite": 0, "totalTokens": 0},
                },
            }
        ),
    ]
    (slug / "session.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_codex_dir(root: Path) -> None:
    day = root / "2026" / "10" / "06"
    day.mkdir(parents=True)
    lines = [
        json.dumps(
            {
                "timestamp": "2026-10-06T13:52:26.997Z",
                "ordinal": 0,
                "type": "session_meta",
                "payload": {
                    "session_id": "sess-codex",
                    "cwd": "D:/work/codex-project",
                    "model_provider": "openai",
                },
            }
        ),
        json.dumps(
            {
                "timestamp": "2026-10-06T13:52:27.888Z",
                "ordinal": 1,
                "type": "turn_context",
                "payload": {"model": "gpt-5.6-terra"},
            }
        ),
        "{malformed",
        json.dumps(
            {
                "timestamp": "2026-10-06T13:52:33.117Z",
                "ordinal": 2,
                "type": "token_usage_record",
                "payload": {
                    "session_id": "sess-codex",
                    "turn_id": "turn-1",
                    "response_id": "resp_abc",
                    "usage": {
                        "input_tokens": 1000,
                        "cached_input_tokens": 600,
                        "cache_write_input_tokens": 100,
                        "output_tokens": 80,
                        "reasoning_output_tokens": 30,
                        "total_tokens": 1080,
                    },
                },
            }
        ),
        # usage record without a usage payload: skipped, not crashed on
        json.dumps(
            {
                "timestamp": "2026-10-06T13:52:34.000Z",
                "ordinal": 3,
                "type": "token_usage_record",
                "payload": {"session_id": "sess-codex"},
            }
        ),
    ]
    (day / "rollout-fixture.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_hermes_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE sessions (id TEXT, cwd TEXT)")
    con.execute(
        "CREATE TABLE session_model_usage ("
        " session_id TEXT, model TEXT, billing_provider TEXT, billing_base_url TEXT,"
        " task TEXT, api_call_count INTEGER, input_tokens INTEGER, output_tokens INTEGER,"
        " cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER,"
        " estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, cost_source TEXT,"
        " first_seen REAL, last_seen REAL)"
    )
    con.execute("INSERT INTO sessions VALUES ('h1', 'D:/work/hermes-project')")
    con.execute("INSERT INTO sessions VALUES ('h2', NULL)")
    con.execute(
        "INSERT INTO session_model_usage VALUES"
        " ('h1', 'model-y', 'nous', 'https://inference-api.nousresearch.com/v1',"
        "  '', 3, 100, 20, 40, 2, 5, 0.0, 1.25, 'reported', 'invoice',"
        "  1791111702.5, 1791111800.5)"
    )
    con.execute(
        "INSERT INTO session_model_usage VALUES"
        " ('h2', 'model-z', '', 'https://example.test/v1',"
        "  'summarize', 1, 10, 5, 0, 0, 0, 0.75, 0.0, 'estimated', 'pricing_table',"
        "  1791112702.5, 1791112800.5)"
    )
    con.commit()
    con.close()


def build_copilot_dir(root: Path) -> None:
    session = root / "uuid-fixture"
    session.mkdir(parents=True)
    lines = [
        json.dumps({"type": "event.other"}),
        "{bad line",
        json.dumps(
            {
                "type": "session.shutdown",
                "timestamp": "2026-10-03T05:46:19.487Z",
                "id": "shutdown-1",
                "data": {
                    "modelMetrics": {
                        "gpt-5.6-luna": {
                            "requests": {"count": 3, "cost": 1.0},
                            "usage": {
                                "inputTokens": 511997,
                                "outputTokens": 900,
                                "cacheReadTokens": 436512,
                                "cacheWriteTokens": 75452,
                                "reasoningTokens": 200,
                            },
                            "tokenDetails": {
                                "input": {"tokenCount": 511997},
                            },
                        }
                    }
                },
            }
        ),
    ]
    (session / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------ parsing


class CollectorParsingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_kilo_parses_usage_rows_and_skips_the_rest(self):
        db_path = self.root / "kilo.db"
        build_kilo_db(db_path)
        events = collectors.collect_kilo(db_path)
        self.assertEqual(len(events), 2)  # usage rows only; user/bad json skipped
        full, zero = events
        self.assertEqual(full.tool, "Kilo")
        self.assertEqual(full.agent, "build")
        self.assertEqual(full.project, "D:/work/repo")
        self.assertEqual(full.provider, "kilo")
        self.assertEqual(full.model, "stepfun/step-3.7-flash:free")
        self.assertEqual(full.total_tokens, 167)
        self.assertEqual(
            (full.input_tokens, full.output_tokens, full.cache_read_tokens,
             full.cache_write_tokens, full.reasoning_tokens),
            (100, 20, 40, 2, 5),
        )
        self.assertTrue(full.exact)
        self.assertTrue(zero.total_tokens == 0)

    def test_pi_parses_session_project_usage_and_malformed_lines(self):
        pi_dir = self.root / "pi"
        build_pi_dir(pi_dir)
        events = collectors.collect_pi(pi_dir)
        self.assertEqual(len(events), 2)  # no-usage message and bad line skipped
        full, zero = events
        self.assertEqual(full.tool, "Pi")
        self.assertEqual(full.source, "pi")
        self.assertEqual(full.project, "D:/work/pi-project")
        self.assertEqual(full.provider, "groq")
        self.assertEqual(full.model, "llama-3.1-8b-instant")
        self.assertEqual(full.total_tokens, 371)
        self.assertEqual(full.cost, 0.5)
        self.assertTrue(full.exact)
        self.assertEqual(zero.total_tokens, 0)

    def test_codex_splits_inclusive_usage_and_tracks_model(self):
        codex_dir = self.root / "codex"
        build_codex_dir(codex_dir)
        events = collectors.collect_codex(codex_dir)
        self.assertEqual(len(events), 1)  # record without usage skipped
        event = events[0]
        self.assertEqual(event.tool, "Codex")
        self.assertEqual(event.provider, "openai")
        self.assertEqual(event.model, "gpt-5.6-terra")
        self.assertEqual(event.project, "D:/work/codex-project")
        self.assertEqual(event.request_id, "resp_abc")
        # input_tokens includes cache, output includes reasoning in the source;
        # the stored split must sum back to the source-reported total.
        self.assertEqual(
            (event.input_tokens, event.cache_read_tokens, event.cache_write_tokens,
             event.output_tokens, event.reasoning_tokens),
            (300, 600, 100, 50, 30),
        )
        self.assertEqual(
            event.input_tokens + event.cache_read_tokens + event.cache_write_tokens
            + event.output_tokens + event.reasoning_tokens,
            1080,
        )
        self.assertTrue(event.exact)

    def test_hermes_rollups_cost_provenance_and_unknown_provider(self):
        hermes_db = self.root / "state.db"
        build_hermes_db(hermes_db)
        events = collectors.collect_hermes(hermes_db)
        self.assertEqual(len(events), 2)
        by_model = {e.model: e for e in events}
        real = by_model["model-y"]
        self.assertEqual(real.tool, "Hermes")
        self.assertEqual(real.provider, "nous")
        self.assertEqual(real.project, "D:/work/hermes-project")
        self.assertEqual(real.cost, 1.25)  # actual cost preferred
        self.assertEqual(real.raw_metadata["requests"], 3)
        self.assertEqual(real.raw_metadata["cost_source"], "invoice")
        est = by_model["model-z"]
        self.assertEqual(est.provider, "unknown")  # empty billing provider
        self.assertIsNone(est.project)  # session cwd was NULL
        self.assertEqual(est.cost, 0.75)  # estimated cost, flagged in metadata
        self.assertEqual(est.raw_metadata["cost_source"], "pricing_table")
        self.assertTrue(all(e.exact for e in events))  # tokens API-counted

    def test_copilot_splits_inclusive_totals_per_model(self):
        copilot_dir = self.root / "session-state"
        build_copilot_dir(copilot_dir)
        events = collectors.collect_copilot(copilot_dir)
        self.assertEqual(len(events), 1)  # malformed + non-shutdown lines skipped
        event = events[0]
        self.assertEqual(event.tool, "GitHub Copilot")
        self.assertEqual(event.provider, "github-copilot")
        self.assertEqual(event.model, "gpt-5.6-luna")
        # tokenDetails.input.tokenCount includes cache; outputTokens includes
        # reasoning. Stored values must split cleanly and sum to the totals.
        self.assertEqual(
            (event.input_tokens, event.cache_read_tokens, event.cache_write_tokens,
             event.output_tokens, event.reasoning_tokens),
            (33, 436512, 75452, 700, 200),
        )
        self.assertEqual(
            event.input_tokens + event.cache_read_tokens + event.cache_write_tokens
            + event.output_tokens + event.reasoning_tokens,
            511997 + 900,
        )
        self.assertEqual(event.raw_metadata["requests"], 3)
        self.assertIsNone(event.cost)  # request-cost units are not documented
        self.assertTrue(event.exact)


# ------------------------------------------------------------- dedup


class CrossSourceDedupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = db.connect(Path(self.tmp.name) / "test.db")
        self.addCleanup(self.conn.close)

    @staticmethod
    def _event(*, source, tool, timestamp, request_id, agent=None):
        return normalize_event(
            timestamp=timestamp,
            provider="openrouter",
            agent=agent,
            tool=tool,
            model="z-ai/glm-5",
            input_tokens=1000,
            output_tokens=200,
            cache_read_tokens=50,
            reasoning_tokens=25,
            exact=True,
            source=source,
            request_id=request_id,
        )

    def test_same_request_seen_by_two_sources_counts_once(self):
        first = self._event(
            source="pi", tool="Pi", timestamp="2026-10-06T10:00:00.100Z",
            request_id="pi-1",
        )
        second = self._event(
            source="opencode", tool="OpenCode", timestamp="2026-10-06T10:00:00.900Z",
            request_id="oc-1",
        )
        self.assertNotEqual(first.id, second.id)  # ids are source-scoped
        self.assertEqual(first.dedup_key, second.dedup_key)  # content identity
        self.assertEqual(db.insert_events(self.conn, [first]), (1, 0, 0))
        self.assertEqual(db.insert_events(self.conn, [second]), (0, 0, 1))
        totals = db.totals(self.conn)
        self.assertEqual(totals["events"], 1)
        self.assertEqual(totals["total_tokens"], 1275)
        # first-seen attribution wins
        self.assertEqual(db.breakdown(self.conn, "tool"), [("Pi", 1275, 1)])

    def test_content_dedup_is_order_independent(self):
        second = self._event(
            source="opencode", tool="OpenCode", timestamp="2026-10-06T10:00:00.900Z",
            request_id="oc-1",
        )
        first = self._event(
            source="pi", tool="Pi", timestamp="2026-10-06T10:00:00.100Z",
            request_id="pi-1",
        )
        db.insert_events(self.conn, [second, first])
        self.assertEqual(db.totals(self.conn)["events"], 1)
        self.assertEqual(db.breakdown(self.conn, "tool"), [("OpenCode", 1275, 1)])

    def test_dedup_fills_missing_tool_attribution(self):
        provider_side = self._event(
            source="openrouter", tool=None, timestamp="2026-10-06T10:00:00.100Z",
            request_id=None,
        )
        agent_side = self._event(
            source="kilo", tool="Kilo", timestamp="2026-10-06T10:00:00.600Z",
            request_id="kilo-7", agent="plan",
        )
        self.assertEqual(db.insert_events(self.conn, [provider_side]), (1, 0, 0))
        self.assertEqual(db.insert_events(self.conn, [agent_side]), (0, 1, 0))
        self.assertEqual(db.totals(self.conn)["events"], 1)
        self.assertEqual(db.breakdown(self.conn, "tool"), [("Kilo", 1275, 1)])

    def test_zero_usage_rows_stay_distinct(self):
        # Zero rows carry no tokens to double-count; merging them would
        # undercount requests, so each source event stays its own row.
        first = normalize_event(
            timestamp="2026-10-06T10:00:00.100Z", provider="groq", model="m",
            exact=True, source="pi", tool="Pi", request_id="z1",
        )
        second = normalize_event(
            timestamp="2026-10-06T10:00:00.200Z", provider="groq", model="m",
            exact=True, source="opencode", tool="OpenCode", request_id="z2",
        )
        self.assertNotEqual(first.dedup_key, second.dedup_key)
        db.insert_events(self.conn, [first, second])
        self.assertEqual(db.totals(self.conn)["events"], 2)

    def test_same_source_distinct_requests_with_identical_tokens_not_merged(self):
        a = self._event(source="pi", tool="Pi", timestamp="2026-10-06T10:00:00.100Z", request_id="r1")
        b = self._event(source="pi", tool="Pi", timestamp="2026-10-06T10:00:00.400Z", request_id="r2")
        self.assertEqual(db.insert_events(self.conn, [a, b]), (2, 0, 0))
        self.assertEqual(db.totals(self.conn)["events"], 2)


# -------------------------------------------------------- aggregation


class AggregationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = db.connect(Path(self.tmp.name) / "test.db")
        self.addCleanup(self.conn.close)
        rows = [
            # tool, provider, model, input, output
            ("OpenCode", "openrouter", "glm-5", 100, 10),
            ("OpenCode", "openrouter", "glm-5", 50, 5),
            ("Oh My Pi", "omp-zen", "mimo-flash", 400, 40),
            ("Hermes", "nous", "model-y", 1000, 100),
        ]
        for index, (tool, provider, model, inp, out) in enumerate(rows):
            event = normalize_event(
                timestamp=f"2026-10-05T10:00:0{index}.000Z",
                provider=provider, model=model, tool=tool, exact=True,
                source="manual", input_tokens=inp, output_tokens=out,
            )
            db.insert_events(self.conn, [event])

    def test_breakdown_by_tool(self):
        result = dict(
            (key, total) for key, total, _events in db.breakdown(self.conn, "tool")
        )
        self.assertEqual(result["OpenCode"], 165)
        self.assertEqual(result["Oh My Pi"], 440)
        self.assertEqual(result["Hermes"], 1100)

    def test_breakdown_by_provider(self):
        result = dict(
            (key, total) for key, total, _events in db.breakdown(self.conn, "provider")
        )
        self.assertEqual(result["openrouter"], 165)
        self.assertEqual(result["omp-zen"], 440)
        self.assertEqual(result["nous"], 1100)

    def test_breakdown_by_model(self):
        result = dict(
            (key, total) for key, total, _events in db.breakdown(self.conn, "model")
        )
        self.assertEqual(result["glm-5"], 165)
        self.assertEqual(result["mimo-flash"], 440)
        self.assertEqual(result["model-y"], 1100)

    def test_time_window_filters_and_exact_split(self):
        recent = normalize_event(
            timestamp="2026-10-06T10:00:00.000Z", provider="kilo",
            model="step", tool="Kilo", exact=False, source="manual",
            input_tokens=1000000,
        )
        db.insert_events(self.conn, [recent])
        window = db.totals(self.conn, since="2026-10-06")
        self.assertEqual(window["events"], 1)
        self.assertEqual(window["total_tokens"], 1000000)
        self.assertEqual(window["estimated_tokens"], 1000000)
        self.assertEqual(window["exact_tokens"], 0)
        lifetime = db.totals(self.conn)
        self.assertEqual(lifetime["events"], 5)
        self.assertEqual(lifetime["exact_events"], 4)


# ------------------------------------------------------------ sync_all


class SyncAllTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.conn = db.connect(self.root / "test.db")
        self.addCleanup(self.conn.close)

    def test_partial_failure_is_isolated_and_recorded(self):
        kilo_db = self.root / "kilo.db"
        build_kilo_db(kilo_db)
        registry = [
            collectors.Collector("kilo", "Kilo", "exact", "agent",
                                 lambda: kilo_db, collectors.collect_kilo),
            collectors.Collector("ghost", "Ghost Agent", "exact", "agent",
                                 lambda: None, collectors.collect_kilo),
            collectors.Collector("openrouter", "OpenRouter", "unavailable", "provider",
                                 unavailable_reason="activity API returned HTTP 403"),
        ]
        with mock.patch.object(collectors, "REGISTRY", registry):
            results = collectors.sync_all(self.conn)
        self.assertEqual(results["kilo"]["status"], "ok")
        self.assertEqual(results["kilo"]["stored"], 2)
        self.assertEqual(results["ghost"]["status"], "error")
        self.assertIn("not found", results["ghost"]["error"])
        self.assertEqual(results["openrouter"]["status"], "unavailable")
        statuses = {row["source"]: row for row in db.collector_statuses(self.conn)}
        self.assertEqual(statuses["kilo"]["capability"], "exact")
        self.assertEqual(statuses["kilo"]["error"], None)
        self.assertEqual(statuses["kilo"]["events"], 2)
        self.assertIsNotNone(statuses["kilo"]["last_sync"])
        self.assertEqual(statuses["ghost"]["capability"], "unavailable")
        self.assertIn("not found", statuses["ghost"]["error"])
        # a failed collector leaves other collectors' data untouched
        self.assertEqual(db.totals(self.conn)["events"], 2)

    def test_repeated_sync_is_idempotent(self):
        kilo_db = self.root / "kilo.db"
        build_kilo_db(kilo_db)
        registry = [
            collectors.Collector("kilo", "Kilo", "exact", "agent",
                                 lambda: kilo_db, collectors.collect_kilo),
        ]
        with mock.patch.object(collectors, "REGISTRY", registry):
            first = collectors.sync_all(self.conn)
            second = collectors.sync_all(self.conn)
        self.assertEqual(first["kilo"]["inserted"], 2)
        self.assertEqual(second["kilo"]["inserted"], 0)
        self.assertEqual(second["kilo"]["unchanged"], 2)
        self.assertEqual(db.totals(self.conn)["events"], 2)
        # historical scan: every sync re-reads full history, not deltas
        statuses = db.collector_statuses(self.conn)
        self.assertEqual(statuses[0]["events"], 2)

    def test_success_clears_a_previous_error(self):
        registry_fail = [
            collectors.Collector("kilo", "Kilo", "exact", "agent",
                                 lambda: None, collectors.collect_kilo),
        ]
        with mock.patch.object(collectors, "REGISTRY", registry_fail):
            collectors.sync_all(self.conn)
        statuses = {r["source"]: r for r in db.collector_statuses(self.conn)}
        self.assertIsNotNone(statuses["kilo"]["error"])
        self.assertIsNone(statuses["kilo"]["last_sync"])

        kilo_db = self.root / "kilo.db"
        build_kilo_db(kilo_db)
        registry_ok = [
            collectors.Collector("kilo", "Kilo", "exact", "agent",
                                 lambda: kilo_db, collectors.collect_kilo),
        ]
        with mock.patch.object(collectors, "REGISTRY", registry_ok):
            collectors.sync_all(self.conn)
        statuses = {r["source"]: r for r in db.collector_statuses(self.conn)}
        self.assertIsNone(statuses["kilo"]["error"])
        self.assertIsNotNone(statuses["kilo"]["last_sync"])
        self.assertEqual(statuses["kilo"]["events"], 2)


# ----------------------------------------------------- legacy imports


class LegacyExportCompatTest(unittest.TestCase):
    """Exports written before tool/dedup_key existed must still import."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = db.connect(Path(self.tmp.name) / "test.db")
        self.addCleanup(self.conn.close)

    def test_legacy_csv_header_imports(self):
        legacy_header = [c for c in EVENT_COLUMNS if c not in ("tool", "dedup_key")]
        self.assertNotIn("tool", legacy_header)
        path = Path(self.tmp.name) / "legacy.csv"
        row = {
            "id": "legacy-1",
            "timestamp": "2026-10-06T10:00:00.000Z",
            "provider": "openrouter",
            "agent": "build",
            "model": "glm-5",
            "project": "D:/work",
            "input_tokens": "100",
            "output_tokens": "20",
            "cache_read_tokens": "10",
            "cache_write_tokens": "0",
            "reasoning_tokens": "5",
            "total_tokens": "135",
            "cost": "0.1",
            "exact": "1",
            "source": "opencode",
            "request_id": "msg_1",
            "raw_metadata": "{}",
        }
        with open(path, "w", newline="", encoding="utf-8") as handle:
            handle.write(",".join(legacy_header) + "\n")
            handle.write(",".join(str(row[c]) for c in legacy_header) + "\n")
        self.assertEqual(db.import_csv(self.conn, path), (1, 0, 0))
        event = db.all_events(self.conn)[0]
        self.assertEqual(event.tool, "OpenCode")  # inferred from source map
        self.assertTrue(event.dedup_key)

    def test_current_header_round_trips(self):
        event = normalize_event(
            timestamp="2026-10-06T10:00:00.000Z", provider="openrouter",
            tool="Pi", exact=True, source="pi", input_tokens=100,
        )
        db.insert_events(self.conn, [event])
        path = Path(self.tmp.name) / "export.csv"
        db.export_csv(self.conn, path)
        self.assertEqual(db.import_csv(self.conn, path), (0, 0, 1))


if __name__ == "__main__":
    unittest.main()
