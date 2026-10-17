param(
  [Parameter(Mandatory = $true)][string]$BackupFile,
  [switch]$DeleteBackupAfterRestore,
  [string]$Image = "pgvector/pgvector:pg17"
)

$ErrorActionPreference = "Stop"
$backup = (Resolve-Path -LiteralPath $BackupFile).Path
$container = "travelpilot-restore-" + [guid]::NewGuid().ToString("N").Substring(0, 10)
$password = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
$dbUser = "restore_user"
$dbName = "travelpilot_restore"

try {
  docker run -d --name $container --network none -e "POSTGRES_USER=$dbUser" -e "POSTGRES_PASSWORD=$password" -e "POSTGRES_DB=$dbName" $Image | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not start isolated restore database" }
  $ready = $false
  for ($i = 0; $i -lt 40; $i++) {
    docker exec $container pg_isready -U $dbUser -d $dbName *> $null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 2
  }
  if (-not $ready) { throw "Isolated PostgreSQL did not become ready" }
  docker cp $backup "${container}:/tmp/backup.dump"
  if ($LASTEXITCODE -ne 0) { throw "Could not copy archive into isolated database" }
  docker exec $container pg_restore -U $dbUser -d $dbName --no-owner --no-privileges /tmp/backup.dump
  if ($LASTEXITCODE -ne 0) { throw "pg_restore failed" }
  $check = "SELECT (SELECT count(*) FROM alembic_version), (SELECT count(*) FROM users), (SELECT count(*) FROM trips);"
  $result = docker exec $container psql -U $dbUser -d $dbName -At -c $check
  if ($LASTEXITCODE -ne 0) { throw "Restored data checks failed" }
  $parts = $result.Trim().Split('|')
  if ($parts.Count -ne 3 -or [int]$parts[0] -lt 1) { throw "Migration revision missing from restored archive: $result" }
  Write-Output "Restore drill passed in isolated container (no host port exposed). alembic_versions=$($parts[0]); users=$($parts[1]); trips=$($parts[2])"
  if ($DeleteBackupAfterRestore) {
    Remove-Item -LiteralPath $backup -Force
    Write-Output "Temporary backup removed after successful restore drill."
  }
} finally {
  docker rm -f $container *> $null
}
