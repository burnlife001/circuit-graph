#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Circuit-Graph WebUI service manager — start, stop, restart (KB + template layer).
.DESCRIPTION
  Launches scripts/webui_server.py in the project's .venv and exposes a management menu.
  Uses a PID file under $env:TEMP to track the running process.
  Menu behaviour: a successful start/restart returns to the menu immediately;
  a failed start/restart pauses so the error stays on screen.
  Non-interactive: run with -Action start|stop|restart|status for headless use.
#>

param(
    [ValidateSet("menu", "start", "stop", "restart", "status")]
    [string]$Action = "menu",
    [int]$Port = 8766
)

# ---- Paths -------------------------------------------------------------
try {
    $scriptPath = $MyInvocation.MyCommand.Path
    if (-not $scriptPath) {
        $scriptPath = $PSScriptRoot
        if (-not $scriptPath) { $scriptPath = Get-Location }
    }
} catch {
    $scriptPath = Get-Location
}
$ProjectRoot = Split-Path -Parent $scriptPath
$ServerScript = Join-Path $ProjectRoot "scripts\webui_server.py"
$VenvPython   = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$PidFile      = Join-Path $env:TEMP "webui.pid"
$LogFile      = Join-Path $env:TEMP "webui.log"

$DefaultPort = $Port
$global:menuPort = $DefaultPort

# ---- Helpers -----------------------------------------------------------

function Write-Banner {
    try { Clear-Host } catch { }
    Write-Host "============================================" -ForegroundColor Cyan
    Write-Host "   Circuit-Graph WebUI (KB + Template)" -ForegroundColor Cyan
    Write-Host "============================================" -ForegroundColor Cyan
    Write-Host ""
}

function Get-PidFromFile {
    if (-not (Test-Path $PidFile)) { return $null }
    try {
        $raw = (Get-Content $PidFile -Raw -ErrorAction Stop).Trim()
        if ([string]::IsNullOrEmpty($raw)) { return $null }
        return [int]$raw
    } catch { return $null }
}

function Test-ServerRunning {
    $pidVal = Get-PidFromFile
    if (-not $pidVal) { return $false }
    try {
        $proc = Get-Process -Id $pidVal -ErrorAction Stop
        return $true
    } catch { return $false }
}

function Start-Server {
    param([int]$Port = $DefaultPort)

    if (Test-ServerRunning) {
        Write-Host "[!] Server is already running (PID $(Get-PidFromFile))." -ForegroundColor Yellow
        return $true
    }

    if (-not (Test-Path $VenvPython)) {
        Write-Host "[!] .venv not found at: $VenvPython" -ForegroundColor Red
        Write-Host "    Run: uv venv && uv pip install -e ." -ForegroundColor Gray
        return $false
    }

    if (-not (Test-Path $ServerScript)) {
        Write-Host "[!] Server script not found: $ServerScript" -ForegroundColor Red
        return $false
    }

    Write-Host "[*] Starting WebUI on port $Port ..." -ForegroundColor Cyan

    $proc = Start-Process -FilePath "cmd" `
        -ArgumentList "/c", "`"`"$VenvPython`" `"$ServerScript`" --port $Port 2>&1`"" `
        -NoNewWindow `
        -PassThru `
        -RedirectStandardOutput $LogFile

    $proc.Id | Out-File -FilePath $PidFile -NoNewline -Encoding utf8

    Start-Sleep -Milliseconds 1000

    if (Test-ServerRunning) {
        Write-Host "[+] Server started — PID $($proc.Id)" -ForegroundColor Green
        Write-Host "    URL:  http://127.0.0.1:$Port/   (/kb  /tpl)" -ForegroundColor Green
        Write-Host "    Log:  $LogFile" -ForegroundColor Gray
        return $true
    } else {
        Write-Host "[-] Server may have failed to start. Check log:" -ForegroundColor Red
        Write-Host "    $LogFile" -ForegroundColor Gray
        if (Test-Path $LogFile) {
            Write-Host "--- last 10 lines ---" -ForegroundColor DarkGray
            try { Get-Content $LogFile -Tail 10 -ErrorAction Stop | ForEach-Object { Write-Host $_ -ForegroundColor DarkGray } } catch { }
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        return $false
    }
}

function Stop-Server {
    $pidVal = Get-PidFromFile
    if (-not $pidVal) {
        Write-Host "[i] No PID file found — server not tracked." -ForegroundColor Gray
        return $true
    }

    try {
        $proc = Get-Process -Id $pidVal -ErrorAction Stop
        Write-Host "[*] Stopping WebUI (PID $pidVal) ..." -ForegroundColor Cyan
        $proc.Kill()
        $proc.WaitForExit(3000)
        Write-Host "[+] Server stopped." -ForegroundColor Green
    } catch {
        Write-Host "[i] Process $pidVal not found — already dead." -ForegroundColor Gray
    }

    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    return $true
}

function Show-Status {
    if (Test-ServerRunning) {
        Write-Host "status: running (http://127.0.0.1:$global:menuPort/)" -ForegroundColor Green
    } else {
        Write-Host "status: stopped" -ForegroundColor Red
    }
}

function Restart-Server {
    param([int]$Port = $DefaultPort)
    Stop-Server | Out-Null
    Start-Sleep -Milliseconds 500
    return (Start-Server -Port $Port)
}

function Wait-Failure {
    Write-Host ""
    Read-Host "  Press Enter to continue"
}

# ---- Non-interactive actions -------------------------------------------

if ($Action -ne "menu") {
    switch ($Action) {
        "start"   { $global:menuPort = $Port; $ok = Start-Server -Port $Port }
        "stop"    { $ok = Stop-Server }
        "restart" { $ok = Restart-Server -Port $Port }
        "status"  { Show-Status; $ok = $true }
    }
    exit ($(if ($ok) { 0 } else { 1 }))
}

# ---- Menu Loop ---------------------------------------------------------

try {
    do {
        Write-Banner
        Show-Status
        Write-Host ""
        Write-Host "  [1] start" -ForegroundColor White
        Write-Host "  [2] stop" -ForegroundColor White
        Write-Host "  [3] restart" -ForegroundColor White
        Write-Host "  [0] exit" -ForegroundColor White
        Write-Host ""

        $choice = Read-Host "  > "

        $needPause = $true
        switch ($choice) {
            "1" {
                $global:menuPort = $DefaultPort
                $ok = Start-Server -Port $global:menuPort
                $needPause = -not $ok
            }
            "2" { $ok = Stop-Server; $needPause = $false }
            "3" {
                $ok = Restart-Server -Port $global:menuPort
                $needPause = -not $ok
            }
            "0" { exit 0 }
            default { Write-Host "[!] Invalid choice." -ForegroundColor Yellow }
        }

        if ($needPause) { Wait-Failure }
    } while ($choice -ne "0")
} catch {
    Write-Host ""
    Write-Host "[FATAL] Unexpected error:" -ForegroundColor Red
    Write-Host "  $_" -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
}
