"""Qt-free derivations behind the workspace thread list (gui/derive.py)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agentops.gui import derive

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


def ago(**delta: float) -> str:
    return (NOW - timedelta(**delta)).isoformat()


def workflow(wf_id: str, status: str, updated: str, **extra: object) -> dict:
    return {
        "id": wf_id,
        "description": f"work {wf_id}",
        "status": status,
        "created_at": updated,
        "updated_at": updated,
        **extra,
    }


class RelAgeTest(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(derive.rel_age(ago(seconds=20), NOW), "now")
        self.assertEqual(derive.rel_age(ago(minutes=17), NOW), "17m")
        self.assertEqual(derive.rel_age(ago(hours=7, minutes=59), NOW), "7h")
        self.assertEqual(derive.rel_age(ago(days=3, hours=2), NOW), "3d")

    def test_naive_z_future_and_bad_values(self):
        self.assertEqual(derive.rel_age("2026-10-28T11:00:00", NOW), "1h")  # naive = UTC
        self.assertEqual(derive.rel_age("2026-10-28T11:58:00Z", NOW), "2m")
        self.assertEqual(derive.rel_age(ago(minutes=-5), NOW), "now")  # clock skew
        for bad in (None, "", "yesterday", 42):
            self.assertEqual(derive.rel_age(bad, NOW), "")


class ParseDiffStatTest(unittest.TestCase):
    def test_summary_line_only(self):
        text = (
            " auth/routes.py | 13 +++++++++++--\n auth/tokens.py | 27 ++++++++++++++++++++-------\n"
            " 2 files changed, 33 insertions(+), 7 deletions(-)\n"
        )
        self.assertEqual(derive.parse_diff_stat(text), (2, 33, 7))

    def test_singular_and_missing_halves(self):
        self.assertEqual(derive.parse_diff_stat("1 file changed, 1 insertion(+)"), (1, 1, 0))
        self.assertEqual(derive.parse_diff_stat("3 files changed, 9 deletions(-)"), (3, 0, 9))

    def test_no_summary_line(self):
        for text in (None, "", " auth/routes.py | 13 ++++--", 7):
            self.assertIsNone(derive.parse_diff_stat(text))


class SettleRuleTest(unittest.TestCase):
    def test_terminal_and_older_than_24h(self):
        for status in ("passed", "failed", "cancelled"):
            self.assertTrue(derive.is_settled(workflow("a", status, ago(hours=25)), NOW))
            self.assertFalse(derive.is_settled(workflow("a", status, ago(hours=23)), NOW))

    def test_non_terminal_never_settles(self):
        for status in ("running", "pending", "blocked"):
            self.assertFalse(derive.is_settled(workflow("a", status, ago(days=30)), NOW))

    def test_unknown_timestamp_stays_active(self):
        self.assertFalse(derive.is_settled(workflow("a", "passed", ""), NOW))


class ThreadItemsTest(unittest.TestCase):
    def test_joins_branch_and_newest_run_and_progress(self):
        workflows = [
            workflow(
                "wf-1",
                "running",
                ago(minutes=17),
                task_count=4,
                task_counts={"passed": 1, "running": 1},
            ),
            workflow("wf-2", "pending", ago(hours=1), task_count=1, task_counts={}),
        ]
        refs = [{"workflow_id": "wf-1", "branch": "agentops/jwt-refresh-e0af"}]
        runs = [  # newest first, as list_recent_agent_runs returns them
            {"workflow_id": "wf-1", "agent": "codex", "role": "implementation"},
            {"workflow_id": "wf-1", "agent": "claude", "role": "architecture"},
        ]
        active, settled = derive.thread_items(workflows, refs, runs, NOW)
        self.assertEqual(settled, [])
        first, second = active
        self.assertEqual(
            first,
            {
                "id": "wf-1",
                "title": "work wf-1",
                "status": "running",
                "branch": "agentops/jwt-refresh-e0af",
                "agent": "codex",
                "role": "Implement",
                "age": "17m",
                "progress": "1/4",
            },
        )
        self.assertEqual((second["branch"], second["agent"], second["role"]), ("", "", ""))
        self.assertEqual(second["progress"], "0/1")

    def test_splits_settled_and_orders_active_by_attention(self):
        workflows = [
            workflow("passed-new", "passed", ago(hours=2)),
            workflow("pending", "pending", ago(hours=3)),
            workflow("failed-old", "failed", ago(days=2)),
            workflow("running", "running", ago(minutes=1)),
            workflow("failed-new", "failed", ago(hours=5)),
        ]
        active, settled = derive.thread_items(workflows, [], [], NOW)
        self.assertEqual(
            [i["id"] for i in active], ["running", "failed-new", "pending", "passed-new"]
        )
        self.assertEqual([i["id"] for i in settled], ["failed-old"])

    def test_unknown_role_is_worded_not_dropped(self):
        runs = [{"workflow_id": "a", "agent": "kilo", "role": "security_review"}]
        (item,), _ = derive.thread_items([workflow("a", "running", ago(minutes=1))], [], runs, NOW)
        self.assertEqual(item["role"], "Security review")


class PollableRepositoriesTest(unittest.TestCase):
    def test_only_other_repos_with_a_state_db(self):
        have_db = {"/r/b", "/r/c"}
        got = derive.pollable_repositories(
            ["/r/a", "/r/b", "", "/r/b", "/r/d", "/r/c"], "/r/a", False, have_db.__contains__
        )
        self.assertEqual(got, ["/r/b", "/r/c"])

    def test_pinned_state_path_reads_no_other_repo(self):
        self.assertEqual(
            derive.pollable_repositories(["/r/a", "/r/b"], "/r/a", True, lambda _p: True), []
        )


def run(started: str, status: str = "completed", seconds: float = 60.0, **extra: object) -> dict:
    return {"started_at": started, "status": status, "duration_seconds": seconds, **extra}


class DailySeriesTest(unittest.TestCase):
    def test_empty_is_fourteen_zero_days_ending_today(self):
        series = derive.daily_series([], days=14, now=NOW)
        self.assertEqual(len(series), 14)
        self.assertEqual(series[-1][0], NOW.date())
        self.assertEqual(series[0][0], (NOW - timedelta(days=13)).date())
        self.assertTrue(all(total == 0 and failed == 0 for _day, total, failed in series))

    def test_one_day_counts_failed_timed_out_and_terminated(self):
        runs = [
            run(ago(hours=1)),
            run(ago(hours=2), "failed"),
            run(ago(hours=3), "timed_out"),
            run(ago(hours=4), "completed", terminated=True),
            run(ago(hours=5), "cancelled"),
            run(ago(days=20), "failed"),  # outside the window
            {"status": "failed"},  # no timestamp
        ]
        series = derive.daily_series(runs, days=14, now=NOW)
        self.assertEqual(series[-1], (NOW.date(), 5, 3))
        self.assertEqual(sum(total for _d, total, _f in series), 5)

    def test_all_zero_window_and_created_at_fallback(self):
        series = derive.daily_series([{"created_at": ago(days=1), "status": "pending"}], 3, NOW)
        self.assertEqual([total for _d, total, _f in series], [0, 1, 0])
        outside = derive.daily_series([run(ago(days=5)), run(ago(days=-2))], 3, NOW)
        self.assertEqual([(t, f) for _d, t, f in outside], [(0, 0)] * 3)


class WeekStatsTest(unittest.TestCase):
    def test_empty_everything_has_no_prior_week(self):
        stats = derive.week_stats([], [], [], 0, NOW)
        self.assertEqual(stats["workflows"], {"value": 0, "delta": None})
        self.assertEqual(stats["pass_rate"], {"value": None, "delta": None})
        self.assertEqual(stats["agent_time"], {"value": 0, "delta": None})
        self.assertEqual(stats["attention"], {"value": 0, "delta": None})

    def test_previous_week_empty_gives_none_deltas(self):
        stats = derive.week_stats(
            [workflow("a", "passed", ago(days=1))],
            [run(ago(days=1), seconds=90)],
            [{"started_at": ago(days=1), "overall_status": "passed"}],
            2,
            NOW,
        )
        self.assertEqual(stats["workflows"], {"value": 1, "delta": None})
        self.assertEqual(stats["pass_rate"], {"value": 100, "delta": None})
        self.assertEqual(stats["agent_time"], {"value": 90, "delta": None})
        self.assertEqual(stats["attention"]["value"], 2)

    def test_deltas_against_the_previous_week(self):
        workflows = [workflow(str(i), "passed", ago(days=1)) for i in range(3)]
        workflows += [workflow("p1", "passed", ago(days=9)), workflow("p2", "x", ago(days=10))]
        workflows.append(workflow("old", "passed", ago(days=30)))
        runs = [run(ago(days=2), seconds=150), run(ago(days=8), seconds=100)]
        verifications = [
            {"started_at": ago(days=1), "overall_status": "passed"},
            {"started_at": ago(days=1), "overall_status": "FAILED"},
            {"started_at": ago(days=1), "overall_status": "cancelled"},  # undecided
            {"started_at": ago(days=1), "overall_status": None},  # still running
            {"started_at": ago(days=8), "overall_status": "passed"},
        ]
        stats = derive.week_stats(workflows, runs, verifications, 5, NOW)
        self.assertEqual(stats["workflows"], {"value": 3, "delta": 50})
        self.assertEqual(stats["pass_rate"], {"value": 50, "delta": -50})
        self.assertEqual(stats["agent_time"], {"value": 150, "delta": 50})

    def test_all_zero_durations_have_no_time_delta(self):
        stats = derive.week_stats(
            [], [run(ago(days=1), seconds=0), run(ago(days=8), seconds=0)], [], 0, NOW
        )
        self.assertEqual(stats["agent_time"], {"value": 0, "delta": None})


class DashboardRowsTest(unittest.TestCase):
    def test_attention_rows_join_workflow_and_agent_in_words(self):
        failures = [
            {
                "workflow_id": "wf-1",
                "task_id": "t-1",
                "agent_run_id": "r-2",
                "category": "TEST_FAILURE",
                "severity": "HIGH",
                "recommended_action": "repair_implementation",
                "primary_error": "3 failed",
                "created_at": ago(hours=6),
            },
            {"workflow_id": "wf-9", "task_id": "t-1", "category": "TIMEOUT", "severity": "MEDIUM"},
            {"workflow_id": "", "category": "GIT_CONFLICT", "recommended_action": "stop"},
            {"category": "USAGE_LIMIT", "recommended_action": "wait_a_bit"},
        ]
        workflows = [{"id": "wf-1", "description": "Cache responses"}]
        runs = [
            {"id": "r-3", "task_id": "t-1", "agent": "codex"},  # newest of t-1
            {"id": "r-2", "task_id": "t-1", "agent": "claude"},
        ]
        rows = derive.attention_rows(failures, workflows, runs, NOW)
        first = rows[0]
        self.assertEqual(first["workflow"], "Cache responses")
        self.assertEqual(first["problem"], "Tests failed")
        self.assertEqual(first["severity"], "high")
        self.assertEqual(first["agent"], "claude")  # the run that failed
        self.assertEqual(first["next_step"], "Repair implementation")
        self.assertEqual(first["age"], "6h")
        self.assertEqual((rows[1]["workflow"], rows[1]["problem"]), ("wf-9", "Agent timed out"))
        self.assertEqual(rows[1]["agent"], "codex")  # newest run of the same task
        self.assertEqual(rows[2]["problem"], "Merge conflict")
        self.assertEqual(rows[2]["next_step"], "Resolve manually")
        self.assertEqual(rows[2]["workflow"], "Unknown workflow")
        self.assertEqual(
            (rows[3]["problem"], rows[3]["next_step"]), ("Usage limit hit", "Wait a bit")
        )


if __name__ == "__main__":
    unittest.main()
