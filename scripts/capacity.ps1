$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class AlignMemoryInfo {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)]
    public class MEMORYSTATUSEX {
        public uint dwLength = (uint)Marshal.SizeOf(typeof(MEMORYSTATUSEX));
        public uint dwMemoryLoad;
        public ulong ullTotalPhys;
        public ulong ullAvailPhys;
        public ulong ullTotalPageFile;
        public ulong ullAvailPageFile;
        public ulong ullTotalVirtual;
        public ulong ullAvailVirtual;
        public ulong ullAvailExtendedVirtual;
    }
    [DllImport("kernel32.dll", CharSet = CharSet.Auto, SetLastError = true)]
    public static extern bool GlobalMemoryStatusEx([In, Out] MEMORYSTATUSEX lpBuffer);
}
'@

$memory = New-Object AlignMemoryInfo+MEMORYSTATUSEX
if (-not [AlignMemoryInfo]::GlobalMemoryStatusEx($memory)) {
    throw 'GlobalMemoryStatusEx failed.'
}

function Get-DockerValue {
    param([string[]]$Arguments)
    try {
        $result = & docker @Arguments 2>$null
        if ($LASTEXITCODE -eq 0) { return ($result | Out-String).Trim() }
    }
    catch {
        # Docker Desktop may be installed while its engine is stopped.
    }
    return 'unavailable'
}

[pscustomobject]@{
    os = [System.Runtime.InteropServices.RuntimeInformation]::OSDescription
    architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
    total_memory_bytes = $memory.ullTotalPhys
    available_memory_bytes = $memory.ullAvailPhys
    total_memory_gib = [Math]::Round($memory.ullTotalPhys / 1GB, 2)
    available_memory_gib = [Math]::Round($memory.ullAvailPhys / 1GB, 2)
    logical_processors = [Environment]::ProcessorCount
    docker_cli = Get-DockerValue @('--version')
    docker_compose = Get-DockerValue @('compose', 'version')
    docker_engine = Get-DockerValue @('info', '--format', '{{.ServerVersion}}')
} | ConvertTo-Json -Depth 3

