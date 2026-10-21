$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$path='C:\ChoiceBridge\installers\python-3.13.16-amd64.exe'
$url='https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.exe'
$bytes=29935584
$expected='fb4f9f5d438b2396da0086dc70b935c530cb578e37adc6d354f7ad2037fee83b'
if(-not (Get-Command curl.exe -ErrorAction SilentlyContinue)){throw 'Windows curl unavailable'}
for($attempt=1;$attempt -le 3;$attempt++){
    $existing=if(Test-Path -LiteralPath $path){(Get-Item -LiteralPath $path).Length}else{0}
    Write-Output ('Download attempt '+$attempt+'; existing bytes '+$existing+'/'+$bytes)
    if($existing -eq $bytes){break}
    if($existing -gt $bytes){throw 'Unexpected installer size; do not resume'}
    if($existing -gt 0){
        & curl.exe --fail --location --silent --show-error --connect-timeout 10 --max-time 180 --continue-at - --output $path $url
    }else{& curl.exe --fail --location --silent --show-error --connect-timeout 10 --max-time 180 --output $path $url}
    if($LASTEXITCODE -notin 0,28){throw ('Official download curl exit '+$LASTEXITCODE)}
}
if((Get-Item -LiteralPath $path).Length -ne $bytes){throw 'Installer download incomplete; installation stopped'}
$hash=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
if($hash -ne $expected){throw 'Official installer hash mismatch'}
$sig=Get-AuthenticodeSignature -LiteralPath $path
Copy-Item -LiteralPath $path -Destination 'C:\ChoiceBridge\installers\python-3.13.16-amd64-verified.exe' -Force
@{path=$path;sha256=$hash;bytes=$bytes;official_hash_match=$true;signature_status=$sig.Status.ToString();signer=$sig.SignerCertificate.Subject;installed=$false}|ConvertTo-Json
