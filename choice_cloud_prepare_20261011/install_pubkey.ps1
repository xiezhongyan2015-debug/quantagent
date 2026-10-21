# Run on the Windows server as Administrator: enable key authentication for the automation key only.
$ErrorActionPreference = 'Stop'
$pub = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICtafKIp9dErJ6laKatXp3ObGWqcUlujkEPOXfmrCp2O choice-executor automation 2026-10-12 mac-leslie'
$file = 'C:\ProgramData\ssh\administrators_authorized_keys'
if (-not (Test-Path $file)) { New-Item -ItemType File -Path $file -Force | Out-Null }
$existing = Get-Content $file -ErrorAction SilentlyContinue
if ($existing -notcontains $pub) { Add-Content -Path $file -Value $pub -Encoding ascii }
icacls $file /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null
$cfg = 'C:\ProgramData\ssh\sshd_config'
$text = Get-Content $cfg -Raw
if ($text -match '(?m)^\s*#?\s*PubkeyAuthentication\s+\S+') { $text = [regex]::Replace($text, '(?m)^\s*#?\s*PubkeyAuthentication\s+\S+', 'PubkeyAuthentication yes') } else { $text = "PubkeyAuthentication yes`r`n" + $text }
Set-Content -Path $cfg -Value $text -Encoding ascii
Restart-Service sshd
Write-Output ('KEY_INSTALLED lines=' + (Get-Content $file).Count + ' pubkey=' + ((Get-Content $cfg) -match '^PubkeyAuthentication')[0])
