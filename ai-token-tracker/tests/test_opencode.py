import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from token_tracker import db, opencode


def build_fixture(path: Path) -> None:
    """Minimal OpenCode-shaped database: the tables/fields collect() reads."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT)")
    con.execute(
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
        "time_created INTEGER NOT NULL, data TEXT NOT NULL)"
    )
    con.execute("INSERT INTO session VALUES (?, ?, ?)", ("ses_1", "D:/code/demo", "build"))
    messages = [
        (
            "msg_1",
            "ses_1",
            1791000000000,
            json.dumps(
                {
                    "role": "assistant",
                    "agent": "build",
                    "mode": "build",
                    "modelID": "z-ai/glm-5",
                    "providerID": "openrouter",
                    "cost": 0.25,
                    "finish": "stop",
                    "time": {"created": 1791000000000, "completed": 1791000010000},
                    "tokens": {
                        "input": 1000,
                        "output": 200,
                        "reasoning": 50,
                        "cache": {"read": 5000, "write": 100},
                    },
                }
            ),
        ),
        ("msg_2", "ses_1", 1791000001000, json.dumps({"role": "user", "time": {"created": 1791000001000}})),
        (
            "msg_3",
            "ses_1",
            1791000002000,
            json.dumps(
                {
                    "role": "assistant",
                    "agent": "plan",
                    "modelID": "space-bunny-free",
                    "providerID": "opencode",
                    "time": {"created": 1791000002000},
                    "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                }
            ),
        ),
        ("msg_4", "ses_1", 1791000003000, json.dumps({"role": "assistant", "modelID": "no-usage"})),
        ("msg_bad", "ses_1", 1791000004000, "{not json"),
    ]
    con.executemany("INSERT INTO message VALUES (?, ?, ?, ?)", messages)
    con.commit()
    con.close()


class OpenCodeCollectorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fixture = Path(self.tmp.name) / "opencode.db"
        build_fixture(self.fixture)

    def test_collects_only_messages_with_recorded_usage(self):
        events = opencode.collect(self.fixture)
        self.assertEqual(sorted(e.request_id for e in events), ["msg_1", "msg_3"])

    def test_normalizes_tokens_exact_flag_and_context(self):
        events = {e.request_id: e for e in opencode.collect(self.fixture)}
        first = events["msg_1"]
        self.assertEqual(first.input_tokens, 1000)
        self.assertEqual(first.output_tokens, 200)
        self.assertEqual(first.cache_read_tokens, 5000)
        self.assertEqual(first.cache_write_tokens, 100)
        self.assertEqual(first.reasoning_tokens, 50)
        self.assertEqual(first.total_tokens, 6350)
        self.assertTrue(first.exact)
        self.assertEqual(first.provider, "openrouter")
        self.assertEqual(first.model, "z-ai/glm-5")
        self.assertEqual(first.agent, "build")
        self.assertEqual(first.project, "D:/code/demo")
        self.assertEqual(first.source, "opencode")
        self.assertEqual(first.cost, 0.25)
        self.assertTrue(first.timestamp.endswith("Z"))
        self.assertEqual(events["msg_3"].agent, "plan")

    def test_import_is_idempotent(self):
        target = db.connect(Path(self.tmp.name) / "usage.db")
        self.addCleanup(target.close)
        events = opencode.collect(self.fixture)
        self.assertEqual(db.insert_events(target, events), (2, 0, 0))
        self.assertEqual(db.insert_events(target, events), (0, 0, 2))
        totals = db.totals(target)
        self.assertEqual(totals["events"], 2)
        self.assertEqual(totals["total_tokens"], 6350)

    def test_find_database_honors_environment_override(self):
        with mock.patch.dict(os.environ, {"OPENCODE_DB": str(self.fixture)}):
            self.assertEqual(opencode.find_database(), self.fixture)


if __name__ == "__main__":
    unittest.main()
