# One-time MySQL setup for the Planning Tool.
# Runs backend/db/setup.sql as root (you will be prompted for the root password).
# Usage:  powershell -File run_setup.ps1
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$sql  = Join-Path $here "setup.sql"

# find mysql.exe (PATH, or the standard MySQL 8.0 install location)
$mysql = (Get-Command mysql.exe -ErrorAction SilentlyContinue).Source
if (-not $mysql) {
  $cand = Get-ChildItem "C:\Program Files\MySQL\MySQL Server *\bin\mysql.exe" -ErrorAction SilentlyContinue |
          Select-Object -First 1
  if ($cand) { $mysql = $cand.FullName }
}
if (-not $mysql) { Write-Error "mysql.exe not found. Add MySQL bin to PATH."; exit 1 }

Write-Host "Using $mysql"
Write-Host "Running setup.sql as root (enter your MySQL root password when prompted)..."
# PowerShell does not support '<' stdin redirection; use the mysql client's
# 'source' command with a forward-slash path instead.
$src = $sql -replace '\\', '/'
& $mysql -u root -p -e "source $src"
if ($LASTEXITCODE -eq 0) { Write-Host "`n✅ Done. Database 'planning_tool' + user 'planning_app' + table 'vooki_fg_map' ready." }
else { Write-Error "Setup failed (exit $LASTEXITCODE)." }
