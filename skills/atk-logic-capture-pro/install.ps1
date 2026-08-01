# ATK-Logic Capture — One-shot deployment script
# Run from the skill source directory (where this script lives).
# Requires: PowerShell 7+, Python 3.12+ on PATH

$ErrorActionPreference = 'Stop'
$Host.UI.RawUI.WindowTitle = 'ATK-Logic Capture — Installer'

# ── helpers ────────────────────────────────────────────────────────────────

function Write-Step { param($msg) Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-OK   { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Write-Fail { param($msg) Write-Host "  [FAIL] $msg" -ForegroundColor Red }

function Test-Admin {
    $current = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($current)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ── 0. preamble ────────────────────────────────────────────────────────────

Write-Host @'

╔══════════════════════════════════════════════════════════════╗
║          ATK-Logic Capture — 一键部署脚本                    ║
║          https://github.com/burnlife001/atk-logic-capture    ║
╚══════════════════════════════════════════════════════════════╝
'@

if ($env:USERNAME -eq 'root') {
    Write-Warn 'Running as SYSTEM/root; env:USERPROFILE may be wrong.'
}

# ── 0. menu ──────────────────────────────────────────────────────────────────

Write-Host "`n  1. Install all (skills + proxy DLL)"
Write-Host '  2. Install proxy DLL only'
Write-Host '  0. Exit'
$choice = Read-Host "`n  Select"
if ($choice -notin '1', '2') {
    Write-Host '  Bye.' -ForegroundColor Gray
    exit 0
}

# ── common: locate source skill directory ───────────────────────────────────

Write-Step 'Locating source skill directory'

$SRC_DIR = $PSScriptRoot
if (-not (Test-Path "$SRC_DIR\SKILL.md")) {
    Write-Fail "SKILL.md not found under $SRC_DIR — run this script from the skill root."
    exit 1
}
Write-OK "Source = $SRC_DIR"

# ── shared: locate ATK-Logic GUI ────────────────────────────────────────────

function Find-ATKLogicGUI {
    $GUI_SEARCH = @(
        'C:\Program Files\ATK-Logic'
        'D:\Program Files\ATK-Logic'
        'C:\Program Files (x86)\ATK-Logic'
        'D:\Program Files (x86)\ATK-Logic'
        'C:\Programs\ATK-Logic'
        'D:\Programs\ATK-Logic'
        "$env:LOCALAPPDATA\ATK-Logic"
        "$env:ProgramFiles\ATK-Logic"
        "${env:ProgramFiles(x86)}\ATK-Logic"
    )

    foreach ($d in $GUI_SEARCH) {
        if ($d -and (Test-Path "$d\ATK-Logic.exe")) {
            return @{ Dir = $d; Exe = "$d\ATK-Logic.exe" }
        }
    }

    Write-Warn 'ATK-Logic.exe not found in standard locations.'
    $manual = Read-Host 'Enter full path to ATK-Logic directory (or press Enter to skip)'
    if ($manual -and (Test-Path "$manual\ATK-Logic.exe")) {
        return @{ Dir = $manual; Exe = "$manual\ATK-Logic.exe" }
    }
    return $null
}

# ── shared: deploy proxy DLL ────────────────────────────────────────────────

function Deploy-ProxyDLL($guiDir) {
    # kill any running ATK-Logic process
    $atkProcs = Get-Process -Name 'ATK-Logic' -ErrorAction SilentlyContinue
    if ($atkProcs) {
        Write-Warn "ATK-Logic is running (PID: $($atkProcs.Id -join ', ')); stopping..."
        $atkProcs | Stop-Process -Force
        Start-Sleep -Milliseconds 500
        Write-OK 'ATK-Logic process(es) terminated'
    }

    $srcDll     = "$SRC_DIR\proxy_dll\Qt5Network.dll"
    $realDll    = "$guiDir\Qt5Network_real.dll"
    $targetDll  = "$guiDir\Qt5Network.dll"

    if (-not (Test-Path $srcDll)) {
        Write-Fail "Proxy DLL not found at $srcDll — build it first with proxy_dll\src\build.ps1"
        exit 1
    }

    if (Test-Path $realDll) {
        Write-OK "Qt5Network_real.dll already exists — overwriting Qt5Network.dll directly"
        Copy-Item -Force $srcDll $targetDll
    } elseif (Test-Path $targetDll) {
        Write-OK 'Backing up original Qt5Network.dll → Qt5Network_real.dll'
        Rename-Item $targetDll 'Qt5Network_real.dll'
        Copy-Item $srcDll $targetDll
    } else {
        Write-Warn "Qt5Network.dll not found in $guiDir — copying proxy DLL anyway"
        Copy-Item $srcDll $targetDll
    }
    Write-OK "Proxy DLL deployed: $targetDll"
}

# ── shared: draw done box ───────────────────────────────────────────────────

function Get-DisplayWidth([string]$s) {
    $w = 0
    foreach ($ch in $s.ToCharArray()) {
        if ([int]$ch -gt 0x2E80) { $w += 2 } else { $w += 1 }
    }
    return $w
}

function Show-DoneBox($skillDir, $guiDir, $dataDir) {
    $lines = @(
        '',
        '部署完成！',
        '',
        "Skill 目录:  $skillDir",
        "ATK-Logic:   $guiDir",
        "数据目录:    $dataDir",
        '',
        '快速测试:',
        '  atk_cli.py capture start --duration 2s',
        ''
    )

    $padLeft  = '  '
    $padRight = '  '
    $padWidth = (Get-DisplayWidth $padLeft) + (Get-DisplayWidth $padRight)

    $innerWidth = ($lines | ForEach-Object { (Get-DisplayWidth $_) + $padWidth } | Measure-Object -Maximum).Maximum
    $innerWidth = [Math]::Max($innerWidth, 40)

    Write-Host ''
    Write-Host ('╔' + [string]::new('═', $innerWidth) + '╗')
    foreach ($line in $lines) {
        $trailPad = $innerWidth - (Get-DisplayWidth $line) - $padWidth
        Write-Host ('║' + $padLeft + $line + [string]::new(' ', [Math]::Max(0, $trailPad)) + $padRight + '║')
    }
    Write-Host ('╚' + [string]::new('═', $innerWidth) + '╝')
}

# ══════════════════════════════════════════════════════════════════════════════
# Option 1 — Install ALL
# ══════════════════════════════════════════════════════════════════════════════

if ($choice -eq '1') {

    # ── locate / create target SKILL_DIR ──────────────────────────────────

    Write-Step '1/5  Locating target Claude skills directory'

    $SKILL_SEARCH = @(
        "$env:USERPROFILE\.claude\skills"
        "$env:LOCALAPPDATA\Claude\skills"
        "$env:APPDATA\Claude\skills"
        "$home\.claude\skills"
        ([Environment]::GetFolderPath('UserProfile') + '\.claude\skills')
    )

    $SKILL_DIR = $null
    foreach ($p in $SKILL_SEARCH) {
        if ($p -and (Test-Path $p)) {
            $SKILL_DIR = $p
            Write-OK "Found skills root: $SKILL_DIR"
            break
        }
    }

    if (-not $SKILL_DIR) {
        Write-Warn 'No existing .claude\skills found; creating one.'
        $SKILL_DIR = "$env:USERPROFILE\.claude\skills"
        New-Item -ItemType Directory -Force -Path $SKILL_DIR | Out-Null
        Write-OK "Created: $SKILL_DIR"
    }

    $DST_DIR = "$SKILL_DIR\atk-logic-capture"

    # ── copy skill files ──────────────────────────────────────────────────

    Write-Step '2/5  Copying skill files'

    if (Test-Path $DST_DIR) {
        Write-Warn "Target already exists: $DST_DIR"
        $answer = Read-Host 'Overwrite? (y/n)'
        if ($answer -ne 'y') {
            Write-Warn 'User cancelled overwrite — aborting.'
            exit 0
        }
        Remove-Item -Recurse -Force $DST_DIR
        Write-OK "Removed old copy"
    }

    $exclude = @('__pycache__', '.codegraph', '.git')
    $toCopy = Get-ChildItem $SRC_DIR -Force | Where-Object { $exclude -notcontains $_.Name }
    foreach ($item in $toCopy) {
        Copy-Item -Recurse -Force $item.FullName $DST_DIR
    }
    Write-OK "Copied to $DST_DIR"

    # ── proxy DLL ─────────────────────────────────────────────────────────

    Write-Step '3/5  Locating ATK-Logic GUI & deploying proxy DLL'

    $gui = Find-ATKLogicGUI
    if ($gui) {
        Write-OK "Found ATK-Logic: $($gui.Exe)"
        Deploy-ProxyDLL $gui.Dir
    } else {
        Write-Warn 'Skipping proxy DLL — ATK-Logic not found.'
    }

    # ── update _config.py ─────────────────────────────────────────────────

    Write-Step "4/5  Updating _config.py with local paths"

    $configFile = "$DST_DIR\scripts\_config.py"
    if (-not (Test-Path $configFile)) {
        Write-Fail "_config.py not found at $configFile"
        exit 1
    }

    $config = Get-Content $configFile -Raw -Encoding UTF8

    if ($gui) {
        $guiDirFwd = $gui.Dir.Replace('\', '/')
        $guiExeFwd = "$guiDirFwd/ATK-Logic.exe"
        $config = $config -replace 'GUI_EXE = Path\("[^"]*"\)', "GUI_EXE = Path(`"$guiExeFwd`")"
        $config = $config -replace 'GUI_DIR = Path\("[^"]*"\)', "GUI_DIR = Path(`"$guiDirFwd`")"
    }

    $dataDir = 'E:\__electric\atk-logic-data'
    $logDir  = 'E:\__electric\atk-logic-data\logs'
    $dataDirFwd = $dataDir.Replace('\', '/')
    $logDirFwd  = $logDir.Replace('\', '/')
    $config = $config -replace 'DATA_DIR = Path\("[^"]*"\)', "DATA_DIR = Path(`"$dataDirFwd`")"
    $config = $config -replace 'LOG_DIR = Path\("[^"]*"\)',  "LOG_DIR = Path(`"$logDirFwd`")"

    $appdataAtk = "$env:APPDATA\ALIENTEK\ATK-LogicView".Replace('\', '/')
    $config = $config -replace 'APPDATA_ATK = Path\("[^"]*"\)', "APPDATA_ATK = Path(`"$appdataAtk`")"

    Set-Content -Path $configFile -Value $config -Encoding UTF8 -NoNewline
    Write-OK "Updated: $configFile"

    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    New-Item -ItemType Directory -Force -Path $logDir  | Out-Null
    Write-OK "Created: $dataDir / logs"

    # ── Python dependency ─────────────────────────────────────────────────

    Write-Step '5/5  Installing Python dependency (uiautomation)'
    $pipCmd = Get-Command pip -ErrorAction SilentlyContinue
    if (-not $pipCmd) { $pipCmd = Get-Command pip3 -ErrorAction SilentlyContinue }
    if (-not $pipCmd) {
        Write-Fail 'pip not found on PATH. Install Python 3.12+ and add it to PATH.'
        exit 1
    }
    & $pipCmd.Source install uiautomation 2>&1 | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-Warn 'pip install returned non-zero; check the output above.'
    } else {
        Write-OK 'uiautomation installed'
    }

    # ── preflight ─────────────────────────────────────────────────────────

    Write-Step 'Running preflight check'

    $preflight = "$DST_DIR\scripts\atk_preflight.py"
    if (Test-Path $preflight) {
        python $preflight --fix 2>&1 | ForEach-Object { Write-Host "  $_" }
        if ($LASTEXITCODE -eq 0) {
            Write-OK 'Preflight PASSED — deployment complete!'
        } else {
            Write-Warn 'Preflight reported issues; review the output above.'
        }
    } else {
        Write-Warn 'Preflight script not found; skipping.'
    }

    # ── done ──────────────────────────────────────────────────────────────

    Show-DoneBox $DST_DIR ($gui.Dir ?? '(未找到)') $dataDir
    Pause
}

# ══════════════════════════════════════════════════════════════════════════════
# Option 2 — Proxy DLL only
# ══════════════════════════════════════════════════════════════════════════════

if ($choice -eq '2') {

    Write-Step 'Deploying proxy DLL'

    $gui = Find-ATKLogicGUI
    if (-not $gui) {
        Write-Fail 'ATK-Logic not found — cannot deploy proxy DLL.'
        exit 1
    }
    Write-OK "Found ATK-Logic: $($gui.Exe)"
    Deploy-ProxyDLL $gui.Dir

    # ── preflight ─────────────────────────────────────────────────────────

    Write-Step 'Running preflight check'

    $SKILL_SEARCH = @(
        "$env:USERPROFILE\.claude\skills"
        "$env:LOCALAPPDATA\Claude\skills"
        "$env:APPDATA\Claude\skills"
        "$home\.claude\skills"
        ([Environment]::GetFolderPath('UserProfile') + '\.claude\skills')
    )

    $preflight = $null
    foreach ($p in $SKILL_SEARCH) {
        $candidate = "$p\atk-logic-capture\scripts\atk_preflight.py"
        if ($p -and (Test-Path $candidate)) {
            $preflight = $candidate
            break
        }
    }

    if ($preflight) {
        python $preflight --fix 2>&1 | ForEach-Object { Write-Host "  $_" }
        if ($LASTEXITCODE -eq 0) {
            Write-OK 'Preflight PASSED — deployment complete!'
        } else {
            Write-Warn 'Preflight reported issues; review the output above.'
        }
    } else {
        Write-Warn 'Preflight script not found; skipping.'
    }

    Show-DoneBox '(unchanged)' $gui.Dir '(unchanged)'
    Pause
}
