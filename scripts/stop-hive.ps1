# Stop HIVE (Windows).
# Closes the desktop app (and its tauri-dev tooling), kills the WSL backend and
# any orphaned HIVE claude workers, then — if nothing else is running in WSL —
# offers to run `wsl --shutdown` to free the VM's memory.
#
# Desktop shortcut: powershell -NoProfile -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu\home\atiasaf1122\hive\scripts\stop-hive.ps1

$ErrorActionPreference = 'Continue'
$Distro = 'Ubuntu'
$killedSomething = $false

# ── 1. Desktop app + dev tooling ─────────────────────────────────────────────
Write-Host '[Windows] closing HIVE desktop app...'
$appProcs = Get-Process -Name 'hive' -ErrorAction SilentlyContinue
if ($appProcs) {
    foreach ($p in $appProcs) {
        Write-Host "  killing hive.exe pid $($p.Id)"
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        $killedSomething = $true
    }
} else {
    Write-Host '  (desktop app not running)'
}

# npm/node/cargo processes left behind by `npm run tauri:dev`. Matches the
# project dir and the hidden cmd wrapper's log redirect (hive-tauri-dev.log,
# named by launch-hive.ps1) since the wrapper's command line has no dir in it.
$devProcs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'hive-desktop|hive-tauri-dev' -and $_.ProcessId -ne $PID }
if ($devProcs) {
    foreach ($p in $devProcs) {
        Write-Host "  killing $($p.Name) pid $($p.ProcessId) (tauri dev tooling)"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        $killedSomething = $true
    }
} else {
    Write-Host '  (no tauri dev tooling running)'
}

# ── 2. WSL backend + orphaned workers ────────────────────────────────────────
Write-Host ''
$wslOut = & wsl.exe -d $Distro -- bash /home/atiasaf1122/hive/scripts/stop-hive-wsl.sh 2>&1
$wslOut | ForEach-Object { Write-Host $_ }
if ($wslOut -match 'killed [1-9]') { $killedSomething = $true }

# ── 3. Optionally free the WSL VM ────────────────────────────────────────────
Write-Host ''
if (-not $killedSomething) { Write-Host 'Nothing was running - nothing killed.' }

if ($wslOut -match 'WSL_IDLE=1') {
    $answer = Read-Host 'Nothing else is running in WSL. Run `wsl --shutdown` to free VM memory? (y/N)'
    if ($answer -match '^[Yy]') {
        Write-Host 'Shutting down WSL...'
        & wsl.exe --shutdown
        Write-Host 'WSL VM stopped.'
    } else {
        Write-Host 'Leaving the WSL VM running.'
    }
} else {
    Write-Host 'Other processes are still running in WSL - not offering wsl --shutdown.'
}

Write-Host ''
Read-Host 'Done. Press Enter to close'
