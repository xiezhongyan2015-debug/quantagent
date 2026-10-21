$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$root='C:\ChoiceBridge\runtime'
$installer='C:\ChoiceBridge\installers\python-3.13.16-amd64-verified.exe'
$expected='fb4f9f5d438b2396da0086dc70b935c530cb578e37adc6d354f7ad2037fee83b'
if((Get-Item -LiteralPath $installer).Length -ne 29935584){throw 'Python transfer incomplete'}
if((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected){throw 'Python official hash mismatch'}
New-Item -ItemType Directory -Force -Path $root | Out-Null
$python=Join-Path $root 'Python313\python.exe'
if(-not (Test-Path -LiteralPath $python)){
    $installerArgs=@('/quiet','InstallAllUsers=0','TargetDir=C:\ChoiceBridge\runtime\Python313','PrependPath=0','Include_launcher=0','InstallLauncherAllUsers=0','Include_test=0','Shortcuts=0','Include_tcltk=1','Include_pip=1')
    $proc=Start-Process -FilePath $installer -ArgumentList $installerArgs -Wait -PassThru
    if($proc.ExitCode -notin 0,3010){throw ('Python installer exit '+$proc.ExitCode)}
    Write-Output ('Python installer exit: '+$proc.ExitCode)
}
if(-not (Test-Path -LiteralPath $python)){throw 'Installed Python executable missing'}
& $python --version
if($LASTEXITCODE -ne 0){throw 'Python version check failed'}
$venv=Join-Path $root 'choice-venv'
if(-not (Test-Path -LiteralPath (Join-Path $venv 'Scripts\python.exe'))){
    & $python -m venv $venv
    if($LASTEXITCODE -ne 0){throw 'Virtual environment creation failed'}
}
$vp=Join-Path $venv 'Scripts\python.exe'
$wheelhouse=Join-Path $root 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
$packages=@('requests==2.34.2','psutil==7.2.2','pandas==3.0.6','pyarrow==26.0.0','cryptography==50.0.2','pywin32==312')
& $vp -m pip --disable-pip-version-check download --only-binary=:all: --index-url https://pypi.org/simple --dest $wheelhouse --retries 1 --timeout 30 @packages
if($LASTEXITCODE -ne 0){throw 'Official registry wheel download failed; no source build allowed'}
$wheels=@(Get-ChildItem -LiteralPath $wheelhouse -Filter '*.whl' | ForEach-Object { @{file=$_.Name;sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()} })
$wheels|ConvertTo-Json -Depth 3|Set-Content (Join-Path $root 'wheel_manifest.json') -Encoding UTF8
& $vp -m pip --disable-pip-version-check install --no-index --find-links $wheelhouse --only-binary=:all: @packages
if($LASTEXITCODE -ne 0){throw 'Offline wheel installation failed'}
& $vp -m pip --disable-pip-version-check check
if($LASTEXITCODE -ne 0){throw 'Dependency consistency check failed'}
& $vp -c "import sys,requests,psutil,pandas,pyarrow,cryptography,win32crypt,tkinter;print('RUNTIME_IMPORTS_OK',sys.version.split()[0])"
if($LASTEXITCODE -ne 0){throw 'Runtime imports failed'}
$freeze=@(& $vp -m pip --disable-pip-version-check freeze)
$receipt=[ordered]@{checked_at=(Get-Date).ToString('o');python=$python;venv_python=$vp;installer_sha256=$expected;packages=$freeze;wheel_count=$wheels.Count;imports_passed=$true;pip_check_passed=$true;automatic_trading_started=$false;orders_submitted=0}
$receipt|ConvertTo-Json -Depth 5|Set-Content (Join-Path $root 'runtime_receipt.json') -Encoding UTF8
$receipt|ConvertTo-Json -Depth 5
