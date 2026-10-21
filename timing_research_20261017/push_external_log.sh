#!/bin/zsh
# Push the hand-maintained 兴证 external readings to the cloud (read by dashboard.py via --external).
set -e
cd "/Users/leslie/Downloads/中证500量化60min"; CFG=choice_cloud_prepare_20261011/ssh_config
scp -q -F $CFG timing_research_20261017/xy_external_log.csv choice-executor-key:C:/ChoiceBridge/live/timing/xy_external_log.csv
echo "pushed $(wc -l < timing_research_20261017/xy_external_log.csv) lines, sha $(shasum -a 256 timing_research_20261017/xy_external_log.csv | cut -c1-16)"
