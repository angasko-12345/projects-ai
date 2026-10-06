import csv
import json
import tempfile
import unittest
from pathlib import Path

from token_tracker import db
from token_tracker.model import EVENT_COLUMNS, normalize_event


def make_event(
    timestamp,
    *,
    provider="openrouter",
    model="z-ai/glm-5",
    agent="build",
    source="opencode",
    request_id=None,
    input_tokens=0,
    output_tokens=0,
    cache_read_tokens=0,
    cache_write_tokens=0,
    reasoning_tokens=0,
    cost=None,
    exact=True,
):
    return normalize_event(
        timestamp=timestamp,
        provider=provider,
        model=model,
        agent=agent,
        source=source,
        request_id=request_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        reasoning_tokens=reasoning_tokens,
        cost=cost,
        exact=exact,
    )


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = db.connect(Path(self.tmp.name) / "test.db")
        self.addCleanup(self.conn.close)

    def test_fresh_database_has_zero_totals(self):
        totals = db.totals(self.conn)
        self.assertEqual(totals["events"], 0)
        self.assertEqual(totals["total_tokens"], 0)
        self.assertEqual(totals["exact_tokens"], 0)
        self.assertEqual(totals["estimated_tokens"], 0)

    def test_reimport_of_identical_event_is_unchanged(self):
        event = make_event("2026-10-06T10:00:00.000Z", request_id="msg_1", input_tokens=100)
        self.assertEqual(db.insert_events(self.conn, [event]), (1, 0, 0))
        self.assertEqual(db.insert_events(self.conn, [event]), (0, 0, 1))
        self.assertEqual(db.totals(self.conn)["events"], 1)

    def test_reimport_refreshes_row_the_source_updated(self):
        # OpenCode stores all-zero tokens mid-stream and fills them in on
        # completion; request id and timestamp stay stable, so a re-import
        # must reconcile the stale first-seen values instead of keeping them.
        stale = make_event("2026-10-06T10:00:00.000Z", request_id="msg_live", input_tokens=0)
        fresh = make_event("2026-10-06T10:00:00.000Z", request_id="msg_live", input_tokens=500)
        self.assertEqual(stale.id, fresh.id)
        self.assertEqual(db.insert_events(self.conn, [stale]), (1, 0, 0))
        self.assertEqual(db.insert_events(self.conn, [fresh]), (0, 1, 0))
        totals = db.totals(self.conn)
        self.assertEqual(totals["events"], 1)
        self.assertEqual(totals["total_tokens"], 500)
        self.assertEqual(db.insert_events(self.conn, [fresh]), (0, 0, 1))

    def test_distinct_request_ids_are_not_duplicates(self):
        first = make_event("2026-10-06T10:00:00.000Z", request_id="msg_1", input_tokens=100)
        second = make_event("2026-10-06T10:00:01.000Z", request_id="msg_2", input_tokens=100)
        self.assertEqual(db.insert_events(self.conn, [first, second]), (2, 0, 0))
        self.assertEqual(db.totals(self.conn)["events"], 2)

    def test_totals_respect_date_window_and_exact_split(self):
        # input 1000 + output 500 + read 2000 + write 0 + reasoning 100 = 3600
        exact_event = make_event(
            "2026-10-06T10:00:00.000Z",
            request_id="a",
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=2000,
            reasoning_tokens=100,
            exact=True,
        )
        estimated_event = make_event(
            "2026-10-01T00:00:00.000Z",
            request_id="b",
            input_tokens=400,
            output_tokens=100,
            exact=False,
        )
        older = make_event("2026-03-01T00:00:00.000Z", request_id="c", input_tokens=100)
        db.insert_events(self.conn, [exact_event, estimated_event, older])

        today = db.totals(self.conn, "2026-10-06")
        self.assertEqual(today["events"], 1)
        self.assertEqual(today["total_tokens"], 3600)
        self.assertEqual(today["exact_tokens"], 3600)
        self.assertEqual(today["estimated_tokens"], 0)

        week = db.totals(self.conn, "2026-09-30")
        self.assertEqual(week["events"], 2)
        self.assertEqual(week["total_tokens"], 4100)
        self.assertEqual(week["exact_tokens"], 3600)
        self.assertEqual(week["estimated_tokens"], 500)

        lifetime = db.totals(self.conn)
        self.assertEqual(lifetime["events"], 3)
        self.assertEqual(lifetime["total_tokens"], 4200)
        self.assertEqual(lifetime["exact_tokens"], 3700)
        self.assertEqual(lifetime["estimated_tokens"], 500)
        self.assertEqual(lifetime["exact_events"], 2)

    def test_daily_series_is_zero_filled(self):
        db.insert_events(
            self.conn,
            [
                make_event("2026-10-05T08:00:00.000Z", request_id="d5", input_tokens=700),
                make_event("2026-10-06T08:00:00.000Z", request_id="d6", input_tokens=300),
            ],
        )
        from datetime import datetime, timezone

        series = db.daily_series(self.conn, days=3, now=datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(
            series,
            [("2026-10-04", 0), ("2026-10-05", 700), ("2026-10-06", 300)],
        )

    def test_breakdown_groups_and_orders_by_tokens(self):
        db.insert_events(
            self.conn,
            [
                make_event("2026-10-01T00:00:00.000Z", request_id="1", provider="openrouter", model="a", input_tokens=100),
                make_event("2026-10-01T00:00:01.000Z", request_id="2", provider="opencode", model="b", input_tokens=50),
                make_event("2026-10-01T00:00:02.000Z", request_id="3", provider="opencode", model="c", input_tokens=25),
            ],
        )
        providers = db.breakdown(self.conn, "provider")
        self.assertEqual(providers, [("openrouter", 100, 1), ("opencode", 75, 2)])
        models = db.breakdown(self.conn, "model")
        self.assertEqual(models, [("a", 100, 1), ("b", 50, 1), ("c", 25, 1)])
        with self.assertRaises(ValueError):
            db.breakdown(self.conn, "id")

    def test_csv_export_writes_all_columns(self):
        db.insert_events(
            self.conn,
            [
                make_event("2026-10-06T10:00:00.000Z", request_id="a", input_tokens=10, exact=True),
                make_event("2026-10-06T11:00:00.000Z", request_id="b", input_tokens=20, exact=False),
            ],
        )
        out = Path(self.tmp.name) / "export.csv"
        count = db.export_csv(self.conn, out)
        self.assertEqual(count, 2)
        with open(out, newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[0], list(EVENT_COLUMNS))
        self.assertEqual(len(rows), 3)
        exact_column = rows[0].index("exact")
        self.assertEqual(sorted(row[exact_column] for row in rows[1:]), ["0", "1"])
        total_column = rows[0].index("total_tokens")
        self.assertEqual(sum(int(row[total_column]) for row in rows[1:]), 30)

    def test_export_json_round_trips(self):
        db.insert_events(
            self.conn,
            [
                make_event("2026-10-06T10:00:00.000Z", request_id="a", input_tokens=10, exact=True),
                make_event("2026-10-06T11:00:00.000Z", request_id="b", output_tokens=20, exact=False),
            ],
        )
        out = Path(self.tmp.name) / "export.json"
        self.assertEqual(db.export_json(self.conn, out), 2)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 2)
        self.assertEqual({row["exact"] for row in data}, {1, 0})
        self.assertEqual(sum(row["total_tokens"] for row in data), 30)

    def test_exports_refuse_to_overwrite_database(self):
        db.insert_events(self.conn, [make_event("2026-10-06T10:00:00.000Z", request_id="a", input_tokens=5)])
        db_path = Path(self.conn.execute("PRAGMA database_list").fetchone()[2])
        with self.assertRaises(ValueError):
            db.export_csv(self.conn, db_path)
        with self.assertRaises(ValueError):
            db.export_json(self.conn, str(db_path))
        # the database must survive the refused writes
        self.assertEqual(db.totals(self.conn)["total_tokens"], 5)

    def test_import_csv_round_trip_is_idempotent(self):
        db.insert_events(
            self.conn,
            [
                make_event("2026-10-06T10:00:00.000Z", request_id="a", input_tokens=1000, output_tokens=50, exact=True),
                make_event("2026-10-05T09:30:00.000Z", request_id="b", output_tokens=25, exact=False),
            ],
        )
        out = Path(self.tmp.name) / "roundtrip.csv"
        db.export_csv(self.conn, out)

        other = db.connect(Path(self.tmp.name) / "other.db")
        self.addCleanup(other.close)
        self.assertEqual(db.import_csv(other, out), (2, 0, 0))
        src, dst = db.totals(self.conn), db.totals(other)
        for key in ("events", "total_tokens", "exact_tokens", "estimated_tokens"):
            self.assertEqual(dst[key], src[key], key)
        # the exact/estimated split must survive: a "0" in the CSV is an estimate
        self.assertEqual(dst["estimated_tokens"], 25)
        self.assertEqual({e.id for e in db.all_events(other)}, {e.id for e in db.all_events(self.conn)})
        self.assertEqual(db.import_csv(other, out), (0, 0, 2))
        self.assertEqual(db.totals(other)["total_tokens"], src["total_tokens"])

    def test_import_csv_rejects_bad_header_without_writing(self):
        bad = Path(self.tmp.name) / "bad.csv"
        bad.write_text("id,timestamp\nx,2026-01-01\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            db.import_csv(self.conn, bad)
        self.assertEqual(db.totals(self.conn)["events"], 0)

    def test_import_csv_aborts_on_invalid_row(self):
        db.insert_events(self.conn, [make_event("2026-10-06T10:00:00.000Z", request_id="ok", input_tokens=5)])
        good = Path(self.tmp.name) / "good.csv"
        db.export_csv(self.conn, good)
        with good.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        bad_row = list(rows[1])
        bad_row[EVENT_COLUMNS.index("input_tokens")] = "-5"
        bad_row[EVENT_COLUMNS.index("request_id")] = "bad"
        rows.append(bad_row)
        bad = Path(self.tmp.name) / "invalid.csv"
        with bad.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(rows)

        other = db.connect(Path(self.tmp.name) / "fresh.db")
        self.addCleanup(other.close)
        with self.assertRaisesRegex(ValueError, "row 2"):
            db.import_csv(other, bad)
        self.assertEqual(db.totals(other)["events"], 0)  # nothing written

    def test_import_json_round_trips_and_validates_shape(self):
        db.insert_events(self.conn, [make_event("2026-10-06T10:00:00.000Z", request_id="j", output_tokens=7, exact=False)])
        out = Path(self.tmp.name) / "roundtrip.json"
        db.export_json(self.conn, out)

        other = db.connect(Path(self.tmp.name) / "other.db")
        self.addCleanup(other.close)
        self.assertEqual(db.import_json(other, out), (1, 0, 0))
        self.assertEqual(db.totals(other)["estimated_tokens"], 7)
        self.assertEqual(db.import_json(other, out), (0, 0, 1))

        shape = Path(self.tmp.name) / "shape.json"
        shape.write_text('{"not": "a list"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            db.import_json(other, shape)


if __name__ == "__main__":
    unittest.main()
