"""Headroom ledger: Codex rollout rate_limits, Claude status line tee, OpenRouter key.

Synthetic fixtures in temp dirs only; no network (the OpenRouter fetcher is
injected), no real HOME, frozen clock.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from unittest import mock

from token_tracker import cli, headroom, statusline_tee

NOW = 1_800_000_000.0  # 2027-01-15T08:00:00Z, frozen
PKG_ROOT = Path(__file__).resolve().parent.parent


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def codex_event(ts, rate_limits, info=None):
    return {
        "timestamp": iso(ts),
        "type": "event_msg",
        "payload": {"type": "token_count", "info": info, "rate_limits": rate_limits},
    }


def rl(primary=None, secondary=None, limit_id="codex", plan="plus", reached=None):
    return {
        "limit_id": limit_id,
        "primary": primary,
        "secondary": secondary,
        "plan_type": plan,
        "rate_limit_reached_type": reached,
    }


def win(pct, minutes, resets_at=None, **extra):
    w = {"used_percent": pct, "window_minutes": minutes}
    if resets_at is not None:
        w["resets_at"] = resets_at
    w.update(extra)
    return w


def write_jsonl(path: Path, objs, raw_lines=()) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(o) for o in objs] + list(raw_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def by_window(provider: dict) -> dict:
    return {(w["limit_id"], w["window"]): w for w in provider["windows"]}


class TempCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.sessions = self.root / "codex home" / "sessions"  # space in path on purpose


# ---------------------------------------------------------------- codex


class CodexHeadroomTest(TempCase):
    def rollout(self, name, objs, raw_lines=(), mtime=None, day="2027/01/15"):
        path = write_jsonl(self.sessions / day / f"rollout-{name}.jsonl", objs, raw_lines)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def test_windows_labelled_by_length_and_fresh(self):
        self.rollout(
            "a",
            [
                codex_event(
                    NOW - 600, rl(win(62.0, 300, NOW + 3600), win(81.0, 10080, NOW + 3 * 86400))
                )
            ],
        )
        p = headroom.read_codex(NOW, root=self.sessions)
        self.assertEqual(p["status"], "ok")
        w = by_window(p)
        five, week = w[("codex", "5h")], w[("codex", "weekly")]
        self.assertEqual((five["used"], five["limit"], five["unit"]), (62.0, 100.0, "percent"))
        self.assertEqual(five["resets_at"], headroom.iso_utc(NOW + 3600))
        self.assertEqual(five["as_of"], headroom.iso_utc(NOW - 600))
        self.assertEqual(five["age_seconds"], 600)
        self.assertEqual((five["freshness"], five["state"]), ("fresh", "ok"))
        self.assertEqual(week["state"], "warn")
        self.assertEqual(five["plan_type"], "plus")
        # 5h window resets in 1h -> 4h elapsed at as_of minus 10 min
        self.assertAlmostEqual(
            five["elapsed_percent"], (4 * 3600 - 600) / (5 * 3600) * 100, places=2
        )
        self.assertAlmostEqual(five["pace"], 62.0 / five["elapsed_percent"], places=2)

    def test_weekly_window_in_primary_slot_is_weekly(self):
        self.rollout("a", [codex_event(NOW - 60, rl(primary=win(40.0, 10080, NOW + 86400)))])
        p = headroom.read_codex(NOW, root=self.sessions)
        self.assertEqual([w["window"] for w in p["windows"]], ["weekly"])

    def test_premium_snapshot_with_null_windows_does_not_blank_last_good(self):
        self.rollout(
            "a",
            [
                codex_event(
                    NOW - 600, rl(win(91.0, 300, NOW + 600), win(100.0, 10080, NOW + 86400))
                ),
                codex_event(NOW - 60, rl(None, None, limit_id="premium")),
            ],
        )
        p = headroom.read_codex(NOW, root=self.sessions)
        w = by_window(p)
        self.assertEqual(set(w), {("codex", "5h"), ("codex", "weekly")})
        self.assertEqual(w[("codex", "5h")]["used_percent"], 91.0)
        self.assertEqual(w[("codex", "weekly")]["state"], "exhausted")

    def test_newest_snapshot_wins_across_files(self):
        self.rollout(
            "old", [codex_event(NOW - 3000, rl(win(10.0, 300, NOW + 600)))], mtime=NOW - 3000
        )
        self.rollout(
            "new",
            [codex_event(NOW - 100, rl(win(55.0, 300, NOW + 600)))],
            mtime=NOW - 100,
            day="2027/01/14",
        )
        p = headroom.read_codex(NOW, root=self.sessions)
        self.assertEqual(by_window(p)[("codex", "5h")]["used_percent"], 55.0)

    def test_archived_sessions_are_read(self):
        archived = self.sessions.parent / "archived_sessions"
        write_jsonl(
            archived / "rollout-arch.jsonl", [codex_event(NOW - 60, rl(win(33.0, 300, NOW + 600)))]
        )
        p = headroom.read_codex(NOW, root=self.sessions)
        self.assertEqual(by_window(p)[("codex", "5h")]["used_percent"], 33.0)

    def test_legacy_resets_in_seconds(self):
        self.rollout("a", [codex_event(NOW - 100, rl(win(20.0, 300, resets_in_seconds=1000)))])
        p = headroom.read_codex(NOW, root=self.sessions)
        self.assertEqual(by_window(p)[("codex", "5h")]["resets_at"], headroom.iso_utc(NOW + 900))

    def test_iso_resets_at_accepted(self):
        self.rollout("a", [codex_event(NOW - 100, rl(win(20.0, 300, iso(NOW + 1200))))])
        p = headroom.read_codex(NOW, root=self.sessions)
        self.assertEqual(by_window(p)[("codex", "5h")]["resets_at"], headroom.iso_utc(NOW + 1200))

    def test_null_rate_limits_only_is_no_data_not_zero(self):
        self.rollout(
            "exec", [codex_event(NOW - 60, None, info={"total_token_usage": {"input_tokens": 5}})]
        )
        p = headroom.read_codex(NOW, root=self.sessions)
        self.assertEqual(p["status"], "no_data")
        self.assertEqual(p["windows"], [])
        self.assertIn("codex exec", p["detail"])

    def test_malformed_lines_and_windows_are_skipped_and_reported(self):
        self.rollout(
            "a",
            [
                codex_event(
                    NOW - 500, rl(win("lots", 300, NOW + 600), win(12.0, 10080, NOW + 9000))
                ),
                codex_event(NOW - 400, rl(primary="not-a-dict")),
                {
                    "timestamp": "not a time",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "rate_limits": rl(win(99.0, 300, NOW + 600)),
                    },
                },
                ["a", "list"],
            ],
            raw_lines=["{broken json", ""],
        )
        p = headroom.read_codex(NOW, root=self.sessions)
        self.assertEqual(p["status"], "ok")
        self.assertEqual(list(by_window(p)), [("codex", "weekly")])
        joined = " ".join(p["warnings"])
        self.assertIn("malformed JSON line", joined)
        self.assertIn("window(s) with missing or invalid fields", joined)

    def test_reached_type_marks_fullest_window_exhausted(self):
        self.rollout(
            "a",
            [
                codex_event(
                    NOW - 60,
                    rl(win(99.6, 300, NOW + 600), win(50.0, 10080, NOW + 9000), reached="primary"),
                )
            ],
        )
        w = by_window(headroom.read_codex(NOW, root=self.sessions))
        self.assertEqual(w[("codex", "5h")]["state"], "exhausted")
        self.assertTrue(w[("codex", "5h")]["reached"])
        self.assertEqual(w[("codex", "weekly")]["state"], "ok")

    def test_window_past_its_reset_is_expired_unknown(self):
        self.rollout("a", [codex_event(NOW - 7200, rl(win(97.0, 300, NOW - 1)))])
        w = by_window(headroom.read_codex(NOW, root=self.sessions))[("codex", "5h")]
        self.assertEqual((w["freshness"], w["state"]), ("expired", "unknown"))
        self.assertIsNone(w["pace"])
        self.assertEqual(w["used_percent"], 97.0)  # observed value kept, never zeroed

    def test_reset_edge_equal_to_now_is_expired(self):
        self.rollout("a", [codex_event(NOW - 60, rl(win(10.0, 300, NOW)))])
        w = by_window(headroom.read_codex(NOW, root=self.sessions))[("codex", "5h")]
        self.assertEqual(w["freshness"], "expired")

    def test_old_reading_is_stale_but_keeps_severity(self):
        # 5h window: stale after 75 min. Used % only rises inside a window, so
        # a stale 96% is still at least critical.
        self.rollout(
            "a",
            [
                codex_event(
                    NOW - 80 * 60, rl(win(96.0, 300, NOW + 600), win(30.0, 10080, NOW + 9e4))
                )
            ],
        )
        w = by_window(headroom.read_codex(NOW, root=self.sessions))
        self.assertEqual(
            (w[("codex", "5h")]["freshness"], w[("codex", "5h")]["state"]), ("stale", "critical")
        )
        self.assertEqual(w[("codex", "weekly")]["freshness"], "fresh")  # weekly threshold is 6h

    def test_stale_after_override(self):
        self.rollout("a", [codex_event(NOW - 120, rl(win(10.0, 300, NOW + 600)))])
        w = by_window(headroom.read_codex(NOW, root=self.sessions, stale_after=60))[("codex", "5h")]
        self.assertEqual(w["freshness"], "stale")

    def test_max_files_limits_scan_to_newest(self):
        self.rollout(
            "old", [codex_event(NOW - 9000, rl(win(5.0, 300, NOW + 600)))], mtime=NOW - 9000
        )
        self.rollout(
            "new",
            [codex_event(NOW - 10, rl(None, win(7.0, 10080, NOW + 9000)))],
            mtime=NOW - 10,
            day="2027/01/13",
        )
        p = headroom.read_codex(NOW, root=self.sessions, max_files=1)
        self.assertEqual(list(by_window(p)), [("codex", "weekly")])

    def test_no_codex_install_is_not_configured(self):
        with mock.patch("token_tracker.collectors.find_codex", return_value=None):
            p = headroom.read_codex(NOW)
        self.assertEqual((p["status"], p["windows"]), ("not_configured", []))

    def test_empty_sessions_dir_is_no_data(self):
        self.sessions.mkdir(parents=True)
        self.assertEqual(headroom.read_codex(NOW, root=self.sessions)["status"], "no_data")


# ---------------------------------------------------------------- claude


def statusline_payload(five=23.5, seven=41.2, spend=None, **extra):
    limits = {}
    if five is not None:
        limits["five_hour"] = {"used_percentage": five, "resets_at": int(NOW + 3600)}
    if seven is not None:
        limits["seven_day"] = {"used_percentage": seven, "resets_at": int(NOW + 4 * 86400)}
    if spend is not None:
        limits["spend_limit"] = spend
    body = {
        "session_id": "s1",
        "version": "2.1.300",
        "model": {"display_name": "Opus"},
        "transcript_path": "C:\\Users\\Hacir\\.claude\\projects\\x.jsonl",
        "rate_limits": limits,
    }
    body.update(extra)
    return body


class StatuslineTeeTest(TempCase):
    def run_tee(self, payload, *args, raw=None):
        stdin = io.BytesIO(raw if raw is not None else json.dumps(payload).encode("utf-8"))
        stdout = io.BytesIO()
        code = statusline_tee.main(list(args), stdin=stdin, stdout=stdout, now_iso=iso(NOW - 30))
        return code, stdout.getvalue()

    def test_records_rate_limits_and_prints_summary(self):
        ledger = self.root / "limits" / "claude.jsonl"
        code, out = self.run_tee(statusline_payload(), "--out", str(ledger))
        self.assertEqual(code, 0)
        self.assertEqual(out, b"5h 24% | 7d 41%\n")
        rec = json.loads(ledger.read_text(encoding="utf-8"))
        self.assertEqual(rec["observed_at"], iso(NOW - 30))
        self.assertEqual(rec["session_id"], "s1")
        self.assertEqual(rec["rate_limits"]["five_hour"]["used_percentage"], 23.5)
        self.assertNotIn("transcript_path", rec)  # only limits are kept

    def test_payload_without_rate_limits_records_nothing(self):
        ledger = self.root / "claude.jsonl"
        payload = statusline_payload()
        del payload["rate_limits"]
        code, out = self.run_tee(payload, "--out", str(ledger))
        self.assertEqual((code, out), (0, b""))
        self.assertFalse(ledger.exists())

    def test_malformed_stdin_is_ignored(self):
        ledger = self.root / "claude.jsonl"
        code, _ = self.run_tee(None, "--out", str(ledger), raw=b"{not json")
        self.assertEqual(code, 0)
        self.assertFalse(ledger.exists())

    def test_wrapped_command_gets_stdin_and_output_passes_through_byte_for_byte(self):
        ledger = self.root / "claude.jsonl"
        echo = "import sys; d=sys.stdin.buffer.read(); sys.stdout.buffer.write(b'\\x1b[36mX\\x1b[0m \\xe2\\x96\\x93' + str(len(d)).encode() + b'\\r\\n'); sys.exit(3)"
        payload = statusline_payload()
        raw = json.dumps(payload).encode("utf-8")
        code, out = self.run_tee(payload, "--out", str(ledger), "--", sys.executable, "-c", echo)
        self.assertEqual(code, 3)
        self.assertEqual(out, b"\x1b[36mX\x1b[0m \xe2\x96\x93" + str(len(raw)).encode() + b"\r\n")
        self.assertTrue(ledger.exists())

    def test_wrapped_shell_command(self):
        ledger = self.root / "claude.jsonl"
        code, out = self.run_tee(
            statusline_payload(),
            "--out",
            str(ledger),
            "--command",
            f'"{sys.executable}" -c "print(42)"',
        )
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), b"42")

    def test_unwritable_ledger_never_breaks_the_status_line(self):
        blocker = self.root / "is-a-file"
        blocker.write_text("x")
        ledger = blocker / "claude.jsonl"  # parent is a file -> OSError
        with mock.patch("sys.stderr", new=io.StringIO()):
            code, out = self.run_tee(
                statusline_payload(),
                "--out",
                str(ledger),
                "--",
                sys.executable,
                "-c",
                "print('still here')",
            )
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), b"still here")

    def test_ledger_is_bounded(self):
        ledger = self.root / "claude.jsonl"
        with (
            mock.patch.object(statusline_tee, "MAX_BYTES", 2000),
            mock.patch.object(statusline_tee, "KEEP_LINES", 3),
        ):
            for _ in range(30):
                self.run_tee(statusline_payload(), "--out", str(ledger))
        self.assertLess(len(ledger.read_text(encoding="utf-8").splitlines()), 30)  # truncated
        self.assertLess(ledger.stat().st_size, 2000 + 400)

    def test_windows_default_path_under_localappdata(self):
        env = {"LOCALAPPDATA": r"C:\Users\Hacir\AppData\Local"}
        with (
            mock.patch.object(statusline_tee, "_is_windows", return_value=True),
            mock.patch.dict(os.environ, env, clear=True),
        ):
            got = statusline_tee.claude_ledger_path()
        self.assertEqual(
            PureWindowsPath(str(got)),
            PureWindowsPath(
                r"C:\Users\Hacir\AppData\Local\ai-token-tracker\limits\claude-statusline.jsonl"
            ),
        )

    def test_posix_default_path_under_xdg_state(self):
        with (
            mock.patch.object(statusline_tee, "_is_windows", return_value=False),
            mock.patch.dict(os.environ, {"XDG_STATE_HOME": "/tmp/state"}, clear=True),
        ):
            got = statusline_tee.claude_ledger_path()
        self.assertEqual(got, Path("/tmp/state/ai-token-tracker/limits/claude-statusline.jsonl"))

    def test_env_overrides(self):
        with mock.patch.dict(
            os.environ, {"TOKEN_TRACKER_LIMITS_DIR": r"D:\ai-token-tracker\limits"}, clear=True
        ):
            self.assertEqual(
                PureWindowsPath(str(statusline_tee.claude_ledger_path())),
                PureWindowsPath(r"D:\ai-token-tracker\limits\claude-statusline.jsonl"),
            )
        with mock.patch.dict(
            os.environ, {"TOKEN_TRACKER_CLAUDE_STATUSLINE": str(self.root / "c.jsonl")}, clear=True
        ):
            self.assertEqual(statusline_tee.claude_ledger_path(), self.root / "c.jsonl")

    def test_runs_as_plain_script_from_another_cwd(self):
        ledger = self.root / "claude.jsonl"
        script = PKG_ROOT / "token_tracker" / "statusline_tee.py"
        done = subprocess.run(
            [sys.executable, str(script), "--out", str(ledger)],
            input=json.dumps(statusline_payload()).encode(),
            capture_output=True,
            cwd=str(self.root),
            timeout=60,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), b"5h 24% | 7d 41%")
        self.assertTrue(ledger.exists())


class ClaudeHeadroomTest(TempCase):
    def ledger(self, records, raw_lines=()):
        return write_jsonl(self.root / "claude.jsonl", records, raw_lines)

    def rec(self, observed, **limits):
        return {"observed_at": iso(observed), "session_id": "s", "rate_limits": limits}

    def test_missing_ledger_is_not_configured(self):
        p = headroom.read_claude(NOW, path=self.root / "nope.jsonl")
        self.assertEqual((p["status"], p["windows"]), ("not_configured", []))
        self.assertIn("claude -p", p["detail"])

    def test_latest_per_window_across_sessions(self):
        path = self.ledger(
            [
                self.rec(
                    NOW - 300,
                    five_hour={"used_percentage": 10, "resets_at": NOW + 3600},
                    seven_day={"used_percentage": 50, "resets_at": NOW + 86400},
                ),
                # a later record that lacks seven_day must not erase the earlier one
                self.rec(NOW - 60, five_hour={"used_percentage": 85.5, "resets_at": NOW + 3600}),
            ]
        )
        p = headroom.read_claude(NOW, path=path)
        w = by_window(p)
        self.assertEqual(w[("five_hour", "5h")]["used_percent"], 85.5)
        self.assertEqual(w[("five_hour", "5h")]["state"], "warn")
        self.assertEqual(w[("five_hour", "5h")]["window_minutes"], 300)
        self.assertEqual(w[("seven_day", "weekly")]["used_percent"], 50.0)
        self.assertEqual(w[("seven_day", "weekly")]["as_of"], headroom.iso_utc(NOW - 300))

    def test_spend_limit_in_dollars_when_reported(self):
        path = self.ledger(
            [
                self.rec(
                    NOW - 30,
                    spend_limit={
                        "used_percentage": 62.8,
                        "resets_at": NOW + 86400,
                        "used_usd": 314.12,
                        "limit_usd": 500,
                        "period": "monthly",
                    },
                )
            ]
        )
        w = by_window(headroom.read_claude(NOW, path=path))[("spend_limit", "spend")]
        self.assertEqual(
            (w["unit"], w["used"], w["limit"], w["remaining"]), ("usd", 314.12, 500.0, 185.88)
        )
        self.assertEqual(w["used_percent"], 62.8)
        self.assertEqual(w["reset_interval"], "monthly")

    def test_spend_limit_percent_only(self):
        path = self.ledger(
            [self.rec(NOW - 30, spend_limit={"used_percentage": 12, "resets_at": NOW + 600})]
        )
        w = by_window(headroom.read_claude(NOW, path=path))[("spend_limit", "spend")]
        self.assertEqual((w["unit"], w["used"], w["limit"]), ("percent", 12.0, 100.0))

    def test_malformed_ledger_lines(self):
        path = self.ledger(
            [
                self.rec(NOW - 30, five_hour={"used_percentage": "high"}),
                {"observed_at": "yesterday-ish", "rate_limits": {}},
                {"observed_at": iso(NOW - 20), "rate_limits": "nope"},
            ],
            raw_lines=["not json", "[1,2]"],
        )
        p = headroom.read_claude(NOW, path=path)
        self.assertEqual(p["status"], "no_data")
        self.assertEqual(len(p["warnings"]), 2)

    def test_tee_output_round_trips_into_report(self):
        ledger = self.root / "limits" / "claude.jsonl"
        statusline_tee.main(
            ["--out", str(ledger)],
            stdin=io.BytesIO(json.dumps(statusline_payload(five=97)).encode()),
            stdout=io.BytesIO(),
            now_iso=iso(NOW - 30),
        )
        w = by_window(headroom.read_claude(NOW, path=ledger))
        self.assertEqual(w[("five_hour", "5h")]["state"], "critical")
        self.assertEqual(w[("five_hour", "5h")]["resets_at"], headroom.iso_utc(NOW + 3600))


# ------------------------------------------------------------ openrouter

KEY = "sk-or-v1-SECRET-DO-NOT-LEAK"


def or_body():
    return {
        "data": {
            "label": "sk-or-v1-abc...xyz",
            "limit": 20.0,
            "limit_reset": "monthly",
            "limit_remaining": 5.5,
            "usage": 14.5,
            "is_free_tier": False,
            "effective_budget": {
                "scope": "member",
                "limit_usd": 50,
                "spend_usd": 40,
                "remaining_usd": 10,
                "reset_interval": "weekly",
                "resets_at": "2027-01-18T00:00:00.000Z",
            },
            "free_model_daily_requests": {"used": 990, "limit": 1000, "remaining": 10},
        }
    }


class OpenRouterHeadroomTest(unittest.TestCase):
    def test_no_key_skips_without_network(self):
        fetch = mock.Mock()
        p = headroom.read_openrouter(NOW, env={}, fetch=fetch)
        self.assertEqual(p["status"], "not_configured")
        fetch.assert_not_called()

    def test_offline_skips_even_with_key(self):
        fetch = mock.Mock()
        p = headroom.read_openrouter(
            NOW, env={"OPENROUTER_API_KEY": KEY}, offline=True, fetch=fetch
        )
        self.assertEqual(p["status"], "skipped")
        fetch.assert_not_called()

    def test_windows_from_key_response(self):
        fetch = mock.Mock(return_value=or_body())
        p = headroom.read_openrouter(NOW, env={"OPENROUTER_API_KEY": KEY}, fetch=fetch)
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[0], KEY)
        w = {x["window"]: x for x in p["windows"]}
        self.assertEqual((w["key_credits"]["used"], w["key_credits"]["limit"]), (14.5, 20.0))
        self.assertIsNone(w["key_credits"]["resets_at"])  # not reported: not invented
        self.assertEqual(w["key_credits"]["reset_interval"], "monthly")
        self.assertEqual(w["budget"]["resets_at"], "2027-01-18T00:00:00Z")
        self.assertEqual(w["budget"]["state"], "warn")
        free = w["free_daily_requests"]
        self.assertEqual(
            (free["unit"], free["used"], free["limit"], free["state"]),
            ("requests", 990.0, 1000.0, "critical"),
        )
        self.assertEqual(free["resets_at"], "2027-01-16T00:00:00Z")
        self.assertEqual(free["resets_at_basis"], "documented_utc_day")
        self.assertEqual(free["as_of"], headroom.iso_utc(NOW))

    def test_key_never_in_output_on_success_or_error(self):
        import urllib.error

        ok = headroom.build_report(
            NOW,
            codex_root=Path("/nonexistent-codex"),
            claude_file=Path("/nonexistent"),
            env={"OPENROUTER_API_KEY": KEY},
            fetch=lambda k, t: or_body(),
        )
        self.assertNotIn(KEY, json.dumps(ok))

        def boom(k, t):
            raise urllib.error.HTTPError(
                "https://openrouter.ai/api/v1/key", 401, "Unauthorized", {}, None
            )

        bad = headroom.read_openrouter(NOW, env={"OPENROUTER_API_KEY": KEY}, fetch=boom)
        self.assertEqual(bad["status"], "error")
        self.assertIn("401", bad["detail"])
        self.assertNotIn(KEY, json.dumps(bad))

    def test_network_and_malformed_errors(self):
        import urllib.error

        def offline(k, t):
            raise urllib.error.URLError(OSError("no route"))

        self.assertEqual(
            headroom.read_openrouter(NOW, env={"OPENROUTER_API_KEY": KEY}, fetch=offline)["status"],
            "error",
        )
        self.assertEqual(
            headroom.read_openrouter(
                NOW, env={"OPENROUTER_API_KEY": KEY}, fetch=lambda k, t: ["x"]
            )["status"],
            "error",
        )
        p = headroom.read_openrouter(
            NOW, env={"OPENROUTER_API_KEY": KEY}, fetch=lambda k, t: {"data": {"limit": None}}
        )
        self.assertEqual(p["status"], "no_data")


# --------------------------------------------------------- report + CLI

PROVIDER_KEYS = {"provider", "source", "status", "detail", "location", "windows", "warnings"}
WINDOW_KEYS = {
    "source",
    "window",
    "limit_id",
    "window_minutes",
    "unit",
    "used",
    "limit",
    "remaining",
    "used_percent",
    "resets_at",
    "resets_at_basis",
    "reset_interval",
    "as_of",
    "age_seconds",
    "freshness",
    "state",
    "reached",
    "elapsed_percent",
    "pace",
    "plan_type",
}


class ReportTest(TempCase):
    def fixture(self):
        write_jsonl(
            self.sessions / "2027/01/15/rollout-a.jsonl",
            [codex_event(NOW - 60, rl(win(62.0, 300, NOW + 3600), win(81.0, 10080, NOW + 86400)))],
        )
        ledger = write_jsonl(
            self.root / "claude.jsonl",
            [
                {
                    "observed_at": iso(NOW - 30),
                    "rate_limits": {"five_hour": {"used_percentage": 20, "resets_at": NOW + 600}},
                }
            ],
        )
        return ledger

    def test_schema_shape_is_stable(self):
        ledger = self.fixture()
        report = headroom.build_report(NOW, codex_root=self.sessions, claude_file=ledger, env={})
        self.assertEqual(
            set(report), {"schema", "schema_version", "generated_at", "thresholds", "providers"}
        )
        self.assertEqual(
            (report["schema"], report["schema_version"]), ("ai-token-tracker/headroom", 1)
        )
        self.assertEqual(report["generated_at"], headroom.iso_utc(NOW))
        self.assertEqual(
            [p["provider"] for p in report["providers"]], ["codex", "claude", "openrouter"]
        )
        for p in report["providers"]:
            self.assertEqual(set(p), PROVIDER_KEYS)
            self.assertIn(p["status"], {"ok", "no_data", "not_configured", "skipped", "error"})
            for w in p["windows"]:
                self.assertEqual(set(w), WINDOW_KEYS)
                self.assertIn(w["freshness"], {"fresh", "stale", "expired", "unknown"})
                self.assertIn(w["state"], {"ok", "warn", "critical", "exhausted", "unknown"})
        json.dumps(report, allow_nan=False)  # strict JSON

    def test_one_broken_source_does_not_hide_others(self):
        ledger = self.fixture()
        with mock.patch.object(headroom, "read_claude", side_effect=RuntimeError("disk on fire")):
            report = headroom.build_report(
                NOW, codex_root=self.sessions, claude_file=ledger, env={}
            )
        status = {p["provider"]: p["status"] for p in report["providers"]}
        self.assertEqual(status, {"codex": "ok", "claude": "error", "openrouter": "not_configured"})

    def test_cli_json_and_out_file(self):
        ledger = self.fixture()
        out_file = self.root / "out dir" / "headroom.json"
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENROUTER_API_KEY", None)
            code = cli.main(
                [
                    "headroom",
                    "--json",
                    "--codex-dir",
                    str(self.sessions),
                    "--claude-file",
                    str(ledger),
                    "--out",
                    str(out_file),
                ],
                stdout=buf,
            )
        self.assertEqual(code, 0)
        doc = json.loads(buf.getvalue())
        self.assertEqual(doc["schema_version"], 1)
        self.assertEqual(
            json.loads(out_file.read_text(encoding="utf-8"))["schema"], "ai-token-tracker/headroom"
        )
        self.assertFalse(out_file.with_name("headroom.json.tmp").exists())

    def test_cli_table(self):
        ledger = self.fixture()
        buf = io.StringIO()
        code = cli.main(
            [
                "headroom",
                "--offline",
                "--codex-dir",
                str(self.sessions),
                "--claude-file",
                str(ledger),
            ],
            stdout=buf,
        )
        self.assertEqual(code, 0)
        text = buf.getvalue()
        self.assertIn("PROVIDER", text)
        self.assertIn("62.0%", text)
        self.assertIn("weekly", text)
        self.assertIn("skipped", text)

    def test_table_marks_old_data(self):
        write_jsonl(
            self.sessions / "2027/01/15/rollout-a.jsonl",
            [codex_event(NOW - 7200, rl(win(62.0, 300, NOW - 5)))],
        )
        report = headroom.build_report(
            NOW, codex_root=self.sessions, claude_file=self.root / "x", offline=True
        )
        text = headroom.render_table(report, tz=timezone.utc)
        self.assertIn("unknown (expired)", text)
        self.assertIn("not configured", text)

    def test_cli_rejects_bad_arguments(self):
        with mock.patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["headroom", "--stale-after", "-5"], stdout=io.StringIO())
        self.assertEqual(ctx.exception.code, 2)

    def test_module_entry_point_is_headless(self):
        ledger = self.fixture()
        code = (
            "import sys, runpy\n"
            f"sys.argv = ['token_tracker', 'headroom', '--json', '--offline', '--codex-dir', {str(self.sessions)!r}, "
            f"'--claude-file', {str(ledger)!r}]\n"
            "try:\n"
            "    runpy.run_module('token_tracker', run_name='__main__')\n"
            "except SystemExit as e:\n"
            "    assert e.code in (0, None), e.code\n"
            "assert 'PySide6' not in sys.modules, 'CLI imported PySide6'\n"
            "assert 'token_tracker.gui' not in sys.modules\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(PKG_ROOT),
            capture_output=True,
            timeout=120,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr.decode(errors="replace"))
        self.assertEqual(json.loads(done.stdout)["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
