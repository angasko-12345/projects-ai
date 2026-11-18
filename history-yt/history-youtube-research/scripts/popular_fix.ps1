param([Parameter(Mandatory=$true)][string[]]$Slugs)
$root = "D:\admin\code\projects\history-yt\history-youtube-research"
$env:AGENT_BROWSER_SESSION = "hist-fix-popular"
$js = Get-Content -Raw "$root\scripts\extract_videos.js"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($js))
foreach ($slugRaw in $Slugs) {
  $handleMap = @{
    "history-dose"="@HistoryDose"; "military-history-visualized"="@MilitaryHistoryVisualized";
    "unchartedx"="@UnchartedX"; "alternatehistoryhub"="@AlternateHistoryHub";
    "bazbattles"="@BazBattles"; "history-hit"="@HistoryHit"
  }
  foreach ($slug in ($slugRaw -split ",")) {
  $handle = $handleMap[$slug.Trim()]; if (-not $handle) { Write-Host "skip $slug (no handle map)"; continue }
  agent-browser open "https://www.youtube.com/$handle/videos" | Out-Null
  Start-Sleep -Seconds 3
  agent-browser find text "Latest" click | Out-Null
  Start-Sleep -Seconds 1
  $snap = (agent-browser snapshot -i -c | Out-String)
  $m = [regex]::Match($snap, 'menuitem\s+"Popular"[^\]]*ref=(e\d+)')
  if (-not $m.Success) { Write-Host "$slug : NO Popular menuitem found"; continue }
  agent-browser click $m.Groups[1].Value | Out-Null
  Start-Sleep -Seconds 3
  $raw = agent-browser eval "btoa(unescape(encodeURIComponent(String(eval(atob('$b64'))))))" 2>&1
  $s = ($raw | Out-String).Trim()
  if ($s.StartsWith('"') -and $s.EndsWith('"')) { $s = $s.Substring(1, $s.Length - 2) }
  try {
    $json = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s))
    [IO.File]::WriteAllText("$root\evidence\channels\$slug-popular.json", $json, [Text.Encoding]::UTF8)
    $first = ($json | ConvertFrom-Json).videos[0].id
    Write-Host "$slug : popular saved (first=$first)"
  } catch { Write-Host "$slug : DECODE FAIL $($_.Exception.Message)" }
  }
}
