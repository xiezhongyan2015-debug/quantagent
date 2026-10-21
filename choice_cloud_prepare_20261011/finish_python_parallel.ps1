$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$url='https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.exe'
$prefix='C:\ChoiceBridge\installers\python-3.13.16-amd64.exe'
$total=29935584
$sha='fb4f9f5d438b2396da0086dc70b935c530cb578e37adc6d354f7ad2037fee83b'
$initial=(Get-Item -LiteralPath $prefix).Length
if($initial -gt $total){throw 'Invalid prefix size'}
$parts='C:\ChoiceBridge\installers\python-parts'
New-Item -ItemType Directory -Force -Path $parts|Out-Null
$jobs=@();$segments=@()
$chunk=[int][math]::Ceiling(($total-$initial)/4)
if($chunk -gt 0){
for($start=$initial;$start -lt $total;$start+=$chunk){
 $end=[math]::Min($start+$chunk-1,$total-1)
 $file=Join-Path $parts ($start.ToString()+'-'+$end.ToString()+'.part')
 $segments+=@{file=$file;size=$end-$start+1}
 if((Test-Path -LiteralPath $file) -and (Get-Item -LiteralPath $file).Length -eq $end-$start+1){continue}
 $jobs+=Start-Job -ArgumentList $url,$file,$start,$end -ScriptBlock {
  param($u,$f,$s,$e)
  $ErrorActionPreference='Stop'
  $have=if(Test-Path -LiteralPath $f){(Get-Item -LiteralPath $f).Length}else{0}
  if($have -gt $e-$s+1){throw 'Partial range unexpectedly oversized'}
  $remaining=$e-$s+1-$have
  $rest=$f+'.remainder'
  & curl.exe --fail --location --silent --show-error --connect-timeout 10 --max-time 180 --range (($s+$have).ToString()+'-'+$e.ToString()) --output $rest $u
  if($LASTEXITCODE -ne 0){throw ('Partial download failed '+$LASTEXITCODE)}
  if((Get-Item -LiteralPath $rest).Length -ne $remaining){throw 'Remaining range size mismatch'}
  $src=[IO.File]::OpenRead($rest);$dest=[IO.File]::Open($f,[IO.FileMode]::Append)
  try{$src.CopyTo($dest)}finally{$src.Dispose();$dest.Dispose()}
  if((Get-Item -LiteralPath $f).Length -ne $e-$s+1){throw 'Range size mismatch'}
  Write-Output ('Range complete '+$s+'-'+$e)
 }
}
Write-Output ('Official remaining download: '+($total-$initial)+' bytes across '+$jobs.Count+' connections')
$jobs|Wait-Job|Out-Null
$jobs|Receive-Job -ErrorAction Stop
if(@($jobs|Where-Object State -ne 'Completed').Count){throw 'Download job failed'}
$jobs|Remove-Job
}
if((Get-Item -LiteralPath $prefix).Length -ne $initial){throw 'Prefix changed during download'}
$out='C:\ChoiceBridge\installers\python-3.13.16-amd64-assembled.exe'
$writer=[IO.File]::Open($out,[IO.FileMode]::Create)
try{
 foreach($f in @($prefix)+@($segments|ForEach-Object{$_.file})){
  $reader=[IO.File]::OpenRead($f)
  try{$reader.CopyTo($writer)}finally{$reader.Dispose()}
 }
}finally{$writer.Dispose()}
if((Get-Item -LiteralPath $out).Length -ne $total){throw 'Assembled installer size mismatch'}
if((Get-FileHash -LiteralPath $out -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sha){throw 'Official checksum mismatch'}
$sig=Get-AuthenticodeSignature -LiteralPath $out
Copy-Item -LiteralPath $out -Destination 'C:\ChoiceBridge\installers\python-3.13.16-amd64-verified.exe' -Force
$r=@{bytes=$total;sha256=$sha;official_hash_match=$true;signature_status=$sig.Status.ToString();signer=$sig.SignerCertificate.Subject;installed=$false}
$r|ConvertTo-Json|Set-Content 'C:\ChoiceBridge\installers\python_verified_receipt.json' -Encoding UTF8
$r|ConvertTo-Json
