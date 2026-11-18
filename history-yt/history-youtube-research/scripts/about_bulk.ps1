param([Parameter(Mandatory=$true)][string]$Session,
      [Parameter(Mandatory=$true)][string[]]$Pairs)  # slug=handle pairs
$root = "D:\admin\code\projects\history-yt\history-youtube-research"
$env:AGENT_BROWSER_SESSION = $Session
$js = Get-Content -Raw "$root\scripts\extract_about_full.js"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($js))
foreach ($pair in $Pairs) {
  $kv = $pair -split "="
  $slug = $kv[0]; $handle = $kv[1]
  if (-not $handle) { Write-Host "skip $slug (no handle)"; continue }
  agent-browser open "https://www.youtube.com/$handle/about" | Out-Null
  Start-Sleep -Seconds 3
  agent-browser eval "window.scrollTo(0, document.body.scrollHeight)" | Out-Null
  Start-Sleep -Seconds 1
  agent-browser eval "window.scrollTo(0, document.body.scrollHeight)" | Out-Null
  Start-Sleep -Seconds 1
  $raw = agent-browser eval "btoa(unescape(encodeURIComponent(String(eval(atob('$b64'))))))" 2>&1
  $s = ($raw | Out-String).Trim()
  if ($s.StartsWith('"') -and $s.EndsWith('"')) { $s = $s.Substring(1, $s.Length - 2) }
  try {
    $txt = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s))
    [IO.File]::WriteAllText("$root\evidence\about_full\$slug.txt", $txt, [Text.Encoding]::UTF8)
    Write-Host "$slug : saved ($($txt.Length) chars)"
  } catch { Write-Host "$slug : FAIL $($_.Exception.Message)" }
}
