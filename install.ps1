# install.ps1 - 一键把 circuit-graph 装到本机。
#
#   [1/6] 找到 uv（没有就装）
#   [2/6] 把 uv 缓存盘挪到跟项目同一个盘（跨盘没法 hardlink）
#   [3/6] 建 .venv 并装本项目
#   [4/6] 清掉旧的 `uv tool install` 残留（它会在 ~/.local\bin 留 cg.exe，
#         抢在 venv 前面命中，导致敲 cg 跑的不是这个项目）
#   [5/6] 把 .venv\Scripts 加进用户 PATH
#   [6/6] 验证
#
# 用法：
#   pwsh -ExecutionPolicy Bypass -File .\install.ps1
#   pwsh -ExecutionPolicy Bypass -File .\install.ps1 -Dev        # 连 pytest/ruff 一起装
#   pwsh -ExecutionPolicy Bypass -File .\install.ps1 -SkipPath   # 不动 PATH
#   pwsh -ExecutionPolicy Bypass -File .\install.ps1 -NoPause    # 装完不等回车（CI）
#
# 幂等：重复跑不会重复加 PATH，也不会重装已有依赖。
# 装完（或失败）会停下来等回车，把结果留在屏幕上。

[CmdletBinding()]
param(
    # 项目目录，默认脚本所在目录
    # （不能在 param 默认值里写 $PSScriptRoot：Windows PowerShell 5.1 那会儿
    #  它还是空的，会让 Resolve-Path 拿到 null 直接报错）
    [string]$ProjectDir = '',

    # 连开发依赖（pytest / ruff）一起装
    [switch]$Dev,

    # 不改用户 PATH
    [switch]$SkipPath,

    # 结束时不暂停等回车
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'

if (-not $ProjectDir) { $ProjectDir = $PSScriptRoot }
if (-not $ProjectDir) { $ProjectDir = (Get-Location).Path }

# uv 本体装哪儿（跟 devops_scripts\basic_install\uv_python_installer.ps1 一致）
$UvInstallDir = if ($env:UV_INSTALL_DIR) { $env:UV_INSTALL_DIR }
                elseif ($env:INSTALL_DIR) { $env:INSTALL_DIR }
                else { 'd:/programs' }

function Wait-Exit {
    # 停下来等回车，让安装结果留在屏幕上。-NoPause 跳过（CI / 自动化）。
    if (-not $NoPause) {
        Read-Host "`nPress Enter to exit"
    }
}

function Assert-Ok {
    param([string]$What)
    if ($LASTEXITCODE -ne 0) {
        throw "$What failed (exit code $LASTEXITCODE)"
    }
}

function Find-Uv {
    # 返回 uv.exe 的路径；找不到返回 $null
    $cmd = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue |
           Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    foreach ($p in @(
            (Join-Path $UvInstallDir 'uv.exe'),
            (Join-Path $env:USERPROFILE '.local\bin\uv.exe'))) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Invoke-Native {
    # 跑一个原生命令。退出码看 $LASTEXITCODE。
    # 两个坑：
    #   1) Windows PowerShell 5.1 把子进程的 stderr 当成错误记录，
    #      $ErrorActionPreference='Stop' 时直接终止脚本（uv tool list 遇到
    #      别的坏工具会打 warning）。跑原生命令时临时放开。
    #   2) **不要**把 stdout 接进管道或变量：接住之后子进程的 stdout 变成
    #      字节流，PowerShell 按自己的编码去解码，Python 写的 UTF-8 在
    #      5.1 下会被当 GBK 解，中文全成乱码。不接住的话它直接写控制台
    #      句柄（Python 走 WriteConsoleW），两种 PowerShell 都对。
    param([string]$Exe, [string[]]$NativeArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @NativeArgs
    }
    finally {
        $ErrorActionPreference = $prev
    }
}

function Invoke-Uv {
    param([string[]]$UvArgs)
    Invoke-Native -Exe $UvExe -NativeArgs $UvArgs
}

try {
    $ProjectDir = (Resolve-Path $ProjectDir).Path
    Set-Location $ProjectDir
    Write-Host "project     : $ProjectDir" -ForegroundColor DarkGray

    # ------------------------------------------------------------------ [1/6]
    Write-Host '[1/6] Locating uv ...' -ForegroundColor Cyan
    $UvExe = Find-Uv
    if (-not $UvExe) {
        Write-Host '      uv not found, installing the official copy ...' -ForegroundColor Yellow
        if (-not (Test-Path $UvInstallDir)) {
            New-Item -ItemType Directory -Path $UvInstallDir -Force | Out-Null
        }
        $env:INSTALL_DIR = $UvInstallDir
        Invoke-Expression (Invoke-RestMethod -Uri 'https://astral.sh/uv/install.ps1' -UseBasicParsing)
        $UvExe = Find-Uv
        if (-not $UvExe) { throw "uv installer finished but uv.exe was not found (looked in $UvInstallDir)." }
    }
    Write-Host "      uv         : $UvExe" -ForegroundColor Green
    $rc = Invoke-Uv @('--version')
    Assert-Ok 'uv --version'

    # ------------------------------------------------------------------ [2/6]
    # uv 默认缓存 %LOCALAPPDATA%\uv\cache (C:)，venv 常在别的盘。跨盘没法
    # hardlink，uv 每次装包都打 "Failed to hardlink files; falling back to
    # full copy" 并退化成整份复制。把缓存挪到跟项目同一个盘就行。
    Write-Host '[2/6] Aligning the uv cache with the project drive ...' -ForegroundColor Cyan
    $drive = (Split-Path -Qualifier $ProjectDir)          # 例如 'E:'
    if ($env:UV_CACHE_DIR) {
        Write-Host "      uv cache   : $env:UV_CACHE_DIR (already set)" -ForegroundColor DarkGray
    }
    elseif ($drive -and $drive -ne 'C:') {
        $cacheDir = "$drive\uv-cache"
        if (-not (Test-Path $cacheDir)) {
            New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
        }
        setx UV_CACHE_DIR "$cacheDir" | Out-Null
        $env:UV_CACHE_DIR = $cacheDir
        Write-Host "      uv cache   : $cacheDir (same drive as the project)" -ForegroundColor DarkGray
    }
    else {
        Write-Host '      uv cache   : <uv default> (project on C:, nothing to do)' -ForegroundColor DarkGray
    }

    # ------------------------------------------------------------------ [3/6]
    Write-Host '[3/6] Creating the venv and installing the project ...' -ForegroundColor Cyan
    # --allow-existing: 重跑脚本时保留已有 .venv（uv venv 默认会拒绝，报
    # "A virtual environment already exists at: .venv")。
    Invoke-Uv @('venv', '--allow-existing')
    Assert-Ok 'uv venv'
    $spec = if ($Dev) { '.[dev]' } else { '.' }
    Invoke-Uv @('pip', 'install', '-e', $spec)
    Assert-Ok "uv pip install -e $spec"

    # ------------------------------------------------------------------ [4/6]
    # `uv tool install` 会在 ~/.local\bin 放一个 cg.exe trampoline。那东西
    # 抢在 venv 的 Scripts 前面被 PATH 命中，敲 cg 跑的就不是这个项目了。
    Write-Host '[4/6] Removing any leftover `uv tool` install ...' -ForegroundColor Cyan
    # uv tool list 的 stderr 可能带 warning（比如别的工具坏了），在
    # $ErrorActionPreference='Stop' 下会直接终止脚本，所以这里临时放开。
    $prevEA = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $tools = (& $UvExe tool list 2>&1) -join "`n"
    $ErrorActionPreference = $prevEA
    if ($tools -match '(?m)^circuit-graph\b') {
        Invoke-Uv @('tool', 'uninstall', 'circuit-graph')
        if ($LASTEXITCODE -eq 0) {
            Write-Host '      removed    : uv tool circuit-graph' -ForegroundColor Green
        }
        else {
            Write-Host '      note       : could not remove the uv tool install' -ForegroundColor Yellow
        }
    }
    else {
        Write-Host '      note       : no uv tool install found (nothing to remove)' -ForegroundColor DarkGray
    }

    # ------------------------------------------------------------------ [5/6]
    $venvScripts = Join-Path $ProjectDir '.venv\Scripts'
    if ($SkipPath) {
        Write-Host '[5/6] Skipping PATH (asked to) ...' -ForegroundColor Cyan
    }
    else {
        Write-Host '[5/6] Publishing .venv\Scripts on the user PATH ...' -ForegroundColor Cyan
        $userPath = [Environment]::GetEnvironmentVariable('PATH', 'User')
        $entries = @($userPath -split ';' | Where-Object { $_ })
        if ($entries -contains $venvScripts) {
            Write-Host '      already on PATH' -ForegroundColor DarkGray
        }
        else {
            [Environment]::SetEnvironmentVariable(
                'PATH', (($entries + $venvScripts) -join ';'), 'User')
            Write-Host "      added      : $venvScripts" -ForegroundColor Green
        }
        # 当前进程立刻生效，不然下面验证找不到 cg
        if (($env:PATH -split ';') -notcontains $venvScripts) {
            $env:PATH = "$venvScripts;$env:PATH"
        }
    }

    # ------------------------------------------------------------------ [6/6]
    Write-Host '[6/6] Verifying ...' -ForegroundColor Cyan
    $cg = Join-Path $venvScripts 'cg.exe'
    if (-not (Test-Path $cg)) { throw "expected launcher was not created: $cg" }
    Invoke-Native -Exe $cg -NativeArgs @('--version')
    Assert-Ok 'cg --version'

    $fixture = Join-Path $ProjectDir 'fixtures\protel\FX-T268-V6-10米-5065.NET'
    if (Test-Path $fixture) {
        Write-Host '      smoke test : cg role R100' -ForegroundColor DarkGray
        Invoke-Native -Exe $cg -NativeArgs @('role', 'R100', $fixture)
        Assert-Ok 'cg role R100'
    }

    # ------------------------------------------------------------------ 结果
    Write-Host ''
    Write-Host '[DONE] circuit-graph is installed.' -ForegroundColor Green
    Write-Host "       project     : $ProjectDir" -ForegroundColor DarkGray
    Write-Host "       venv        : $venvScripts" -ForegroundColor DarkGray
    Write-Host "       cg          : $cg" -ForegroundColor DarkGray
    Write-Host "       uv          : $UvExe" -ForegroundColor DarkGray
    Write-Host "       uv cache    : $env:UV_CACHE_DIR" -ForegroundColor DarkGray
    if ($SkipPath) {
        Write-Host '       user PATH   : untouched (-SkipPath)' -ForegroundColor DarkGray
    }
    else {
        Write-Host '       user PATH   : updated (.venv\Scripts added)' -ForegroundColor DarkGray
    }
    Write-Host ''
    Write-Host '       Start a NEW terminal so the PATH change is picked up.' -ForegroundColor Green
    Write-Host '       Then, from anywhere:  cg role R100 <your-file>.NET' -ForegroundColor Green
    exit 0
}
catch {
    Write-Host ''
    Write-Host "[FAIL] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "       project     : $ProjectDir" -ForegroundColor DarkGray
    Write-Host "       venv        : $(Join-Path $ProjectDir '.venv\Scripts')" -ForegroundColor DarkGray
    Write-Host '       fix the problem above, then run this script again.' -ForegroundColor DarkGray
    exit 1
}
finally {
    # 成功和失败都停下来等回车，让结果留在屏幕上（exit 也会走 finally）。
    Wait-Exit
}
