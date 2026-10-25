"""Codex collector: record-type selection and archived sessions.

Synthetic rollout fixtures only, built in temp directories. Covers:

* older rollouts carrying only ``event_msg``/``token_count`` usage;
* newer dual-write rollouts (``token_usage_record`` + ``token_count`` for the
  same responses) counting each response once;
* ``archived_sessions`` being scanned, and a session present in both the live
  and archived dirs being collected once.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from token_tracker import collectors, db


def _usage(inp, cached, out, reasoning):
    return {
        "input_tokens": inp,
        "cached_input_tokens": cached,
        "output_tokens": out,
        "reasoning_output_tokens": reasoning,
        "total_tokens": inp + out,
    }


def _meta(session_id="sess-legacy", cwd="D:/work/legacy"):
    return {
        "timestamp": "2026-03-01T10:00:00.000Z",
        "type": "session_meta",
        "payload": {"id": session_id, "cwd": cwd, "model_provider": "openai"},
    }


def _turn(model="gpt-5-codex", ts="2026-03-01T10:00:01.000Z"):
    return {"timestamp": ts, "type": "turn_context", "payload": {"model": model}}


def _token_count(ts, last, total, rate_limits=None):
    info = None
    if last is not None or total is not None:
        info = {"model_context_window": 272000}
        if last is not None:
            info["last_token_usage"] = last
        if total is not None:
            info["total_token_usage"] = total
    return {
        "timestamp": ts,
        "type": "event_msg",
        "payload": {"type": "token_count", "info": info, "rate_limits": rate_limits},
    }


def _record(ts, response_id, usage, session_id="sess-new"):
    return {
        "timestamp": ts,
        "type": "token_usage_record",
        "payload": {
            "session_id": session_id,
            "turn_id": "t-" + response_id,
            "response_id": response_id,
            "usage": usage,
        },
    }


def _write(path: Path, objs) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(o) for o in objs) + "\n", encoding="utf-8")
    return path


def legacy_rollout():
    """Two responses, token_count only; a repeated snapshot and a
    rate-limit-only (info=null) event must not add events."""
    u1 = _usage(1000, 400, 100, 20)
    u2 = _usage(2000, 1500, 300, 50)
    t1 = u1
    t2 = {k: u1[k] + u2[k] for k in u1}
    return [
        _meta(),
        _turn(),
        _token_count("2026-03-01T10:00:05.000Z", None, None,
                     rate_limits={"primary": {"used_percent": 3.0}}),
        _token_count("2026-03-01T10:00:10.000Z", u1, t1),
        _token_count("2026-03-01T10:00:10.500Z", u1, t1),  # repeat, same total
        _token_count("2026-03-01T10:01:00.000Z", u2, t2),
    ]


class CodexCollectorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.codex = self.home / ".codex"

    # ------------------------------------------------ record-type selection

    def test_token_count_only_rollout_yields_events(self):
        sessions = self.codex / "sessions"
        _write(sessions / "2026" / "03" / "01" / "rollout-2026-03-01-legacy.jsonl",
               legacy_rollout())
        events = collectors.collect_codex(sessions)
        self.assertEqual(len(events), 2)
        e1, e2 = events
        self.assertEqual(e1.source, "codex")
        self.assertEqual(e1.model, "gpt-5-codex")
        self.assertEqual(e1.project, "D:/work/legacy")
        self.assertIsNone(e1.request_id)
        self.assertTrue(e1.exact)
        self.assertEqual(
            (e1.input_tokens, e1.cache_read_tokens, e1.output_tokens, e1.reasoning_tokens),
            (600, 400, 80, 20),
        )
        self.assertEqual(e1.total_tokens, 1100)
        self.assertEqual(e2.total_tokens, 2300)
        self.assertEqual(sum(e.total_tokens for e in events), 3400)

    def test_token_count_without_last_usage_uses_total_delta(self):
        sessions = self.codex / "sessions"
        t1 = _usage(1000, 400, 100, 20)
        t2 = _usage(3000, 1900, 400, 70)
        _write(sessions / "2026" / "03" / "02" / "rollout-2026-03-02-delta.jsonl", [
            _meta(),
            _turn(),
            _token_count("2026-03-02T10:00:10.000Z", None, t1),
            _token_count("2026-03-02T10:01:00.000Z", None, t2),
        ])
        events = collectors.collect_codex(sessions)
        self.assertEqual([e.total_tokens for e in events], [1100, 2300])
        second = events[1]
        self.assertEqual(
            (second.input_tokens, second.cache_read_tokens,
             second.output_tokens, second.reasoning_tokens),
            (500, 1500, 250, 50),
        )

    def test_dual_write_rollout_counts_each_response_once(self):
        sessions = self.codex / "sessions"
        u1 = _usage(1000, 600, 80, 30)
        u2 = _usage(500, 0, 40, 0)
        t2 = {k: u1[k] + u2[k] for k in u1}
        _write(sessions / "2026" / "10" / "06" / "rollout-2026-10-06-dual.jsonl", [
            _meta("sess-new", "D:/work/new"),
            _turn("gpt-5.5"),
            _record("2026-10-06T01:00:00.000Z", "resp_1", u1),
            _token_count("2026-10-06T01:00:00.100Z", u1, u1),
            _record("2026-10-06T01:01:00.000Z", "resp_2", u2),
            _token_count("2026-10-06T01:01:00.100Z", u2, t2),
        ])
        events = collectors.collect_codex(sessions)
        self.assertEqual(len(events), 2)  # not 4
        self.assertEqual([e.request_id for e in events], ["resp_1", "resp_2"])
        self.assertEqual(sum(e.total_tokens for e in events), 1080 + 540)

    # ------------------------------------------------------- archived dir

    def test_archived_sessions_are_scanned(self):
        sessions = self.codex / "sessions"
        _write(sessions / "2026" / "10" / "06" / "rollout-2026-10-06-live.jsonl", [
            _meta("sess-live"), _turn(),
            _record("2026-10-06T01:00:00.000Z", "resp_live", _usage(100, 0, 10, 0)),
        ])
        _write(self.codex / "archived_sessions" / "rollout-2026-03-01-legacy.jsonl",
               legacy_rollout())
        events = collectors.collect_codex(sessions)
        self.assertEqual(len(events), 3)
        self.assertEqual(sum(e.total_tokens for e in events), 110 + 3400)

    def test_find_codex_falls_back_to_archived_only_home(self):
        archived = self.codex / "archived_sessions"
        _write(archived / "rollout-2026-03-01-legacy.jsonl", legacy_rollout())
        with mock.patch.object(collectors, "_user_home", lambda: self.home), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CODEX_SESSIONS", None)
            found = collectors.find_codex()
        self.assertEqual(found, archived)
        self.assertEqual(len(collectors.collect_codex(found)), 2)

    def test_find_codex_prefers_live_sessions_dir(self):
        (self.codex / "sessions").mkdir(parents=True)
        (self.codex / "archived_sessions").mkdir(parents=True)
        with mock.patch.object(collectors, "_user_home", lambda: self.home):
            os.environ.pop("CODEX_SESSIONS", None)
            self.assertEqual(collectors.find_codex(), self.codex / "sessions")

    def test_session_in_both_live_and_archived_is_counted_once(self):
        name = "rollout-2026-03-01-legacy.jsonl"
        sessions = self.codex / "sessions"
        _write(sessions / "2026" / "03" / "01" / name, legacy_rollout())
        _write(self.codex / "archived_sessions" / name, legacy_rollout())
        events = collectors.collect_codex(sessions)
        self.assertEqual(len(events), 2)
        # Same result when collection starts from the archived side.
        self.assertEqual(
            len(collectors.collect_codex(self.codex / "archived_sessions")), 2)

    def test_repeated_sync_with_archive_is_idempotent_in_db(self):
        name = "rollout-2026-03-01-legacy.jsonl"
        sessions = self.codex / "sessions"
        _write(sessions / "2026" / "03" / "01" / name, legacy_rollout())
        conn = db.connect(self.root / "usage.db")
        self.addCleanup(conn.close)
        db.insert_events(conn, collectors.collect_codex(sessions))
        # The session is then archived (moved) and a copy lingers live.
        _write(self.codex / "archived_sessions" / name, legacy_rollout())
        db.insert_events(conn, collectors.collect_codex(sessions))
        (n,) = conn.execute("SELECT COUNT(*) FROM events WHERE source='codex'").fetchone()
        self.assertEqual(n, 2)


if __name__ == "__main__":
    unittest.main()
