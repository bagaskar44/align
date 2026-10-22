param(
    [switch]$SkipDocker,
    [switch]$DeployDevelopment
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'promotion-orchestration.ps1')
$temporaryEnvironmentNames = @(
    'APP_ORIGIN', 'ALIGN_HOST_PORT', 'ALIGN_WEB_BASE_URL', 'ALIGN_ALIAS_BASE_URL',
    'ALIGN_QA_REDIS_QUEUE_PREFIX',
    'ALIGN_DEV_PARSER_DOCUMENT_MEMORY_BYTES', 'ALIGN_QA_PARSER_DOCUMENT_MEMORY_BYTES',
    'PARSER_NATIVE_MEMORY_BYTES', 'PARSER_DOCUMENT_MEMORY_BYTES', 'PARSER_NODE_MEMORY_BUDGET_BYTES',
    'PARSER_NODE_HEADROOM_BYTES', 'CI', 'UV_CACHE_DIR', 'PLAYWRIGHT_BROWSERS_PATH',
    'PLAYWRIGHT_JSON_OUTPUT_NAME'
)
$previousTemporaryEnvironment = @{}
foreach ($name in $temporaryEnvironmentNames) {
    $previousTemporaryEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

if (-not (Test-Path -LiteralPath '.env')) {
    Write-Output 'No private environment exists in this isolated checkout; creating fresh synthetic QA credentials.'
    & (Join-Path $PSScriptRoot 'bootstrap.ps1')
    if (-not (Test-Path -LiteralPath '.env')) {
        throw 'Could not create the isolated synthetic QA environment.'
    }
}
Get-Content -LiteralPath '.env' -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith('#')) { return }
    $separator = $line.IndexOf('=')
    if ($separator -lt 1) { return }
    [Environment]::SetEnvironmentVariable($line.Substring(0, $separator), $line.Substring($separator + 1), 'Process')
}
$env:ALIGN_DEV_PARSER_DOCUMENT_MEMORY_BYTES = '4294967296'
$env:PARSER_NATIVE_MEMORY_BYTES = '2147483648'
$env:PARSER_DOCUMENT_MEMORY_BYTES = '4294967296'
$env:PARSER_NODE_MEMORY_BUDGET_BYTES = '4294967296'
$env:PARSER_NODE_HEADROOM_BYTES = '1073741824'
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
$promotionStarted = $false
$stageResults = [ordered]@{}
$stageRecords = [ordered]@{}
$qaImageIds = [ordered]@{}
$activeStage = $null
$promotionStatePath = Join-Path $evidenceRoot 'development-promotion-state.json'
$promotionRecord = $null
$incompleteReason = $null
$pipelineStartedUtc = [DateTime]::UtcNow.ToString('o')
New-Item -ItemType Directory -Path $evidenceRoot -Force | Out-Null

function Start-QAStage {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Command
    )
    $script:activeStage = $Name
    $script:stageRecords[$Name] = [ordered]@{
        command = $Command
        started_at_utc = [DateTime]::UtcNow.ToString('o')
        finished_at_utc = $null
        status = 'running'
        summary = $null
    }
}

function Complete-QAStage {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$Status = 'passed',
        [object]$Summary = $null
    )
    $record = $script:stageRecords[$Name]
    if ($null -eq $record) { throw "QA stage was not started: $Name" }
    $record.finished_at_utc = [DateTime]::UtcNow.ToString('o')
    $record.status = $Status
    $record.summary = $Summary
    $script:stageRecords[$Name] = $record
    if ($script:activeStage -eq $Name) { $script:activeStage = $null }
}

function Get-ConfigFileDigests {
    $paths = @('docker-compose.yml', 'docker-compose.production.yml', 'config/release.baseline.json') +
        @('.env', '.env.api', '.env.worker', '.env.postgres', '.env.migration', '.env.tests')
    $digests = [ordered]@{}
    foreach ($path in $paths) {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $digests[$path] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    return $digests
}

function Get-DevelopmentRuntimeSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$Project,
        [Parameter(Mandatory = $true)][string[]]$Services,
        [string]$OverridePath
    )
    $containers = [System.Collections.Generic.List[object]]::new()
    foreach ($service in $Services) {
        $containerId = (& docker compose --project-name $Project ps --all -q $service | Select-Object -Last 1)
        if ($LASTEXITCODE -ne 0) { throw "Could not snapshot current deployment service '$service'." }
        $containerId = "$containerId".Trim()
        if (-not $containerId) { continue }
        $raw = & docker inspect --format '{{json .}}' $containerId
        if ($LASTEXITCODE -ne 0 -or -not $raw) { throw "Could not inspect deployment container for '$service'." }
        $container = (($raw -join "`n") | ConvertFrom-Json)
        $containers.Add([ordered]@{
            service = $service
            container_id = $container.Id
            image_id = $container.Image
            configured_image = $container.Config.Image
        })
    }
    $configDigests = Get-ConfigFileDigests
    if ($OverridePath -and (Test-Path -LiteralPath $OverridePath -PathType Leaf)) {
        $configDigests['promotion-images.yml'] = (Get-FileHash -LiteralPath $OverridePath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    return [ordered]@{
        captured_at_utc = [DateTime]::UtcNow.ToString('o')
        project = $Project
        services = $containers.ToArray()
        config_sha256 = $configDigests
    }
}

function Write-ValidationManifest {
    param([Parameter(Mandatory = $true)][string]$Status)
    $requiredStages = [ordered]@{
        'python-dependencies' = 'uv sync --frozen --all-packages --group dev'
        'api-client-contract' = 'uv run --no-sync python scripts/openapi_contract.py --check'
        'frontend-dependencies' = 'corepack pnpm install --frozen-lockfile'
        'python-static-checks' = 'Ruff and mypy'
        'windows-unit' = 'pytest tests/unit; JUnit XML gate'
        'promotion-orchestration' = 'PowerShell promotion test with a mocked Docker command and partial-failure journal assertions'
        'frontend-checks' = 'pnpm lint, typecheck, build'
        'docker-vm-capacity' = 'Docker Linux VM memory inspection'
        'integration-database' = 'fresh isolated PostgreSQL migrations'
        'parser-capacity-preflight' = 'Linux Docker free-memory check before parser tests'
        'parser-capacity-runtime' = 'repeat Linux Docker free-memory check after isolating parser QA services'
        'integration-seed-release' = 'seed twice and idempotent baseline registration/activation'
        'linux-unit' = 'pytest tests/unit in Linux test image; JUnit XML gate'
        'integration-suite' = 'pytest tests/integration; exact opt-in skips only'
        'docling-parser' = '8 real parser cases, health samples, and fingerprints'
        'parser-oom' = 'isolated 1 GiB Linux OOM case'
        'e2e-database' = 'second fresh isolated PostgreSQL migrations and baseline'
        'worker-smoke' = 'broker health and successful worker operation'
        'browser-e2e' = '12 Playwright cases with strict JSON gate'
        'parser-healthcheck' = 'unresponsive worker detection and recovery'
    }
    foreach ($name in $requiredStages.Keys) {
        if (-not $script:stageRecords.Contains($name)) {
            $script:stageRecords[$name] = [ordered]@{
                command = $requiredStages[$name]
                started_at_utc = $null
                finished_at_utc = $null
                status = 'not_run'
                summary = $null
            }
        }
    }
    $sha = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Could not resolve candidate commit SHA for the validation manifest.' }
    $tree = (& git rev-parse 'HEAD^{tree}').Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Could not resolve candidate tree for the validation manifest.' }
    $workingTree = (& git status --porcelain --untracked-files=all)
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect candidate working tree for the validation manifest.' }
    $parserConfigs = [ordered]@{}
    Get-ChildItem -LiteralPath 'config' -Filter 'parser*.json' -File | Sort-Object Name | ForEach-Object {
        $parserConfigs[$_.Name] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $manifest = [ordered]@{
        schema_version = 1
        status = $Status
        incomplete_reason = $incompleteReason
        started_at_utc = $pipelineStartedUtc
        finished_at_utc = [DateTime]::UtcNow.ToString('o')
        candidate = [ordered]@{
            branch = (& git branch --show-current).Trim()
            commit_sha = $sha
            tree_sha = $tree
            working_tree_clean = [string]::IsNullOrWhiteSpace(($workingTree -join "`n"))
            dirty_paths = @($workingTree | ForEach-Object { $_.Substring(3) })
        }
        lockfiles = [ordered]@{
            'uv.lock' = (Get-FileHash -LiteralPath 'uv.lock' -Algorithm SHA256).Hash.ToLowerInvariant()
            'pnpm-lock.yaml' = (Get-FileHash -LiteralPath 'pnpm-lock.yaml' -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        parser_config_sha256 = $parserConfigs
        parser_profiles = [ordered]@{
            development = [ordered]@{ document_memory_bytes = 4294967296; container_limit_bytes = 5368709120; headroom_bytes = 1073741824 }
            production = [ordered]@{ document_memory_bytes = 8589934592; container_limit_bytes = 9663676416; headroom_bytes = 1073741824 }
        }
        qa_image_ids = $qaImageIds
        required_stages = @($requiredStages.Keys)
        stages = $stageRecords
        deployment_requested = [bool]$DeployDevelopment
        evidence_directory = $evidenceRoot
    }
    if ($Status -eq 'passed' -and -not [string]::IsNullOrWhiteSpace(($workingTree -join "`n"))) {
        throw 'Cannot label validation passed because the candidate working tree is not clean.'
    }
    $manifestPath = Join-Path $evidenceRoot 'validation-manifest.json'
    [System.IO.File]::WriteAllText(
        $manifestPath,
        ($manifest | ConvertTo-Json -Depth 16),
        [System.Text.UTF8Encoding]::new($false)
    )
    return $manifestPath
}

function Invoke-JUnitGate {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$Report,
        [Parameter(Mandatory = $true)][string]$SummaryPath,
        [int]$ExpectedTests = 0,
        [int]$ExpectedPassed = 0,
        [string[]]$AllowedSkips = @()
    )
    $arguments = @('scripts/qa_report_guard.py', 'junit', '--report', $Report, '--label', $Label)
    if ($ExpectedTests -gt 0) { $arguments += @('--expected-tests', "$ExpectedTests") }
    if ($ExpectedPassed -gt 0) { $arguments += @('--expected-passed', "$ExpectedPassed") }
    foreach ($allowedSkip in $AllowedSkips) { $arguments += @('--allow-skipped', $allowedSkip) }
    $output = & uv run --no-sync python @arguments
    if ($LASTEXITCODE -ne 0) { throw "QA report rejected for '$Label'." }
    $result = (($output | Select-Object -Last 1) | ConvertFrom-Json)
    if ($result.status -ne 'passed') { throw "QA report did not pass for '$Label'." }
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $SummaryPath -Encoding UTF8
    $stageResults[$Label] = $result
}

function Invoke-PlaywrightGate {
    param([Parameter(Mandatory = $true)][string]$Report)
    $output = & uv run --no-sync python scripts/qa_report_guard.py playwright --report $Report --label e2e --expected-passed 12
    if ($LASTEXITCODE -ne 0) { throw 'QA report rejected for E2E.' }
    $result = (($output | Select-Object -Last 1) | ConvertFrom-Json)
    if ($result.status -ne 'passed') { throw 'E2E report did not pass.' }
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidenceRoot 'e2e-summary.json') -Encoding UTF8
    $stageResults['e2e'] = $result
}

function Save-QAImageIds {
    param([Parameter(Mandatory = $true)][string]$Project)
    foreach ($service in @('migrate', 'api', 'worker', 'outbox-dispatcher', 'scheduler', 'parser-worker', 'test-parser-worker', 'test-outbox-dispatcher', 'test-runner', 'web')) {
        $image = "$Project-$service"
        $id = & docker image ls --no-trunc --format '{{.ID}}' $image 2>$null | Select-Object -First 1
        if ($id) { $qaImageIds["$Project/$service"] = "$id".Trim() }
    }
}

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
    Start-QAStage -Name 'python-dependencies' -Command 'uv sync --frozen --all-packages --group dev'
    uv sync --frozen --all-packages --group dev
    if ($LASTEXITCODE -ne 0) { throw 'uv sync failed.' }
    Complete-QAStage -Name 'python-dependencies'

    Write-Output '==> Verify generated OpenAPI and TypeScript contracts'
    Start-QAStage -Name 'api-client-contract' -Command 'uv run --no-sync python scripts/openapi_contract.py --check'
    uv run --no-sync python scripts/openapi_contract.py --check
    if ($LASTEXITCODE -ne 0) { throw 'OpenAPI contract verification failed.' }
    Complete-QAStage -Name 'api-client-contract'

    Write-Output '==> Install frozen frontend workspace'
    Start-QAStage -Name 'frontend-dependencies' -Command 'corepack pnpm install --frozen-lockfile'
    $env:CI = 'true'
    corepack pnpm install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw 'pnpm install failed.' }
    Complete-QAStage -Name 'frontend-dependencies'

    Write-Output '==> Python lint and typecheck'
    Start-QAStage -Name 'python-static-checks' -Command 'uv run --no-sync ruff check packages services tests migrations scripts; uv run --no-sync mypy packages/domain/src services/api/src services/worker/src'
    uv run --no-sync ruff check packages services tests migrations scripts
    if ($LASTEXITCODE -ne 0) { throw 'Ruff failed.' }
    uv run --no-sync mypy packages/domain/src services/api/src services/worker/src
    if ($LASTEXITCODE -ne 0) { throw 'mypy failed.' }
    Complete-QAStage -Name 'python-static-checks'
    Write-Output '==> Windows unit suite'
    Start-QAStage -Name 'windows-unit' -Command 'uv run --no-sync pytest tests/unit --junitxml <evidence>/windows-unit.xml'
    $windowsUnitReport = Join-Path $evidenceRoot 'windows-unit.xml'
    uv run --no-sync pytest tests/unit --junitxml $windowsUnitReport
    if ($LASTEXITCODE -ne 0) { throw 'Windows Python unit suite failed.' }
    Invoke-JUnitGate -Label 'windows-unit' -Report $windowsUnitReport -SummaryPath (Join-Path $evidenceRoot 'windows-unit-summary.json')
    Complete-QAStage -Name 'windows-unit' -Summary $stageResults['windows-unit']
    Start-QAStage -Name 'promotion-orchestration' -Command 'tests/powershell/test-promotion-orchestration.ps1 (Docker command mocked)'
    & (Join-Path $repoRoot 'tests/powershell/test-promotion-orchestration.ps1')
    Complete-QAStage -Name 'promotion-orchestration'

    Write-Output '==> Frontend lint, typecheck, and production build'
    Start-QAStage -Name 'frontend-checks' -Command 'corepack pnpm lint; corepack pnpm typecheck; corepack pnpm build'
    corepack pnpm lint
    if ($LASTEXITCODE -ne 0) { throw 'Frontend lint failed.' }
    corepack pnpm typecheck
    if ($LASTEXITCODE -ne 0) { throw 'Frontend typecheck failed.' }
    corepack pnpm build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend production build failed.' }
    Complete-QAStage -Name 'frontend-checks'

    if (-not $SkipDocker) {
        $env:ALIGN_DEV_PARSER_DOCUMENT_MEMORY_BYTES = '4294967296'
        Write-Output '==> Inspect the Linux Docker VM memory ceiling before creating QA services'
        Start-QAStage -Name 'docker-vm-capacity' -Command "docker info --format '{{.MemTotal}}'; parser preflight inside the Linux Docker environment"
        $dockerTotalText = (& docker info --format '{{.MemTotal}}' | Select-Object -Last 1)
        if ($LASTEXITCODE -ne 0 -or "$dockerTotalText" -notmatch '^\d+$') {
            $incompleteReason = 'Docker Linux engine unavailable; no Windows host RAM fallback was accepted.'
            throw 'Could not read Docker Linux VM memory. Windows host RAM is not accepted as parser capacity evidence.'
        }
        $dockerTotalBytes = [long]$dockerTotalText
        $requiredContainerBytes = 5L * 1024 * 1024 * 1024
        if ($dockerTotalBytes -lt $requiredContainerBytes) {
            $incompleteReason = "Docker Linux VM has $dockerTotalBytes bytes; at least $requiredContainerBytes are required."
            throw "Docker Linux VM has $dockerTotalBytes bytes; at least $requiredContainerBytes are required for the 5 GiB development parser container."
        }
        [ordered]@{ docker_linux_total_bytes = $dockerTotalBytes; required_container_bytes = $requiredContainerBytes } |
            ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidenceRoot 'docker-vm-memory.json') -Encoding UTF8
        Complete-QAStage -Name 'docker-vm-capacity' -Summary ([ordered]@{ docker_linux_total_bytes = $dockerTotalBytes })
        $portProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, [int]$hostPort)
        try { $portProbe.Start() } catch { throw "QA host port $hostPort is already in use; stop its owner or set another port." }
        finally { $portProbe.Stop() }
        $env:ALIGN_HOST_PORT = $hostPort
        $env:ALIGN_WEB_BASE_URL = "http://localhost:$hostPort"
        $env:ALIGN_ALIAS_BASE_URL = "http://127.0.0.1:$hostPort"
        $env:APP_ORIGIN = $env:ALIGN_WEB_BASE_URL
        $env:ALIGN_QA_REDIS_QUEUE_PREFIX = 'align-test'

        Write-Output '==> Create fresh isolated PostgreSQL integration QA project'
        Start-QAStage -Name 'integration-database' -Command 'docker compose --project-name <isolated> up --build --detach --wait; apply all migrations to fresh QA database'
        $integrationStarted = $true
        Invoke-QACompose $integrationProject @('config', '--quiet') | Out-Null
        Invoke-QACompose -Project $integrationProject -ComposeArgs @('--progress', 'plain', 'up', '--build', '--detach', '--wait') -Stream
        Complete-QAStage -Name 'integration-database'

        Write-Output '==> Check live Linux Docker memory available to the parser before any parser tests'
        Start-QAStage -Name 'parser-capacity-preflight' -Command "docker compose --project-name $integrationProject --profile test run --rm --no-deps test-runner python scripts/parser_capacity_preflight.py --docker-total-bytes $dockerTotalBytes"
        $capacityOutput = Invoke-QACompose $integrationProject @('--profile', 'test', 'run', '--rm', '--build', '--no-deps', 'test-runner', 'python', 'scripts/parser_capacity_preflight.py', '--docker-total-bytes', "$dockerTotalBytes")
        $capacityReport = (($capacityOutput | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json)
        if ($capacityReport.accepted -ne $true) {
            $capacityReport | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidenceRoot 'parser-capacity-preflight.json') -Encoding UTF8
            $incompleteReason = 'Linux Docker VM currently lacks the required 4 GiB parser profile plus 1 GiB headroom.'
            throw 'Linux Docker VM has insufficient currently available parser capacity; parser tests were not started.'
        }
        $capacityReport | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidenceRoot 'parser-capacity-preflight.json') -Encoding UTF8
        Complete-QAStage -Name 'parser-capacity-preflight' -Summary $capacityReport

        Start-QAStage -Name 'integration-seed-release' -Command 'seed synthetic accounts twice; register, activate and re-register baseline idempotently'
        Invoke-SeedAccounts $integrationProject
        Register-AndActivateQABaseline $integrationProject
        Complete-QAStage -Name 'integration-seed-release'

        Write-Output '==> Linux Python unit suite in the clean test image'
        Start-QAStage -Name 'linux-unit' -Command 'pytest tests/unit --junitxml /qa-evidence/linux-unit.xml'
        $dockerEvidenceRoot = $evidenceRoot.Replace('\', '/')
        Invoke-QACompose $integrationProject @('--profile', 'test', 'run', '--rm', '--build', '--no-deps', '--volume', "${dockerEvidenceRoot}:/qa-evidence", 'test-runner', 'uv', 'run', '--no-sync', 'pytest', 'tests/unit', '-q', '--junitxml', '/qa-evidence/linux-unit.xml') | Tee-Object -FilePath (Join-Path $evidenceRoot 'linux-unit.log')
        if ($LASTEXITCODE -ne 0) { throw 'Linux Python unit suite failed.' }
        Invoke-JUnitGate -Label 'linux-unit' -Report (Join-Path $evidenceRoot 'linux-unit.xml') -SummaryPath (Join-Path $evidenceRoot 'linux-unit-summary.json')
        Complete-QAStage -Name 'linux-unit' -Summary $stageResults['linux-unit']
        Write-Output '==> Full PostgreSQL integration suite with Linux process-isolation tests enabled'
        Start-QAStage -Name 'integration-suite' -Command 'pytest tests/integration --junitxml /qa-evidence/integration.xml (only 7 parser-runtime + 1 OOM opt-in skips allowed)'
        Invoke-QACompose $integrationProject @('stop', 'outbox-dispatcher', 'worker') | Out-Null
        Invoke-QACompose $integrationProject @('--profile', 'test', 'up', '--build', '--detach', '--wait', 'test-parser-worker', 'test-outbox-dispatcher') | Out-Null
        Invoke-QACompose $integrationProject @('stop', 'parser-worker') | Out-Null
        Invoke-QACompose $integrationProject @('--profile', 'test', 'run', '--rm', '--build', '--no-deps', '--volume', "${dockerEvidenceRoot}:/qa-evidence", '-e', 'ALIGN_RUN_INTEGRATION=true', '-e', "PARSER_DOCUMENT_MEMORY_BYTES=$env:ALIGN_DEV_PARSER_DOCUMENT_MEMORY_BYTES", 'test-runner', 'uv', 'run', '--no-sync', 'pytest', 'tests/integration', '-q', '--junitxml', '/qa-evidence/integration.xml') | Tee-Object -FilePath (Join-Path $evidenceRoot 'integration.log')
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL integration suite failed.' }
        Invoke-JUnitGate -Label 'integration' -Report (Join-Path $evidenceRoot 'integration.xml') -SummaryPath (Join-Path $evidenceRoot 'integration-summary.json') -AllowedSkips @(
            'test_b1_parser_runtime::test_real_docling_source_hash_original_anchor_and_checkpoint_replay=7',
            'test_b3_parser_oom::test_confirmed_cgroup_oom_maps_to_parser_memory_limit=1'
        )
        Complete-QAStage -Name 'integration-suite' -Summary $stageResults['integration']
        Invoke-QACompose $integrationProject @('stop', 'worker', 'scheduler', 'web', 'proxy') | Out-Null
        Write-Output '==> Recheck live Linux Docker capacity after isolating parser QA services'
        Start-QAStage -Name 'parser-capacity-runtime' -Command "docker compose --project-name $integrationProject --profile test run --rm --no-deps test-runner python scripts/parser_capacity_preflight.py --docker-total-bytes $dockerTotalBytes"
        $runtimeCapacityOutput = Invoke-QACompose $integrationProject @('--profile', 'test', 'run', '--rm', '--build', '--no-deps', 'test-runner', 'python', 'scripts/parser_capacity_preflight.py', '--docker-total-bytes', "$dockerTotalBytes")
        $runtimeCapacityReport = (($runtimeCapacityOutput | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json)
        $runtimeCapacityReport | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidenceRoot 'parser-capacity-runtime.json') -Encoding UTF8
        if ($runtimeCapacityReport.accepted -ne $true) {
            $incompleteReason = 'Linux Docker VM lacks the required 4 GiB parser profile plus 1 GiB headroom after non-parser QA services were stopped.'
            throw 'Live Linux Docker capacity is below the parser budget; real parser tests were not started.'
        }
        Complete-QAStage -Name 'parser-capacity-runtime' -Summary $runtimeCapacityReport
        Write-Output '==> Real Docling model/OCR and XLSX submission integration tests (opt-in, must not be skipped)'
        Start-QAStage -Name 'docling-parser' -Command 'pytest tests/integration/test_b1_parser_runtime.py tests/integration/test_b3_submission_flow.py --junitxml /qa-evidence/docling.xml; require 8 passes and 0 skips'
        $parserContainerId = (Invoke-QACompose $integrationProject @('--profile', 'test', 'ps', '-q', 'test-parser-worker') | Select-Object -Last 1).Trim()
        if (-not $parserContainerId) { throw 'The 4 GiB QA parser worker container was not running for health sampling.' }
        $healthMonitorStopPath = Join-Path $evidenceRoot 'stop-parser-health-monitor'
        $healthMonitor = Start-Job -ArgumentList $parserContainerId, $healthMonitorStopPath -ScriptBlock {
            param($containerId, $stopPath)
            $failures = [System.Collections.Generic.List[string]]::new()
            $watch = [System.Diagnostics.Stopwatch]::StartNew()
            $sample = 0
            while (-not (Test-Path -LiteralPath $stopPath) -and $watch.Elapsed.TotalMinutes -lt 90) {
                $sample++
                & docker exec $containerId python -m align_worker.runtime_check --healthcheck 2>$null
                if ($LASTEXITCODE -ne 0) { $failures.Add("sample $sample failed") }
                if (-not (Test-Path -LiteralPath $stopPath)) { Start-Sleep -Seconds 1 }
            }
            $watch.Stop()
            if (-not (Test-Path -LiteralPath $stopPath)) { throw 'Parser health monitor reached its 90-minute safety bound.' }
            if ($sample -eq 0) { throw 'Parser health monitor collected no health samples.' }
            if ($failures.Count) { throw "Parser worker failed health pings during real parsing: $($failures -join ', ')" }
            "$sample parser health pings passed over $([Math]::Round($watch.Elapsed.TotalSeconds, 1)) seconds during the real parser test."
        }
        try {
            Invoke-QACompose $integrationProject @('--profile', 'test', 'run', '--rm', '--build', '--no-deps', '--volume', "${dockerEvidenceRoot}:/qa-evidence", '-e', 'ALIGN_RUN_INTEGRATION=true', '-e', 'ALIGN_RUN_PARSER_INTEGRATION=true', '-e', "PARSER_DOCUMENT_MEMORY_BYTES=$env:ALIGN_DEV_PARSER_DOCUMENT_MEMORY_BYTES", 'test-runner', 'uv', 'run', '--no-sync', 'pytest', 'tests/integration/test_b1_parser_runtime.py', 'tests/integration/test_b3_submission_flow.py', '-v', '-s', '--junitxml', '/qa-evidence/docling.xml') | Tee-Object -FilePath (Join-Path $evidenceRoot 'docling-integration.log')
            if ($LASTEXITCODE -ne 0) { throw 'Real parser integration failed.' }
        } finally {
            New-Item -ItemType File -Path $healthMonitorStopPath -Force | Out-Null
            Wait-Job -Job $healthMonitor -Timeout 30 | Out-Null
            try {
                if ($healthMonitor.State -ne 'Completed') { throw 'Parser health monitor did not stop after the test completed.' }
                $healthSamples = Receive-Job -Job $healthMonitor -ErrorAction Stop
                $healthSamples | Set-Content -LiteralPath (Join-Path $evidenceRoot 'parser-health-samples.log') -Encoding UTF8
            } finally {
                Remove-Job -Job $healthMonitor -Force -ErrorAction SilentlyContinue
            }
        }
        Invoke-JUnitGate -Label 'docling' -Report (Join-Path $evidenceRoot 'docling.xml') -SummaryPath (Join-Path $evidenceRoot 'docling-summary.json') -ExpectedTests 8 -ExpectedPassed 8
        $doclingText = Get-Content -LiteralPath (Join-Path $evidenceRoot 'docling-integration.log') -Raw
        $parserFingerprints = @([regex]::Matches($doclingText, 'parser_profile_fingerprint=[a-f0-9]{64}; memory_bytes=\d+; peak_memory_bytes=\d+; format=[^\r\n]+') | ForEach-Object { $_.Value })
        if ($parserFingerprints.Count -ne 9) { throw "Expected nine real parser profile fingerprints across eight parser test cases; found $($parserFingerprints.Count)." }
        $parserFingerprints | Set-Content -LiteralPath (Join-Path $evidenceRoot 'parser-profile-fingerprints.log') -Encoding UTF8
        Complete-QAStage -Name 'docling-parser' -Summary ([ordered]@{ tests = 8; fingerprints = $parserFingerprints.Count; health_samples = (Get-Content -LiteralPath (Join-Path $evidenceRoot 'parser-health-samples.log')).Count })
        Write-Output '==> Confirm parser OOM attribution in a disposable 1 GiB Linux container'
        Start-QAStage -Name 'parser-oom' -Command 'docker run --rm --memory=1g --memory-swap=1g --network none; pytest tests/integration/test_b3_parser_oom.py --junitxml /qa-evidence/oom.xml'
        $oomImage = "$integrationProject-test-runner"
        docker image inspect $oomImage --format '{{.Id}}' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Linux test image is missing for OOM verification: $oomImage" }
        docker run --rm --memory=1g --memory-swap=1g --network none --volume "${dockerEvidenceRoot}:/qa-evidence" -e ALIGN_B3_OOM_INTEGRATION=true $oomImage uv run --no-sync pytest tests/integration/test_b3_parser_oom.py -q --junitxml /qa-evidence/oom.xml 2>&1 | Tee-Object -FilePath (Join-Path $evidenceRoot 'oom-integration.log')
        if ($LASTEXITCODE -ne 0) { throw 'Cgroup OOM integration test failed.' }
        Invoke-JUnitGate -Label 'oom' -Report (Join-Path $evidenceRoot 'oom.xml') -SummaryPath (Join-Path $evidenceRoot 'oom-summary.json') -ExpectedTests 1 -ExpectedPassed 1
        Complete-QAStage -Name 'parser-oom' -Summary $stageResults['oom']
        Save-QAImageIds -Project $integrationProject
        Remove-QAProject $integrationProject
        $integrationStarted = $false

        Write-Output '==> Create a second, fresh QA database for browser E2E'
        $env:ALIGN_QA_REDIS_QUEUE_PREFIX = 'align-e2e'
        Start-QAStage -Name 'e2e-database' -Command 'docker compose --project-name <isolated> up --build --detach --wait; fresh migrations, synthetic seed, idempotent release baseline'
        $e2eStarted = $true
        Invoke-QACompose $e2eProject @('config', '--quiet') | Out-Null
        Invoke-QACompose -Project $e2eProject -ComposeArgs @('--progress', 'plain', 'up', '--build', '--detach', '--wait') -Stream
        Invoke-SeedAccounts $e2eProject
        Register-AndActivateQABaseline $e2eProject
        Complete-QAStage -Name 'e2e-database'
        $health = Invoke-QACompose $e2eProject @('exec', '-T', 'parser-worker', 'uv', 'run', '--no-sync', '--package', 'align-worker', 'python', '-m', 'align_worker.runtime_check', '--healthcheck')
        if ($LASTEXITCODE -ne 0) { throw 'Parser worker did not answer the broker in isolated E2E QA.' }
        Write-Output '==> Parser broker health and general worker smoke'
        Start-QAStage -Name 'worker-smoke' -Command 'parser broker ping; align operations smoke; require succeeded'
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
        Complete-QAStage -Name 'worker-smoke' -Summary $operationStatus

        Write-Output '==> Run all twelve browser E2Es on the separate database and retain screenshots'
        Start-QAStage -Name 'browser-e2e' -Command 'corepack pnpm --dir apps/web exec playwright test --reporter=json; require 12 passes and 0 skips/flakes'
        $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $evidenceRoot 'playwright-browsers'
        $env:ALIGN_ALIAS_BASE_URL = "http://127.0.0.1:$hostPort"
        $playwrightReport = Join-Path $evidenceRoot 'e2e-playwright.json'
        $env:PLAYWRIGHT_JSON_OUTPUT_NAME = $playwrightReport
        corepack pnpm --dir apps/web exec playwright install chromium
        if ($LASTEXITCODE -ne 0) { throw 'Playwright Chromium install failed.' }
        corepack pnpm --dir apps/web exec playwright test --reporter=json --output (Join-Path $evidenceRoot 'playwright') 2>&1 | Tee-Object -FilePath (Join-Path $evidenceRoot 'e2e.log')
        if ($LASTEXITCODE -ne 0) { throw 'Playwright browser E2E failed.' }
        Invoke-PlaywrightGate -Report $playwrightReport
        Complete-QAStage -Name 'browser-e2e' -Summary $stageResults['e2e']

        $workerLogs = Invoke-QACompose $e2eProject @('logs', '--no-color', 'parser-worker', 'worker', 'outbox-dispatcher', 'scheduler', 'api')
        $workerLogs | Set-Content -LiteralPath (Join-Path $evidenceRoot 'runtime.log') -Encoding UTF8
        if ($workerLogs -match 'unregistered task|Traceback \(most recent call last\)|\[browser\].*error') {
            throw 'QA runtime logs contain an unregistered task or application error.'
        }
        Save-QAImageIds -Project $e2eProject

        Write-Output '==> Verify the parser healthcheck detects a nonresponsive worker in isolated QA'
        Start-QAStage -Name 'parser-healthcheck' -Command 'derive unhealthy/recovery deadlines from Docker healthcheck; pause, detect, resume and recover worker'
        $e2eParserContainer = (Invoke-QACompose $e2eProject @('--profile', 'test', 'ps', '-q', 'parser-worker') | Select-Object -Last 1).Trim()
        $healthConfigJson = & docker inspect --format '{{json .Config.Healthcheck}}' $e2eParserContainer
        if ($LASTEXITCODE -ne 0 -or -not $healthConfigJson) { throw 'Could not read Docker parser healthcheck configuration.' }
        $deadlineOutput = & uv run --no-sync python scripts/qa_report_guard.py health-deadline --docker-config "$healthConfigJson"
        if ($LASTEXITCODE -ne 0) { throw 'Docker parser healthcheck configuration is invalid.' }
        $unhealthyDeadlineSeconds = [int](($deadlineOutput | Select-Object -Last 1 | ConvertFrom-Json).unhealthy_deadline_seconds)
        $healthStage = $stageResults['e2e']
        $healthStage | Add-Member -NotePropertyName 'unhealthy_deadline_seconds' -NotePropertyValue $unhealthyDeadlineSeconds
        $stageResults['e2e'] = $healthStage
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
            while ($failureWatch.Elapsed.TotalSeconds -lt $unhealthyDeadlineSeconds) {
                $parserHealth = (& docker inspect --format '{{.State.Health.Status}}' $e2eParserContainer | Select-Object -Last 1).Trim()
                if ($parserHealth -eq 'unhealthy') { break }
                Start-Sleep -Seconds 2
            }
            $failureWatch.Stop()
            if ($parserHealth -ne 'unhealthy') { throw "A paused parser worker was not marked unhealthy within $unhealthyDeadlineSeconds seconds (state=$parserHealth)." }
            "paused_worker_detected_after_seconds=$([Math]::Round($failureWatch.Elapsed.TotalSeconds, 1)); status=$parserHealth" |
                Set-Content -LiteralPath (Join-Path $evidenceRoot 'parser-health-failure-detection.log') -Encoding UTF8
            & docker exec $e2eParserContainer python -c $resumeWorker ($workerProcessIds -join ',')
            if ($LASTEXITCODE -ne 0) { throw 'Could not resume the isolated parser worker after the healthcheck test.' }
            $workerProcessIds = @()
            $recoveryDeadline = [System.Diagnostics.Stopwatch]::StartNew()
            do {
                Start-Sleep -Seconds 2
                $parserHealth = (& docker inspect --format '{{.State.Health.Status}}' $e2eParserContainer | Select-Object -Last 1).Trim()
            } while ($parserHealth -ne 'healthy' -and $recoveryDeadline.Elapsed.TotalSeconds -lt $unhealthyDeadlineSeconds)
            if ($parserHealth -ne 'healthy') { throw 'Parser worker health did not recover after the worker resumed.' }
        } finally {
            if ($workerProcessIds.Count) {
                & docker exec $e2eParserContainer python -c $resumeWorker ($workerProcessIds -join ',') | Out-Null
            }
        }
        Complete-QAStage -Name 'parser-healthcheck' -Summary ([ordered]@{ unhealthy_deadline_seconds = $unhealthyDeadlineSeconds; recovered = $parserHealth -eq 'healthy' })
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
            $releasePlanPath = Join-Path $evidenceRoot 'development-release-plan.json'
            $registeredTargetPath = Join-Path $evidenceRoot 'development-target-release.json'
            $targetRelease | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $registeredTargetPath -Encoding UTF8
            $registrationCheck = & uv run --no-sync python scripts/release_guard.py --verify-registered --plan-json-file $releasePlanPath --registered-json-file $registeredTargetPath
            if ($LASTEXITCODE -ne 0) { throw "Registered target did not match the protected release plan: $($registrationCheck -join ' ')" }

            $developmentServices = $services + 'proxy'
            $beforeSnapshot = Get-DevelopmentRuntimeSnapshot -Project $developmentProject -Services $developmentServices -OverridePath $overridePath
            $promotionRecord = Invoke-RecordedPromotion `
                -JournalPath $promotionStatePath `
                -Before $beforeSnapshot `
                -TargetImageIds $promotedImages `
                -TargetConfigDigests (Get-ConfigFileDigests) `
                -ReleaseId $targetRelease.release_id `
                -ReleaseHash $targetRelease.config_hash `
                -RecoveryNote 'Restoring image IDs does not restore database schema/data. Back up database and object storage before migration 0018.' `
                -StartServices {
                    $script:promotionStarted = $true
                    docker compose --project-name $developmentProject -f docker-compose.yml -f $overridePath up --pull never --no-build --detach --wait @developmentServices
                    if ($LASTEXITCODE -ne 0) { throw 'Development stack did not become healthy on the verified QA images.' }
                    docker compose --project-name $developmentProject -f docker-compose.yml -f $overridePath ps
                    if ($LASTEXITCODE -ne 0) { throw 'Could not verify development Compose health.' }
                } `
                -CaptureAfter {
                    $snapshot = Get-DevelopmentRuntimeSnapshot -Project $developmentProject -Services $developmentServices -OverridePath $overridePath
                    foreach ($service in $services) {
                        $runtime = @($snapshot.services | Where-Object { $_.service -eq $service } | Select-Object -Last 1)
                        if (-not $runtime.Count -or $runtime[0].image_id -ne $promotedImages[$service]) {
                            throw "Promoted service '$service' is not running the exact tested QA image."
                        }
                    }
                    return $snapshot
                }
            $developmentPromoted = $true

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
            Set-Content -LiteralPath (Join-Path $evidenceRoot 'deployment-runtime-evidence.txt') -Value @(
                "image_project=$e2eProject",
                "image_ids=$($promotedImages | ConvertTo-Json -Compress)",
                "release_id=$($targetRelease.release_id)",
                "release_hash=$($targetRelease.config_hash)",
                "parser_profile_fingerprints=$($parserFingerprints -join ',')",
                "parser_document_memory_bytes=$env:ALIGN_DEV_PARSER_DOCUMENT_MEMORY_BYTES",
                'browser_flow=upload, parse, analysis, lecturer review, feedback, publication, student download passed'
            ) -Encoding UTF8
            $promotionRecord.status = 'completed'
            $promotionRecord.finished_at_utc = [DateTime]::UtcNow.ToString('o')
            $promotionRecord.after = Get-DevelopmentRuntimeSnapshot -Project $developmentProject -Services $developmentServices -OverridePath $overridePath
            Write-PromotionJournal -Path $promotionStatePath -Record $promotionRecord
            $promotionStarted = $false
            $developmentPromoted = $false
            $developmentValidated = $true
        }
    }
    if ($SkipDocker) {
        $incompleteReason = 'Docker, parser, database, OOM and browser gates were explicitly skipped.'
        $manifestPath = Write-ValidationManifest -Status 'incomplete'
        Write-Output "Local checks completed; Docker/parser/browser QA remains incomplete. Evidence manifest: $manifestPath"
    } else {
        $manifestPath = Write-ValidationManifest -Status 'passed'
        $candidateSha = (& git rev-parse HEAD).Trim()
        Write-Output "Validation pipeline passed for candidate $candidateSha. Evidence manifest: $manifestPath"
    }
}
catch {
    $failureMessage = $_.Exception.Message
    $failedStage = $activeStage
    if ($activeStage -and $stageRecords.Contains($activeStage)) {
        $stageFailureStatus = if ($incompleteReason) { 'incomplete' } else { 'failed' }
        Complete-QAStage -Name $activeStage -Status $stageFailureStatus -Summary ([ordered]@{ error = $failureMessage })
    }
    if ($promotionStarted) {
        try {
            if ($null -eq $promotionRecord -and (Test-Path -LiteralPath $promotionStatePath)) {
                $promotionObject = Get-Content -LiteralPath $promotionStatePath -Raw | ConvertFrom-Json
                $promotionRecord = [ordered]@{}
                foreach ($property in $promotionObject.PSObject.Properties) {
                    $promotionRecord[$property.Name] = $property.Value
                }
            }
            $promotionRecord.status = 'partial_failure'
            $promotionRecord.failure = $failureMessage
            $promotionRecord.recorded_at_utc = [DateTime]::UtcNow.ToString('o')
            try {
                $promotionRecord.after = Get-DevelopmentRuntimeSnapshot -Project $developmentProject -Services $developmentServices -OverridePath $overridePath
            } catch {
                $promotionRecord.after_snapshot_error = $_.Exception.Message
            }
            Write-PromotionJournal -Path $promotionStatePath -Record $promotionRecord
            Write-Error "Partial development promotion was recorded before service replacement at $promotionStatePath. Restore image/config using the recorded before snapshot; database rollback requires separate recovery."
        } catch {
            Write-Error "Could not update promotion failure evidence: $($_.Exception.Message). Initial promotion journal: $promotionStatePath"
        }
    }
    try {
        $manifestStatus = if ($incompleteReason) { 'incomplete' } else { 'failed' }
        $manifestPath = Write-ValidationManifest -Status $manifestStatus
        if ($manifestStatus -eq 'incomplete') {
            Write-Output "Validation incomplete at stage '$failedStage': $incompleteReason Evidence manifest: $manifestPath"
        } else {
            Write-Output "Validation failed at stage '$failedStage'. Failure evidence manifest: $manifestPath"
        }
    } catch {
        Write-Error "Could not write failure manifest: $($_.Exception.Message)"
    }
    if ($developmentPromoted -and -not $developmentValidated) {
        Write-Error "Development post-promotion validation failed. The promotion journal records previous and current image/config state. Restore database schema separately. Details: $failureMessage"
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
