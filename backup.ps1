# backup.ps1 - One-click backup of the S&OP Planning Tool.
# Dumps the local MySQL "planning_tool" database AND copies the JSON fallback stores
# (MSL snapshots, roles, users, SRDMS, settings), then zips everything with a timestamp.
#
# Usage (from the project folder):
#   powershell -ExecutionPolicy Bypass -File backup.ps1
#   powershell -ExecutionPolicy Bypass -File backup.ps1 -OutDir "D:\backups"
#
# MySQL connection is read from backend\.env (MYSQL_HOST/PORT/DB/USER/PASSWORD).

param(
  [string]$OutDir = (Join-Path $PSScriptRoot "backups")
)

$ErrorActionPreference = "Stop"
$root    = $PSScriptRoot
$backend = Join-Path $root "backend"
$envFile = Join-Path $backend ".env"

# --- read MySQL settings from backend\.env -----------------------------------
$cfg = @{ MYSQL_HOST = "127.0.0.1"; MYSQL_PORT = "3306"; MYSQL_DB = "planning_tool";
          MYSQL_USER = "root";      MYSQL_PASSWORD = "" }
if (Test-Path $envFile) {
  foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*#') { continue }
    if ($line -match '^\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$') {
      $k = $matches[1]; $v = $matches[2].Trim('"').Trim("'")
      if ($cfg.ContainsKey($k)) { $cfg[$k] = $v }
    }
  }
}

# --- locate mysqldump --------------------------------------------------------
$dump = (Get-Command mysqldump -ErrorAction SilentlyContinue).Source
if (-not $dump) {
  $cand = Get-ChildItem "C:\Program Files\MySQL\*\bin\mysqldump.exe",
                        "C:\Program Files (x86)\MySQL\*\bin\mysqldump.exe",
                        "C:\xampp\mysql\bin\mysqldump.exe" -ErrorAction SilentlyContinue |
          Select-Object -First 1
  if ($cand) { $dump = $cand.FullName }
}
if (-not $dump) { throw "mysqldump not found. Install the MySQL client or add it to PATH." }

# --- prepare the timestamped backup set --------------------------------------
$stamp  = Get-Date -Format 'yyyyMMdd_HHmmss'
$setDir = Join-Path $OutDir "planning_tool_$stamp"
New-Item -ItemType Directory -Force -Path $setDir | Out-Null

# --- 1) dump MySQL (consistent snapshot, no locks; works with a DML-only user) --
$sql = Join-Path $setDir "planning_tool_$stamp.sql"
$env:MYSQL_PWD = $cfg.MYSQL_PASSWORD
& $dump -h $cfg.MYSQL_HOST -P $cfg.MYSQL_PORT -u $cfg.MYSQL_USER `
        --single-transaction --no-tablespaces --skip-lock-tables --skip-triggers `
        --databases $cfg.MYSQL_DB --result-file="$sql"
$code = $LASTEXITCODE
$env:MYSQL_PWD = $null
if ($code -ne 0) { throw "mysqldump failed (exit $code)." }

# --- 2) copy the JSON fallback stores ----------------------------------------
$stores = @("planning_settings.json", "app_roles.json", "srdms_store.json",
            "user_master.json", "msl_store.json")
$copied = 0
foreach ($f in $stores) {
  $src = Join-Path $backend $f
  if (Test-Path $src) { Copy-Item $src -Destination $setDir -Force; $copied++ }
}

# --- 3) zip the whole set -----------------------------------------------------
$zip = Join-Path $OutDir "planning_tool_backup_$stamp.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $setDir "*") -DestinationPath $zip -Force

# --- report ------------------------------------------------------------------
$zsize = (Get-Item $zip).Length
Write-Host ""
Write-Host ("Backup complete -> {0}" -f $zip)
Write-Host ("  MySQL dump + {0} JSON store(s); zip size {1:N0} bytes" -f $copied, $zsize)
Write-Host ("  Unzipped set: {0}" -f $setDir)
Write-Host "  NEXT: copy the .zip to OneDrive / a network share / USB so it lives off this machine."
