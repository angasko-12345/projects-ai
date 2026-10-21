# Installs a machine-local post-commit hook that re-runs the Obsidian
# agent-memory mirror best-effort after each commit. Hook failures print a
# warning but never block the commit. The hook is untracked (.git/hooks/).
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$HookDir = Join-Path $RepoRoot ".git\hooks"
$HookPath = Join-Path $HookDir "post-commit"
$lines = @(
  "#!/bin/sh",
  "# Best-effort Obsidian agent-memory mirror (local only, never blocks commit).",
  "# Installed by tools/obsidian-sync/install-hook.ps1.",
  "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$RepoRoot\tools\obsidian-sync\sync-agent-memory.ps1`" >> `"$RepoRoot\.git\obsidian-sync.log`" 2>&1 || echo '[obsidian-sync] mirror failed (non-blocking); run tools\obsidian-sync\sync-now.bat manually' >&2",
  "exit 0"
)
New-Item -ItemType Directory -Force -Path $HookDir | Out-Null
Set-Content -Path $HookPath -Value $lines -Encoding UTF8
Write-Host "Installed post-commit hook: $HookPath"
Write-Host "Vault path source: git config --local obsidian.vaultPath"
