# atk-logic-capture — One-shot deployment script.
# Architecture: atk_bg.exe (C# .NET 4.0, 12.5 KB) drives the ATK-LogicView
# GUI via PostMessage. NO proxy DLL, NO TCP server, NO DLL injection.
#
# Run from the skill source directory (where this script lives).
# Requires: PowerShell 7+, Python 3.12+ on PATH, .NET Framework 4.0 (for csc.exe).

$ErrorActionPreference = 'Stop'
$Host.UI.RawUI.WindowTitle = 'atk-logic-capture — Installer'

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
║          atk-logic-capture — 一键部署脚本                    ║
║          PostMessage-based, no DLL injection                ║
╚══════════════════════════════════════════════════════════════╝
'@

if ($env:USERNAME -eq 'root') {
    Write-Warn 'Running as SYSTEM/root; env:USERPROFILE may be wrong.'
}

# ── 0. menu ──────────────────────────────────────────────────────────────────

Write-Host "`n  1. Install all (skill + atk_bg.exe)"
Write-Host '  0. Exit'
$choice = Read-Host "`n  Select"
if ($choice -ne '1') {
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

# ── shared: build atk_bg.exe ──────────────────────────────────────────────

function Build-AtkBg($rootDir) {
    $srcCs   = Join-Path $rootDir 'tools\atk_bg\atk_bg.cs'
    $exeOut  = Join-Path $rootDir 'tools\atk_bg\atk_bg.exe'
    $buildPs = Join-Path $rootDir 'tools\atk_bg\build.ps1'

    if (-not (Test-Path $srcCs)) {
        Write-Fail "Source not found: $srcCs"
        return $false
    }
    if (-not (Test-Path $buildPs)) {
        Write-Fail "Build script not found: $buildPs"
        return $false
    }
    if (Test-Path $exeOut) {
        Write-OK "atk_bg.exe already present: $exeOut"
        return $true
    }
    Write-OK 'Building atk_bg.exe (csc.exe, .NET 4.0)...'
    & pwsh -NoProfile -ExecutionPolicy Bypass -File $buildPs
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "atk_bg build failed (exit $LASTEXITCODE)"
        return $false
    }
    if (-not (Test-Path $exeOut)) {
        Write-Fail "Build script reported success but $exeOut is missing."
        return $false
    }
    Write-OK "Built: $exeOut"
    return $true
}

# ── shared: verify Qt5Network.dll is the original (no proxy) ───────────────

function Verify-Qt5NetworkOriginal($guiDir) {
    # The old proxy was ~250–400 KB; original Qt5Network.dll is ~1.3 MB.
    $dll = Join-Path $guiDir 'Qt5Network.dll'
    if (-not (Test-Path $dll)) {
        Write-Warn "$dll not found — GUI install may be incomplete."
        return
    }
    $size = (Get-Item $dll).Length
    if ($size -lt 800000) {
        Write-Warn ("Qt5Network.dll size is {0} KB — this looks like the OLD proxy DLL!" -f [int]($size/1024))
        Write-Warn "If you migrated from the old proxy-based installer, restore the original:"
        Write-Warn "  Rename $guiDir\Qt5Network.dll → Qt5Network_proxy.dll.bak"
        Write-Warn "  Rename $guiDir\Qt5Network_real.dll → Qt5Network.dll"
    } else {
        Write-OK "Qt5Network.dll is the original ($([math]::Round($size/1024, 1)) KB) — no proxy present."
    }
}

# ── shared: draw done box ──────────────────────────────────────────────────

function Get-DisplayWidth([string]$s) {
    $w = 0
    foreach ($ch in $s.ToCharArray()) {
        if ([int]$ch -gt 0x2E80) { $w += 2 } else { $w += 1 }
    }
    return $w
}

function Show-DoneBox($skillDir, $guiDir, $dataDir) {
    $shimDir = Join-Path $env:USERPROFILE 'bin'
    $lines = @(
        '',
        '部署完成！',
        '',
        "Skill 目录:    $skillDir",
        "atk_bg.exe:    $skillDir\tools\atk_bg\atk_bg.exe",
        "ATK-Logic:     $guiDir",
        "数据目录:      $dataDir",
        "PATH shim:     $shimDir\atk.cmd, $shimDir\atk",
        '',
        '快速测试（任意目录）:',
        '  atk capture start --duration 2s',
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

# ── locate / create target SKILL_DIR ──────────────────────────────────────

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

# ── copy skill files ─────────────────────────────────────────────────────

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

# ── build atk_bg.exe ─────────────────────────────────────────────────────

Write-Step '3/5  Building atk_bg.exe'

$atkBgBuilt = Build-AtkBg $DST_DIR
if (-not $atkBgBuilt) {
    Write-Fail 'atk_bg.exe build failed — cannot continue.'
    exit 1
}

# ── locate ATK-Logic + sanity-check Qt5Network.dll ───────────────────────

Write-Step '4/5  Locating ATK-Logic and sanity-checking Qt5Network.dll'

$gui = Find-ATKLogicGUI
if ($gui) {
    Write-OK "Found ATK-Logic: $($gui.Exe)"
    Verify-Qt5NetworkOriginal $gui.Dir
} else {
    Write-Warn 'ATK-Logic not found — install the GUI from the official site, then re-run.'
}

$dataDir = 'E:\__electric\atk-logic-data'
$logDir  = 'E:\__electric\atk-logic-data\logs'
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
New-Item -ItemType Directory -Force -Path $logDir  | Out-Null
Write-OK "Ensured: $dataDir / logs"

# ── 4b/5 install PATH shim ───────────────────────────────────────────────

Write-Step '4b/5  Installing PATH shim (`atk` command)'

$userBin = Join-Path $env:USERPROFILE 'bin'
$srcBin  = Join-Path $DST_DIR 'bin'

if (-not (Test-Path $srcBin)) {
    Write-Warn "Skill bin/ not found at $srcBin — skipping shim install."
} else {
    New-Item -ItemType Directory -Force -Path $userBin | Out-Null
    foreach ($f in 'atk.cmd', 'atk', 'atk.py') {
        $s = Join-Path $srcBin $f
        $d = Join-Path $userBin $f
        if (Test-Path $s) {
            Copy-Item -Force $s $d
            Write-OK "Shim: $d"
        }
    }

    $currentUserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($currentUserPath -notlike "*$userBin*") {
        [Environment]::SetEnvironmentVariable(
            'Path',
            "$userBin;$currentUserPath",
            'User'
        )
        Write-OK "Added $userBin to user PATH (restart shell to pick up)."
    } else {
        Write-OK "$userBin already on user PATH."
    }
}

# ── preflight ────────────────────────────────────────────────────────────

Write-Step '5/5  Running preflight check'

$preflight = "$DST_DIR\scripts\capture\atk_preflight.py"
if (Test-Path $preflight) {
    python $preflight --fix --json 2>&1 | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -eq 0) {
        Write-OK 'Preflight PASSED — deployment complete!'
    } else {
        Write-Warn 'Preflight reported issues; review the output above.'
    }
} else {
    Write-Warn "Preflight script not found at $preflight"
}

# ── done ──────────────────────────────────────────────────────────────────

Show-DoneBox $DST_DIR ($gui.Dir ?? '(未找到)') $dataDir
Pause