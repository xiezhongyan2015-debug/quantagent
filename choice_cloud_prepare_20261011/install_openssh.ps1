# Authorized scope: Microsoft OpenSSH service, TCP 22 from this Mac's IP only.
# No credentials, account bindings, Choice processes, or trading tasks are created.
$ErrorActionPreference = 'Stop'
$sourceAddress = '192.0.2.10/32'
$capabilityName = 'OpenSSH.Server~~~~0.0.1.0'
$capability = Get-WindowsCapability -Online -Name $capabilityName
if ($capability.State -ne 'Installed') {
    $installed = Add-WindowsCapability -Online -Name $capabilityName
    if ($installed.RestartNeeded) { throw 'OpenSSH installation requires a user-managed restart.' }
}
$ruleName = 'OpenSSH-Server-In-TCP'
if (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) {
    Set-NetFirewallRule -Name $ruleName -Enabled True -RemoteAddress $sourceAddress
} else {
    New-NetFirewallRule -Name $ruleName -DisplayName 'OpenSSH from current Mac only' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 -RemoteAddress $sourceAddress | Out-Null
}
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
$service = Get-Service sshd
if ($service.Status -ne 'Running') { throw 'sshd is not running.' }
Write-Output ('SSHD_STATUS=' + $service.Status)
Write-Output ('SSH_FIREWALL_SOURCE=' + $sourceAddress)
Get-NetTCPConnection -LocalPort 22 -State Listen | ForEach-Object { Write-Output ('SSH_LISTENER=' + $_.LocalAddress + ':' + $_.LocalPort) }
$keygen = Join-Path $env:WINDIR 'System32\OpenSSH\ssh-keygen.exe'
Get-ChildItem 'C:\ProgramData\ssh\ssh_host_*_key.pub' | ForEach-Object { & $keygen -lf $_.FullName }
