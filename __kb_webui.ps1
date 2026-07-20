#!/usr/bin/env pwsh
<#
.SYNOPSIS
  KB WebUI service manager — start, stop, restart, and check the knowledge-base web UI.
.DESCRIPTION
  Launches scripts/kb_server.py in the project's .venv and exposes a management menu.
  Uses a PID file under $env:TEMP to track the running process.
#>

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
$ServerScript = Join-Path $ProjectRoot "scripts\kb_server.py"
$VenvPython   = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$PidFile      = Join-Path $env:TEMP "kb_webui.pid"
$LogFile      = Join-Path $env:TEMP "kb_webui.log"

$DefaultPort = 8765
$global:menuPort = $DefaultPort

# ---- Helpers -----------------------------------------------------------

function Write-Banner {
    try { Clear-Host } catch { }
    Write-Host "============================================" -ForegroundColor Cyan
    Write-Host "   Circuit-Graph KB WebUI Manager" -ForegroundColor Cyan
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
        return
    }

    if (-not (Test-Path $VenvPython)) {
        Write-Host "[!] .venv not found at: $VenvPython" -ForegroundColor Red
        Write-Host "    Run: uv venv && uv pip install -e ." -ForegroundColor Gray
        return
    }

    if (-not (Test-Path $ServerScript)) {
        Write-Host "[!] Server script not found: $ServerScript" -ForegroundColor Red
        return
    }

    Write-Host "[*] Starting KB WebUI on port $Port ..." -ForegroundColor Cyan

    $proc = Start-Process -FilePath "cmd" `
        -ArgumentList "/c", "`"`"$VenvPython`" `"$ServerScript`" --port $Port 2>&1`"" `
        -NoNewWindow `
        -PassThru `
        -RedirectStandardOutput $LogFile

    $proc.Id | Out-File -FilePath $PidFile -NoNewline -Encoding utf8

    Start-Sleep -Milliseconds 1000

    if (Test-ServerRunning) {
        Write-Host "[+] Server started — PID $($proc.Id)" -ForegroundColor Green
        Write-Host "    URL:  http://localhost:$Port" -ForegroundColor Green
        Write-Host "    Log:  $LogFile" -ForegroundColor Gray
    } else {
        Write-Host "[-] Server may have failed to start. Check log:" -ForegroundColor Red
        Write-Host "    $LogFile" -ForegroundColor Gray
        if (Test-Path $LogFile) {
            Write-Host "--- last 10 lines ---" -ForegroundColor DarkGray
            try { Get-Content $LogFile -Tail 10 -ErrorAction Stop | ForEach-Object { Write-Host $_ -ForegroundColor DarkGray } } catch { }
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
}

function Stop-Server {
    $pidVal = Get-PidFromFile
    if (-not $pidVal) {
        Write-Host "[i] No PID file found — server not tracked." -ForegroundColor Gray
        return
    }

    try {
        $proc = Get-Process -Id $pidVal -ErrorAction Stop
        Write-Host "[*] Stopping KB WebUI (PID $pidVal) ..." -ForegroundColor Cyan
        $proc.Kill()
        $proc.WaitForExit(3000)
        Write-Host "[+] Server stopped." -ForegroundColor Green
    } catch {
        Write-Host "[i] Process $pidVal not found — already dead." -ForegroundColor Gray
    }

    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

function Show-Status {
    if (Test-ServerRunning) {
        Write-Host "status: running (http://localhost:$global:menuPort)" -ForegroundColor Green
    } else {
        Write-Host "status: stopped" -ForegroundColor Red
    }
}

function Restart-Server {
    param([int]$Port = $DefaultPort)
    Stop-Server
    Start-Sleep -Milliseconds 500
    Start-Server -Port $Port
}

function Read-PortInput {
    $inp = Read-Host "  Port (default $DefaultPort)"
    if ([string]::IsNullOrWhiteSpace($inp)) { return $DefaultPort }
    try {
        $p = [int]$inp
        if ($p -lt 1 -or $p -gt 65535) {
            Write-Host "[!] Port must be 1-65535, using default $DefaultPort" -ForegroundColor Yellow
            return $DefaultPort
        }
        return $p
    } catch {
        Write-Host "[!] Invalid port, using default $DefaultPort" -ForegroundColor Yellow
        return $DefaultPort
    }
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

        switch ($choice) {
            "1" {
                $global:menuPort = Read-PortInput
                Start-Server -Port $global:menuPort
            }
            "2" { Stop-Server }
            "3" { Restart-Server -Port $global:menuPort }
            "0" { Write-Host "Bye." -ForegroundColor Cyan }
            default { Write-Host "[!] Invalid choice." -ForegroundColor Yellow }
        }

        if ($choice -ne "0") {
            Write-Host ""
            Read-Host "  Press Enter to continue"
        }
    } while ($choice -ne "0")
} catch {
    Write-Host ""
    Write-Host "[FATAL] Unexpected error:" -ForegroundColor Red
    Write-Host "  $_" -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
}
