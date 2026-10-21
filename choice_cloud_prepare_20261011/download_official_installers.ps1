$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$dir='C:\ChoiceBridge\installers'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$items=@(
    @{name='python-3.13.16-amd64.exe';url='https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.exe';expected='fb4f9f5d438b2396da0086dc70b935c530cb578e37adc6d354f7ad2037fee83b'},
    @{name='ChoiceSetupLocal-8.1.0.2.exe';url='https://cftdlcdn.eastmoney.com/Choice/OfflinePackageV4/8.1.0.2/ChoiceSetupLocal.exe';expected=$null}
)
$receipts=@()
foreach($item in $items){
    $dest=Join-Path $dir $item.name
    Invoke-WebRequest -Uri $item.url -UseBasicParsing -OutFile $dest -TimeoutSec 300
    $hash=(Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash.ToLowerInvariant()
    $sig=Get-AuthenticodeSignature -LiteralPath $dest
    $r=[ordered]@{path=$dest;url=$item.url;size=(Get-Item -LiteralPath $dest).Length;sha256=$hash;official_hash_match=($item.expected -and $hash -eq $item.expected);signature_status=$sig.Status.ToString();signer=$sig.SignerCertificate.Subject;installed=$false}
    $receipts+=$r
    $receipts | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $dir 'download_receipts.json') -Encoding UTF8
    $r|ConvertTo-Json -Compress
}
