#!/usr/bin/env bash
# Waits for the external research drive (F:) to be mounted, then for >= 6GB free commit and no other heavy python
# process, then launches the A15 resume chain once. Logs to /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv2/logs/pv2_resume.log.
cd "$(dirname "$0")/.."
while true; do
  if [ -d /f/A_Layer_Research/competition/pv2 ] && [ -d /f/A_Layer_OOS/panel ]; then
    FREE=$(powershell -NoProfile -Command "\$os=Get-CimInstance Win32_OperatingSystem; [math]::Floor(\$os.FreeVirtualMemory/1MB)" 2>/dev/null | tr -d '\r')
    HEAVY=$(powershell -NoProfile -Command "@(Get-CimInstance Win32_Process | Where-Object { \$_.Name -eq 'python.exe' -and \$_.WorkingSetSize -gt 2GB }).Count" 2>/dev/null | tr -d '\r')
    if [ "${FREE:-0}" -ge 6 ] && [ "${HEAVY:-0}" -eq 0 ]; then
      echo "$(date +%F' '%T) F mounted, free ${FREE}GB, launching resume" >> /f/A_Layer_Research/competition/pv2/logs/pv2_wait_f.log
      nohup bash scripts/pv2_resume.sh > /f/A_Layer_Research/competition/pv2/logs/pv2_resume.log 2>&1 &
      exit 0
    fi
  fi
  sleep 60
done
