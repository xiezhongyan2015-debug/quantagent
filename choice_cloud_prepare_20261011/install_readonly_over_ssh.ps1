$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
$root='C:\ChoiceBridge\runtime'
$dest=Join-Path $root 'readonly-wheelhouse';New-Item -ItemType Directory -Force $dest|Out-Null
$file=Join-Path $dest 'requests-2.34.2-py3-none-any.whl'
& curl.exe --silent --show-error --fail --connect-timeout 5 --max-time 90 --output $file 'http://192.0.2.10:51045/requests-2.34.2-py3-none-any.whl'
if($LASTEXITCODE -ne 0){throw 'Encrypted channel file transfer failed'}
if((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne '2a0d60c172f83ac6ab31e4554906c0f3b3588d37b5cb939b1c061f4907e278e0'){throw 'Official PyPI hash mismatch'}
Write-Output 'OFFICIAL_HASH_OK requests'
$file=Join-Path $dest 'cryptography-50.0.2-cp311-abi3-win_amd64.whl'
& curl.exe --silent --show-error --fail --connect-timeout 5 --max-time 90 --output $file 'http://192.0.2.10:51045/cryptography-50.0.2-cp311-abi3-win_amd64.whl'
if($LASTEXITCODE -ne 0){throw 'Encrypted channel file transfer failed'}
if((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne '7afa5a6602a9f29af1f3a2965f831bae7c9d5d597b7cbb716d41ab3b7d89879c'){throw 'Official PyPI hash mismatch'}
Write-Output 'OFFICIAL_HASH_OK cryptography'
$file=Join-Path $dest 'charset_normalizer-3.5.2-cp313-cp313-win_amd64.whl'
& curl.exe --silent --show-error --fail --connect-timeout 5 --max-time 90 --output $file 'http://192.0.2.10:51045/charset_normalizer-3.5.2-cp313-cp313-win_amd64.whl'
if($LASTEXITCODE -ne 0){throw 'Encrypted channel file transfer failed'}
if((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne '78456a747de8dc58360ffa581f30a002baf5aa28cb262536545e91f113ed7639'){throw 'Official PyPI hash mismatch'}
Write-Output 'OFFICIAL_HASH_OK charset-normalizer'
$file=Join-Path $dest 'idna-3.20-py3-none-any.whl'
& curl.exe --silent --show-error --fail --connect-timeout 5 --max-time 90 --output $file 'http://192.0.2.10:51045/idna-3.20-py3-none-any.whl'
if($LASTEXITCODE -ne 0){throw 'Encrypted channel file transfer failed'}
if((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'ab7ae7122974553370f0bdb919e1a960b2cd1bc1ef0276416d896db81c14582c'){throw 'Official PyPI hash mismatch'}
Write-Output 'OFFICIAL_HASH_OK idna'
$file=Join-Path $dest 'urllib3-2.8.0-py3-none-any.whl'
& curl.exe --silent --show-error --fail --connect-timeout 5 --max-time 90 --output $file 'http://192.0.2.10:51045/urllib3-2.8.0-py3-none-any.whl'
if($LASTEXITCODE -ne 0){throw 'Encrypted channel file transfer failed'}
if((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne '0cf3cae568d36aa9576b28dfb35f11328f1cb974ca7647d9475ebb86c75ac6e3'){throw 'Official PyPI hash mismatch'}
Write-Output 'OFFICIAL_HASH_OK urllib3'
$file=Join-Path $dest 'certifi-2026.7.22-py3-none-any.whl'
& curl.exe --silent --show-error --fail --connect-timeout 5 --max-time 90 --output $file 'http://192.0.2.10:51045/certifi-2026.7.22-py3-none-any.whl'
if($LASTEXITCODE -ne 0){throw 'Encrypted channel file transfer failed'}
if((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne '62f22742b58a1a33014a2b6b706588a8d7e2a88ae7bd1a6ebe8c992928483775'){throw 'Official PyPI hash mismatch'}
Write-Output 'OFFICIAL_HASH_OK certifi'
$file=Join-Path $dest 'cffi-2.1.1-cp313-cp313-win_amd64.whl'
& curl.exe --silent --show-error --fail --connect-timeout 5 --max-time 90 --output $file 'http://192.0.2.10:51045/cffi-2.1.1-cp313-cp313-win_amd64.whl'
if($LASTEXITCODE -ne 0){throw 'Encrypted channel file transfer failed'}
if((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne '1aa5645c30469b09530c4ebca77ebf8f17618293c58f8549cb1a543a50236e7d'){throw 'Official PyPI hash mismatch'}
Write-Output 'OFFICIAL_HASH_OK cffi'
$file=Join-Path $dest 'pycparser-3.11-py3-none-any.whl'
& curl.exe --silent --show-error --fail --connect-timeout 5 --max-time 90 --output $file 'http://192.0.2.10:51045/pycparser-3.11-py3-none-any.whl'
if($LASTEXITCODE -ne 0){throw 'Encrypted channel file transfer failed'}
if((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne '51d5a8ba2be0bbe440b99d2112604c95bbbc3c2748a64260186c541e1729cd80'){throw 'Official PyPI hash mismatch'}
Write-Output 'OFFICIAL_HASH_OK pycparser'
& 'C:\ChoiceBridge\runtime\choice-venv\Scripts\python.exe' -X utf8 -m pip --disable-pip-version-check install --no-index --find-links $dest --require-hashes --only-binary=:all: -r (Join-Path $root 'requirements-readonly-hashed.txt')
if($LASTEXITCODE -ne 0){throw 'Readonly dependency installation failed'}
& 'C:\ChoiceBridge\runtime\choice-venv\Scripts\python.exe' -X utf8 -c "import requests,cryptography;print('READONLY_IMPORTS_OK')"
if($LASTEXITCODE -ne 0){throw 'Readonly import check failed'}
