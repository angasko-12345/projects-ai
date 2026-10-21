# Part 1 of 4: params, env, repo root (NO git calls here).
[CmdletBinding()]
param([string]$VaultPath = "")

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Resolve-VaultPath([string]$Explicit) {
  if ($Explicit -and $Explicit.Trim()) { return $Explicit.Trim() }
  if ($env:OBSIDIAN_VAULT_PATH -and $env:OBSIDIAN_VAULT_PATH.Trim()) {
    return $env:OBSIDIAN_VAULT_PATH.Trim()
  }
  $cfgFile = Join-Path $RepoRoot ".git\config"
  if (Test-Path $cfgFile) {
    $inObs = $false
    foreach ($line in (Get-Content $cfgFile -Encoding UTF8)) {
      $t = $line.Trim()
      if ($t -match "^\[obsidian\]") { $inObs = $true; continue }
      if ($t -match "^\[") { $inObs = $false; continue }
      if ($inObs -and $t -match "^vaultPath\s*=\s*(.+)$") { return $Matches[1].Trim() }
    }
  }
  throw "No vault path. Set git config --local obsidian.vaultPath or -VaultPath."
}
# Part 2 of 4: guards and static file list (no git calls at all).
function Test-IsSecretLike([string]$P) {
  $pats = @('\.env(\.|$)', '\.pem$', '\.key$', '\.pfx$', '\.p12$',
    'id_rsa', 'id_ed25519', 'secrets?\.json$', 'credentials?\.json$')
  foreach ($pat in $pats) { if ($P -match $pat) { return $true } }
  return $false
}

$Vault = Resolve-VaultPath $VaultPath
if (-not (Test-Path (Join-Path $Vault ".obsidian"))) {
  throw "Vault marker missing at $Vault. Refusing to sync."
}
$MirrorRoot = Join-Path $Vault "Agent Memory\projects-ai"
New-Item -ItemType Directory -Force -Path $MirrorRoot | Out-Null
Write-Host "MIRROR_ROOT_READY $MirrorRoot"

$StaticList = "sync-filelist.txt"
$ListPath = Join-Path $PSScriptRoot $StaticList
$relPaths = @(Get-Content $ListPath -Encoding UTF8 | Where-Object { $_ -and $_.Trim() })
$AllowedExt = @(".md", ".json", ".jsonl", ".yaml", ".yml", ".txt")
$copied = @()
$skippedSecrets = @()
$skippedExcluded = 0
$skippedMissing = 0
$slash = [string][char]92
foreach ($raw in $relPaths) {
  $rel = $raw.Trim().Replace("/", $slash)
  if ($rel -match "tmp-tests") { $skippedExcluded++; continue }
  if ($rel -match "__pycache__") { $skippedExcluded++; continue }
  $ext = [IO.Path]::GetExtension($rel)
  if ($ext -notin $AllowedExt) { $skippedExcluded++; continue }
  if (Test-IsSecretLike $rel) { $skippedSecrets += $rel; continue }
  $src = Join-Path $RepoRoot $rel
  if (-not (Test-Path $src -PathType Leaf)) { $skippedMissing++; continue }
  $dest = Join-Path $MirrorRoot $rel
  $destDir = Split-Path $dest -Parent
  if (-not (Test-Path $destDir)) {
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
  }
  Copy-Item -Force $src $dest
  $copied += $rel.Replace($slash, "/")
}
Write-Host "COPIED_COUNT $($copied.Count)"
# Part 4 of 4: notice, manifest cleanup, status (no git calls).
$lines = @(
  "# projects-ai agent-memory mirror (read-only copy)",
  "",
  "> READ-ONLY MIRROR. Authority lives in the projects-ai repo",
  "> (.agents/, product AGENTS.md files, tasks/*.md).",
  "> Do NOT edit this folder to change memory; edit the repo,",
  "> then re-run the sync. Next sync overwrites local edits.",
  "",
  "- Script: tools/obsidian-sync/sync-agent-memory.ps1.",
  "- Vault path is machine-local: git config --local obsidian.vaultPath.",
  "- Cleanup removes only files in .sync-manifest.txt."
)
Set-Content -Path (Join-Path $MirrorRoot "_MIRROR-README.md") -Value $lines -Encoding UTF8
$copied += "_MIRROR-README.md"
$manifestPath = Join-Path $MirrorRoot ".sync-manifest.txt"
$previous = @()
if (Test-Path $manifestPath) {
  $previous = @(Get-Content $manifestPath -Encoding UTF8)
}
$wanted = [System.Collections.Generic.HashSet[string]]::new([string[]]$copied)
$removed = @()
foreach ($old in $previous) {
  if (-not $old -or -not $old.Trim()) { continue }
  $norm = $old.Trim().Replace("\", "/")
  if (-not $wanted.Contains($norm)) {
    $tail = $norm.Replace("/", [string][IO.Path]::DirectorySeparatorChar)
    $abs = Join-Path $MirrorRoot $tail
    $resolved = $null
    try { $resolved = (Resolve-Path $abs -ErrorAction Stop).Path } catch { }
    if ($resolved -and $resolved.StartsWith($MirrorRoot, 1)) {
      Remove-Item -Force $resolved
      $removed += $norm
    }
  }
}
Set-Content -Path $manifestPath -Value ($copied | Sort-Object) -Encoding UTF8
$stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$sl = @("# Sync status", "",
  "Last sync (UTC): $stamp",
  "Files mirrored: $($copied.Count)",
  "Stale removed: $($removed.Count)",
  "Missing upstream (list refresh needed): $skippedMissing",
  "Secret-like skipped: $($skippedSecrets.Count)",
  "Excluded: $skippedExcluded", "")
Set-Content -Path (Join-Path $MirrorRoot "SYNC-STATUS.md") -Value $sl -Encoding UTF8
Write-Host "Mirror OK: $($copied.Count) files -> $MirrorRoot"
Write-Host "Removed: $($removed.Count); secret-skipped: $($skippedSecrets.Count)"
