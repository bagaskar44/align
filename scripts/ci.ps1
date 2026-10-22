param(
    [switch]$SkipDocker,
    [switch]$DeployDevelopment
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
$temporaryEnvironmentNames = @(
    'APP_ORIGIN', 'ALIGN_HOST_PORT', 'ALIGN_WEB_BASE_URL', 'ALIGN_ALIAS_BASE_URL',
    'ALIGN_DEV_PARSER_DOCUMENT_MEMORY_BYTES', 'ALIGN_QA_PARSER_DOCUMENT_MEMORY_BYTES',
    'CI', 'UV_CACHE_DIR', 'PLAYWRIGHT_BROWSERS_PATH'
)
$previousTemporaryEnvironment = @{}
foreach ($name in $temporaryEnvironmentNames) {
    $previousTemporaryEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

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
$developmentPromoted = $false
$developmentValidated = $false
$developmentProject = $null
$rollbackOverridePath = $null
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

function Invoke-SeedAccounts {
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
    Write-Output "Synthetic account seed is safely rerunnable: $Project"
}

function Register-AndActivateQABaseline {
    param([Parameter(Mandatory = $true)][string]$Project)
    $releaseOutput = Invoke-QACompose $Project @('--profile', 'operator', 'run', '--rm', '-T', '--build', '--no-deps', 'operator', 'release', 'register', '--file', 'config/release.baseline.json')
    $release = (($releaseOutput | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json)
    if ($release.state -eq 'retired') { throw "QA baseline is retired and cannot be reactivated: $Project" }
    Invoke-QACompose $Project @('--profile', 'operator', 'run', '--rm', '-T', '--build', '--no-deps', 'operator', 'release', 'activate', $release.release_id) | Out-Null
    $repeatOutput = Invoke-QACompose $Project @('--profile', 'operator', 'run', '--rm', '-T', '--build', '--no-deps', 'operator', 'release', 'register', '--file', 'config/release.baseline.json')
    $repeatRelease = (($repeatOutput | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json)
    if ($repeatRelease.release_id -ne $release.release_id -or $repeatRelease.config_hash -ne $release.config_hash -or $repeatRelease.state -ne 'active') {
        throw "Repeated QA release registration changed the active release identity: $Project"
    }
    Write-Output "Isolated QA release registered and activated: $Project"
}

function Get-DevelopmentReleasePlan {
    param([Parameter(Mandatory = $true)][string]$Project)
    $query = "SELECT COALESCE(json_agg(json_build_object('id', id::text, 'version_label', version_label, 'config_hash', config_hash, 'provider_mode', manifest_payload->>'provider_mode', 'data_mode', manifest_payload->>'data_mode', 'capabilities_available', manifest_payload->'capabilities_available')), '[]'::json)::text FROM release_manifests WHERE state='active'"
    $snapshot = $query | & docker compose --project-name $Project exec -T postgres sh -c 'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At'
    if ($LASTEXITCODE -ne 0) { throw "Could not read active release state from deployment database '$Project'." }
    $snapshotPath = Join-Path $evidenceRoot 'development-active-releases.json'
    [System.IO.File]::WriteAllText($snapshotPath, ($snapshot -join "`n"), [System.Text.UTF8Encoding]::new($false))
    $planOutput = uv run --no-sync python scripts/release_guard.py --target config/release.baseline.json --active-json-file $snapshotPath
    if ($LASTEXITCODE -ne 0) { throw "Development release preflight refused promotion: $($planOutput -join ' ')" }
    $plan = (($planOutput | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json)
    $plan | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidenceRoot 'development-release-plan.json') -Encoding UTF8
    return $plan
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
        $env:ALIGN_DEV_PARSER_DOCUMENT_MEMORY_BYTES = '4294967296'
        Write-Output '==> Check development parser capacity and 1 GiB host reserve'
        uv run --no-sync python scripts/parser_capacity_preflight.py | Tee-Object -FilePath (Join-Path $evidenceRoot 'parser-capacity-preflight.json')
        if ($LASTEXITCODE -ne 0) { throw 'Parser capacity preflight failed; rollout stopped.' }
        Write-Output 'QA/development parser document memory profile: 4294967296 bytes; container limit: 5 GiB'
        $portProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, [int]$hostPort)
        try { $portProbe.Start() } catch { throw "QA host port $hostPort is already in use; stop its owner or set another port." }
        finally { $portProbe.Stop() }
        $env:ALIGN_HOST_PORT = $hostPort
        $env:ALIGN_WEB_BASE_URL = "http://localhost:$hostPort"
        $env:ALIGN_ALIAS_BASE_URL = "http://127.0.0.1:$hostPort"
        $env:APP_ORIGIN = $env:ALIGN_WEB_BASE_URL

        Write-Output '==> Create fresh isolated PostgreSQL integration QA project'
        $integrationStarted = $true
        Invoke-QACompose $integrationProject @('config', '--quiet') | Out-Null
        Invoke-QACompose -Project $integrationProject -ComposeArgs @('--progress', 'plain', 'up', '--build', '--detach', '--wait') -Stream
        Invoke-SeedAccounts $integrationProject
        Register-AndActivateQABaseline $integrationProject

        Write-Output '==> Linux Python unit suite in the clean test image'
        Invoke-QACompose $integrationProject @('--profile', 'test', 'run', '--rm', '--build', '--no-deps', 'test-runner', 'uv', 'run', '--no-sync', 'pytest', 'tests/unit', '-q') | Tee-Object -FilePath (Join-Path $evidenceRoot 'linux-unit.log')
        Write-Output '==> Full PostgreSQL integration suite with Linux process-isolation tests enabled'
        Invoke-QACompose $integrationProject @('stop', 'outbox-dispatcher') | Out-Null
        Invoke-QACompose $integrationProject @('--profile', 'test', 'up', '--build', '--detach', '--wait', 'test-parser-worker', 'test-outbox-dispatcher') | Out-Null
        Invoke-QACompose $integrationProject @('--profile', 'test', 'run', '--rm', '--build', '--no-deps', '-e', 'ALIGN_RUN_INTEGRATION=true', '-e', "PARSER_DOCUMENT_MEMORY_BYTES=$env:ALIGN_DEV_PARSER_DOCUMENT_MEMORY_BYTES", 'test-runner', 'uv', 'run', '--no-sync', 'pytest', 'tests/integration', '-q') | Tee-Object -FilePath (Join-Path $evidenceRoot 'integration.log')
        Write-Output '==> Real Docling model/OCR integration tests (opt-in, must not be skipped)'
        $parserContainerId = (Invoke-QACompose $integrationProject @('--profile', 'test', 'ps', '-q', 'test-parser-worker') | Select-Object -Last 1).Trim()
        if (-not $parserContainerId) { throw 'The 4 GiB QA parser worker container was not running for health sampling.' }
        $healthMonitor = Start-Job -ArgumentList $parserContainerId -ScriptBlock {
            param($containerId)
            $failures = [System.Collections.Generic.List[string]]::new()
            for ($sample = 1; $sample -le 60; $sample++) {
                & docker exec $containerId python -m align_worker.runtime_check --healthcheck 2>$null
                if ($LASTEXITCODE -ne 0) { $failures.Add("sample $sample failed") }
                if ($sample -lt 60) { Start-Sleep -Seconds 1 }
            }
            if ($failures.Count) { throw "Parser worker failed health pings during real parsing: $($failures -join ', ')" }
            '60 parser health pings passed during the real parser test.'
        }
        try {
            Invoke-QACompose $integrationProject @('--profile', 'test', 'run', '--rm', '--build', '--no-deps', '-e', 'ALIGN_RUN_PARSER_INTEGRATION=true', '-e', "PARSER_DOCUMENT_MEMORY_BYTES=$env:ALIGN_DEV_PARSER_DOCUMENT_MEMORY_BYTES", 'test-runner', 'uv', 'run', '--no-sync', 'pytest', 'tests/integration/test_b1_parser_runtime.py', '-v', '-s') | Tee-Object -FilePath (Join-Path $evidenceRoot 'docling-integration.log')
            if ($LASTEXITCODE -ne 0) { throw 'Real parser integration failed.' }
            Wait-Job -Job $healthMonitor | Out-Null
            $healthSamples = Receive-Job -Job $healthMonitor -ErrorAction Stop
            if ($healthMonitor.State -ne 'Completed') { throw 'Parser health monitor did not complete successfully.' }
            $healthSamples | Set-Content -LiteralPath (Join-Path $evidenceRoot 'parser-health-samples.log') -Encoding UTF8
        } finally {
            Remove-Job -Job $healthMonitor -Force -ErrorAction SilentlyContinue
        }
        $doclingText = Get-Content -LiteralPath (Join-Path $evidenceRoot 'docling-integration.log') -Raw
        if ($doclingText -match '(?m)^=*\s*[1-9]\d* skipped\b' -or $doclingText -notmatch '(?m)^=*\s*\d+ passed\b') {
            throw 'Real Docling integration was skipped or did not report passed cases.'
        }
        $parserFingerprints = @([regex]::Matches($doclingText, 'parser_profile_fingerprint=[a-f0-9]{64}; memory_bytes=\d+; peak_memory_bytes=\d+; format=[^\r\n]+') | ForEach-Object { $_.Value })
        if ($parserFingerprints.Count -ne 8) { throw "Expected eight real parser profile fingerprints; found $($parserFingerprints.Count)." }
        $parserFingerprints | Set-Content -LiteralPath (Join-Path $evidenceRoot 'parser-profile-fingerprints.log') -Encoding UTF8
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
        Invoke-SeedAccounts $e2eProject
        Register-AndActivateQABaseline $e2eProject
        $health = Invoke-QACompose $e2eProject @('exec', '-T', 'parser-worker', 'uv', 'run', '--no-sync', '--package', 'align-worker', 'python', '-m', 'align_worker.runtime_check', '--healthcheck')
        if ($LASTEXITCODE -ne 0) { throw 'Parser worker did not answer the broker in isolated E2E QA.' }
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
        $env:ALIGN_ALIAS_BASE_URL = "http://127.0.0.1:$hostPort"
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

        Write-Output '==> Verify the parser healthcheck detects a nonresponsive worker in isolated QA'
        $e2eParserContainer = (Invoke-QACompose $e2eProject @('--profile', 'test', 'ps', '-q', 'parser-worker') | Select-Object -Last 1).Trim()
        $workerProcessIds = @()
        $resumeWorker = 'import os,signal,sys; [os.kill(int(pid),signal.SIGCONT) for pid in sys.argv[1].split(",")]'
        try {
            $pauseWorker = @'
import os, signal
matches = []
for name in os.listdir('/proc'):
    if not name.isdigit() or int(name) == os.getpid():
        continue
    try:
        command = open('/proc/' + name + '/cmdline', 'rb').read()
    except OSError:
        continue
    if b'align_worker.celery_app:celery_app' in command and b'worker' in command and b'--healthcheck' not in command:
        matches.append(int(name))
if not matches:
    raise SystemExit('celery worker process not found')
print(','.join(str(pid) for pid in matches), flush=True)
for pid in matches:
    os.kill(pid, signal.SIGSTOP)
'@
            $pausedOutput = & docker exec $e2eParserContainer python -c $pauseWorker
            $workerProcessIds = @(($pausedOutput -join '').Split(',') | Where-Object { $_ -match '^\d+$' })
            if ($LASTEXITCODE -ne 0 -or -not $workerProcessIds.Count) { throw 'Could not pause the isolated parser worker for the failure-detection check.' }
            $failureWatch = [System.Diagnostics.Stopwatch]::StartNew()
            $parserHealth = ''
            while ($failureWatch.Elapsed.TotalSeconds -lt 40) {
                $parserHealth = (& docker inspect --format '{{.State.Health.Status}}' $e2eParserContainer | Select-Object -Last 1).Trim()
                if ($parserHealth -eq 'unhealthy') { break }
                Start-Sleep -Seconds 2
            }
            $failureWatch.Stop()
            if ($parserHealth -ne 'unhealthy') { throw "A paused parser worker was not marked unhealthy within 40 seconds (state=$parserHealth)." }
            "paused_worker_detected_after_seconds=$([Math]::Round($failureWatch.Elapsed.TotalSeconds, 1)); status=$parserHealth" |
                Set-Content -LiteralPath (Join-Path $evidenceRoot 'parser-health-failure-detection.log') -Encoding UTF8
            & docker exec $e2eParserContainer python -c $resumeWorker ($workerProcessIds -join ',')
            if ($LASTEXITCODE -ne 0) { throw 'Could not resume the isolated parser worker after the healthcheck test.' }
            $workerProcessIds = @()
            $recoveryDeadline = [System.Diagnostics.Stopwatch]::StartNew()
            do {
                Start-Sleep -Seconds 2
                $parserHealth = (& docker inspect --format '{{.State.Health.Status}}' $e2eParserContainer | Select-Object -Last 1).Trim()
            } while ($parserHealth -ne 'healthy' -and $recoveryDeadline.Elapsed.TotalSeconds -lt 40)
            if ($parserHealth -ne 'healthy') { throw 'Parser worker health did not recover after the worker resumed.' }
        } finally {
            if ($workerProcessIds.Count) {
                & docker exec $e2eParserContainer python -c $resumeWorker ($workerProcessIds -join ',') | Out-Null
            }
        }
        Remove-QAProject $e2eProject
        $e2eStarted = $false

        if ($DeployDevelopment) {
            $env:ALIGN_HOST_PORT = '3000'
            $env:APP_ORIGIN = 'http://localhost:3000'
            $env:ALIGN_WEB_BASE_URL = $env:APP_ORIGIN
            $env:ALIGN_ALIAS_BASE_URL = 'http://127.0.0.1:3000'
            $env:ALIGN_DEV_PARSER_DOCUMENT_MEMORY_BYTES = '4294967296'
            Remove-Item Env:ALIGN_QA_PARSER_DOCUMENT_MEMORY_BYTES -ErrorAction SilentlyContinue
            Write-Output '==> Read and protect the deployment database release before image promotion'
            $developmentProject = if ($env:ALIGN_COMPOSE_PROJECT) { $env:ALIGN_COMPOSE_PROJECT } else { 'align' }
            $releasePlan = Get-DevelopmentReleasePlan $developmentProject
            Write-Output "Development release action: $($releasePlan.action) ($($releasePlan.reason))"
            Write-Output '==> Promote the exact E2E-QA images to the existing development Compose project'
            $services = @('migrate', 'api', 'worker', 'parser-worker', 'outbox-dispatcher', 'scheduler', 'web')
            $promotedImages = [ordered]@{}
            foreach ($service in $services) {
                $image = "$e2eProject-$service"
                $imageId = docker image inspect $image --format '{{.Id}}'
                if ($LASTEXITCODE -ne 0) { throw "Verified QA image is missing: $image" }
                $promotedImages[$service] = "$imageId".Trim()
            }
            $overridePath = Join-Path $evidenceRoot 'promote-images.yml'
            $override = [System.Collections.Generic.List[string]]::new()
            $override.Add('services:')
            foreach ($service in $services) {
                $override.Add("  ${service}:")
                $override.Add(("    image: {0}-{1}" -f $e2eProject, $service))
            }
            $override.Add('  operator:')
            $override.Add(("    image: {0}-api" -f $e2eProject))
            [System.IO.File]::WriteAllText($overridePath, ($override -join "`n") + "`n", [System.Text.UTF8Encoding]::new($false))

            Write-Output '==> Register the immutable target through the operator CLI and verify the active hash'
            $registration = & docker compose --project-name $developmentProject -f docker-compose.yml -f $overridePath --profile operator run --rm -T --no-deps operator release register --file config/release.baseline.json
            if ($LASTEXITCODE -ne 0) { throw 'Could not register the target release in the deployment database; no images were promoted.' }
            $targetRelease = (($registration | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json)
            if ($releasePlan.action -eq 'verify_target') {
                if ($targetRelease.state -ne 'active' -or $targetRelease.release_id -ne $releasePlan.active_release_id -or $targetRelease.config_hash -ne $releasePlan.active_config_hash) {
                    throw 'The active B5 baseline hash differs from the tested target; image promotion stopped.'
                }
            } elseif ($targetRelease.state -eq 'retired') {
                throw 'The target release is retired and cannot be reactivated; register a new release version before promotion.'
            }
            $targetRelease | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidenceRoot 'development-target-release.json') -Encoding UTF8

            $developmentServices = $services + 'proxy'
            docker compose --project-name $developmentProject -f docker-compose.yml -f $overridePath up --pull never --no-build --detach --wait @developmentServices
            if ($LASTEXITCODE -ne 0) { throw 'Development stack did not become healthy on the verified QA images.' }
            $developmentPromoted = $true
            docker compose --project-name $developmentProject ps
            if ($LASTEXITCODE -ne 0) { throw 'Could not verify development Compose health.' }

            if ($releasePlan.action -eq 'register_activate') {
                Write-Output '==> Activate the tested B5 target on the deployment database'
                $activation = & docker compose --project-name $developmentProject exec -T api align release activate $targetRelease.release_id
                if ($LASTEXITCODE -ne 0) { throw 'The tested release could not be activated on the deployment database.' }
                $activation | Set-Content -LiteralPath (Join-Path $evidenceRoot 'development-release-activation.json') -Encoding UTF8
            } else {
                Write-Output 'The target release hash is already active; activation was skipped.'
            }

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

            Write-Output '==> Run the full lecturer/student analysis and publication flow on development'
            corepack pnpm --dir apps/web exec playwright test e2e/assignments.spec.ts --output (Join-Path $evidenceRoot 'development-playwright') 2>&1 | Tee-Object -FilePath (Join-Path $evidenceRoot 'development-e2e.log')
            if ($LASTEXITCODE -ne 0) { throw 'Direct development browser smoke failed; the deployment is not validated.' }
            $developmentE2E = Get-Content -LiteralPath (Join-Path $evidenceRoot 'development-e2e.log') -Raw
            if ($developmentE2E -match '(?m)^\s*[1-9]\d* skipped\b') { throw 'Development browser smoke skipped a required flow.' }
            $developmentPromoted = $false
            $developmentValidated = $true
            Set-Content -LiteralPath (Join-Path $evidenceRoot 'deployment-runtime-evidence.txt') -Value @(
                "image_project=$e2eProject",
                "image_ids=$($promotedImages | ConvertTo-Json -Compress)",
                "release_id=$($targetRelease.release_id)",
                "release_hash=$($targetRelease.config_hash)",
                "parser_profile_fingerprints=$($parserFingerprints -join ',')",
                "parser_document_memory_bytes=$env:ALIGN_DEV_PARSER_DOCUMENT_MEMORY_BYTES",
                'browser_flow=upload, parse, analysis, lecturer review, feedback, publication, student download passed'
            ) -Encoding UTF8
        }
    }
    Write-Output "Validation pipeline passed. Evidence: $evidenceRoot"
}
catch {
    if ($developmentPromoted -and -not $developmentValidated) {
        Write-Error "Development post-promotion validation failed. The known previous image/config pair must be restored through the release recovery procedure. Details: $($_.Exception.Message)"
    }
    throw
}
finally {
    if ($integrationStarted) { Remove-QAProject $integrationProject }
    if ($e2eStarted) { Remove-QAProject $e2eProject }
    if ($null -eq $previousNoColor) {
        Remove-Item Env:NO_COLOR -ErrorAction SilentlyContinue
    } else {
        $env:NO_COLOR = $previousNoColor
    }
    foreach ($name in $temporaryEnvironmentNames) {
        $previousValue = $previousTemporaryEnvironment[$name]
        if ($null -eq $previousValue) {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable($name, $previousValue, 'Process')
        }
    }
}
