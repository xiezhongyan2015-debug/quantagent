# Prepared, pending user approval: replace old /32, do not widen the subnet.
$ErrorActionPreference = 'Stop'
$source = '192.0.2.10/32'
foreach ($name in @('OpenSSH-Server-In-TCP','Choice-RDP-Current-Mac')) {
    Set-NetFirewallRule -Name $name -RemoteAddress $source
    $rule = Get-NetFirewallRule -Name $name
    Write-Output ($name + ': ' + (($rule | Get-NetFirewallAddressFilter).RemoteAddress -join ','))
}
