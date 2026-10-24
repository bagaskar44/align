[CmdletBinding()]
param(
    [switch]$InstallHostTrust
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$privateDir = Join-Path $repoRoot 'data\b9\private'
New-Item -ItemType Directory -Path $privateDir -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $repoRoot 'data\b9\qa') -Force | Out-Null

function New-RandomSecret([int]$ByteCount = 32) {
    $bytes = New-Object byte[] $ByteCount
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
        return [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
    } finally {
        $generator.Dispose()
    }
}

function Get-OrCreateSecret([string]$Name, [int]$ByteCount = 32) {
    $path = Join-Path $privateDir $Name
    if (Test-Path -LiteralPath $path) {
        return (Get-Content -LiteralPath $path -Raw).Trim()
    }
    $secret = New-RandomSecret $ByteCount
    [System.IO.File]::WriteAllText($path, $secret + "`n", [System.Text.UTF8Encoding]::new($false))
    return $secret
}

function Get-OrCreateMoodlePassword([string]$Name) {
    $path = Join-Path $privateDir $Name
    if (Test-Path -LiteralPath $path) {
        $existing = (Get-Content -LiteralPath $path -Raw).Trim()
        if ($existing -match '^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z\d]).{20,}$') {
            return $existing
        }
    }
    $secret = 'B9-' + (New-RandomSecret 32) + 'xA1!'
    Write-PrivateText $Name ($secret + "`n")
    return $secret
}

function Write-PrivateText([string]$Name, [string]$Content) {
    $path = Join-Path $privateDir $Name
    if (Test-Path -LiteralPath $path) {
        $item = Get-Item -LiteralPath $path -Force
        $item.Attributes = $item.Attributes -band (-bnot [System.IO.FileAttributes]::Hidden)
    }
    [System.IO.File]::WriteAllText($path, $Content, [System.Text.UTF8Encoding]::new($false))
}

$rootCertPath = Join-Path $privateDir 'ca.crt'
$leafCertPath = Join-Path $privateDir 'align-b9.crt'
$leafKeyPath = Join-Path $privateDir 'align-b9.key'
$rootKeyPath = Join-Path $privateDir 'ca.key'
$rootThumbPath = Join-Path $privateDir 'ca-thumbprint.txt'
$certificateFiles = @($rootCertPath, $leafCertPath, $leafKeyPath, $rootKeyPath, $rootThumbPath)
$certificateCount = @($certificateFiles | Where-Object { Test-Path -LiteralPath $_ }).Count
if ($certificateCount -ne $certificateFiles.Count) {
    $python = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        throw 'B9 certificate creation needs the repository Python environment (.venv).'
    }
    & $python (Join-Path $PSScriptRoot 'generate_ca.py') $privateDir
    if ($LASTEXITCODE -ne 0) {
        throw 'B9 sandbox CA generation failed.'
    }
}

$moodleDbPassword = Get-OrCreateSecret 'moodle-db-password.txt'
$alignDbPassword = Get-OrCreateSecret 'align-db-password.txt'
$bootstrapDbPassword = Get-OrCreateSecret 'bootstrap-db-password.txt'
$migrationDbPassword = Get-OrCreateSecret 'migration-db-password.txt'
$apiDbPassword = Get-OrCreateSecret 'api-db-password.txt'
$workerDbPassword = Get-OrCreateSecret 'worker-db-password.txt'
$operatorDbPassword = Get-OrCreateSecret 'operator-db-password.txt'
$csrfSecret = Get-OrCreateSecret 'csrf-secret.txt'
$storageSecret = Get-OrCreateSecret 'storage-secret.txt'
$seedPassword = Get-OrCreateSecret 'align-seed-password.txt'
$moodleAdminPassword = Get-OrCreateMoodlePassword 'moodle-admin-password.txt'
$moodleTestPassword = Get-OrCreateMoodlePassword 'moodle-test-password.txt'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'B9 setup needs the repository Python environment (.venv).'
}
& $python (Join-Path $PSScriptRoot 'generate_lti_keys.py') $privateDir
if ($LASTEXITCODE -ne 0) {
    throw 'B9 LTI key generation or verification failed.'
}

$privateValues = [ordered]@{
    ALIGN_HOST_PORT = '8443'
    APP_ORIGIN = 'https://align-b9.localhost:8443'
    ALIGN_CADDYFILE = './docker/Caddyfile.b9'
    ALIGN_POSTGRES_ENV_FILE = './data/b9/private/postgres.env'
    ALIGN_MIGRATION_ENV_FILE = './data/b9/private/migration.env'
    ALIGN_API_ENV_FILE = './data/b9/private/api.env'
    ALIGN_OPERATOR_ENV_FILE = './data/b9/private/operator.env'
    ALIGN_WORKER_ENV_FILE = './data/b9/private/worker.env'
    ALIGN_DISPATCHER_ENV_FILE = './data/b9/private/worker.env'
    ALIGN_SCHEDULER_ENV_FILE = './data/b9/private/worker.env'
    ALIGN_QA_REDIS_QUEUE_PREFIX = 'align-b9'
    POSTGRES_DB = 'align_b9'
    POSTGRES_PASSWORD = $bootstrapDbPassword
    OPERATOR_DB_PASSWORD = $operatorDbPassword
    S3_BUCKET = 'align-b9-private'
    B9_MOODLE_DB_PASSWORD = $moodleDbPassword
    B9_MOODLE_ADMIN_USER = 'b9admin'
    B9_MOODLE_ADMIN_PASSWORD = $moodleAdminPassword
    B9_MOODLE_ADMIN_EMAIL = 'admin@b9.localhost'
    B9_MOODLE_TEST_PASSWORD = $moodleTestPassword
}
$composeEnv = ($privateValues.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join "`n"
Write-PrivateText 'compose.env' ($composeEnv + "`n")

$postgresEnv = @"
POSTGRES_USER=align_db_bootstrap
POSTGRES_PASSWORD=$bootstrapDbPassword
POSTGRES_DB=align_b9
MIGRATION_DB_PASSWORD=$migrationDbPassword
API_DB_PASSWORD=$apiDbPassword
WORKER_DB_PASSWORD=$workerDbPassword
OPERATOR_DB_PASSWORD=$operatorDbPassword
"@
$migrationEnv = @"
APP_ENV=development
DATA_MODE=synthetic
PROVIDER_MODE=off
MIGRATION_DATABASE_URL=postgresql+psycopg://align_migration:$migrationDbPassword@postgres:5432/align_b9
"@
$apiEnv = @"
APP_ENV=development
APP_DEMO_MODE=true
DATA_MODE=synthetic
PROVIDER_MODE=off
LMS_ADAPTER_ENABLED=true
DATABASE_URL=postgresql+psycopg://align_api:$apiDbPassword@postgres:5432/align_b9
WORKER_DATABASE_URL=postgresql+psycopg://align_worker:$workerDbPassword@postgres:5432/align_b9
REDIS_URL=redis://redis:6379/0
REDIS_QUEUE_PREFIX=align-b9
S3_ENDPOINT=http://object-storage:9090
S3_BUCKET=align-b9-private
S3_ACCESS_KEY_ID=align-app
S3_SECRET_ACCESS_KEY=$storageSecret
CSRF_SERVER_SECRET=$csrfSecret
ALIGN_SEED_PASSWORD=$seedPassword
LTI_CALLBACK_URL=https://align-b9.localhost:8443/api/v1/lti/launch
LTI_ALLOWED_ORIGINS=https://moodle-b9.localhost:8443
MOODLE_WS_TOKEN_FILE=/run/b9-secrets/ws-token.txt
LTI_TOOL_JWKS_FILE=/run/b9-config/lti-tool-jwks.json
MOODLE_WS_TIMEOUT_SECONDS=120
MOODLE_WS_MAX_RESPONSE_BYTES=1048576
"@
$operatorEnv = @"
APP_ENV=development
DATA_MODE=synthetic
PROVIDER_MODE=off
OPERATOR_DATABASE_URL=postgresql+psycopg://align_operator:$operatorDbPassword@postgres:5432/align_b9
"@
$workerEnv = @"
APP_ENV=development
DATA_MODE=synthetic
PROVIDER_MODE=off
WORKER_DATABASE_URL=postgresql+psycopg://align_worker:$workerDbPassword@postgres:5432/align_b9
DATABASE_URL=postgresql+psycopg://align_worker:$workerDbPassword@postgres:5432/align_b9
REDIS_URL=redis://redis:6379/0
REDIS_QUEUE_PREFIX=align-b9
S3_ENDPOINT=http://object-storage:9090
S3_BUCKET=align-b9-private
S3_ACCESS_KEY_ID=align-app
S3_SECRET_ACCESS_KEY=$storageSecret
"@
foreach ($entry in @(
    @{ Name = 'postgres.env'; Value = $postgresEnv },
    @{ Name = 'migration.env'; Value = $migrationEnv },
    @{ Name = 'api.env'; Value = $apiEnv },
    @{ Name = 'operator.env'; Value = $operatorEnv },
    @{ Name = 'worker.env'; Value = $workerEnv }
)) {
    Write-PrivateText $entry.Name ($entry.Value.Trim() + "`n")
}

foreach ($secretFile in Get-ChildItem -LiteralPath $privateDir -File) {
    $secretFile.Attributes = $secretFile.Attributes -bor [System.IO.FileAttributes]::Hidden
}
if ($InstallHostTrust) {
    $thumbprint = (Get-Content -LiteralPath $rootThumbPath -Raw).Trim()
    & certutil.exe -user -store Root $thumbprint *> $null
    if ($LASTEXITCODE -ne 0) {
        & certutil.exe -user -addstore -f Root $rootCertPath *> $null
        if ($LASTEXITCODE -ne 0) {
            throw 'B9 sandbox CA could not be added to the current-user trust store.'
        }
    }
}
if ($InstallHostTrust) {
    Write-Output 'B9 private configuration, sandbox CA, and user trust are ready. Secret values were not displayed.'
} else {
    Write-Output 'B9 private configuration and container trust materials are ready. Host browser trust was not changed; secret values were not displayed.'
}
