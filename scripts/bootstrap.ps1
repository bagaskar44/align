param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot '.env'

if ((Test-Path -LiteralPath $envPath) -and -not $Force) {
    throw '.env already exists. Review it or pass -Force to replace it.'
}

function New-SecretHex {
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $bytes = New-Object byte[] 32
        $random.GetBytes($bytes)
        return [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $random.Dispose()
    }
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Content)
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

$values = @{
    'replace-with-random-secret' = New-SecretHex
    'replace-with-at-least-32-random-bytes' = New-SecretHex
    'replace-with-random-development-password' = New-SecretHex
}
$envTemplate = Get-Content -LiteralPath (Join-Path $repoRoot '.env.example') -Raw -Encoding UTF8
foreach ($placeholder in $values.Keys) {
    $envTemplate = $envTemplate.Replace($placeholder, $values[$placeholder])
}
$envTemplate = $envTemplate.Replace('http://object-storage:9000', 'http://object-storage:9090')
$envTemplate = $envTemplate.Replace(
    'DATABASE_URL=postgresql+psycopg://align_api:' + $values['replace-with-random-secret'] + '@postgres:5432/align',
    'DATABASE_URL=postgresql+psycopg://align_api:' + (New-SecretHex) + '@postgres:5432/align'
)

# Give each database role an independent credential and rebuild its DSN from the generated values.
$rootPassword = New-SecretHex
$migrationPassword = New-SecretHex
$apiPassword = New-SecretHex
$workerPassword = New-SecretHex
$operatorPassword = New-SecretHex
$s3Password = New-SecretHex
$envTemplate = $envTemplate.Replace('POSTGRES_PASSWORD=' + $values['replace-with-random-secret'], 'POSTGRES_PASSWORD=' + $rootPassword)
$envTemplate = $envTemplate.Replace('MIGRATION_DB_PASSWORD=' + $values['replace-with-random-secret'], 'MIGRATION_DB_PASSWORD=' + $migrationPassword)
$envTemplate = $envTemplate.Replace('API_DB_PASSWORD=' + $values['replace-with-random-secret'], 'API_DB_PASSWORD=' + $apiPassword)
$envTemplate = $envTemplate.Replace('WORKER_DB_PASSWORD=' + $values['replace-with-random-secret'], 'WORKER_DB_PASSWORD=' + $workerPassword)
$envTemplate = $envTemplate.Replace('OPERATOR_DB_PASSWORD=' + $values['replace-with-random-secret'], 'OPERATOR_DB_PASSWORD=' + $operatorPassword)
$envTemplate = $envTemplate.Replace('S3_SECRET_ACCESS_KEY=' + $values['replace-with-random-secret'], 'S3_SECRET_ACCESS_KEY=' + $s3Password)

$envTemplate = [regex]::Replace($envTemplate, '(?m)^DATABASE_URL=.*$', "DATABASE_URL=postgresql+psycopg://align_api:$apiPassword@postgres:5432/align")
$envTemplate = [regex]::Replace($envTemplate, '(?m)^WORKER_DATABASE_URL=.*$', "WORKER_DATABASE_URL=postgresql+psycopg://align_worker:$workerPassword@postgres:5432/align")
$envTemplate = [regex]::Replace($envTemplate, '(?m)^MIGRATION_DATABASE_URL=.*$', "MIGRATION_DATABASE_URL=postgresql+psycopg://align_migration:$migrationPassword@postgres:5432/align")
$envTemplate = [regex]::Replace($envTemplate, '(?m)^OPERATOR_DATABASE_URL=.*$', "OPERATOR_DATABASE_URL=postgresql+psycopg://align_operator:$operatorPassword@postgres:5432/align")
$envTemplate = [regex]::Replace($envTemplate, '(?m)^S3_ACCESS_KEY_ID=.*$', "S3_ACCESS_KEY_ID=align-$(New-SecretHex)")
$envTemplate = [regex]::Replace($envTemplate, '(?m)^S3_SECRET_ACCESS_KEY=.*$', "S3_SECRET_ACCESS_KEY=$s3Password")

Write-Utf8NoBom -Path $envPath -Content $envTemplate
$environmentValues = @{}
Get-Content -LiteralPath $envPath -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith('#')) { return }
    $separator = $line.IndexOf('=')
    if ($separator -lt 1) { return }
    $environmentName = $line.Substring(0, $separator)
    $environmentValue = $line.Substring($separator + 1)
    $environmentValues[$environmentName] = $environmentValue
}

function Write-ServiceEnvironment {
    param([string]$FileName, [string[]]$Names)
    $serviceLines = foreach ($name in $Names) {
        if (-not $environmentValues.ContainsKey($name)) {
            throw "Missing environment value $name while preparing $FileName."
        }
        "$name=$($environmentValues[$name])"
    }
    $content = ($serviceLines -join "`n") + "`n"
    Write-Utf8NoBom -Path (Join-Path $repoRoot $FileName) -Content $content
}

$apiNames = @(
    'APP_ENV','APP_ORIGIN','APP_DEBUG','APP_DEMO_MODE','APP_LOG_LEVEL','DATABASE_URL',
    'REDIS_URL','REDIS_QUEUE_PREFIX','S3_ENDPOINT','S3_BUCKET','S3_ACCESS_KEY_ID',
    'S3_SECRET_ACCESS_KEY','S3_REGION','S3_USE_PATH_STYLE','CSRF_SERVER_SECRET',
    'PROVIDER_MODE','OPENAI_API_KEY','MODEL_EFFICIENT','MODEL_COMPLEX','MODEL_VISION',
    'EMBEDDING_MODEL','EMBEDDING_DIMENSIONS','MODEL_RATE_MANIFEST','ROUTING_POLICY_FILE',
    'BATCHING_POLICY_FILE','OCR_ENGINE','OCR_LANGUAGE','DATA_MODE','LMS_ADAPTER_ENABLED',
    'ALIGN_SEED_PASSWORD','BUDGET_CONFIRMED_USD','BUDGET_RESERVE_USD','BUDGET_PLANNED_TOPUP_USD',
    'RETENTION_SOURCE_DAYS','RETENTION_REVIEW_DAYS','BACKUP_RETENTION_DAYS',
    'MAX_FILES_PER_BUNDLE','MAX_FILE_BYTES','MAX_IMAGE_PIXELS','MAX_PDF_IMAGE_PAGES_PER_BUNDLE',
    'MAX_XLSX_SHEETS','MAX_XLSX_NONEMPTY_CELLS','MAX_DOCX_NODES','MAX_DOCX_MEDIA',
    'MAX_OOXML_ENTRIES','MAX_OOXML_EXPANDED_BYTES','JOB_MAX_CALLS','JOB_MAX_VISION_CALLS',
    'JOB_MAX_TEXT_INPUT_TOKENS','JOB_MAX_TEXT_OUTPUT_TOKENS','JOB_MAX_EMBEDDING_TOKENS',
    'JOB_MAX_ACTIVE_SECONDS','ANALYSIS_BUDGET_USD','FEEDBACK_BUDGET_USD','CONTEXT_DRAFT_BUDGET_USD',
    'SPEC_DRAFT_BUDGET_USD','PARSER_PROFILE_NATIVE','PARSER_PROFILE_DOCUMENT',
    'PARSER_NATIVE_MEMORY_BYTES','PARSER_DOCUMENT_MEMORY_BYTES','PARSER_LIGHT_MEMORY_BYTES',
    'PARSER_NODE_MEMORY_BUDGET_BYTES','PARSER_NODE_HEADROOM_BYTES','PARSER_NODE_ID',
    'PARSER_HEAVY_MAX_PER_NODE','PARSER_PAGE_BATCH_SIZE',
    'PARSER_TASK_SOFT_SECONDS','PARSER_TASK_HARD_SECONDS','PARSER_MAX_ATTEMPTS','STAGE_SOFT_SECONDS','STAGE_HARD_SECONDS',
    'BROKER_VISIBILITY_SECONDS','WORKER_LEASE_SECONDS','WORKER_HEARTBEAT_SECONDS',
    'REAPER_INTERVAL_SECONDS','BATCH_BASELINE_ELEMENTS','BATCH_MAX_ELEMENTS'
)
$workerNames = @(
    'WORKER_DATABASE_URL','REDIS_URL','REDIS_QUEUE_PREFIX','APP_LOG_LEVEL','S3_ENDPOINT','S3_BUCKET',
    'S3_ACCESS_KEY_ID','S3_SECRET_ACCESS_KEY','S3_REGION','S3_USE_PATH_STYLE','OCR_ENGINE','OCR_LANGUAGE',
    'MAX_FILE_BYTES','MAX_IMAGE_PIXELS','MAX_DOCX_NODES','MAX_DOCX_MEDIA','MAX_OOXML_ENTRIES',
    'MAX_OOXML_EXPANDED_BYTES','PARSER_PROFILE_NATIVE','PARSER_PROFILE_DOCUMENT',
    'PARSER_NATIVE_MEMORY_BYTES','PARSER_DOCUMENT_MEMORY_BYTES','PARSER_NODE_MEMORY_BUDGET_BYTES',
    'PARSER_NODE_HEADROOM_BYTES','PARSER_NODE_ID','PARSER_HEAVY_MAX_PER_NODE','PARSER_TASK_SOFT_SECONDS',
    'PARSER_TASK_HARD_SECONDS','PARSER_MAX_ATTEMPTS','STAGE_SOFT_SECONDS',
    'STAGE_HARD_SECONDS','BROKER_VISIBILITY_SECONDS','WORKER_LEASE_SECONDS',
    'WORKER_HEARTBEAT_SECONDS','REAPER_INTERVAL_SECONDS'
)
$migrationNames = @('MIGRATION_DATABASE_URL')
$postgresNames = @(
    'POSTGRES_DB','POSTGRES_USER','POSTGRES_PASSWORD','MIGRATION_DB_PASSWORD',
    'API_DB_PASSWORD','WORKER_DB_PASSWORD','OPERATOR_DB_PASSWORD'
)
Write-ServiceEnvironment '.env.api' $apiNames
Write-ServiceEnvironment '.env.worker' $workerNames
Write-ServiceEnvironment '.env.migration' $migrationNames
Write-ServiceEnvironment '.env.postgres' $postgresNames
Write-ServiceEnvironment '.env.tests' @('DATABASE_URL','WORKER_DATABASE_URL')

$testEnvironmentPath = Join-Path $repoRoot '.env.tests'
$testEnvironment = [System.IO.File]::ReadAllText($testEnvironmentPath) + "ALIGN_RUN_INTEGRATION=true`n"
Write-Utf8NoBom -Path $testEnvironmentPath -Content $testEnvironment

Write-Output 'Created a private development .env with independent random secrets.'
Write-Output 'Created filtered service environments so each container receives only its database role.'
Write-Output 'The synthetic demo password is stored as ALIGN_SEED_PASSWORD in .env; it was not printed.'
Write-Output 'Do not use this generated environment for production.'

