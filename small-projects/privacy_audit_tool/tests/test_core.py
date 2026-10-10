import json
import sqlite3
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from privacy_audit import main as m
from privacy_audit.main import (
    domain_from_email,
    domain_from_url,
    extract_username_candidates,
    parse_message_bytes,
)


class CoreTests(unittest.TestCase):
    def test_domains(self):
        self.assertEqual(domain_from_email('x@www.testsite.com'), 'testsite.com')
        self.assertEqual(domain_from_url('https://www.testsite.com/a'), 'testsite.com')

    def test_usernames(self):
        vals = extract_username_candidates('hello old_username_2019 and gamer-name42')
        self.assertIn('old_username_2019', vals)
        self.assertIn('gamer-name42', vals)

    def test_username_noise_filtered(self):
        vals = extract_username_candidates('see a.com v2.0 e-mail user_123')
        self.assertEqual(vals, ['user_123'])

    def test_message_parsing(self):
        raw = (
            b'Subject: Welcome - verify your email\n'
            b'From: site@testsite.com\n'
            b'To: user@gmail.com\n\n'
            b'Welcome! Verify your email at https://testsite.com/verify'
        )
        c, d = [], Counter()
        parse_message_bytes(raw, 'test.eml', c, d)
        self.assertIn('testsite.com', d)
        self.assertTrue(any(x.evidence_type == 'signup-marker' for x in c))

    def test_repeated_word_does_not_inflate_confidence(self):
        raw = b'Subject: hi\nFrom: a@shop.com\nTo: me@gmail.com\n\nverify verify verify'
        c, d = [], Counter()
        parse_message_bytes(raw, 't', c, d)
        self.assertFalse(any(x.evidence_type == 'signup-marker' for x in c))

    def test_marker_attributed_to_sender_only(self):
        raw = (b'Subject: Welcome\nFrom: a@shop.com\nTo: friend@other.org\n\n'
               b'thanks for signing up')
        c, d = [], Counter()
        parse_message_bytes(raw, 't', c, d)
        doms = {x.domain for x in c if x.evidence_type == 'signup-marker'}
        self.assertEqual(doms, {'shop.com'})


class SafetyTests(unittest.TestCase):
    def test_javascript_url_not_linked(self):
        with tempfile.TemporaryDirectory() as td:
            rec = [m.BrowserRecord('Chrome', 'history', 't', 'javascript:alert(1)', '', 'x'),
                   m.BrowserRecord('Chrome', 'history', 't', 'https://ok.example.net/a', '', 'x')]
            s = {'unique_domains': 0, 'account_candidates': 0, 'browser_records': 2, 'usernames': 0, 'warnings': 0}
            p = Path(td) / 'r.html'
            m.write_html(p, s, [], Counter(), rec, [])
            text = p.read_text()
            self.assertNotIn('href="javascript:', text)
            self.assertIn('href="https://ok.example.net/a"', text)

    def test_csv_formula_neutralised(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'b.csv'
            rec = [m.BrowserRecord('Chrome', 'history', '=HYPERLINK("x")', 'https://a.b', '', 'f')]
            m.write_csv(p, rec, ['browser', 'kind', 'title', 'url', 'last_visit', 'source_file'])
            self.assertIn("'=HYPERLINK", p.read_text(encoding='utf-8-sig'))

    def test_maigret_cmd_uses_double_dash(self):
        cmd = m.build_maigret_cmd('maigret', ['-evil', 'ok'], Path('out'))
        self.assertEqual(cmd[cmd.index('--') + 1:], ['-evil', 'ok'])
        self.assertFalse(m.SAFE_USERNAME_RE.match('-evil'))
        self.assertTrue(m.SAFE_USERNAME_RE.match('gamer-name42'))

    def test_maigret_gets_only_supplied_usernames(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / 'u.txt').write_text('mine_2019\n')
            (td / 'a.eml').write_bytes(b'Subject: Welcome\nFrom: x@site.com\n\ninferred_name_77 here')
            sent = []
            orig = m.run_maigret
            m.run_maigret = lambda names, out, **k: sent.append(list(names)) or 'stub'
            try:
                m.main(['--mail', str(td / 'a.eml'), '--usernames', str(td / 'u.txt'),
                        '--out', str(td / 'out'), '--online-usernames'])
            finally:
                m.run_maigret = orig
            self.assertEqual(sent, [['mine_2019']])


class RobustnessTests(unittest.TestCase):
    def test_sqlite_path_with_special_chars(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / 'a#b %c'
            d.mkdir()
            db = d / 'History'
            con = sqlite3.connect(db)
            con.execute('CREATE TABLE urls (url TEXT, title TEXT, last_visit_time INTEGER)')
            con.execute("INSERT INTO urls VALUES ('https://x.io','X',13300000000000000)")
            con.commit(); con.close()
            recs = []
            m.scan_chromium_history(db, 'Chrome', recs)
            self.assertEqual(len(recs), 1)

    def test_failures_are_reported(self):
        m.WARNINGS.clear()
        m.scan_chromium_history(Path('/nonexistent/History'), 'Chrome', [])
        m.scan_takeout_zip(Path(__file__), [], Counter())
        self.assertGreaterEqual(len(m.WARNINGS), 2)

    def test_end_to_end_writes_outputs_and_warnings(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / 'a.eml').write_bytes(b'Subject: Welcome\nFrom: x@site.com\n\nhello https://site.com/a')
            out = td / 'out'
            m.main(['--mail', str(td / 'a.eml'), '--usernames', str(td / 'missing.txt'), '--out', str(out)])
            for f in ('report.html', 'accounts.csv', 'domains.csv', 'browser.csv', 'usernames.txt', 'summary.json', 'warnings.txt'):
                self.assertTrue((out / f).exists(), f)
            self.assertGreaterEqual(json.loads((out / 'summary.json').read_text())['warnings'], 1)


if __name__ == '__main__':
    unittest.main()
