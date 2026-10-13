# SmartSearch installer for Windows 10/11 (x64). No admin rights needed.
#
#   One line, in PowerShell:
#     irm https://raw.githubusercontent.com/Ayushmaniar/smartsearch/main/install.ps1 | iex
#   Or from a downloaded copy of the repo:
#     double-click install.bat   (or: powershell -ExecutionPolicy Bypass -File install.ps1)
#
# What it does: installs uv (Astral's Python manager) if missing, a private Python 3.12 and the
# packages in requirements.txt, downloads the search model (485 MB), adds a Start Menu shortcut,
# turns on "Start with Windows" and starts SmartSearch. Everything stays in one folder:
#   %LOCALAPPDATA%\SmartSearch   (or the repo folder you ran it from)
#
# Options (environment variables, set before running):
#   $env:SMARTSEARCH_DIR = "D:\Apps\SmartSearch"   install somewhere else
#   $env:SMARTSEARCH_NO_AUTOSTART = "1"            don't start with Windows
#   $env:SMARTSEARCH_NO_LAUNCH = "1"               don't start it at the end
#   $env:SMARTSEARCH_NO_SHORTCUT = "1"             no Start Menu shortcut

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is many times faster without the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"  # Windows without Developer Mode: the cache copies instead; harmless
$Repo = "Ayushmaniar/smartsearch"
$Branch = "main"

function Say($msg) { Write-Host "  $msg" }
function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host ""; Write-Host "  $msg" -ForegroundColor Red; throw $msg }
# Run a program, echo its output, return its exit code. Windows PowerShell 5.1 would otherwise turn
# anything a program writes to stderr (uv's normal progress) into a terminating error.
function Native([string]$exe, [string[]]$argv) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $exe @argv 2>&1 | ForEach-Object { Say "$_" } } finally { $ErrorActionPreference = $old }
    return $LASTEXITCODE
}

Write-Host ""
Write-Host "  SmartSearch - search your PC by meaning, privately" -ForegroundColor White
Write-Host "  ----------------------------------------------------"

# ---- checks -------------------------------------------------------------------------------------
if (-not [Environment]::Is64BitOperatingSystem) { Fail "SmartSearch needs 64-bit Windows." }
if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64" -or $env:PROCESSOR_ARCHITEW6432 -eq "ARM64") {
    Fail "Windows on ARM is not supported yet (the model runtime ships for x64 only)."
}
if ([Environment]::OSVersion.Version.Major -lt 10) { Fail "SmartSearch needs Windows 10 or 11." }

# ---- where ------------------------------------------------------------------------------------
$here = $null
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "smartsearch\app.py"))) { $here = $PSScriptRoot }
if ($env:SMARTSEARCH_DIR) { $Dir = $env:SMARTSEARCH_DIR }
elseif ($here) { $Dir = $here }
else { $Dir = Join-Path $env:LOCALAPPDATA "SmartSearch" }
$Dir = [IO.Path]::GetFullPath($Dir)

# A running copy would lock files we are about to replace.
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*smartsearch*" -and $_.ExecutablePath -like "$Dir*" } |
    ForEach-Object { Say "Stopping the running SmartSearch (pid $($_.ProcessId))"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# ---- code -------------------------------------------------------------------------------------
if ($here -and ($Dir -eq [IO.Path]::GetFullPath($here))) {
    Step "Installing in place: $Dir"
} else {
    Step "Downloading SmartSearch into $Dir"
    $tmp = Join-Path $env:TEMP ("smartsearch-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        $zip = Join-Path $tmp "src.zip"
        Invoke-WebRequest -UseBasicParsing "https://github.com/$Repo/archive/refs/heads/$Branch.zip" -OutFile $zip
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $src = Get-ChildItem -Path $tmp -Directory | Where-Object { Test-Path (Join-Path $_.FullName "smartsearch\app.py") } | Select-Object -First 1
        if (-not $src) { Fail "The download did not contain SmartSearch." }
        New-Item -ItemType Directory -Force -Path $Dir | Out-Null
        # Update the code; keep the index (.data), the model (models) and the Python environment (.venv).
        if (Test-Path (Join-Path $Dir "smartsearch")) { Remove-Item -Recurse -Force (Join-Path $Dir "smartsearch") }
        Get-ChildItem -Path $src.FullName -Force | Copy-Item -Destination $Dir -Recurse -Force
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}
Push-Location $Dir

# ---- uv + Python ------------------------------------------------------------------------------
Step "Python"
$uv = (Get-Command uv -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $uv) {
    foreach ($p in @("$env:USERPROFILE\.local\bin\uv.exe", "$env:USERPROFILE\.cargo\bin\uv.exe")) { if (Test-Path $p) { $uv = $p; break } }
}
if (-not $uv) {
    Say "Installing uv (https://docs.astral.sh/uv) ..."
    $installer = Invoke-RestMethod -UseBasicParsing "https://astral.sh/uv/install.ps1"
    $env:UV_NO_MODIFY_PATH = "1"
    Invoke-Expression $installer | Out-Null
    $uv = "$env:USERPROFILE\.local\bin\uv.exe"
    if (-not (Test-Path $uv)) { Fail "uv did not install. See https://docs.astral.sh/uv/getting-started/installation/" }
}
Say "uv: $uv"

$py = Join-Path $Dir ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    # uv fetches a standalone Python 3.12 if this PC has none; nothing is installed system-wide.
    $null = Native $uv @("venv", "--python", "3.12", (Join-Path $Dir ".venv"))
}
if (-not (Test-Path $py)) { Fail "Could not create the Python environment." }

Step "Packages (first install downloads ~400 MB)"
if ((Native $uv @("pip", "install", "--python", $py, "-r", (Join-Path $Dir "requirements.txt"))) -ne 0) {
    Fail "Package install failed (see above)."
}
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Say "NVIDIA GPU found: adding CUDA libraries for fast voice search"
    if ((Native $uv @("pip", "install", "--python", $py, "-r", (Join-Path $Dir "requirements-nvidia.txt"))) -ne 0) {
        Say "(CUDA libraries failed to install; voice search will use the CPU)"
    }
}

# ---- model ------------------------------------------------------------------------------------
Step "Search model (EmbeddingGemma 2, 485 MB, Apache-2.0)"
$get = "import os; from smartsearch import config; from huggingface_hub import hf_hub_download as d; " +
       "p = config.MODEL_PATH; print('already here') if os.path.exists(p) else " +
       "print('saved to', d(config.MODEL_REPO, os.path.basename(p), local_dir=os.path.dirname(p)))"
if ((Native $py @("-c", $get)) -ne 0) { Say "(model download failed; SmartSearch will retry when it starts)" }

# ---- shortcut, autostart, launch ----------------------------------------------------------------
$pyw = Join-Path $Dir ".venv\Scripts\pythonw.exe"
if ($env:SMARTSEARCH_NO_SHORTCUT -ne "1") {
    Step "Shortcuts"
    $programs = [Environment]::GetFolderPath("Programs")
    $lnk = Join-Path $programs "SmartSearch.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = $pyw
    $s.Arguments = "-m smartsearch.app"
    $s.WorkingDirectory = $Dir
    $s.IconLocation = (Join-Path $Dir "smartsearch\assets\icon.ico")
    $s.Description = "Search your PC by meaning (Alt+Space)"
    $s.Save()
    Say "Start Menu: SmartSearch"
}

if ($env:SMARTSEARCH_NO_AUTOSTART -ne "1") {
    if ((Native $py @("-c", "from smartsearch import autostart; autostart.set_enabled(True)")) -eq 0) {
        Say "Starts with Windows (turn off from the tray icon menu)"
    }
}

if ($env:SMARTSEARCH_NO_LAUNCH -ne "1") {
    Step "Starting SmartSearch"
    Start-Process -FilePath $pyw -ArgumentList "-m", "smartsearch.app" -WorkingDirectory $Dir
}

Write-Host ""
Write-Host "  Done. Press Alt+Space anywhere to search." -ForegroundColor Green
Write-Host "  The first index of your Documents, Desktop, Downloads, Pictures, Videos and Music runs in"
Write-Host "  the background; recent files come first. Everything stays on this PC."
Write-Host "  Uninstall: powershell -ExecutionPolicy Bypass -File `"$Dir\uninstall.ps1`""
Write-Host ""
Pop-Location
