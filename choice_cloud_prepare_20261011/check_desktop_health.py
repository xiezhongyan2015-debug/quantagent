import base64,subprocess,json,time
from pathlib import Path
p=Path(__file__).resolve().parent
code=r"""[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$os=Get-CimInstance Win32_OperatingSystem
$cpu=Get-CimInstance Win32_Processor
$procs=Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 12 Name,Id,CPU,@{n='MemoryMB';e={[math]::Round($_.WorkingSet64/1MB)}},Responding,SessionId
$apps=@(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue | Where-Object {$_.DisplayName -match 'Choice|Python'} | Select-Object DisplayName,DisplayVersion,InstallLocation)
[ordered]@{time=(Get-Date).ToString('o');cpu_percent=@($cpu.LoadPercentage);free_memory_mb=[math]::Round($os.FreePhysicalMemory/1024);total_memory_mb=[math]::Round($os.TotalVisibleMemorySize/1024);processes=@($procs);apps=$apps;desktop_sessions=(query user 2>&1 | Out-String)} | ConvertTo-Json -Depth 5
"""
encoded=base64.b64encode(code.encode('utf-16-le')).decode()
t=time.monotonic()
r=subprocess.run(['ssh','-S','/tmp/choice-ssh-leslie-20261011/control','-F',str(p/'ssh_config'),'-o','BatchMode=yes','choice-executor','powershell.exe -NoLogo -NoProfile -EncodedCommand '+encoded],capture_output=True,text=True,timeout=30)
print('Roundtrip seconds:',round(time.monotonic()-t,2))
if r.returncode: print(r.stderr);raise SystemExit(r.returncode)
data=json.loads(r.stdout.lstrip('\ufeff'))
(p/'desktop_health.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
print(json.dumps(data,ensure_ascii=False,indent=2))
