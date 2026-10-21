# User approved inbound TCP 3389 only from this Mac's current public IP.
$ErrorActionPreference = 'Stop'
$name = 'Choice-RDP-Current-Mac'
$address = '192.0.2.10/32'
if (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue) {
    Set-NetFirewallRule -Name $name -Enabled True -Direction Inbound -Action Allow -Profile Any -RemoteAddress $address
} else {
    New-NetFirewallRule -Name $name -DisplayName 'Choice RDP - current Mac IP only' -Enabled True -Direction Inbound -Action Allow -Profile Any -Protocol TCP -LocalPort 3389 -RemoteAddress $address | Out-Null
}
@{
    rule = $name
    enabled = (Get-NetFirewallRule -Name $name).Enabled.ToString()
    source = @((Get-NetFirewallRule -Name $name | Get-NetFirewallAddressFilter).RemoteAddress)
    port = @((Get-NetFirewallRule -Name $name | Get-NetFirewallPortFilter).LocalPort)
} | ConvertTo-Json -Compress
