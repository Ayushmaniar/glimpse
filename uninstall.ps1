# Remove Glimpse: stops it, removes "Start with Windows", the Start Menu shortcut and its folder
# (code, Python environment, model and index). Your own files are never touched.
# Also cleans up after SmartSearch, the app's name before the rename.
#   powershell -ExecutionPolicy Bypass -File uninstall.ps1

$ErrorActionPreference = "Stop"
$Dir = if ($PSScriptRoot) { $PSScriptRoot } else { Join-Path $env:LOCALAPPDATA "Glimpse" }
$OldDir = Join-Path $env:LOCALAPPDATA "SmartSearch"
Write-Host ""
Write-Host "  This removes Glimpse from $Dir (the app, its model and its search index)."
if ((Test-Path $OldDir) -and ($OldDir -ne $Dir)) { Write-Host "  It also removes the old SmartSearch copy in $OldDir." }
$ok = Read-Host "  Continue? [y/N]"
if ($ok -notmatch '^(y|yes)$') { Write-Host "  Nothing removed."; return }

# The .venv's pythonw.exe is only a launcher; the app runs in its child process, so stop that too.
$all = @(Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue)
$ids = @($all | Where-Object {
        ($_.CommandLine -like "*glimpse*" -or $_.CommandLine -like "*smartsearch*") -and
        ($_.ExecutablePath -like "$Dir\*" -or $_.CommandLine -like "*$Dir*" -or
         $_.ExecutablePath -like "$OldDir\*" -or $_.CommandLine -like "*$OldDir*") } | ForEach-Object { $_.ProcessId })
$ids += @($all | Where-Object { $ids -contains $_.ParentProcessId } | ForEach-Object { $_.ProcessId })
$ids | Select-Object -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500

$programs = [Environment]::GetFolderPath("Programs")
foreach ($name in @("Glimpse", "SmartSearch")) {
    Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name $name -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $programs "$name.lnk") -ErrorAction SilentlyContinue
}

Set-Location $env:TEMP
$left = @()
foreach ($d in @($Dir, $OldDir) | Select-Object -Unique) {
    if (-not (Test-Path $d)) { continue }
    Remove-Item -Recurse -Force $d -ErrorAction SilentlyContinue
    if (Test-Path $d) { $left += $d }
}
if ($left.Count) { Write-Host "  Some files were in use; delete $($left -join ', ') by hand after a restart." }
else { Write-Host "  Glimpse is removed." -ForegroundColor Green }
