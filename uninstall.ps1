# Remove SmartSearch: stops it, removes "Start with Windows", the Start Menu shortcut and its folder
# (code, Python environment, model and index). Your own files are never touched.
#   powershell -ExecutionPolicy Bypass -File uninstall.ps1

$ErrorActionPreference = "Stop"
$Dir = if ($PSScriptRoot) { $PSScriptRoot } else { Join-Path $env:LOCALAPPDATA "SmartSearch" }
Write-Host ""
Write-Host "  This removes SmartSearch from $Dir (the app, its model and its search index)."
$ok = Read-Host "  Continue? [y/N]"
if ($ok -notmatch '^(y|yes)$') { Write-Host "  Nothing removed."; return }

Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*smartsearch*" -and $_.ExecutablePath -like "$Dir*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500

Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "SmartSearch" -ErrorAction SilentlyContinue
Remove-Item (Join-Path ([Environment]::GetFolderPath("Programs")) "SmartSearch.lnk") -ErrorAction SilentlyContinue

Set-Location $env:TEMP
Remove-Item -Recurse -Force $Dir -ErrorAction SilentlyContinue
if (Test-Path $Dir) { Write-Host "  Some files were in use; delete $Dir by hand after a restart." }
else { Write-Host "  SmartSearch is removed." -ForegroundColor Green }
