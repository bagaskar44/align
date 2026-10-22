$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\..\scripts\promotion-orchestration.ps1')

$testRoot = Join-Path $env:TEMP "align-promotion-test-$([Guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $testRoot | Out-Null
$script:mockJournalPath = Join-Path $testRoot 'success.json'
$script:mockDockerCommands = [System.Collections.Generic.List[string]]::new()
$script:mockDockerFailure = $false

function docker {
    $script:mockDockerCommands.Add(($args -join ' '))
    $journal = Get-Content -LiteralPath $script:mockJournalPath -Raw | ConvertFrom-Json
    if ($journal.status -ne 'promotion_started') { throw 'Docker was called before the promotion journal was durable.' }
    if ($script:mockDockerFailure) {
        $global:LASTEXITCODE = 1
        return
    }
    $global:LASTEXITCODE = 0
}

$before = @{ services = @(@{ service = 'api'; image_id = 'sha256:old' }); config_sha256 = @{ 'docker-compose.yml' = 'a' * 64 } }
$targetImages = [ordered]@{ api = 'sha256:target' }
$targetConfig = [ordered]@{ 'docker-compose.yml' = 'b' * 64 }
$success = Invoke-RecordedPromotion `
    -JournalPath $script:mockJournalPath `
    -Before $before `
    -TargetImageIds $targetImages `
    -TargetConfigDigests $targetConfig `
    -ReleaseId 'release-1' `
    -ReleaseHash ('c' * 64) `
    -StartServices { docker compose --project-name align-qa up --pull never --no-build --detach --wait api; if ($LASTEXITCODE -ne 0) { throw 'mock Docker promotion failed' } } `
    -CaptureAfter { return @{ services = @(@{ service = 'api'; image_id = 'sha256:target' }) } }
if ($success.status -ne 'services_healthy' -or $script:mockDockerCommands.Count -ne 1) {
    throw 'Successful mocked promotion did not record its Docker command and final state.'
}

$script:mockJournalPath = Join-Path $testRoot 'partial.json'
$script:mockDockerFailure = $true
try {
    Invoke-RecordedPromotion `
        -JournalPath $script:mockJournalPath `
        -Before $before `
        -TargetImageIds $targetImages `
        -TargetConfigDigests $targetConfig `
        -ReleaseId 'release-2' `
        -ReleaseHash ('d' * 64) `
        -StartServices { docker compose --project-name align-qa up --pull never --no-build --detach --wait api; if ($LASTEXITCODE -ne 0) { throw 'mock Docker partial failure' } } `
        -CaptureAfter { return @{ services = @(@{ service = 'api'; image_id = 'sha256:partially-replaced' }) } } | Out-Null
    throw 'Expected the mocked Docker failure to propagate.'
} catch {
    if ($_.Exception.Message -notmatch 'mock Docker partial failure') { throw }
}
$partial = Get-Content -LiteralPath $script:mockJournalPath -Raw | ConvertFrom-Json
if ($partial.status -ne 'partial_failure' -or $partial.before.services[0].image_id -ne 'sha256:old' -or
    $partial.target_image_ids.api -ne 'sha256:target' -or
    $partial.after.services[0].image_id -ne 'sha256:partially-replaced') {
    throw 'Partial promotion did not preserve before, target and after image evidence.'
}

Remove-Item -LiteralPath (Join-Path $testRoot 'success.json')
Remove-Item -LiteralPath (Join-Path $testRoot 'partial.json')
Remove-Item -LiteralPath $testRoot
Write-Output 'Mocked Docker promotion orchestration passed.'
