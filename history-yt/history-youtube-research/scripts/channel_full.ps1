param(
  [Parameter(Mandatory=$true)][string]$Session,
  [Parameter(Mandatory=$true)][string]$Handle,   # e.g. @KingsAndGenerals
  [Parameter(Mandatory=$true)][string]$Slug,     # e.g. kings-and-generals
  [Parameter(Mandatory=$true)][string]$OutDir
)
$env:AGENT_BROWSER_SESSION = $Session
$root = "D:\admin\code\projects\history-yt\history-youtube-research"
$extract = "$root\scripts\extract_videos.js"
$about = "$root\scripts\extract_about.js"
$log = @()
function Log($m){ $script:log += $m; Write-Host $m }

# 1) latest videos
powershell -NoProfile -ExecutionPolicy Bypass -File "$root\scripts\extract.ps1" -Session $Session -Url "https://www.youtube.com/$Handle/videos" -JsFile $extract -OutFile "$OutDir\$Slug-latest.json" 2>&1 | ForEach-Object { Log $_ }

# 2) popular videos (click combobox Latest -> Popular)
try {
  agent-browser open "https://www.youtube.com/$Handle/videos" | Out-Null
  Start-Sleep -Seconds 3
  agent-browser find text "Latest" click 2>&1 | ForEach-Object { Log "sort-open: $_" }
  Start-Sleep -Seconds 1
  agent-browser find menuitem "Popular" click 2>&1 | ForEach-Object { Log "sort-set: $_" }
  Start-Sleep -Seconds 3
  $js = Get-Content -Raw $extract
  $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($js))
  $raw = agent-browser eval "btoa(unescape(encodeURIComponent(String(eval(atob('$b64'))))))" 2>&1
  $s = ($raw | Out-String).Trim()
  if ($s.StartsWith('"') -and $s.EndsWith('"')) { $s = $s.Substring(1, $s.Length - 2) }
  [IO.File]::WriteAllText("$OutDir\$Slug-popular.json", [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s)), [Text.Encoding]::UTF8)
  Log "popular: OK"
} catch {
  Log "popular: FAIL $($_.Exception.Message)"
}

# 3) about tab (joined date, total views)
try {
  agent-browser open "https://www.youtube.com/$Handle/about" | Out-Null
  Start-Sleep -Seconds 3
  $js = Get-Content -Raw $about
  $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($js))
  $raw = agent-browser eval "btoa(unescape(encodeURIComponent(String(eval(atob('$b64'))))))" 2>&1
  $s = ($raw | Out-String).Trim()
  if ($s.StartsWith('"') -and $s.EndsWith('"')) { $s = $s.Substring(1, $s.Length - 2) }
  [IO.File]::WriteAllText("$OutDir\$Slug-about.txt", [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s)), [Text.Encoding]::UTF8)
  Log "about: OK"
} catch {
  Log "about: FAIL $($_.Exception.Message)"
}

Set-Content -Encoding UTF8 "$OutDir\$Slug-runlog.txt" ($log -join "`n")
Log "DONE $Slug"
