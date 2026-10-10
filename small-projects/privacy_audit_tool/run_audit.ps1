param(
  [string]$Mail = '',
  [string]$Usernames = 'inputs\usernames.txt',
  [switch]$Browsers,
  [switch]$OnlineUsernames,
  [string]$Out = 'output'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# Do not name this $args: that is a PowerShell automatic variable.
$pyArgs = @('-m','privacy_audit.main','--usernames',$Usernames,'--out',$Out)
if ($Mail -ne '') { $pyArgs += @('--mail',$Mail) }
if ($Browsers) { $pyArgs += '--browsers' }
if ($OnlineUsernames) { $pyArgs += '--online-usernames' }

py @pyArgs
