# Adds the login password column to the existing sc_app_user table (as root).
# Run ONCE:  powershell -File run_user_password_migration.ps1  (prompts for MySQL root password)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$sql  = Join-Path $here "migrate_user_password.sql"
$mysql = (Get-Command mysql.exe -ErrorAction SilentlyContinue).Source
if (-not $mysql) {
  $cand = Get-ChildItem "C:\Program Files\MySQL\MySQL Server *\bin\mysql.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($cand) { $mysql = $cand.FullName }
}
if (-not $mysql) { Write-Error "mysql.exe not found. Add MySQL bin to PATH."; exit 1 }
Write-Host "Adding password_hash column (enter your MySQL root password when prompted)..."
$src = ($sql -replace '\\', '/')
& $mysql -u root -p -e "source $src"
if ($LASTEXITCODE -eq 0) {
  Write-Host "[OK] password_hash column added. Reload the User Master page and click Initialize default passwords."
} else {
  Write-Host "If it said Duplicate column name password_hash, the column already exists - you are done."
}
