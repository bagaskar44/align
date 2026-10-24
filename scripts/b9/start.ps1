[CmdletBinding()]
param(
    [switch]$IncludeParserWorker,
    [switch]$NoBuild
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$envFile = Join-Path $repoRoot 'data\b9\private\compose.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    throw 'B9 private configuration is missing; run scripts/b9/setup.ps1 first.'
}

$composePrefix = @(
    'compose', '--project-name', 'align-b9-sandbox', '--env-file', $envFile,
    '-f', (Join-Path $repoRoot 'docker-compose.yml'),
    '-f', (Join-Path $repoRoot 'docker-compose.b9.yml')
)
$statusArgs = $composePrefix + @('ps', '--services', '--status', 'running', 'proxy')
$runningProxy = @(& docker @statusArgs)
if ($LASTEXITCODE -ne 0) {
    throw 'B9 Compose status could not be read; no containers were started.'
}
if ($IncludeParserWorker -and $runningProxy -contains 'proxy') {
    try {
        & (Join-Path $PSScriptRoot 'guard.ps1') -HeavyParser -AllowExistingB9Proxy
    } catch {
        throw "B9 parser resource guard failed: $($_.Exception.Message) The existing stack was left untouched."
    }
    $composeArgs = $composePrefix + @('up', '-d')
    if ($NoBuild) {
        $composeArgs += '--no-build'
    } else {
        $composeArgs += '--build'
    }
    $composeArgs += 'parser-worker'
    & docker @composeArgs
    if ($LASTEXITCODE -ne 0) {
        throw 'B9 parser worker startup failed; inspect the named B9 project logs before retrying.'
    }
    Write-Output 'B9 parser worker started beside the running sandbox. Run scripts/b9/readiness.ps1 before testing.'
    exit 0
}

try {
    if ($IncludeParserWorker) {
        & (Join-Path $PSScriptRoot 'guard.ps1') -HeavyParser
    } else {
        & (Join-Path $PSScriptRoot 'guard.ps1')
    }
} catch {
    throw "B9 resource guard failed: $($_.Exception.Message) No containers were started."
}

$services = @(
    'postgres', 'redis', 'object-storage', 'migrate', 'api', 'web', 'proxy',
    'worker', 'outbox-dispatcher', 'scheduler', 'ca-store', 'moodle-db', 'moodle'
)
if ($IncludeParserWorker) {
    $services += 'parser-worker'
}
$composeArgs = @(
    'compose', '--project-name', 'align-b9-sandbox', '--env-file', $envFile,
    '-f', (Join-Path $repoRoot 'docker-compose.yml'),
    '-f', (Join-Path $repoRoot 'docker-compose.b9.yml'),
    'up', '-d'
)
if (-not $NoBuild) {
    $composeArgs += '--build'
}
$composeArgs += $services
& docker @composeArgs
if ($LASTEXITCODE -ne 0) {
    throw 'B9 Compose startup failed; inspect the named B9 project logs before retrying.'
}
Write-Output 'B9 Compose services started under align-b9-sandbox. Run scripts/b9/readiness.ps1 before testing.'
