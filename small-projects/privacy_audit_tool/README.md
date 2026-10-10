# Local Privacy Audit Tool for Windows

A read-only, local-first footprint discovery tool. It does **not** delete accounts, change settings, submit removal requests, or collect passwords.

## What it can scan

- Google Takeout Gmail `.zip` archives
- `.mbox` and `.eml` mail files
- Local Chrome, Edge, Brave, and Firefox browsing history/bookmarks
- A manual list of old usernames
- Optional Maigret username checks, only when explicitly requested

## Privacy model

Default operation is offline. The tool itself makes no web requests.

`--online-usernames` is different: it runs Maigret and contacts external websites. Use it only when you understand that tradeoff.

Never give the tool passwords, 2FA codes, recovery codes, session cookies, or browser cookies.

## Requirements

Python 3.10+ on Windows. No third-party packages are needed for the core scanner.

## Quick start

1. Put old usernames, one per line, in `inputs\\usernames.txt`.
2. Obtain a local Google Takeout Gmail export. Keep the archive private.
3. Run:

```powershell
.\\run_audit.ps1 -Mail 'C:\\Path\\Takeout.zip'
```

To also inspect local browser history/bookmarks:

```powershell
.\\run_audit.ps1 -Mail 'C:\\Path\\Takeout.zip' -Browsers
```

Output appears in `output\\`.

If PowerShell blocks the script (execution policy), use `run_audit.bat` with the same options (`run_audit.bat --mail C:\Path\Takeout.zip --browsers`) or run `powershell -ExecutionPolicy Bypass -File .\run_audit.ps1 ...`.

## Outputs

- `report.html` - readable local report
- `accounts.csv` - evidence for possible accounts/services
- `domains.csv` - domains found and how often they appeared
- `browser.csv` - browser history/bookmark discoveries
- `usernames.txt` - deduplicated supplied + conservatively inferred usernames
- `summary.json` - machine-readable summary
- `warnings.txt` - written only if something could not be read (locked browser DB, bad mail file, etc.)

## Optional online username scan

Install Maigret separately:

```powershell
py -m pip install maigret
```

Then explicitly opt in. **Only usernames you put in your usernames file are sent.** Usernames the tool infers from your mail are never sent, and names that begin with `-` or contain unusual characters are skipped. Usernames go out in batches of 20.

```powershell
.\\run_audit.ps1 -Usernames 'inputs\\usernames.txt' -OnlineUsernames
```

The tool creates `ONLINE_SCAN_WARNING.txt` to make the networked nature explicit.

## Suggested workflow

1. Build your username list.
2. Export Gmail data locally.
3. Run the default offline scan.
4. Review `report.html`.
5. Confirm each candidate manually.
6. Secure needed accounts and delete abandoned ones manually.
7. Only then start data-broker removal.

## Important limitations

This is an evidence finder, not a perfect account detector. A domain in your mail/history may be a newsletter, purchase, login provider, or unrelated link. Always verify candidates before deleting an account.

Browser scanning is best-effort because browsers can keep database files locked. Close the relevant browser before scanning. Read failures are reported in the console, `warnings.txt` and the report; a `Warnings: 0` line means nothing was skipped. Only the 5000 most recent history rows per profile are read, and the HTML report shows the first 5000 candidates / 1000 browser rows (CSVs are complete).

Confidence labels are heuristic: they count distinct signup/security phrases in the message and attribute them to the sender only.

CSV cells that start with `=`, `+`, `-` or `@` are prefixed with an apostrophe so Excel cannot run them as formulas.

Keep the generated reports private: they can contain emails, usernames, URLs, and other sensitive information.
