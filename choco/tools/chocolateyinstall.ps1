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
