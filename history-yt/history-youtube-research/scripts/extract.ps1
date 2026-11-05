param(
  [Parameter(Mandatory=$true)][string]$Session,
  [Parameter(Mandatory=$true)][string]$Url,
  [Parameter(Mandatory=$true)][string]$JsFile,
  [Parameter(Mandatory=$true)][string]$OutFile
)
$env:AGENT_BROWSER_SESSION = $Session
$bytes = [IO.File]::ReadAllBytes($JsFile)
$b64js = [Convert]::ToBase64String($bytes)
agent-browser open $Url | Out-Null
$raw = agent-browser eval "btoa(unescape(encodeURIComponent(String(eval(atob('$b64js'))))))" 2>&1
$s = ($raw | Out-String).Trim()
# strip leading/trailing quotes the CLI adds, if any
if ($s.StartsWith('"') -and $s.EndsWith('"')) { $s = $s.Substring(1, $s.Length - 2) }
try {
  $json = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s))
  New-Item -ItemType Directory -Force (Split-Path $OutFile) | Out-Null
  [IO.File]::WriteAllText($OutFile, $json, [Text.Encoding]::UTF8)
  $parsed = $json | ConvertFrom-Json
  Write-Host "OK count=$($parsed.count) -> $OutFile"
} catch {
  Write-Host "DECODE_FAIL: $($_.Exception.Message)"
  [IO.File]::WriteAllText("$OutFile.raw", ($raw | Out-String), [Text.Encoding]::UTF8)
  Write-Host "raw saved to $OutFile.raw"
}
