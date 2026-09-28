# Collects everything needed to debug a failed retail launch into diagnostics.txt.
# Driven by DIAGNOSE.bat. Read-only: it does not change anything.

$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$out = Join-Path $root 'diagnostics.txt'
$KNOWN_EXE_SHA = 'EF67D348ECCA066CE1698683FD0FD810075357164B345D83ED9296D676E0EBE2'

$L = New-Object System.Collections.Generic.List[string]
function Add($t) { $L.Add([string]$t) }
function Sec($t) { Add ''; Add ('===== ' + $t + ' =====') }

Sec 'SYSTEM'
Add ("Date: " + (Get-Date))
Add ("OS: " + (Get-CimInstance Win32_OperatingSystem).Caption + " " + [Environment]::OSVersion.Version)
Add ("Repo: " + $root)

Sec 'PYTHON / DEPS'
Add (cmd /c "py -3 --version 2>&1")
Add (cmd /c "py -3 -m pip show protobuf websockets 2>&1" | Select-String 'Name:|Version:')

Sec 'OVERWATCH.EXE (build check)'
$exe = if (Test-Path "$root\game_path.txt") { (Get-Content "$root\game_path.txt" -Raw).Trim() } else { $null }
if ($exe -and (Test-Path $exe)) {
    $f = Get-Item $exe
    $sha = (Get-FileHash $exe -Algorithm SHA256).Hash
    Add ("Path: " + $exe)
    Add ("Size: " + $f.Length + " bytes")
    Add ("SHA256: " + $sha)
    Add ("Matches known-good build: " + ($(if ($sha -eq $KNOWN_EXE_SHA) { 'YES' } else { 'NO (different repack)' })))
} else { Add "No saved game path (game_path.txt missing) - run START.bat first." }

Sec 'LISTENING PORTS'
foreach ($p in 3724,3725,1119,21119,6969,3730) {
    $t = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    Add ("$p : " + $(if ($t) { "LISTEN (pid $($t.OwningProcess -join ','))" } else { "down" }))
}
$ow = Get-Process Overwatch -ErrorAction SilentlyContinue
Add ("Overwatch running: " + $(if ($ow) { "yes (pid $($ow.Id))" } else { "no" }))

Sec 'HOSTS (blizzard/battle.net redirects)'
$h = Get-Content "$env:WinDir\System32\drivers\etc\hosts" -ErrorAction SilentlyContinue |
     Where-Object { $_ -notmatch '^\s*#' -and $_ -match 'battle|blizzard' }
if ($h) { $h | ForEach-Object { Add $_ } } else { Add "no Blizzard/battle.net redirects (clean)" }

# --- relay log ---
Sec 'RELAY LOG (relay\log\wfd.log) - last 60 lines'
$wfd = "$root\relay\log\wfd.log"
$wfdText = ''
if (Test-Path $wfd) {
    $wfdText = Get-Content $wfd -Raw
    Get-Content $wfd -Tail 60 | ForEach-Object { Add $_ }
} else { Add "NOT FOUND - the relay never wrote a log (it may not have been injected)." }

# --- latest launch logs ---
Sec 'LATEST LAUNCH LOGS'
$dir = Get-ChildItem "$root\logs\launch-*" -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
$bnetText = ''
if ($dir) {
    Add ("Folder: " + $dir.FullName)
    Add '--- manifest.json ---'; Add (Get-Content "$($dir.FullName)\manifest.json" -Raw)
    Add '--- bnet.log ---'; $bnetText = Get-Content "$($dir.FullName)\bnet.log" -Raw; Add $bnetText
    Add '--- lobby.log (tail) ---'; Add ((Get-Content "$($dir.FullName)\lobby.log" -Tail 20) -join "`n")
} else { Add "No logs\launch-* folder - the retail launcher never started the servers this run." }

# --- verdict ---
Sec 'QUICK VERDICT'
$swaps = $null
if ($wfdText -match '(?s).*swaps=(\d+)') { $swaps = [int]$Matches[1] }
$sawLogon = $bnetText -match 'OnLogonComplete|ProcessTaskResponse|Logon program'
$sawStall = $bnetText -match '\[stall\]'
if ($sawLogon) {
    Add "OK: the game reached the LOCAL Battle.net and logged in (relay worked). If it still says 'Unable to Authenticate', close everything (game + python) and relaunch once."
} elseif ($sawStall) {
    Add "PROBLEM: the game connected but its login was NOT redirected to our server (no Logon received) => the relay's TLS swap didn't take effect."
    Add " - Make sure you launch ONLY via run_retail.bat / START.bat (not a shortcut)."
    Add " - Check antivirus / Windows Defender: allow the game folder and the relay, or add an exclusion, then retry."
} else {
    Add "No login attempt reached the local Battle.net server. The relay likely isn't injected or the game wasn't launched by run_retail.bat/START.bat."
}
if ($swaps -ne $null) { Add ("Relay swaps reported: " + $swaps + $(if ($swaps -eq 0) { "  (0 = the relay did not hook the game's TLS stream)" } else { "" })) }

Set-Content -Path $out -Value ($L -join "`r`n") -Encoding UTF8
Write-Host "Diagnostics written to: $out" -ForegroundColor Green
Write-Host "Opening it now - send this file (or paste its text) for help.`n"
Start-Process notepad.exe $out
