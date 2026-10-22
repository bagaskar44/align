param(
    [switch]$SkipDocker,
    [switch]$DeployDevelopment
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Test-Path -LiteralPath '.env')) {
    throw 'Local .env is required. CI will not create or replace credentials.'
}
Get-Content -LiteralPath '.env' -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith('#')) { return }
    $separator = $line.IndexOf('=')
    if ($separator -lt 1) { return }
    [Environment]::SetEnvironmentVariable($line.Substring(0, $separator), $line.Substring($separator + 1), 'Process')
}
$previousQaParserMemory = [Environment]::GetEnvironmentVariable('ALIGN_QA_PARSER_DOCUMENT_MEMORY_BYTES', 'Process')
$previousNoColor = [Environment]::GetEnvironmentVariable('NO_COLOR', 'Process')
Remove-Item Env:NO_COLOR -ErrorAction SilentlyContinue

if ($SkipDocker -and $DeployDevelopment) {
    throw '-DeployDevelopment requires Docker QA to run; remove -SkipDocker.'
}
$env:UV_CACHE_DIR = Join-Path $repoRoot '.uv-cache'

$runToken = [Guid]::NewGuid().ToString('N').Substring(0, 12)
$integrationProject = "align-int-$runToken"
$e2eProject = "align-e2e-$runToken"
$hostPort = '3100'
$evidenceRoot = Join-Path $env:TEMP "align-validation-$runToken"
$integrationStarted = $false
$e2eStarted = $false
New-Item -ItemType Directory -Path $evidenceRoot -Force | Out-Null

function Invoke-QACompose {
    param(
        [Parameter(Mandatory = $true)][string]$Project,
        [Parameter(Mandatory = $true)][string[]]$ComposeArgs,
        [switch]$Stream
    )
    if ($Stream) {
        & docker compose --project-name $Project @ComposeArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Compose command failed ($Project): docker compose $($ComposeArgs -join ' ')"
        }
        return
    }
    $output = & docker compose --project-name $Project @ComposeArgs
    if ($LASTEXITCODE -ne 0) {
        $output | Write-Output
        throw "Compose command failed ($Project): docker compose $($ComposeArgs -join ' ')"
    }
    return $output
}

function Invoke-SeedAndRelease {
    param([Parameter(Mandatory = $true)][string]$Project)

    $first = Invoke-QACompose $Project @('run', '--rm', '-T', 'api', 'align', 'seed')
    $second = Invoke-QACompose $Project @('run', '--rm', '-T', 'api', 'align', 'seed')
    $firstSeed = (($first | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json)
    $secondSeed = (($second | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json)
    if ($firstSeed.status -ne 'ready' -or $secondSeed.status -ne 'ready' -or
        $secondSeed.accounts_created -ne 0 -or $secondSeed.courses_created -ne 0 -or
        $secondSeed.memberships_created -ne 0) {
        throw "Synthetic seed was not safely rerunnable in $Project."
    }
    $releaseOutput = Invoke-QACompose $Project @('run', '--rm', '-T', 'api', 'align', 'release', 'register', '--file', 'config/release.baseline.json')
    $release = (($releaseOutput | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json)
    Invoke-QACompose $Project @('run', '--rm', '-T', 'api', 'align', 'release', 'activate', $release.release_id) | Out-Null
    Write-Output "Bootstrap and immutable baseline registration passed: $Project"
}

function Remove-QAProject {
    param([Parameter(Mandatory = $true)][string]$Project)
    if ($Project -notmatch '^align-(int|e2e)-[a-f0-9]{12}$') {
        throw "Refusing cleanup outside this run's isolated Compose project: $Project"
    }
    & docker compose --project-name $Project down --volumes --remove-orphans
    if ($LASTEXITCODE -ne 0) { throw "Could not clean isolated QA project $Project." }
}

try {
    Write-Output "Evidence and Playwright screenshots: $evidenceRoot"
    Write-Output '==> Install frozen Python workspace from uv.lock'
    uv sync --frozen --all-packages --group dev
    if ($LASTEXITCODE -ne 0) { throw 'uv sync failed.' }

    Write-Output '==> Verify generated OpenAPI and TypeScript contracts'
    uv run --no-sync python scripts/openapi_contract.py --check
    if ($LASTEXITCODE -ne 0) { throw 'OpenAPI contract verification failed.' }

    Write-Output '==> Install frozen frontend workspace'
    $env:CI = 'true'
    corepack pnpm install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw 'pnpm install failed.' }

    Write-Output '==> Python lint, typecheck, and Windows unit suite'
    uv run --no-sync ruff check packages services tests migrations scripts
    if ($LASTEXITCODE -ne 0) { throw 'Ruff failed.' }
    uv run --no-sync mypy packages/domain/src services/api/src services/worker/src
    if ($LASTEXITCODE -ne 0) { throw 'mypy failed.' }
    uv run --no-sync pytest tests/unit
    if ($LASTEXITCODE -ne 0) { throw 'Windows Python unit suite failed.' }

    Write-Output '==> Frontend lint, typecheck, and production build'
    corepack pnpm lint
    if ($LASTEXITCODE -ne 0) { throw 'Frontend lint failed.' }
    corepack pnpm typecheck
    if ($LASTEXITCODE -ne 0) { throw 'Frontend typecheck failed.' }
    corepack pnpm build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend production build failed.' }

    if (-not $SkipDocker) {
        if (-not $env:ALIGN_QA_PARSER_DOCUMENT_MEMORY_BYTES) {
            # The QA host has less than the production 8 GiB parser budget
            # available. Keep the QA API and parser fingerprints aligned at 4 GiB.
            $env:ALIGN_QA_PARSER_DOCUMENT_MEMORY_BYTES = '4294967296'
        }
        Write-Output "QA document parser memory profile: $env:ALIGN_QA_PARSER_DOCUMENT_MEMORY_BYTES bytes"
        $portProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, [int]$hostPort)
        try { $portProbe.Start() } catch { throw "QA host port $hostPort is already in use; stop its owner or set another port." }
        finally { $portProbe.Stop() }
        $env:ALIGN_HOST_PORT = $hostPort
        $env:ALIGN_WEB_BASE_URL = "http://127.0.0.1:$hostPort"
        $env:APP_ORIGIN = $env:ALIGN_WEB_BASE_URL

        Write-Output '==> Create fresh isolated PostgreSQL integration QA project'
        $integrationStarted = $true
        Invoke-QACompose $integrationProject @('config', '--quiet') | Out-Null
        Invoke-QACompose -Project $integrationProject -ComposeArgs @('--progress', 'plain', 'up', '--build', '--detach', '--wait') -Stream
        Invoke-SeedAndRelease $integrationProject

        Write-Output '==> Linux Python unit suite in the clean test image'
        Invoke-QACompose $integrationProject @('--profile', 'test', 'run', '--rm', '--build', '--no-deps', 'test-runner', 'uv', 'run', '--no-sync', 'pytest', 'tests/unit', '-q') | Tee-Object -FilePath (Join-Path $evidenceRoot 'linux-unit.log')
        Write-Output '==> Full PostgreSQL integration suite with Linux process-isolation tests enabled'
        Invoke-QACompose $integrationProject @('stop', 'outbox-dispatcher') | Out-Null
        Invoke-QACompose $integrationProject @('--profile', 'test', 'up', '--build', '--detach', '--wait', 'test-parser-worker', 'test-outbox-dispatcher') | Out-Null
        Invoke-QACompose $integrationProject @('--profile', 'test', 'run', '--rm', '--build', '--no-deps', '-e', 'ALIGN_RUN_INTEGRATION=true', '-e', "PARSER_DOCUMENT_MEMORY_BYTES=$env:ALIGN_QA_PARSER_DOCUMENT_MEMORY_BYTES", 'test-runner', 'uv', 'run', '--no-sync', 'pytest', 'tests/integration', '-q') | Tee-Object -FilePath (Join-Path $evidenceRoot 'integration.log')
        Write-Output '==> Real Docling model/OCR integration tests (opt-in, must not be skipped)'
        Invoke-QACompose $integrationProject @('--profile', 'test', 'run', '--rm', '--build', '--no-deps', '-e', 'ALIGN_RUN_PARSER_INTEGRATION=true', '-e', "PARSER_DOCUMENT_MEMORY_BYTES=$env:ALIGN_QA_PARSER_DOCUMENT_MEMORY_BYTES", 'test-runner', 'uv', 'run', '--no-sync', 'pytest', 'tests/integration/test_b1_parser_runtime.py', '-v') | Tee-Object -FilePath (Join-Path $evidenceRoot 'docling-integration.log')
        $doclingText = Get-Content -LiteralPath (Join-Path $evidenceRoot 'docling-integration.log') -Raw
        if ($doclingText -match '(?m)^=*\s*[1-9]\d* skipped\b' -or $doclingText -notmatch '(?m)^=*\s*\d+ passed\b') {
            throw 'Real Docling integration was skipped or did not report passed cases.'
        }
        Write-Output '==> Confirm parser OOM attribution in a disposable 1 GiB Linux container'
        $oomImage = "$integrationProject-test-runner"
        docker image inspect $oomImage --format '{{.Id}}' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Linux test image is missing for OOM verification: $oomImage" }
        docker run --rm --memory=1g --network none -e ALIGN_B3_OOM_INTEGRATION=true $oomImage uv run --no-sync pytest tests/integration/test_b3_parser_oom.py -q 2>&1 | Tee-Object -FilePath (Join-Path $evidenceRoot 'oom-integration.log')
        if ($LASTEXITCODE -ne 0) { throw 'Cgroup OOM integration test failed.' }
        $oomText = Get-Content -LiteralPath (Join-Path $evidenceRoot 'oom-integration.log') -Raw
        if ($oomText -match '(?m)^=*\s*[1-9]\d* skipped\b' -or $oomText -notmatch '(?m)^=*\s*\d+ passed\b') {
            throw 'Cgroup OOM integration was skipped or did not report passed cases.'
        }
        Remove-QAProject $integrationProject
        $integrationStarted = $false

        Write-Output '==> Create a second, fresh QA database for browser E2E'
        $e2eStarted = $true
        Invoke-QACompose $e2eProject @('config', '--quiet') | Out-Null
        Invoke-QACompose -Project $e2eProject -ComposeArgs @('--progress', 'plain', 'up', '--build', '--detach', '--wait') -Stream
        Invoke-SeedAndRelease $e2eProject
        $health = Invoke-QACompose $e2eProject @('exec', '-T', 'parser-worker', 'uv', 'run', '--no-sync', '--package', 'align-worker', 'python', '-m', 'align_worker.runtime_check', '--healthcheck')
        Write-Output '==> Parser broker health and general worker smoke'
        $operation = Invoke-QACompose $e2eProject @('run', '--rm', '-T', 'api', 'align', 'operations', 'smoke')
        $operationRecord = (($operation | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json)
        $operationStatus = $null
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            Start-Sleep -Seconds 2
            $statusOutput = Invoke-QACompose $e2eProject @('run', '--rm', '-T', 'api', 'align', 'operations', 'status', $operationRecord.operation_id)
            $operationStatus = (($statusOutput | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json)
            if ($operationStatus.status -in @('succeeded', 'failed')) { break }
        }
        if ($operationStatus.status -ne 'succeeded') { throw "General worker smoke did not succeed: $($operationStatus.status)" }

        Write-Output '==> Run the ten browser E2Es on the separate database and retain screenshots'
        $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $repoRoot '.playwright-browsers'
        corepack pnpm --dir apps/web exec playwright install chromium
        if ($LASTEXITCODE -ne 0) { throw 'Playwright Chromium install failed.' }
        corepack pnpm --dir apps/web exec playwright test --output (Join-Path $evidenceRoot 'playwright') 2>&1 | Tee-Object -FilePath (Join-Path $evidenceRoot 'e2e.log')
        if ($LASTEXITCODE -ne 0) { throw 'Playwright browser E2E failed.' }

        $workerLogs = Invoke-QACompose $e2eProject @('logs', '--no-color', 'parser-worker', 'worker', 'outbox-dispatcher', 'scheduler', 'api')
        $workerLogs | Set-Content -LiteralPath (Join-Path $evidenceRoot 'runtime.log') -Encoding UTF8
        if ($workerLogs -match 'unregistered task|Traceback \(most recent call last\)|\[browser\].*error') {
            throw 'QA runtime logs contain an unregistered task or application error.'
        }
        $e2eText = Get-Content -LiteralPath (Join-Path $evidenceRoot 'e2e.log') -Raw
        if ($e2eText -match '(?m)^\s*[1-9]\d* skipped\b') { throw 'A required browser E2E was skipped.' }
        Remove-QAProject $e2eProject
        $e2eStarted = $false

        if ($DeployDevelopment) {
            if ($null -eq $previousQaParserMemory) {
                Remove-Item Env:ALIGN_QA_PARSER_DOCUMENT_MEMORY_BYTES -ErrorAction SilentlyContinue
            } else {
                $env:ALIGN_QA_PARSER_DOCUMENT_MEMORY_BYTES = $previousQaParserMemory
            }
            Write-Output '==> Promote the exact E2E-QA images to the existing development Compose project'
            $services = @('migrate', 'api', 'worker', 'parser-worker', 'outbox-dispatcher', 'scheduler', 'web')
            foreach ($service in $services) {
                $image = "$e2eProject-$service"
                docker image inspect $image --format '{{.Id}}' | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "Verified QA image is missing: $image" }
            }
            $overridePath = Join-Path $evidenceRoot 'promote-images.yml'
            $override = [System.Collections.Generic.List[string]]::new()
            $override.Add('services:')
            foreach ($service in $services) {
                $override.Add("  ${service}:")
                $override.Add(("    image: {0}-{1}" -f $e2eProject, $service))
            }
            [System.IO.File]::WriteAllText($overridePath, ($override -join "`n") + "`n", [System.Text.UTF8Encoding]::new($false))
            $developmentProject = if ($env:ALIGN_COMPOSE_PROJECT) { $env:ALIGN_COMPOSE_PROJECT } else { 'align' }
            $developmentServices = $services + 'proxy'
            docker compose --project-name $developmentProject -f docker-compose.yml -f $overridePath up --pull never --no-build --detach --wait @developmentServices
            if ($LASTEXITCODE -ne 0) { throw 'Development stack did not become healthy on the verified QA images.' }
            docker compose --project-name $developmentProject ps
            if ($LASTEXITCODE -ne 0) { throw 'Could not verify development Compose health.' }

            Write-Output '==> Diagnose and idempotently recover eligible old development parser jobs'
            $candidateQuery = 'from sqlalchemy import select; from align_api.models import Job; from align_worker.database import SessionFactory; db=SessionFactory(); [print(str(job.id)) for job in db.scalars(select(Job).where(Job.status=="queued", Job.type.in_(["context_draft","rubric_draft","submission_parse"]))).all()]; db.close()'
            $candidateIds = & docker compose --project-name $developmentProject exec -T parser-worker uv run --no-sync --package align-worker python -c $candidateQuery
            if ($LASTEXITCODE -ne 0) { throw 'Could not inspect queued development parser jobs using worker database access.' }
            $recoveryRows = [System.Collections.Generic.List[string]]::new()
            foreach ($jobId in $candidateIds) {
                $jobId = "$jobId".Trim()
                if (-not $jobId) { continue }
                $diagnosis = & docker compose --project-name $developmentProject exec -T parser-worker align parser recover-job $jobId --reason "QA-verified lost broker message diagnosis"
                if ($LASTEXITCODE -ne 0) {
                    $recoveryRows.Add("$jobId rejected: $($diagnosis -join ' ')")
                    continue
                }
                $record = (($diagnosis | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json)
                if ($record.status -ne 'eligible') {
                    $recoveryRows.Add("$jobId diagnosis status: $($record.status)")
                    continue
                }
                $idempotencyKey = "qa-$runToken-$jobId"
                $execution = & docker compose --project-name $developmentProject exec -T parser-worker align parser recover-job $jobId --reason "QA-verified missing parser broker message" --execute --idempotency-key $idempotencyKey
                if ($LASTEXITCODE -ne 0) { throw "Eligible parser job could not be safely reissued: $jobId" }
                $result = (($execution | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json)
                if ($result.status -notin @('reissued', 'already_reissued')) { throw "Unexpected parser recovery result for $jobId." }
                $recoveryRows.Add("$jobId reissued: $($result.status)")
            }
            $recoveryRows | Set-Content -LiteralPath (Join-Path $evidenceRoot 'development-job-recovery.log') -Encoding UTF8
            $recoveryRows | ForEach-Object { Write-Output $_ }
            docker compose --project-name $developmentProject exec -T parser-worker uv run --no-sync --package align-worker python -m align_worker.runtime_check --healthcheck
            if ($LASTEXITCODE -ne 0) { throw 'Parser worker did not answer the broker after job recovery.' }
        }
    }
    Write-Output "Validation pipeline passed. Evidence: $evidenceRoot"
}
finally {
    if ($integrationStarted) { Remove-QAProject $integrationProject }
    if ($e2eStarted) { Remove-QAProject $e2eProject }
    if ($null -eq $previousNoColor) {
        Remove-Item Env:NO_COLOR -ErrorAction SilentlyContinue
    } else {
        $env:NO_COLOR = $previousNoColor
    }
}
