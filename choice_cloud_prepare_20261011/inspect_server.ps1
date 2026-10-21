# Read-only inspection, suitable for Aliyun Cloud Assistant or an SSH session.
$ErrorActionPreference = 'Stop'
$info = [ordered]@{}
$info.time = (Get-Date).ToString('o')
$info.timezone = (Get-TimeZone).Id
$info.openssh = @((Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0') | Select-Object Name, @{Name='State';Expression={$_.State.ToString()}})
$info.services = @(Get-Service sshd,TermService -ErrorAction SilentlyContinue | Select-Object Name, @{Name='Status';Expression={$_.Status.ToString()}}, @{Name='StartType';Expression={$_.StartType.ToString()}})
$info.listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in 22,3389 } | Select-Object LocalAddress, LocalPort)
$info.rdp_disabled = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server').fDenyTSConnections
$info.python = @(Get-Command python,py -ErrorAction SilentlyContinue | Select-Object Name, Source)
$info.python_registry = @(Get-ChildItem 'HKLM:\SOFTWARE\Python\PythonCore','HKCU:\SOFTWARE\Python\PythonCore' -ErrorAction SilentlyContinue | Select-Object PSChildName)
$info.choice_installed = @(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match 'Choice|金融终端' } | Select-Object DisplayName, DisplayVersion)
$info.disk = @(Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Select-Object DeviceID, FreeSpace, Size)
$ErrorActionPreference = 'Continue'
$info.interactive_sessions = @(quser 2>$null)
$info.interactive_query_exit = $LASTEXITCODE
$info.rdp_firewall = @(Get-NetFirewallRule -Name '*RemoteDesktop*' -ErrorAction SilentlyContinue | Select-Object Name,Enabled,Direction,Action,Profile)
$info | ConvertTo-Json -Depth 5
exit 0
