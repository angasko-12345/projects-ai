from __future__ import annotations

import argparse
import csv
import html
import json
import mailbox
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import urlparse

APP_VERSION = '1.1.0'

# Signup/security phrases. Generic single words ('verify', 'registration') are
# deliberately excluded: they match newsletters and marketing mail too often.
SIGNUP_MARKERS = [
    'welcome', 'verify your email', 'confirm your email', 'activate your account',
    'account created', 'thanks for signing up', 'password reset',
    'reset your password', 'security alert', 'new login', 'confirm your account',
    'email verification', 'verify account',
]
IGNORED_DOMAINS = {
    'google.com', 'googleusercontent.com', 'gmail.com', 'googlemail.com',
    'microsoft.com', 'office.com', 'outlook.com', 'live.com', 'windows.com',
    'apple.com', 'icloud.com', 'mozilla.org', 'w3.org', 'schema.org',
    'example.com', 'example.org', 'example.net',
}

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
USERNAME_RE = re.compile(r"(?<![A-Za-z0-9_@.-])([A-Za-z][A-Za-z0-9._-]{2,31})(?![A-Za-z0-9_@.-])")
# Usernames we are willing to pass to an external tool: no leading '-', no spaces.
SAFE_USERNAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')

MAX_HTML_CANDIDATES = 5000
MAX_HTML_BROWSER = 1000
CHROMIUM_HISTORY_LIMIT = 5000

WARNINGS: list[str] = []
_MAX_STORED_WARNINGS = 500
_MAX_PRINTED_WARNINGS = 25
_warn_total = 0


def warn(msg: str) -> None:
    """Record a non-fatal problem so it shows up in the output instead of vanishing."""
    global _warn_total
    _warn_total += 1
    if len(WARNINGS) < _MAX_STORED_WARNINGS:
        WARNINGS.append(msg)
    if _warn_total <= _MAX_PRINTED_WARNINGS:
        print(f'WARNING: {msg}', file=sys.stderr)


@dataclass
class AccountCandidate:
    domain: str
    evidence_type: str
    evidence: str
    source: str
    confidence: str
    email: str = ''
    username: str = ''


@dataclass
class BrowserRecord:
    browser: str
    kind: str
    title: str
    url: str
    last_visit: str
    source_file: str


def norm_domain(value: str) -> str:
    value = value.strip().lower().rstrip('.')
    if value.startswith('www.'):
        value = value[4:]
    return value


def domain_from_email(value: str) -> str:
    m = EMAIL_RE.search(value or '')
    return norm_domain(m.group(0).split('@', 1)[1]) if m else ''


def domain_from_url(value: str) -> str:
    try:
        host = (urlparse(value).hostname or '').lower()
        return norm_domain(host)
    except Exception:
        return ''


def should_keep_domain(domain: str) -> bool:
    if not domain or '.' not in domain:
        return False
    return domain not in IGNORED_DOMAINS


def extract_text(msg) -> str:
    parts = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() in {'text/plain', 'text/html'}:
                try:
                    payload = part.get_content()
                except Exception:
                    payload = part.get_payload(decode=True) or b''
                    if isinstance(payload, bytes):
                        payload = payload.decode('utf-8', errors='replace')
                parts.append(str(payload))
    else:
        try:
            parts.append(msg.get_content())
        except Exception:
            payload = msg.get_payload(decode=True) or b''
            if isinstance(payload, bytes):
                payload = payload.decode('utf-8', errors='replace')
            parts.append(str(payload))
    return '\n'.join(parts)


def parse_message_bytes(raw: bytes, source: str, candidates: list[AccountCandidate], domains: Counter) -> bool:
    """Parse one message. Returns False if it could not be parsed."""
    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        headers = '\n'.join(str(msg.get(h, '')) for h in ('From', 'To', 'Cc', 'Reply-To', 'Subject'))
        body = extract_text(msg)
    except Exception:
        return False
    blob = (headers + '\n' + body).lower()

    for e in EMAIL_RE.findall(headers):
        d = domain_from_email(e)
        if should_keep_domain(d):
            domains[d] += 1
            candidates.append(AccountCandidate(
                domain=d, evidence_type='email-header', evidence=e, source=source,
                confidence='medium', email=e.lower()
            ))

    for url in URL_RE.findall(body + '\n' + headers):
        d = domain_from_url(url)
        if should_keep_domain(d):
            domains[d] += 1
            candidates.append(AccountCandidate(
                domain=d, evidence_type='url', evidence=url[:500], source=source,
                confidence='low'
            ))

    # Count DISTINCT markers (repeating one word must not inflate confidence) and
    # attribute them only to the sender, not to every recipient/cc address.
    hits = sorted(mk for mk in SIGNUP_MARKERS if mk in blob)
    if hits:
        sender = str(msg.get('From', '')) + ' ' + str(msg.get('Reply-To', ''))
        for e in set(EMAIL_RE.findall(sender)):
            d = domain_from_email(e)
            if should_keep_domain(d):
                candidates.append(AccountCandidate(
                    domain=d, evidence_type='signup-marker',
                    evidence=f'{len(hits)} distinct marker(s): {", ".join(hits)}'[:500],
                    source=source,
                    confidence='high' if len(hits) >= 2 else 'medium', email=e.lower()
                ))
    return True


def scan_mbox(path: Path, candidates: list[AccountCandidate], domains: Counter):
    failed = 0
    try:
        box = mailbox.mbox(str(path), factory=None, create=False)
        try:
            for key in box.iterkeys():
                try:
                    msg = box.get_message(key)
                    if not parse_message_bytes(msg.as_bytes(policy=policy.default), str(path), candidates, domains):
                        failed += 1
                except Exception:
                    failed += 1
        finally:
            box.close()
    except Exception as e:
        warn(f'could not read mbox {path.name}: {e}')
    if failed:
        warn(f'{failed} message(s) in {path.name} could not be parsed and were skipped')


def scan_eml(path: Path, candidates: list[AccountCandidate], domains: Counter):
    try:
        ok = parse_message_bytes(path.read_bytes(), str(path), candidates, domains)
    except Exception as e:
        warn(f'could not read {path}: {e}')
        return
    if not ok:
        warn(f'could not parse {path}')


def scan_takeout_zip(path: Path, candidates: list[AccountCandidate], domains: Counter):
    found = 0
    with tempfile.TemporaryDirectory(prefix='privacy_audit_') as td:
        temp = Path(td)
        try:
            with zipfile.ZipFile(path) as z:
                members = [n for n in z.namelist() if n.lower().endswith(('.mbox', '.eml'))]
                for i, name in enumerate(members):
                    found += 1
                    # Only mail files are extracted, into a disposable temp dir. A per-member
                    # index prevents same-named files from overwriting each other.
                    target = temp / f'{i}_{Path(name).name}'
                    with z.open(name) as src, open(target, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                    if target.suffix.lower() == '.mbox':
                        scan_mbox(target, candidates, domains)
                    else:
                        scan_eml(target, candidates, domains)
                    target.unlink(missing_ok=True)
        except zipfile.BadZipFile:
            warn(f'{path.name} is not a valid zip file')
        except Exception as e:
            warn(f'error while reading {path.name}: {e}')
    if found == 0:
        warn(f'no .mbox or .eml files found inside {path.name}')


def scan_mail_tree(root: Path, candidates: list[AccountCandidate], domains: Counter):
    if not root.exists():
        warn(f'mail path does not exist: {root}')
        return
    if root.is_file():
        suffix = root.suffix.lower()
        if suffix == '.mbox':
            scan_mbox(root, candidates, domains)
        elif suffix == '.eml':
            scan_eml(root, candidates, domains)
        elif suffix == '.zip':
            scan_takeout_zip(root, candidates, domains)
        else:
            warn(f'unsupported mail file type: {root.name}')
        return
    for p in root.rglob('*'):
        if p.is_file():
            if p.suffix.lower() in {'.mbox', '.eml'}:
                scan_mbox(p, candidates, domains) if p.suffix.lower() == '.mbox' else scan_eml(p, candidates, domains)
            elif p.suffix.lower() == '.zip' and 'takeout' in p.name.lower():
                scan_takeout_zip(p, candidates, domains)


def chrome_epoch_to_iso(value):
    try:
        # Chrome/Edge timestamps: microseconds since 1601-01-01 UTC.
        ts = value / 1_000_000 - 11644473600
        if ts <= 0:
            return ''
        return datetime.fromtimestamp(ts).isoformat(timespec='seconds')
    except Exception:
        return ''


def firefox_epoch_to_iso(value):
    try:
        ts = value / 1_000_000
        if ts <= 0:
            return ''
        return datetime.fromtimestamp(ts).isoformat(timespec='seconds')
    except Exception:
        return ''


def sqlite_copy(path: Path) -> Path | None:
    td = None
    try:
        td = Path(tempfile.mkdtemp(prefix='privacy_audit_sqlite_'))
        dst = td / path.name
        shutil.copy2(path, dst)
        return dst
    except Exception as e:
        if td:
            shutil.rmtree(td, ignore_errors=True)
        warn(f'could not copy {path} (browser open/locked? close it and retry): {e}')
        return None


def _query_history(path: Path, browser: str, sql: str, to_iso, records: list[BrowserRecord]):
    copy = sqlite_copy(path)
    if not copy:
        return
    try:
        # as_uri() percent-encodes '#', '%', spaces and handles Windows drive letters.
        con = sqlite3.connect(copy.as_uri() + '?mode=ro', uri=True)
        try:
            for url, title, ts in con.execute(sql).fetchall():
                records.append(BrowserRecord(browser, 'history', title or '', url or '', to_iso(ts), str(path)))
        finally:
            con.close()
    except Exception as e:
        warn(f'could not read {browser} history {path}: {e}')
    finally:
        shutil.rmtree(copy.parent, ignore_errors=True)


def scan_chromium_history(path: Path, browser: str, records: list[BrowserRecord]):
    _query_history(
        path, browser,
        f'SELECT url, title, last_visit_time FROM urls ORDER BY last_visit_time DESC LIMIT {CHROMIUM_HISTORY_LIMIT}',
        chrome_epoch_to_iso, records)


def scan_firefox_history(path: Path, browser: str, records: list[BrowserRecord]):
    _query_history(
        path, browser,
        'SELECT url, title, last_visit_date FROM moz_places WHERE last_visit_date IS NOT NULL '
        f'ORDER BY last_visit_date DESC LIMIT {CHROMIUM_HISTORY_LIMIT}',
        firefox_epoch_to_iso, records)


def scan_chromium_bookmarks(path: Path, browser: str, records: list[BrowserRecord]):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        warn(f'could not read {browser} bookmarks {path}: {e}')
        return

    def walk(node):
        if isinstance(node, dict):
            if node.get('type') == 'url':
                records.append(BrowserRecord(browser, 'bookmark', node.get('name', ''), node.get('url', ''), '', str(path)))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(data)


def find_browser_files():
    home = Path(os.environ.get('USERPROFILE', Path.home()))
    out = []
    local = Path(os.environ.get('LOCALAPPDATA', home / 'AppData/Local'))
    roam = Path(os.environ.get('APPDATA', home / 'AppData/Roaming'))
    chrome = local / 'Google/Chrome/User Data'
    edge = local / 'Microsoft/Edge/User Data'
    brave = local / 'BraveSoftware/Brave-Browser/User Data'
    for base, name in [(chrome, 'Chrome'), (edge, 'Edge'), (brave, 'Brave')]:
        if base.exists():
            for profile in [base / 'Default', *base.glob('Profile *')]:
                if profile.exists():
                    for hist in [profile / 'History', profile / 'Bookmarks']:
                        if hist.exists():
                            out.append((name, hist))
    firefox = roam / 'Mozilla/Firefox/Profiles'
    if firefox.exists():
        for profile in firefox.iterdir():
            if profile.is_dir():
                hist = profile / 'places.sqlite'
                if hist.exists():
                    out.append(('Firefox', hist))
    return out


def scan_usernames(path: Path):
    values = []
    if not path.exists():
        warn(f'usernames file not found: {path}')
        return values
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        v = line.strip()
        if v and not v.startswith('#'):
            values.append(v)
    return sorted(set(values), key=str.lower)


def extract_username_candidates(text: str):
    # Conservative: keep tokens containing a digit or underscore, and drop
    # domain-like ('a.com') and version-like ('v2.0') tokens.
    vals = set()
    for m in USERNAME_RE.finditer(text):
        token = m.group(1)
        if not any(ch in token for ch in '_0123456789'):
            continue
        if re.fullmatch(r'[vV]?\d+(\.\d+)+', token) or re.search(r'\.[A-Za-z]{2,}$', token):
            continue
        if token.lower() in {'subject', 'account', 'security'}:
            continue
        vals.add(token)
    return sorted(vals, key=str.lower)


def merge_candidates(items: list[AccountCandidate]):
    seen = set()
    out = []
    for x in items:
        key = (x.domain, x.evidence_type, x.evidence, x.email, x.username)
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


def csv_safe(value):
    """Neutralise spreadsheet formula injection (=, +, -, @, tab, CR at cell start)."""
    if isinstance(value, str) and value and value[0] in '=+-@\t\r':
        return "'" + value
    return value


def write_csv(path: Path, rows, fieldnames):
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            d = asdict(r) if hasattr(r, '__dataclass_fields__') else r
            w.writerow({k: csv_safe(v) for k, v in d.items()})


def is_http_url(url: str) -> bool:
    return url.strip().lower().startswith(('http://', 'https://'))


def write_html(path: Path, summary, candidates, domains, browser_records, usernames, warnings=()):
    esc = html.escape
    rows = []
    for c in candidates[:MAX_HTML_CANDIDATES]:
        rows.append(f'<tr><td>{esc(c.domain)}</td><td>{esc(c.evidence_type)}</td><td>{esc(c.confidence)}</td><td>{esc(c.email)}</td><td>{esc(c.username)}</td><td>{esc(c.evidence)}</td><td>{esc(c.source)}</td></tr>')
    brow = []
    for r in browser_records[:MAX_HTML_BROWSER]:
        if is_http_url(r.url):
            link = f'<a href="{esc(r.url)}" rel="noreferrer noopener">{esc(r.url[:250])}</a>'
        else:
            link = esc(r.url[:250])  # never emit javascript:/data: links
        brow.append(f'<tr><td>{esc(r.browser)}</td><td>{esc(r.kind)}</td><td>{esc(r.title[:200])}</td><td>{link}</td><td>{esc(r.last_visit)}</td></tr>')
    dom = []
    for d, n in sorted(domains.items(), key=lambda kv: (-kv[1], kv[0])):
        dom.append(f'<tr><td>{esc(d)}</td><td>{n}</td></tr>')
    user_rows = ''.join(f'<li><code>{esc(u)}</code></li>' for u in usernames)
    trunc = ''
    if len(candidates) > MAX_HTML_CANDIDATES:
        trunc += f'<p><em>Showing first {MAX_HTML_CANDIDATES} of {len(candidates)} candidates; see accounts.csv for all.</em></p>'
    if len(browser_records) > MAX_HTML_BROWSER:
        trunc += f'<p><em>Showing first {MAX_HTML_BROWSER} of {len(browser_records)} browser records; see browser.csv for all.</em></p>'
    warn_html = ''
    if warnings:
        warn_html = '<h2>Warnings</h2><div class="note"><p>Some inputs could not be read. Results may be incomplete.</p><ul>' + ''.join(f'<li>{esc(w)}</li>' for w in warnings) + '</ul></div>'
    html_doc = f'''<!doctype html><html><head><meta charset="utf-8"><title>Privacy Audit Report</title>
<style>body{{font-family:system-ui,Segoe UI,Arial;margin:32px;line-height:1.45}}table{{border-collapse:collapse;width:100%;margin:12px 0 28px}}th,td{{border:1px solid #ccc;padding:6px;text-align:left;vertical-align:top}}th{{background:#eee}}code{{background:#f3f3f3;padding:2px 4px}}.note{{padding:12px;border-left:4px solid #555;background:#f7f7f7}}small{{color:#666}}</style></head><body>
<h1>Local Privacy Audit</h1><p>Generated {esc(datetime.now().isoformat(timespec='seconds'))}. Tool version {APP_VERSION}.</p>
<div class="note"><strong>Read-only:</strong> This report records discoveries only. It does not log into services, delete accounts, change settings, or submit removal requests.</div>
{warn_html}
<h2>Summary</h2><ul><li>Unique domains: {summary['unique_domains']}</li><li>Account candidates: {summary['account_candidates']}</li><li>Browser records: {summary['browser_records']}</li><li>Usernames: {summary['usernames']}</li><li>Warnings: {summary['warnings']}</li></ul>
<h2>Usernames</h2><ul>{user_rows}</ul>
<h2>Domains</h2><table><tr><th>Domain</th><th>Evidence count</th></tr>{''.join(dom)}</table>
<h2>Account candidates</h2>{trunc if candidates else ''}<table><tr><th>Domain</th><th>Evidence</th><th>Confidence</th><th>Email</th><th>Username</th><th>Details</th><th>Source</th></tr>{''.join(rows)}</table>
<h2>Browser discoveries</h2><table><tr><th>Browser</th><th>Type</th><th>Title</th><th>URL</th><th>Last visit</th></tr>{''.join(brow)}</table>
<p><small>Keep this report private. It may contain emails, usernames, URLs, and other sensitive personal data.</small></p></body></html>'''
    path.write_text(html_doc, encoding='utf-8')


def build_maigret_cmd(exe: str, batch: list[str], output_dir: Path) -> list[str]:
    # '--' ends option parsing so a username can never be read as a flag.
    return [exe, '--html', '--folderoutput', str(output_dir / 'maigret'), '--', *batch]


def run_maigret(usernames, output_dir: Path, batch_size: int = 20, timeout: int = 900):
    """Run Maigret on SUPPLIED usernames only, in batches. CONTACTS EXTERNAL WEBSITES."""
    if not usernames:
        return 'No usernames supplied; nothing was sent.'
    exe = shutil.which('maigret')
    if not exe:
        return 'Maigret not found. Install separately with: py -m pip install maigret'
    valid = []
    for u in usernames:
        if SAFE_USERNAME_RE.match(u):
            valid.append(u)
        else:
            warn(f'skipped username not safe to pass to Maigret: {u!r}')
    if not valid:
        return 'No valid usernames; nothing was sent.'
    log, summary = [], []
    for n, i in enumerate(range(0, len(valid), batch_size), start=1):
        batch = valid[i:i + batch_size]
        try:
            p = subprocess.run(build_maigret_cmd(exe, batch, output_dir), capture_output=True,
                               text=True, encoding='utf-8', errors='replace', timeout=timeout)
            log.append(f'--- batch {n}: {", ".join(batch)} ---\n{p.stdout}\n{p.stderr}\n')
            summary.append(f'batch {n} ({len(batch)} usernames): exit code {p.returncode}')
            if p.returncode != 0:
                warn(f'Maigret batch {n} exited with code {p.returncode}')
        except subprocess.TimeoutExpired:
            summary.append(f'batch {n}: timed out after {timeout}s')
            warn(f'Maigret batch {n} timed out after {timeout}s')
        except Exception as e:
            summary.append(f'batch {n}: failed: {e}')
            warn(f'Maigret batch {n} failed: {e}')
    (output_dir / 'maigret_stdout.txt').write_text('\n'.join(log), encoding='utf-8')
    return '\n'.join(summary)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Local, read-only privacy footprint audit for Windows.')
    parser.add_argument('--mail', type=Path, help='MBOX/EML file, folder, or Google Takeout ZIP to scan.')
    parser.add_argument('--usernames', type=Path, default=Path('inputs/usernames.txt'), help='Text file containing usernames, one per line.')
    parser.add_argument('--browsers', action='store_true', help='Scan local Chrome/Edge/Brave/Firefox history and bookmarks.')
    parser.add_argument('--online-usernames', action='store_true', help='Run Maigret against the usernames YOU supplied in --usernames (not inferred ones). THIS CONTACTS EXTERNAL WEBSITES.')
    parser.add_argument('--out', type=Path, default=Path('output'), help='Output directory.')
    args = parser.parse_args(argv)

    global _warn_total
    WARNINGS.clear()
    _warn_total = 0
    args.out.mkdir(parents=True, exist_ok=True)
    candidates: list[AccountCandidate] = []
    domains = Counter()
    browser_records: list[BrowserRecord] = []

    supplied = scan_usernames(args.usernames)
    usernames = list(supplied)
    if args.mail:
        scan_mail_tree(args.mail, candidates, domains)
        found_text = '\n'.join(f'{c.email} {c.evidence}' for c in candidates)
        usernames = sorted(set(usernames) | set(extract_username_candidates(found_text)), key=str.lower)

    if args.browsers:
        files = find_browser_files()
        if not files:
            warn('no Chrome/Edge/Brave/Firefox profiles found')
        for browser, path in files:
            if browser == 'Firefox':
                scan_firefox_history(path, browser, browser_records)
            elif path.name == 'History':
                scan_chromium_history(path, browser, browser_records)
            elif path.name == 'Bookmarks':
                scan_chromium_bookmarks(path, browser, browser_records)
        if files and not browser_records:
            warn('browser scan returned 0 records; close the browsers and retry')
        for r in browser_records:
            d = domain_from_url(r.url)
            if should_keep_domain(d):
                domains[d] += 1

    candidates = merge_candidates(candidates)
    for r in browser_records:
        d = domain_from_url(r.url)
        if should_keep_domain(d) and any(k in (r.url + ' ' + r.title).lower() for k in ['login', 'signin', 'account', 'register', 'signup', 'auth']):
            candidates.append(AccountCandidate(d, 'browser-url', r.url[:500], r.source_file, 'low'))
    candidates = merge_candidates(candidates)

    if args.online_usernames:
        # Written BEFORE the scan so the warning exists even if the run is interrupted.
        (args.out / 'ONLINE_SCAN_WARNING.txt').write_text(
            'Online username scanning was explicitly requested.\n'
            'This contacts external websites and therefore is NOT offline-only.\n'
            'Only usernames from your --usernames file were sent:\n'
            + '\n'.join(supplied) + '\n', encoding='utf-8')
        print(f'Online scan: sending {len(supplied)} supplied username(s) to external sites via Maigret')
        note = run_maigret(supplied, args.out)
        with (args.out / 'ONLINE_SCAN_WARNING.txt').open('a', encoding='utf-8') as f:
            f.write('\nResult:\n' + str(note) + '\n')

    write_csv(args.out / 'accounts.csv', candidates, ['domain', 'evidence_type', 'evidence', 'source', 'confidence', 'email', 'username'])
    write_csv(args.out / 'domains.csv', [{'domain': d, 'count': n} for d, n in sorted(domains.items(), key=lambda kv: (-kv[1], kv[0]))], ['domain', 'count'])
    write_csv(args.out / 'browser.csv', browser_records, ['browser', 'kind', 'title', 'url', 'last_visit', 'source_file'])
    (args.out / 'usernames.txt').write_text('\n'.join(usernames) + ('\n' if usernames else ''), encoding='utf-8')

    summary = {
        'unique_domains': len(domains),
        'account_candidates': len(candidates),
        'browser_records': len(browser_records),
        'usernames': len(usernames),
        'warnings': _warn_total,
    }
    (args.out / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    if WARNINGS:
        extra = f'\n(+{_warn_total - len(WARNINGS)} more not stored)' if _warn_total > len(WARNINGS) else ''
        (args.out / 'warnings.txt').write_text('\n'.join(WARNINGS) + extra + '\n', encoding='utf-8')
    write_html(args.out / 'report.html', summary, candidates, domains, browser_records, usernames, WARNINGS)

    print(f'Privacy Audit {APP_VERSION}')
    print(f'Unique domains:      {len(domains)}')
    print(f'Account candidates:  {len(candidates)}')
    print(f'Browser records:     {len(browser_records)}')
    print(f'Usernames:           {len(usernames)}')
    print(f'Warnings:            {_warn_total}' + ('  (see warnings.txt; results may be incomplete)' if _warn_total else ''))
    print(f'Report:              {args.out / "report.html"}')
    print('Read-only: no accounts or files outside the output directory were modified.')


if __name__ == '__main__':
    main()
