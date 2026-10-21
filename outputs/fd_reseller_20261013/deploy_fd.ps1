$ErrorActionPreference = 'Stop'
$c5 = 'C:\ChoiceBridge\c5_stage_20261012\payload\c5_core\code\QuantaAgents\scripts'; $tools = 'C:\ChoiceBridge\quant\current\实盘日更\tools'
$bk = 'C:\ChoiceBridge\live\backups\fd_chain_' + (Get-Date -Format 'yyyyMMdd_HHmmss'); New-Item -ItemType Directory -Force -Path $bk | Out-Null
Copy-Item "$c5\c5_daily.sh" "$bk\c5_daily.sh"; Copy-Item -LiteralPath "$tools\data_audit.py" "$bk\data_audit.py"
Copy-Item C:\ChoiceBridge\staging\fd_after\c5_daily.sh "$c5\c5_daily.sh" -Force
Copy-Item C:\ChoiceBridge\staging\fd_after\fd_update_reseller.py "$c5\fd_update_reseller.py" -Force
Copy-Item -LiteralPath C:\ChoiceBridge\staging\fd_after\data_audit.py -Destination "$tools\data_audit.py" -Force
'backup ' + $bk
foreach ($f in "$c5\c5_daily.sh", "$c5\fd_update_reseller.py", "$tools\data_audit.py") { (Get-FileHash -LiteralPath $f).Hash.ToLower().Substring(0,16) + '  ' + (Split-Path $f -Leaf) }
& 'C:\Program Files\Git\bin\bash.exe' -n /c/ChoiceBridge/c5_stage_20261012/payload/c5_core/code/QuantaAgents/scripts/c5_daily.sh; 'bash -n exit ' + $LASTEXITCODE
