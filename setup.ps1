# Overwatch 1.74 lobby server - one-click setup & launcher.
# Driven by START.bat. Handles: Python check, dependencies, relay DLL
# (download + unblock), picking Overwatch.exe, and launching the chosen route.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$RELEASE_DLL = 'https://github.com/squeeeezy/overwatch-1.74-lobby-server/releases/download/relay-1.74.0.0.104319/owwfd_relay.dll'
$DLL_SHA256  = '60F62B9928E7FE15D2B49CC9557029D44B25C3F00C01633CF0C4C32452F6592F'
$DLL_PATH    = Join-Path $root 'relay\owwfd_relay.dll'
$CFG_PATH    = Join-Path $root 'game_path.txt'

function Line { param($t,$c='Gray') Write-Host $t -ForegroundColor $c }
function Die  { param($t) Line "`n[X] $t" 'Red'; Line "`nPress Enter to close."; [void](Read-Host); exit 1 }

Line "==================================================================" 'Cyan'
Line "   Overwatch 1.74 - Lobby server (one-click setup & launch)" 'Cyan'
Line "==================================================================" 'Cyan'

# --- 0. choose route up front (default: retail) ---
Line "`nWhich mode do you want to start?"
Line "   [1] Retail   - full main menu WITH the lobby hero  (default)"
Line "   [2] Tournament - simpler menu, no hero, no relay needed"
$mode = Read-Host "Enter 1 or 2 (or just press Enter for Retail)"
if ($mode -eq '2') { $retail = $false; Line "-> Tournament mode selected." 'Yellow' }
else               { $retail = $true;  Line "-> Retail mode selected." 'Green' }

# --- 1. Python ---
Line "`n[1/5] Checking Python..."
$py = $null
foreach ($cand in @('py -3','python','py')) {
    try { $v = & cmd /c "$cand --version" 2>$null; if ($LASTEXITCODE -eq 0 -and $v) { $py = $cand; break } } catch {}
}
if (-not $py) {
    Line "Python is not installed." 'Red'
    Line "Install Python 3.14 (64-bit) from the page that will now open, then run START.bat again."
    Start-Process 'https://www.python.org/downloads/windows/'
    Die "Python required."
}
Line "    Python found ($py)." 'Green'

# --- 2. dependencies ---
Line "`n[2/5] Installing dependencies..."
& cmd /c "$py -m pip install -q -r requirements.txt"
if ($LASTEXITCODE -ne 0) { Die "Failed to install dependencies (check your internet connection)." }
Line "    Dependencies ready." 'Green'

# --- 3. relay DLL (retail only) ---
if ($retail) {
    Line "`n[3/5] Checking the relay DLL..."
    if (-not (Test-Path $DLL_PATH)) {
        Line "    Not found - downloading from GitHub Releases..."
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            New-Item -ItemType Directory -Force -Path (Split-Path $DLL_PATH) | Out-Null
            Invoke-WebRequest -Uri $RELEASE_DLL -OutFile $DLL_PATH -UseBasicParsing
        } catch { Die "Could not download the relay DLL. Download it manually from the Releases page and put it in the 'relay' folder." }
    }
    try { Unblock-File -Path $DLL_PATH -ErrorAction SilentlyContinue } catch {}
    try {
        $h = (Get-FileHash -Path $DLL_PATH -Algorithm SHA256).Hash
        if ($h -ne $DLL_SHA256) { Line "    WARNING: relay DLL checksum does not match the expected build." 'Yellow' }
        else { Line "    Relay DLL present and verified." 'Green' }
    } catch { Line "    Relay DLL present." 'Green' }
} else {
    Line "`n[3/5] Tournament mode - no relay needed. Skipping."
}

# --- 4. Overwatch.exe path ---
Line "`n[4/5] Locating Overwatch.exe..."
function Test-Exe { param($p)
    return ($p -and (Test-Path -LiteralPath $p) -and ((Split-Path $p -Leaf).ToLower() -eq 'overwatch.exe'))
}
$exe = $null
if (Test-Path $CFG_PATH) {
    $saved = (Get-Content $CFG_PATH -Raw).Trim()
    if (Test-Exe $saved) { $exe = $saved; Line "    Using saved path: $exe" 'Green' }
}
if (-not $exe) {
    Line "    Please select your game's Overwatch.exe (the game .exe, NOT a shortcut or a launcher)."
    Add-Type -AssemblyName System.Windows.Forms | Out-Null
    while (-not $exe) {
        $dlg = New-Object System.Windows.Forms.OpenFileDialog
        $dlg.Title = 'Select Overwatch.exe (the game executable, not a shortcut/launcher)'
        $dlg.Filter = 'Overwatch game executable (Overwatch.exe)|Overwatch.exe'
        $dlg.CheckFileExists = $true
        $dlg.DereferenceLinks = $true
        if ($dlg.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { Die "No game selected." }
        if (Test-Exe $dlg.FileName) { $exe = $dlg.FileName }
        else { Line "    That is not Overwatch.exe. Pick the file named exactly 'Overwatch.exe'." 'Yellow' }
    }
    Set-Content -Path $CFG_PATH -Value $exe -Encoding ASCII
    Line "    Saved for next time." 'Green'
}

# --- 5. launch ---
Line "`n[5/5] Cleaning up anything already running and launching fresh..."
# stop our old helper processes (lobby / bnet / launcher) so nothing stale is reused
try {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='py.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'lobbyserv\.py|bnet\.main|launch_retail\.py' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
} catch {}
# close any running game so the retail launcher never hits "already running"
try {
    Get-Process Overwatch -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 800
} catch {}

if ($retail) {
    & cmd /c "$py -B tools\launch_retail.py --game-exe `"$exe`""
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        Line "`nRetail launch reported a problem (exit $code)." 'Yellow'
        Line "If the game shows 'Unable to Authenticate' or crashes, run START.bat again and choose [2] Tournament to check the rest works."
    } else {
        Line "`nDone. The game is starting on the retail route." 'Green'
        Line "Open the dashboard at http://127.0.0.1:3725 to manage the profile, events and lobby hero."
        Line "IMPORTANT: don't open the game any other way (no shortcut) - START.bat launches it for you."
    }
} else {
    Start-Process -FilePath 'cmd' -ArgumentList '/c', "$py -3 server\lobbyserv.py" -WorkingDirectory $root
    Start-Sleep -Seconds 4
    Start-Process -FilePath $exe -ArgumentList '--tank_TournamentMode','--lobbyServer=127.0.0.1:3724','--console'
    Line "`nDone. Tournament mode is starting (reduced menu, no lobby hero)." 'Green'
    Line "Open the dashboard at http://127.0.0.1:3725 to manage the profile and events."
}

Line "`nPress Enter to close this window (the server and game keep running)."
[void](Read-Host)
