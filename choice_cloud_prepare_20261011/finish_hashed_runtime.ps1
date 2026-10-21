$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$root='C:\ChoiceBridge\runtime'
$vp=Join-Path $root 'choice-venv\Scripts\python.exe'
$req=Join-Path $root 'requirements-hashed.txt'
if((Get-FileHash -LiteralPath $req -Algorithm SHA256).Hash.ToLowerInvariant() -ne '3a5dd5b8c73f53d19cc2dfd35d950c549a5bce1bdd28be5e4c87e2b8a1d8bd71'){throw 'Official wheel manifest hash mismatch'}
if(-not (Test-Path -LiteralPath $vp)){throw 'Isolated runtime missing'}
$wheelhouse=Join-Path $root 'wheelhouse'
Write-Output 'Downloading pinned wheels from Aliyun PyPI mirror with official PyPI hashes'
& $vp -X utf8 -m pip --disable-pip-version-check download --require-hashes --only-binary=:all: --index-url https://mirrors.aliyun.com/pypi/simple/ --dest $wheelhouse --retries 1 --timeout 30 -r $req
if($LASTEXITCODE -ne 0){throw 'Hashed wheel download failed'}
$wheels=@(Get-ChildItem -LiteralPath $wheelhouse -Filter '*.whl'|ForEach-Object {@{file=$_.Name;sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}})
$wheels|ConvertTo-Json -Depth 3|Set-Content (Join-Path $root 'wheel_manifest.json') -Encoding UTF8
Write-Output 'All downloaded wheels accepted by official hash requirements; installing offline'
& $vp -X utf8 -m pip --disable-pip-version-check install --require-hashes --no-index --find-links $wheelhouse --only-binary=:all: -r $req
if($LASTEXITCODE -ne 0){throw 'Offline hashed wheel installation failed'}
& $vp -X utf8 -m pip --disable-pip-version-check check
if($LASTEXITCODE -ne 0){throw 'Dependency check failed'}
& $vp -X utf8 -c "import sys,requests,psutil,pandas,pyarrow,cryptography,win32crypt,tkinter;assert sys.version_info[:3]==(3,13,16);print('RUNTIME_IMPORTS_OK',sys.version.split()[0])"
if($LASTEXITCODE -ne 0){throw 'Runtime import check failed'}
$receipt=[ordered]@{checked_at=(Get-Date).ToString('o');python=(Join-Path $root 'Python313\python.exe');venv_python=$vp;installer_sha256='fb4f9f5d438b2396da0086dc70b935c530cb578e37adc6d354f7ad2037fee83b';packages=@(& $vp -m pip --disable-pip-version-check freeze);wheel_count=$wheels.Count;requirements_sha256='3a5dd5b8c73f53d19cc2dfd35d950c549a5bce1bdd28be5e4c87e2b8a1d8bd71';hashes_source='https://pypi.org/';download_registry='https://mirrors.aliyun.com/pypi/simple/';official_hashes_required=$true;imports_passed=$true;pip_check_passed=$true;automatic_trading_started=$false;orders_submitted=0}
$receipt|ConvertTo-Json -Depth 5|Set-Content (Join-Path $root 'runtime_receipt.json') -Encoding UTF8
$receipt|ConvertTo-Json -Depth 5
