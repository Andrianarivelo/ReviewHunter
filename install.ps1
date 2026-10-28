# =============================================================================
#  ReviewHunter one-line installer for Windows (PowerShell, no administrator rights)
#
#      irm https://github.com/Andrianarivelo/ReviewHunter/releases/latest/download/install.ps1 | iex
#
#  Or from the unzipped folder:  powershell -ExecutionPolicy Bypass -File install.ps1 [--cli --yes]
#  Steps: 1) find Python 3.10+; if missing, install a private Python for this user with uv
#            (https://docs.astral.sh/uv)  2) locate or download the app  3) open the setup window
#  Environment: RH_FORCE_UV=1 always uses the private Python (tests); RH_ARGS adds installer options
#  when piped into iex (which cannot pass arguments).
# =============================================================================
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ZipUrl = 'https://github.com/Andrianarivelo/ReviewHunter/releases/latest/download/ReviewHunter.zip'
$PrivatePython = '3.12'

function Say($text) { Write-Host "  $text" }
function Test-Python($exe) {
    # Python >= 3.10 with venv; the Microsoft Store stub "python.exe" prints nothing and fails here.
    # 'Continue' in this scope: in Windows PowerShell 5.1, a native program writing to stderr while the
    # preference is 'Stop' raises a terminating error even though the program succeeded.
    $ErrorActionPreference = 'Continue'
    if (-not $exe) { return $false }
    try {
        & $exe -c "import sys, venv, ensurepip; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)" 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}
function Find-Python {
    if ($env:RH_FORCE_UV -eq '1') { return $null }
    $cands = @()
    try { $cands += (& py -3 -c "import sys; print(sys.executable)" 2>$null) } catch {}
    foreach ($name in 'python', 'python3') {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notlike '*WindowsApps*') { $cands += $cmd.Source }
    }
    $cands += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
              Sort-Object FullName -Descending | ForEach-Object FullName
    foreach ($c in $cands) { if (Test-Python $c) { return $c } }
    return $null
}
function Install-PrivatePython {
    $ErrorActionPreference = 'Continue'   # uv reports progress on stderr (see Test-Python); exit codes are checked
    $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $uv) { $uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe' }
    if (-not (Test-Path $uv)) {
        Say 'Downloading uv (a small tool that installs Python for your user account)...'
        $env:UV_NO_MODIFY_PATH = '1'           # leave the user's PATH untouched
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression | Out-Null
    }
    if (-not (Test-Path $uv)) { throw 'uv could not be installed' }
    Say "Installing a private Python $PrivatePython (one time, about 30 MB)..."
    & $uv python install $PrivatePython 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "uv python install failed (exit code $LASTEXITCODE)" }
    $py = "$(& $uv python find $PrivatePython 2>$null)".Trim()
    if (-not (Test-Python $py)) { throw "the private Python does not work ($py)" }
    return $py
}

Write-Host ''
Write-Host '  ReviewHunter Setup' -ForegroundColor Cyan
Write-Host '  ------------------'

$py = Find-Python
if (-not $py) {
    Say 'Python 3.10+ was not found: installing a private Python (no administrator rights needed).'
    try { $py = Install-PrivatePython }
    catch {
        Say "Automatic Python installation failed: $_"
        Say 'Install Python from https://www.python.org/downloads/ (tick "Add python.exe to PATH") and run this again.'
        Start-Process 'https://www.python.org/downloads/'
        return
    }
}
Say "Using Python: $py"

$installer = $null
if ($PSScriptRoot) {
    $local = Join-Path $PSScriptRoot 'installer\reviewhunter_installer.py'
    if (Test-Path $local) { $installer = $local }
}
if (-not $installer) {
    $dl = Join-Path $env:TEMP 'ReviewHunterSetup'
    if (Test-Path $dl) { Remove-Item -Recurse -Force $dl }
    New-Item -ItemType Directory -Force $dl | Out-Null
    Say 'Downloading the latest ReviewHunter release...'
    Invoke-WebRequest -UseBasicParsing $ZipUrl -OutFile (Join-Path $dl 'ReviewHunter.zip')
    Expand-Archive -Force (Join-Path $dl 'ReviewHunter.zip') $dl
    $installer = (Get-ChildItem $dl -Recurse -Filter reviewhunter_installer.py | Select-Object -First 1).FullName
    if (-not $installer) { throw 'The downloaded release does not contain the installer.' }
}

$options = @($args)
if ($env:RH_ARGS) { $options += $env:RH_ARGS -split '\s+' | Where-Object { $_ } }
Say 'Opening the installer (follow the progress there)...'
$ErrorActionPreference = 'Continue'      # the installer's own messages may use stderr; its exit code decides
& $py $installer @options
if ($LASTEXITCODE -ne 0) {
    Say "Setup did not finish (code $LASTEXITCODE). Log: $env:TEMP\reviewhunter-install.log"
    if ($PSCommandPath) { exit $LASTEXITCODE }
}
