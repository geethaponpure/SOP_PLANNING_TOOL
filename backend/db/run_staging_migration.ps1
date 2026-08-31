# One-time migration for Phase 1 of the sync-to-DB architecture.
# Creates: sync_runs, sync_requests, stg_stock_lots, stg_item_segments.
# Runs backend/db/migrate_staging.sql as root (you will be prompted for the root password).
# Usage:  powershell -File run_staging_migration.ps1
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$sql  = Join-Path $here "migrate_staging.sql"

# find mysql.exe (PATH, or the standard MySQL 8.0 install location)
$mysql = (Get-Command mysql.exe -ErrorAction SilentlyContinue).Source
if (-not $mysql) {
  $cand = Get-ChildItem "C:\Program Files\MySQL\MySQL Server *\bin\mysql.exe" -ErrorAction SilentlyContinue |
          Select-Object -First 1
  if ($cand) { $mysql = $cand.FullName }
}
if (-not $mysql) { Write-Error "mysql.exe not found. Add MySQL bin to PATH."; exit 1 }

Write-Host "Using $mysql"
Write-Host "Running migrate_staging.sql as root (enter your MySQL root password when prompted)..."
$src = $sql -replace '\\', '/'
& $mysql -u root -p -e "source $src"
if ($LASTEXITCODE -eq 0) { Write-Host "`n✅ Done. Staging + sync tables created in 'planning_tool'." }
else { Write-Error "Migration failed (exit $LASTEXITCODE)." }
