function Write-PromotionJournal {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Record
    )
    [System.IO.File]::WriteAllText(
        $Path,
        ($Record | ConvertTo-Json -Depth 16),
        [System.Text.UTF8Encoding]::new($false)
    )
}

function Invoke-RecordedPromotion {
    param(
        [Parameter(Mandatory = $true)][string]$JournalPath,
        [Parameter(Mandatory = $true)][object]$Before,
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$TargetImageIds,
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$TargetConfigDigests,
        [Parameter(Mandatory = $true)][string]$ReleaseId,
        [Parameter(Mandatory = $true)][string]$ReleaseHash,
        [Parameter(Mandatory = $true)][scriptblock]$StartServices,
        [Parameter(Mandatory = $true)][scriptblock]$CaptureAfter,
        [string]$RecoveryNote = 'Image rollback does not restore database changes.'
    )
    $record = [ordered]@{
        schema_version = 1
        status = 'prepared'
        recorded_at_utc = [DateTime]::UtcNow.ToString('o')
        release_id = $ReleaseId
        release_config_hash = $ReleaseHash
        before = $Before
        target_image_ids = $TargetImageIds
        target_config_sha256 = $TargetConfigDigests
        after = $null
        failure = $null
        recovery_note = $RecoveryNote
    }
    Write-PromotionJournal -Path $JournalPath -Record $record
    $record.status = 'promotion_started'
    $record.recorded_at_utc = [DateTime]::UtcNow.ToString('o')
    Write-PromotionJournal -Path $JournalPath -Record $record
    try {
        & $StartServices
        $record.after = & $CaptureAfter
        $record.status = 'services_healthy'
        $record.recorded_at_utc = [DateTime]::UtcNow.ToString('o')
        Write-PromotionJournal -Path $JournalPath -Record $record
        return $record
    } catch {
        $record.status = 'partial_failure'
        $record.failure = $_.Exception.Message
        $record.recorded_at_utc = [DateTime]::UtcNow.ToString('o')
        try {
            $record.after = & $CaptureAfter
        } catch {
            $record.after_snapshot_error = $_.Exception.Message
        }
        Write-PromotionJournal -Path $JournalPath -Record $record
        throw
    }
}
