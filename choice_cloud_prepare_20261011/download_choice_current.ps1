$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$dest='C:\ChoiceBridge\installers\ChoiceSetup_win_x86.exe'
$url='https://choice-app.eastmoney.com/choice/OfflinePackage/ChoiceSetup_win_x86.exe'
if(Get-Command curl.exe -ErrorAction SilentlyContinue){
    & curl.exe --fail --location --silent --show-error --connect-timeout 10 --max-time 180 --output $dest $url
    if($LASTEXITCODE -ne 0){throw 'Official download failed'}
}else{Invoke-WebRequest -Uri $url -UseBasicParsing -OutFile $dest -TimeoutSec 180}
$f=Get-Item -LiteralPath $dest
$sig=Get-AuthenticodeSignature -LiteralPath $dest
$info=[ordered]@{source_page='https://choice.eastmoney.com/downloads';official_short_link='https://tc.eastmoney.com/u/DeHw';url=$url;path=$dest;bytes=$f.Length;sha256=(Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash.ToLowerInvariant();signature_status=$sig.Status.ToString();signer=$sig.SignerCertificate.Subject;file_version=$f.VersionInfo.FileVersion;product_version=$f.VersionInfo.ProductVersion;installed=$false}
$info|ConvertTo-Json|Set-Content 'C:\ChoiceBridge\installers\choice_download_receipt.json' -Encoding UTF8
$info|ConvertTo-Json
