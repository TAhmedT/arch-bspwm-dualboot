[CmdletBinding()]
param(
    [switch]$Prepare,
    [switch]$SuspendBitLocker,
    [switch]$ResumeBitLocker
)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open PowerShell as Administrator and run this script again.'
}
if ($SuspendBitLocker -and $ResumeBitLocker) { throw 'Choose suspend OR resume, not both.' }
Get-Disk | Select-Object Number,FriendlyName,PartitionStyle,Size,IsBoot,IsSystem | Format-Table
Write-Host 'Secure Boot:' (Confirm-SecureBootUEFI)
Get-Tpm | Select-Object TpmPresent,TpmReady,TpmEnabled | Format-List
& manage-bde.exe -status C:
if ($LASTEXITCODE -ne 0) { throw 'Cannot read BitLocker status. Resolve this before changing boot settings.' }
if ($Prepare) {
    # Windows and Linux both use UTC for RTC. No timezone change.
    $timeKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\TimeZoneInformation'
    New-ItemProperty -LiteralPath $timeKey -Name RealTimeIsUniversal -PropertyType DWord -Value 1 -Force | Out-Null
    & powercfg.exe /h off
    if ($LASTEXITCODE -ne 0) { throw 'Failed to disable hibernation / Fast Startup.' }
    Write-Host 'UTC RTC enabled. Hibernation and Fast Startup disabled; normal sleep remains available.'
}
if ($SuspendBitLocker) {
    $confirmation = Read-Host 'After saving your recovery key outside this PC, type RECOVERY KEY SAVED'
    if ($confirmation -cne 'RECOVERY KEY SAVED') { throw 'Cancelled; BitLocker protection unchanged.' }
    Suspend-BitLocker -MountPoint 'C:' -RebootCount 0
    Write-Host 'Protection is suspended until you explicitly resume it. The drive stays encrypted.'
}
if ($ResumeBitLocker) {
    Resume-BitLocker -MountPoint 'C:'
    Write-Host 'BitLocker protection resumed.'
}
Write-Host 'This script never changes Secure Boot, firmware keys, TPM, partitions, or BIOS settings.'
