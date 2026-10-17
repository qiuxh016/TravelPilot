param(
  [string]$ComposeFile = "docker-compose.prod.yml",
  [string]$EnvFile = ".env.production",
  [string]$OutputDirectory = ".data/backups"
)

$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion -lt [version]"7.0") { throw "PowerShell 7 or newer is required for binary-safe output capture" }
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$outputPath = Join-Path $root $OutputDirectory
New-Item -ItemType Directory -Force -Path $outputPath | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = Join-Path $outputPath "travelpilot-$stamp.dump"
$compose = Join-Path $root $ComposeFile
$envPath = Join-Path $root $EnvFile
if (-not (Test-Path -LiteralPath $compose)) { throw "Compose file not found: $compose" }
if (-not (Test-Path -LiteralPath $envPath)) { throw "Environment file not found: $envPath" }

Push-Location $root
try {
  $start = [System.Diagnostics.ProcessStartInfo]::new("docker")
  $arguments = @("compose", "--env-file", $envPath, "-f", $compose, "exec", "-T", "postgres", "sh", "-c", 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom')
  foreach ($argument in $arguments) { [void]$start.ArgumentList.Add($argument) }
  $start.UseShellExecute = $false
  $start.RedirectStandardOutput = $true
  $start.RedirectStandardError = $true
  $process = [System.Diagnostics.Process]::new()
  $process.StartInfo = $start
  if (-not $process.Start()) { throw "Could not start docker compose pg_dump" }
  $stderrTask = $process.StandardError.ReadToEndAsync()
  $stream = [System.IO.File]::Open($backup, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
  try { $process.StandardOutput.BaseStream.CopyTo($stream) } finally { $stream.Dispose() }
  $process.WaitForExit()
  $stderr = $stderrTask.GetAwaiter().GetResult()
  if ($process.ExitCode -ne 0 -or (Get-Item -LiteralPath $backup).Length -lt 100) {
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
    throw "pg_dump failed or returned an empty archive: $stderr"
  }
  $process.Dispose()
  Write-Output "Database backup created: $backup"
  Write-Output "Before production use, encrypt and copy this file to a separate region/account."
} finally {
  Pop-Location
}
