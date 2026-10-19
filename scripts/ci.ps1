param(
    [switch]$SkipDocker
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Test-Path -LiteralPath '.env')) {
    & (Join-Path $PSScriptRoot 'bootstrap.ps1')
}

Get-Content -LiteralPath '.env' -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith('#')) { return }
    $separator = $line.IndexOf('=')
    if ($separator -lt 1) { return }
    [Environment]::SetEnvironmentVariable($line.Substring(0, $separator), $line.Substring($separator + 1), 'Process')
}

Write-Output '==> Install frozen Python workspace from uv.lock'
uv sync --frozen --all-packages --group dev
if ($LASTEXITCODE -ne 0) { throw "uv sync failed with exit code $LASTEXITCODE" }

Write-Output '==> Install frozen frontend workspace'
corepack pnpm install --frozen-lockfile
if ($LASTEXITCODE -ne 0) { throw "pnpm install failed with exit code $LASTEXITCODE" }

Write-Output '==> Python lint, typecheck, and unit/API tests'
uv run ruff check packages services tests migrations scripts
if ($LASTEXITCODE -ne 0) { throw "ruff failed with exit code $LASTEXITCODE" }
uv run mypy packages/domain/src services/api/src services/worker/src
if ($LASTEXITCODE -ne 0) { throw "mypy failed with exit code $LASTEXITCODE" }
uv run pytest
if ($LASTEXITCODE -ne 0) { throw "pytest failed with exit code $LASTEXITCODE" }

Write-Output '==> Frontend lint, typecheck, and production build'
corepack pnpm lint
if ($LASTEXITCODE -ne 0) { throw "frontend lint failed with exit code $LASTEXITCODE" }
corepack pnpm typecheck
if ($LASTEXITCODE -ne 0) { throw "frontend typecheck failed with exit code $LASTEXITCODE" }
corepack pnpm build
if ($LASTEXITCODE -ne 0) { throw "frontend build failed with exit code $LASTEXITCODE" }

if (-not $SkipDocker) {
    # Keep the CI smoke isolated from unrelated host apps that may already own port 3000.
    $env:ALIGN_HOST_PORT = '3100'
    $env:ALIGN_WEB_BASE_URL = 'http://127.0.0.1:3100'
    $env:APP_ORIGIN = $env:ALIGN_WEB_BASE_URL

    Write-Output '==> Check Compose and PostgreSQL+pgvector integration'
    docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw "docker compose config failed with exit code $LASTEXITCODE" }
    docker compose up --build --detach --wait
    if ($LASTEXITCODE -ne 0) { throw "docker compose up failed with exit code $LASTEXITCODE" }

    $firstSeedOutput = docker compose run --rm -T api align seed
    if ($LASTEXITCODE -ne 0) { throw "first synthetic seed failed with exit code $LASTEXITCODE" }
    $secondSeedOutput = docker compose run --rm -T api align seed
    if ($LASTEXITCODE -ne 0) { throw "second synthetic seed failed with exit code $LASTEXITCODE" }
    $firstSeed = (($firstSeedOutput | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json)
    $secondSeed = (($secondSeedOutput | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json)
    if ($firstSeed.status -ne 'ready' -or $secondSeed.status -ne 'ready' -or
        $secondSeed.accounts_created -ne 0 -or $secondSeed.courses_created -ne 0 -or
        $secondSeed.memberships_created -ne 0) {
        throw 'Synthetic seed was not safely rerunnable.'
    }

    docker compose --profile test run --rm --build test-runner
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL integration tests failed with exit code $LASTEXITCODE" }

    $releaseOutput = docker compose run --rm -T api align release register --file config/release.baseline.json
    if ($LASTEXITCODE -ne 0) { throw "baseline release registration failed with exit code $LASTEXITCODE" }
    $release = (($releaseOutput | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json)
    docker compose run --rm -T api align release activate $release.release_id
    if ($LASTEXITCODE -ne 0) { throw "baseline release activation failed with exit code $LASTEXITCODE" }

    $operationOutput = docker compose run --rm -T api align operations smoke
    if ($LASTEXITCODE -ne 0) { throw "outbox smoke enqueue failed with exit code $LASTEXITCODE" }
    $operation = (($operationOutput | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json)
    $operationStatus = $null
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 2
        $statusOutput = docker compose run --rm -T api align operations status $operation.operation_id
        if ($LASTEXITCODE -ne 0) { throw "outbox smoke status failed with exit code $LASTEXITCODE" }
        $operationStatus = (($statusOutput | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json)
        if ($operationStatus.status -in @('succeeded', 'failed')) { break }
    }
    if ($operationStatus.status -ne 'succeeded') { throw "outbox worker smoke did not succeed: $($operationStatus.status)" }

    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $repoRoot '.playwright-browsers'
    corepack pnpm --dir apps/web exec playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "Playwright Chromium install failed with exit code $LASTEXITCODE" }
    corepack pnpm --dir apps/web test:e2e
    if ($LASTEXITCODE -ne 0) { throw "Playwright smoke failed with exit code $LASTEXITCODE" }
}

Write-Output 'CI-local pipeline completed.'
