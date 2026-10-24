[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$composeFile = Join-Path $repoRoot 'docker-compose.b9.yml'
$envFile = Join-Path $repoRoot 'data\b9\private\compose.env'
docker compose --project-name align-b9-sandbox --env-file $envFile -f (Join-Path $repoRoot 'docker-compose.yml') -f $composeFile ps
if ($LASTEXITCODE -ne 0) {
    throw 'B9 Compose status could not be read.'
}
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
& $python (Join-Path $PSScriptRoot 'readiness.py') (Join-Path $repoRoot 'data\b9\private\ca.crt')
if ($LASTEXITCODE -ne 0) {
    throw 'B9 HTTPS readiness check failed.'
}
