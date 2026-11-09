import json
import tempfile
import unittest
from pathlib import Path

from claude_eyes import cli, core

# Shape captured from a real Claude Code v2.1.319 statusline payload (2026-11-09), numbers only.
REAL = {"rate_limits": {"five_hour": {"used_percentage": 95, "resets_at": 1794206400},
                        "seven_day": {"used_percentage": 41, "resets_at": 1794682800}}}
NOW = 1794206400 - 3600


class ParseTests(unittest.TestCase):
    def test_real_payload(self):
        s, w = core.parse_rate_limits(REAL)
        self.assertEqual((s.used_pct, s.resets_at), (95.0, 1794206400))
        self.assertEqual((w.used_pct, w.resets_at), (41.0, 1794682800))

    def test_missing_rate_limits(self):
        for payload in ({}, {"rate_limits": None}, {"rate_limits": "x"}, None):
            s, w = core.parse_rate_limits(payload)
            self.assertIsNone(s.used_pct)
            self.assertIsNone(w.used_pct)

    def test_one_window_dropped(self):
        s, w = core.parse_rate_limits({"rate_limits": {"seven_day": {"used_percentage": 12.5, "resets_at": 1}}})
        self.assertIsNone(s.used_pct)
        self.assertEqual(w.used_pct, 12.5)

    def test_bad_types(self):
        s, _ = core.parse_rate_limits({"rate_limits": {"five_hour": {"used_percentage": "95", "resets_at": True}}})
        self.assertIsNone(s.used_pct)
        self.assertIsNone(s.resets_at)


class FormatTests(unittest.TestCase):
    def test_et_times(self):
        self.assertEqual(core.fmt_et(1794206400), "Mon 2026-11-09 01:40 ET")
        self.assertEqual(core.fmt_et(1794682800), "Sat 2026-11-14 14:00 ET")
        self.assertEqual(core.fmt_et(None), "UNKNOWN")

    def test_pct(self):
        self.assertEqual(core.fmt_pct(95.0), "95%")
        self.assertEqual(core.fmt_pct(23.5), "23.5%")


class AlertTests(unittest.TestCase):
    def snap(self, s, w, warnings=()):
        return core.Snapshot(core.Window(s, 1), core.Window(w, 1), NOW, list(warnings))

    def test_thresholds_inclusive(self):
        self.assertEqual(core.alerts(self.snap(79.9, 69.9)), [])
        self.assertEqual(len(core.alerts(self.snap(80, 10))), 1)
        self.assertEqual(len(core.alerts(self.snap(10, 70))), 1)
        self.assertEqual(len(core.alerts(self.snap(None, None, ["You'll run out soon"]))), 1)

    def test_render_alert_on_top(self):
        s, w = core.parse_rate_limits(REAL)
        md = core.render(core.Snapshot(s, w, NOW), NOW, "probe-box")
        first = md.splitlines()[0]
        self.assertTrue(first.startswith("**ALERT"))
        self.assertIn("session 95%", first)
        self.assertNotIn("weekly", first)
        self.assertIn("| Current session (5-hour) | 95% | Mon 2026-11-09 01:40 ET |", md)
        self.assertIn("| Weekly — all models (7-day) | 41% | Sat 2026-11-14 14:00 ET |", md)

    def test_render_no_alert(self):
        md = core.render(self.snap(10, 10), NOW, "x")
        self.assertTrue(md.startswith("# Claude usage"))

    def test_render_unknown_and_stale(self):
        md = core.render(core.Snapshot(), NOW, "x")
        self.assertIn("| Current session (5-hour) | UNKNOWN | UNKNOWN |", md)
        self.assertIn("_Data captured:_ UNKNOWN", md)
        md = core.render(self.snap(1, 1), NOW + 4 * 3600, "x")
        self.assertIn("STALE", md)

    def test_render_warning_verbatim(self):
        md = core.render(self.snap(50, 50, ["You'll run out of usage by 3pm"]), NOW, "x")
        self.assertIn("> You'll run out of usage by 3pm", md)
        self.assertTrue(md.startswith("**ALERT"))


class WarningTests(unittest.TestCase):
    def test_finds_warning_lines(self):
        screen = ("❯ reply with just: ok\n● ok\n"
                  "  Approaching usage limit · resets 1:40am\n"
                  "  You've used 95% of your session limit\n"
                  "  You'll run out of usage before your limit resets\n"
                  "  Approaching usage limit · resets 1:40am\n")
        self.assertEqual(core.find_warnings(screen), [
            "Approaching usage limit · resets 1:40am",
            "You've used 95% of your session limit",
            "You'll run out of usage before your limit resets",
        ])

    def test_ignores_normal_screen(self):
        screen = ("Claude Code v2.1.319\nOpus 5.5 is now your default model and it draws down usage faster "
                  "than Sonnet 5.\n❯ reply with just: ok\n● ok\n5h 95% · 7d 41%\n")
        self.assertEqual(core.find_warnings(screen), [])


class CacheTests(unittest.TestCase):
    def test_cache_stores_numbers_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "latest.json"
            payload = dict(REAL, session_id="abc", transcript_path="/secret/path", cwd="/x")
            self.assertTrue(cli.save_cache(payload, p))
            raw = json.loads(p.read_text())
            self.assertEqual(set(raw), {"captured_at", "five_hour", "seven_day"})
            self.assertNotIn("secret", p.read_text())
            snap = cli.load_cache(p)
            self.assertEqual(snap.session.used_pct, 95.0)
            self.assertEqual(snap.weekly.resets_at, 1794682800)

    def test_no_rate_limits_keeps_old_cache(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "latest.json"
            cli.save_cache(REAL, p)
            before = p.read_text()
            self.assertFalse(cli.save_cache({"model": {}}, p))
            self.assertEqual(p.read_text(), before)


if __name__ == "__main__":
    unittest.main()
