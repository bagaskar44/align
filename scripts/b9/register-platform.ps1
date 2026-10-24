[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$envFile = Join-Path $repoRoot 'data\b9\private\compose.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    throw 'B9 private configuration is missing; run scripts/b9/setup.ps1 first.'
}
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$docker = if ($dockerCommand) {
    $dockerCommand.Source
} else {
    'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
}
if (-not (Test-Path -LiteralPath $docker)) {
    throw 'Docker CLI was not found.'
}

$composePrefix = @(
    'compose', '--project-name', 'align-b9-sandbox', '--env-file', $envFile,
    '-f', (Join-Path $repoRoot 'docker-compose.yml'),
    '-f', (Join-Path $repoRoot 'docker-compose.b9.yml')
)
& $docker @composePrefix exec -T api align seed | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Synthetic ALIGN seed did not complete.'
}

$registrationLines = & $docker @composePrefix exec -T moodle php /usr/local/bin/moodle-b9-register-tool.php
if ($LASTEXITCODE -ne 0) {
    throw 'Moodle did not register the local LTI 1.3 tool.'
}
$registration = ($registrationLines -join "`n") | ConvertFrom-Json

$identityQuery = 'from sqlalchemy import select; from align_api.db import SessionFactory; from align_api.models import User; db=SessionFactory(); rows=db.scalars(select(User).order_by(User.normalized_email)).all(); print([(row.normalized_email,row.global_role,str(row.id)) for row in rows]); db.close()'
$identityOutput = & $docker exec align-b9-sandbox-api-1 uv run --no-sync --package align-api python -c $identityQuery
if ($LASTEXITCODE -ne 0 -or $identityOutput -notmatch "'admin@align.local', 'admin', '([^']+)'") {
    throw 'The seeded ALIGN operator identity could not be resolved.'
}
$actorId = $Matches[1]

$registerArgs = @(
    'lms', 'platform-register',
    '--issuer', [string]$registration.issuer,
    '--client-id', [string]$registration.client_id,
    '--deployment-id', [string]$registration.deployment_id,
    '--authorization-url', [string]$registration.authorization_url,
    '--token-url', [string]$registration.token_url,
    '--jwks-url', [string]$registration.jwks_url,
    '--moodle-origin', [string]$registration.moodle_origin,
    '--actor-user-id', $actorId
)
$registered = & $docker exec align-b9-sandbox-api-1 align @registerArgs
if ($LASTEXITCODE -ne 0) {
    throw 'ALIGN rejected the Moodle-generated platform registration.'
}

$platforms = (& $docker exec align-b9-sandbox-api-1 align lms platform-list | ConvertFrom-Json)
$platform = @($platforms | Where-Object {
    $_.issuer -eq $registration.issuer -and
    $_.client_id -eq $registration.client_id -and
    $_.deployment_id -eq $registration.deployment_id
}) | Select-Object -First 1
if (-not $platform) {
    throw 'Registered Moodle platform was not found in ALIGN.'
}
$webService = & $docker exec align-b9-sandbox-api-1 align lms ws-check --platform-id $platform.id
if ($LASTEXITCODE -ne 0) {
    throw 'Moodle read-only Web Services validation failed.'
}
$webServiceResult = $webService | ConvertFrom-Json

& $docker @composePrefix exec -T moodle php /usr/local/bin/moodle-b9-seed.php | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Moodle synthetic courses and LTI activities could not be ensured.'
}
$fixtureLines = & $docker @composePrefix exec -T moodle php /usr/local/bin/moodle-b9-fixtures.php
if ($LASTEXITCODE -ne 0) {
    throw 'Moodle did not return the actual synthetic fixture IDs.'
}
$moodleFixtures = ($fixtureLines -join [Environment]::NewLine) | ConvertFrom-Json

$identityBindings = @(
    @{ Moodle = 'b9teacher1'; Email = 'dosen@align.local' },
    @{ Moodle = 'b9teacher2'; Email = 'dosen2@align.local' },
    @{ Moodle = 'b9student1'; Email = 'mahasiswa@align.local' },
    @{ Moodle = 'b9student2'; Email = 'mahasiswa2@align.local' },
    @{ Moodle = 'b9student3'; Email = 'mahasiswa3@align.local' },
    @{ Moodle = 'b9rolemismatch'; Email = 'dosen@align.local' }
)
foreach ($binding in $identityBindings) {
    $pattern = "'{0}', '[^']+', '([^']+)'" -f [regex]::Escape($binding.Email)
    if ($identityOutput -notmatch $pattern) {
        throw "Synthetic ALIGN identity was not found for $($binding.Email)."
    }
    $localUserId = $Matches[1]
    $moodleUser = $moodleFixtures.users.PSObject.Properties[$binding.Moodle].Value
    $moodleUserId = [string]$moodleUser.id
    $bindArgs = @(
        'lms', 'identity-bind', '--platform-id', $platform.id,
        '--subject', $moodleUserId, '--moodle-user-id', $moodleUserId,
        '--user-id', $localUserId, '--actor-user-id', $actorId
    )
    & $docker exec align-b9-sandbox-api-1 align @bindArgs | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not bind synthetic Moodle identity $($binding.Moodle)."
    }
}

$courseQuery = 'from sqlalchemy import select; from align_api.db import SessionFactory; from align_api.models import Course; db=SessionFactory(); rows=db.scalars(select(Course).order_by(Course.code)).all(); print([(row.code,str(row.id)) for row in rows]); db.close()'
$courseOutput = & $docker exec align-b9-sandbox-api-1 uv run --no-sync --package align-api python -c $courseQuery
if ($LASTEXITCODE -ne 0) {
    throw 'Synthetic ALIGN course IDs could not be resolved.'
}
foreach ($courseCode in @('IF101', 'IF202')) {
    if ($courseOutput -notmatch "'$courseCode', '([^']+)'") {
        throw "Synthetic ALIGN course $courseCode was not found."
    }
    $courseId = $Matches[1]
    & $docker exec align-b9-sandbox-api-1 align lms course-grant --platform-id $platform.id --course-id $courseId --actor-user-id $actorId | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Moodle platform could not be granted access to $courseCode."
    }
}

$qaDir = Join-Path $repoRoot 'data\b9\qa'
New-Item -ItemType Directory -Path $qaDir -Force | Out-Null
$record = [ordered]@{
    platform_id = $platform.id
    issuer = $registration.issuer
    client_id = $registration.client_id
    deployment_id = $registration.deployment_id
    authorization_url = $registration.authorization_url
    token_url = $registration.token_url
    jwks_url = $registration.jwks_url
    moodle_origin = $registration.moodle_origin
    moodle_release = $webServiceResult.release
    moodle_version = $webServiceResult.version
    enabled_read_functions = $webServiceResult.enabled_read_functions
}
$recordPath = Join-Path $qaDir 'moodle-platform.json'
[System.IO.File]::WriteAllText(
    $recordPath,
    (($record | ConvertTo-Json -Depth 5) + "`n"),
    [System.Text.UTF8Encoding]::new($false)
)
$fixturePath = Join-Path $qaDir 'moodle-fixtures.json'
$fixtureRecord = [ordered]@{
    platform = $record
    moodle = $moodleFixtures
}
[System.IO.File]::WriteAllText(
    $fixturePath,
    (($fixtureRecord | ConvertTo-Json -Depth 8) + [Environment]::NewLine),
    [System.Text.UTF8Encoding]::new($false)
)
Write-Output ("B9 LTI platform registered from live Moodle metadata; platform id {0}; Moodle {1}." -f $platform.id, $webServiceResult.release)
Write-Output 'Actual Moodle fixture IDs and safe platform metadata saved under ignored data/b9/qa/moodle-fixtures.json.'
