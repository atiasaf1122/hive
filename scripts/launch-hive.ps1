# HIVE one-click launcher (Windows).
# Starts the backend inside WSL (if not already up), waits for it, then opens
# the desktop app. Prefers a built release exe; falls back to `npm run tauri:dev`.
#
# Desktop shortcut: powershell -NoProfile -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu\home\atiasaf1122\hive\scripts\launch-hive.ps1

param(
    [int]$Port = 8765,
    [int]$BackendTimeoutSec = 30,
    [int]$AppTimeoutSec = 180
)

$ErrorActionPreference = 'Stop'
$Distro = 'Ubuntu'
$DesktopDir = 'C:\Users\The One\hive-desktop'
$BackendLog = '/tmp/hive-backend.log'
$DevLog = Join-Path $env:TEMP 'hive-tauri-dev.log'

function Test-BackendPort {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if ($async.AsyncWaitHandle.WaitOne(500) -and $client.Connected) { return $true }
        return $false
    } catch { return $false } finally { $client.Close() }
}

function Fail([string]$Message) {
    Write-Host ''
    Write-Host "ERROR: $Message" -ForegroundColor Red
    Write-Host "--- last 25 lines of $BackendLog (WSL) ---" -ForegroundColor Yellow
    & wsl.exe -d $Distro -- bash -c "tail -n 25 $BackendLog 2>/dev/null || echo '(no backend log found)'"
    Write-Host ''
    Read-Host 'Press Enter to close'
    exit 1
}

# ── 1. Backend ────────────────────────────────────────────────────────────────
if (Test-BackendPort) {
    Write-Host "Backend already up on 127.0.0.1:$Port"
} else {
    Write-Host "Starting HIVE backend in WSL ($Distro)..."
    # Start logic lives in a WSL-side script: complex commands passed inline
    # through wsl.exe get their quoting mangled, and backgrounded jobs lose a
    # teardown race when wsl.exe exits. The script daemonizes with setsid.
    & wsl.exe -d $Distro -- bash /home/atiasaf1122/hive/scripts/start-hive-backend.sh
    if ($LASTEXITCODE -ne 0) { Fail "wsl.exe exited with code $LASTEXITCODE while starting the backend." }

    Write-Host -NoNewline "Waiting for 127.0.0.1:$Port "
    $deadline = (Get-Date).AddSeconds($BackendTimeoutSec)
    $up = $false
    while ((Get-Date) -lt $deadline) {
        if (Test-BackendPort) { $up = $true; break }
        Write-Host -NoNewline '.'
        Start-Sleep -Milliseconds 700
    }
    Write-Host ''
    if (-not $up) { Fail "Backend did not respond on port $Port within ${BackendTimeoutSec}s." }
    Write-Host 'Backend is up.'
}

# ── 2. Desktop app ────────────────────────────────────────────────────────────
if (Get-Process -Name 'hive' -ErrorAction SilentlyContinue) {
    Write-Host 'HIVE desktop app is already running.'
    exit 0
}

$releaseExe = @(
    (Join-Path $DesktopDir 'src-tauri\target\release\hive.exe'),
    (Join-Path $DesktopDir 'src-tauri\target\release\HIVE.exe')
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($releaseExe) {
    Write-Host "Launching release build: $releaseExe"
    Start-Process -FilePath $releaseExe -WorkingDirectory $DesktopDir
    exit 0
}

Write-Host "No release build found - starting dev mode (log: $DevLog)..."
Start-Process -FilePath 'cmd.exe' `
    -ArgumentList '/c', "npm run tauri:dev > `"$DevLog`" 2>&1" `
    -WorkingDirectory $DesktopDir -WindowStyle Hidden

Write-Host -NoNewline 'Waiting for the HIVE window '
$deadline = (Get-Date).AddSeconds($AppTimeoutSec)
while ((Get-Date) -lt $deadline) {
    if (Get-Process -Name 'hive' -ErrorAction SilentlyContinue) {
        Write-Host ''
        Write-Host 'HIVE desktop app is up.'
        exit 0
    }
    Write-Host -NoNewline '.'
    Start-Sleep -Seconds 2
}
Write-Host ''
Write-Host "ERROR: desktop app did not appear within ${AppTimeoutSec}s. Check the dev log:" -ForegroundColor Red
Write-Host "  $DevLog"
Get-Content $DevLog -Tail 25 -ErrorAction SilentlyContinue
Read-Host 'Press Enter to close'
exit 1
