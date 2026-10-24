[CmdletBinding()]
param(
    [switch]$HeavyParser,
    [switch]$AllowExistingB9Proxy
)

$ErrorActionPreference = 'Stop'
$minimumFreeRam = if ($HeavyParser) { 12GB } else { 6GB }
$minimumFreeDisk = if ($HeavyParser) { 25GB } else { 15GB }
$workspaceDrive = [System.IO.Path]::GetPathRoot($PSScriptRoot).TrimEnd('\').TrimEnd(':')
$freeRam = [int64](node -e "process.stdout.write(String(require('os').freemem()))")
if ($LASTEXITCODE -ne 0) {
    throw 'B9 resource guard: Windows available-memory query failed.'
}
$driveRoot = "${workspaceDrive}:\"
$drive = [System.IO.DriveInfo]::new($driveRoot)
$freeDisk = [int64]$drive.AvailableFreeSpace
$parserBudget = 9GB

if ($freeRam -lt $minimumFreeRam) {
    throw "B9 resource guard: available RAM is below $([math]::Round($minimumFreeRam / 1GB, 1)) GiB."
}
if ($freeDisk -lt $minimumFreeDisk) {
    throw "B9 resource guard: free disk is below $([math]::Round($minimumFreeDisk / 1GB, 1)) GiB."
}
if ($HeavyParser -and $freeRam -lt ($parserBudget + 1GB)) {
    throw 'B9 parser guard: the 9 GiB parser budget plus 1 GiB host headroom is unavailable.'
}
$listener = Get-NetTCPConnection -LocalPort 8443 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    if (-not $AllowExistingB9Proxy) {
        throw 'B9 resource guard: TCP port 8443 is already occupied; the existing service was left untouched.'
    }
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    $envFile = Join-Path $repoRoot 'data\b9\private\compose.env'
    $composeArgs = @(
        'compose', '--project-name', 'align-b9-sandbox', '--env-file', $envFile,
        '-f', (Join-Path $repoRoot 'docker-compose.yml'),
        '-f', (Join-Path $repoRoot 'docker-compose.b9.yml'),
        'ps', '--services', '--status', 'running', 'proxy'
    )
    $runningProxy = @(& docker @composeArgs)
    if ($LASTEXITCODE -ne 0 -or $runningProxy -notcontains 'proxy') {
        throw 'B9 resource guard: port 8443 is occupied, but the B9 proxy is not the running listener.'
    }
}
try {
    $dockerMemory = [int64](docker info --format '{{.MemTotal}}')
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Engine query failed.'
    }
} catch {
    throw 'B9 resource guard: Docker Engine is unavailable or inaccessible.'
}
if ($dockerMemory -lt 6GB) {
    throw 'B9 resource guard: Docker Engine has less than 6 GiB of memory assigned.'
}
if ($HeavyParser -and $dockerMemory -lt ($parserBudget + 1GB)) {
    throw 'B9 parser guard: Docker Engine memory is below the 9 GiB parser budget plus 1 GiB headroom.'
}
Write-Output ("B9 guard passed: free RAM {0:N1} GiB, free disk {1:N1} GiB, parser budget 9 GiB, port 8443 free." -f ($freeRam / 1GB), ($freeDisk / 1GB))
