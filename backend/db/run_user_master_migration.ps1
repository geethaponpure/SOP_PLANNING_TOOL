# Creates the User Master tables (sc_app_user + sc_app_user_menu) as root.
# Run this ONCE in a normal PowerShell window; you'll be prompted for the MySQL root password.
#   powershell -File run_user_master_migration.ps1
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$sql  = Join-Path $here "migrate_user_master.sql"

$mysql = (Get-Command mysql.exe -ErrorAction SilentlyContinue).Source
if (-not $mysql) {
  $cand = Get-ChildItem "C:\Program Files\MySQL\MySQL Server *\bin\mysql.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($cand) { $mysql = $cand.FullName }
}
if (-not $mysql) { Write-Error "mysql.exe not found. Add MySQL bin to PATH."; exit 1 }

Write-Host "Using $mysql"
Write-Host "Creating User Master tables (enter your MySQL root password when prompted)..."
$src = ($sql -replace '\\', '/')
& $mysql -u root -p -e "source $src"
if ($LASTEXITCODE -eq 0) { Write-Host "`n[OK] Tables sc_app_user + sc_app_user_menu ready in planning_tool." }
else { Write-Error "Migration failed (exit $LASTEXITCODE)." }
