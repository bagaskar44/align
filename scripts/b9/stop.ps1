[CmdletBinding()]
param(
    [switch]$RemoveHostTrust
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$composeFile = Join-Path $repoRoot 'docker-compose.b9.yml'
$envFile = Join-Path $repoRoot 'data\b9\private\compose.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    Write-Output 'B9 sandbox is not configured; no stack was stopped.'
    exit 0
}
docker compose --project-name align-b9-sandbox --env-file $envFile -f (Join-Path $repoRoot 'docker-compose.yml') -f $composeFile down
if ($RemoveHostTrust) {
    $thumbprintPath = Join-Path $repoRoot 'data\b9\private\ca-thumbprint.txt'
    if (Test-Path -LiteralPath $thumbprintPath) {
        $thumbprint = (Get-Content -LiteralPath $thumbprintPath -Raw).Trim()
        & certutil.exe -user -store Root $thumbprint *> $null
        if ($LASTEXITCODE -eq 0) {
            & certutil.exe -user -delstore Root $thumbprint *> $null
            if ($LASTEXITCODE -ne 0) {
                throw 'Could not remove the ALIGN B9 sandbox CA from the current-user trust store.'
            }
            Write-Output 'Removed only the ALIGN B9 sandbox CA from the current-user trust store.'
        }
    }
}
Write-Output 'B9 containers stopped; named database, queue, object-storage, trust, and Moodle volumes were preserved.'
