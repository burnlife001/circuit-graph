# atk_bg.exe build script (.NET Framework 4.0, csc.exe)
# Output lands next to this script.

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

# .NET Framework 4.0 reference assemblies are still in v4.0.30319.
$CscExe = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$Src    = Join-Path $ScriptDir 'atk_bg.cs'
$Out    = Join-Path $ScriptDir 'atk_bg.exe'

if (-not (Test-Path $CscExe)) { throw "csc.exe not found at $CscExe" }
if (-not (Test-Path $Src))    { throw "source not found: $Src" }

Write-Host "Building atk_bg.exe (csc.exe, .NET 4.0)..." -ForegroundColor Cyan
& $CscExe /nologo /o+ /out:$Out $Src
if ($LASTEXITCODE -ne 0) { throw "csc.exe failed (exit $LASTEXITCODE)" }

if (-not (Test-Path $Out)) { throw "output not produced: $Out" }
$dll = Get-Item $Out
Write-Host "  OK: $Out  ($([math]::Round($dll.Length / 1024, 1)) KB)" -ForegroundColor Green
