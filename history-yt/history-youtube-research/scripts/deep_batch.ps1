param([Parameter(Mandatory=$true)][string]$Session,
      [Parameter(Mandatory=$true)][string]$CandidatesJson,
      [Parameter(Mandatory=$true)][int]$StartIndex,
      [Parameter(Mandatory=$true)][int]$Count)
$root = "D:\admin\code\projects\history-yt\history-youtube-research"
$env:AGENT_BROWSER_SESSION = $Session
New-Item -ItemType Directory -Force "$root\data\video_meta" | Out-Null
$vids = Get-Content -Raw $CandidatesJson | ConvertFrom-Json
$js = Get-Content -Raw "$root\scripts\deep_video.js"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($js))
for ($i = $StartIndex; $i -lt [Math]::Min($StartIndex + $Count, $vids.Count); $i++) {
  $v = $vids[$i]
  $out = "$root\data\video_meta\$($v.id).json"
  if (Test-Path $out) { Write-Host "skip $($v.id)"; continue }
  Write-Host "[$($i+1)/$($vids.Count)] $($v.slug) / $($v.title) .."
  agent-browser open "https://www.youtube.com/watch?v=$($v.id)" | Out-Null
  Start-Sleep -Seconds 4
  $raw = agent-browser eval "btoa(unescape(encodeURIComponent(String(eval(atob('$b64'))))))" 2>&1
  $s = ($raw | Out-String).Trim()
  if ($s.StartsWith('"') -and $s.EndsWith('"')) { $s = $s.Substring(1, $s.Length - 2) }
  try {
    $json = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s))
    [IO.File]::WriteAllText($out, $json, [Text.Encoding]::UTF8)
    Write-Host "  saved" ($json.Length) "chars"
  } catch { Write-Host "  FAIL $($_.Exception.Message)" }
}
Write-Host "BATCH DONE"
