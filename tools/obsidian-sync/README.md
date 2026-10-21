# tools/obsidian-sync — one-way agent-memory mirror to Obsidian

This folder holds a small documented script that copies canonical agent
knowledge out of this repository into an Obsidian-visible directory in your
existing vault. The repository stays authoritative; the vault holds a
read-only mirror.

- Script: `sync-agent-memory.ps1`
- File list: `sync-filelist.txt` (regenerate when files are added/renamed)
- Mirror: `<vault>/Agent Memory/projects-ai/`
- Hook installer: `install-hook.ps1` (safe best-effort post-commit trigger)

## One-time setup (machine-local, never committed)

From the repository root:

```bat
git config --local obsidian.vaultPath "D:\unorganized\Obsidian\Agentics"
```

The vault path lives only in `.git/config` (local). Tracked files contain
no personal path. The script also accepts `-VaultPath` or the
`OBSIDIAN_VAULT_PATH` environment variable.

## Running the sync

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File tools\obsidian-sync\sync-agent-memory.ps1
```

Or via the wrapper:

```bat
tools\obsidian-sync\sync-now.bat
```

The script:

1. Refuses unless the vault has a `.obsidian` marker directory.
2. Copies every listed text file under `.agents/` and `tasks/`, plus the
   root and product `AGENTS.md` files and copilot instructions.
3. Skips secret-like filenames (`.env`, `*.pem`, keys, credential JSON),
   binary/unsupported extensions, and `tmp-tests/` / `__pycache__/`.
4. Writes `_MIRROR-README.md` (read-only notice) and `SYNC-STATUS.md`.
5. Records `.sync-manifest.txt`; stale cleanup deletes only files the sync
   previously created that no longer exist upstream. Your other vault notes
   are never touched or deleted.

## Refreshing the file list

`sync-filelist.txt` is generated from git-tracked files so the sync itself
never needs to enumerate the tree:

```bat
git ls-files .agents tasks AGENTS.md pi_AGENTS.md agentops/AGENTS.md universal-game-agent/AGENTS.md small-projects/mini-llm/AGENTS.md ai-token-tracker/AGENTS.md qr-generator/AGENTS.md .github/copilot-instructions.md > tools\obsidian-sync\sync-filelist.txt
```

Regenerate it when agent-memory files are added, renamed, or removed.

## Automatic trigger (optional, safe)

Install a local post-commit hook that re-runs the sync best-effort after
each commit. Hook failures never block the commit:

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File tools\obsidian-sync\install-hook.ps1
```

The hook lives in `.git/hooks/` (untracked, machine-local). The canonical
instruction is in `tasks/after-task.md` §4: agents run the sync after
substantial `.agents/` memory or instruction changes.

## Direction of authority

`.agents/` in this repository is authoritative. The Obsidian mirror is a
convenience copy for browsing and linking. Never edit the mirror to change
memory; edit the repository and re-run the sync.
