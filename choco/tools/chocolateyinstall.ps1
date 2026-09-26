$ErrorActionPreference = 'Stop'

$packageName = 'gt7telem'
$url64       = '__URL64__'
$checksum64  = '__CHECKSUM64__'
$toolsDir    = "$(Split-Path -parent $MyInvocation.MyCommand.Definition)"

Install-ChocolateyZipPackage -PackageName $packageName `
  -Url64bit $url64 `
  -UnzipLocation $toolsDir `
  -Checksum64 $checksum64 `
  -ChecksumType64 'sha256'

# TRACE.exe is a windowed (GUI) app. Without a <exe>.gui marker Chocolatey's
# shim treats it as a console program and keeps a console attached / waits
# on it when launched from a terminal.
New-Item -ItemType File -Path (Join-Path $toolsDir 'TRACE.exe.gui') -Force | Out-Null
