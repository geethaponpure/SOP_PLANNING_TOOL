# Creates the user access audit-log table (sc_user_access_log) as root.
# Run ONCE:  powershell -File run_user_access_log_migration.ps1  (prompts for MySQL root password)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$sql  = Join-Path $here "migrate_user_access_log.sql"
$mysql = (Get-Command mysql.exe -ErrorAction SilentlyContinue).Source
if (-not $mysql) {
  $cand = Get-ChildItem "C:\Program Files\MySQL\MySQL Server *\bin\mysql.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($cand) { $mysql = $cand.FullName }
}
if (-not $mysql) { Write-Error "mysql.exe not found. Add MySQL bin to PATH."; exit 1 }
Write-Host "Creating sc_user_access_log (enter your MySQL root password when prompted)..."
& $mysql -u root -p -e "source $($sql -replace '\\','/')"
if ($LASTEXITCODE -eq 0) { Write-Host "[OK] sc_user_access_log ready. Access changes now log to the DB." }
else { Write-Error "Migration failed (exit $LASTEXITCODE)." }
