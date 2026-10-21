#!/bin/zsh
# Uses the user's logged-in ControlMaster connection (SSH登录.command) to install the automation public key, then tests key login.
set -e
prep="/Users/leslie/Downloads/中证500量化60min/choice_cloud_prepare_20261011"; ctl=/tmp/choice-ssh-leslie-20261011/control
ssh -S $ctl -F $prep/ssh_config -o BatchMode=yes -O check choice-executor
scp -o ControlPath=$ctl -F $prep/ssh_config $prep/install_pubkey.ps1 'choice-executor:C:/ChoiceBridge/staging/install_pubkey.ps1'
ssh -S $ctl -F $prep/ssh_config choice-executor 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\ChoiceBridge\staging\install_pubkey.ps1'
echo "---- key login test (no control socket, no password):"
ssh -F $prep/ssh_config -o BatchMode=yes choice-executor-key 'hostname && whoami && echo KEY_LOGIN_OK'
