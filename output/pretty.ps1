#Requires -Version 5.1
<#
.SYNOPSIS
    Menu-driven JSON humanizer for circuit-graph outputs.

.DESCRIPTION
    Scans the output directory for .json files and converts the selected one
    into a pretty-printed, UTF-8 (no BOM) <filename>_human.json file.

.USAGE
    .\output\pretty.ps1
#>

$ScanRoot = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($ScanRoot)) {
    $ScanRoot = (Get-Location).Path
}

$ExcludedDirs = @('.git', '.venv', 'node_modules', '__pycache__', '.claude', '.pytest_cache')

function Get-JsonFiles {
    Get-ChildItem -Path $ScanRoot -Filter '*.json' -Recurse -File |
        Where-Object {
            $full = $_.FullName
            foreach ($dir in $ExcludedDirs) {
                if ($full -like "*\$dir\*") { return $false }
            }
            return $_.BaseName -notlike '*_human'
        } |
        Sort-Object FullName
}

function Show-Menu {
    Clear-Host
    Write-Host '=== JSON Humanizer Menu ===' -ForegroundColor Cyan
    Write-Host ''

    $files = Get-JsonFiles
    $script:menuItems = @()

    if ($files.Count -eq 0) {
        Write-Host 'No .json files found.' -ForegroundColor Yellow
    } else {
        $index = 1
        foreach ($file in $files) {
            $targetName = '{0}_human.json' -f $file.BaseName
            $targetPath = Join-Path $file.DirectoryName $targetName
            $script:menuItems += [PSCustomObject]@{
                Index  = $index
                Source = $file.FullName
                Target = $targetPath
            }
            Write-Host ('{0,2}. {1}' -f $index, $file.FullName)
            Write-Host ('    -> {0}' -f $targetPath) -ForegroundColor DarkGray
            $index++
        }
    }

    Write-Host ''
    Write-Host ' 0. Exit' -ForegroundColor Red
    Write-Host ''
}

function Convert-ToHumanJson {
    param(
        [Parameter(Mandatory)]
        [PSCustomObject]$Item
    )

    try {
        $raw = Get-Content -Path $Item.Source -Raw -Encoding UTF8
        $obj = $raw | ConvertFrom-Json
        $pretty = $obj | ConvertTo-Json -Depth 100
        [System.IO.File]::WriteAllText($Item.Target, $pretty, [System.Text.UTF8Encoding]::new($false))
        Write-Host ('Created: {0}' -f $Item.Target) -ForegroundColor Green
    } catch {
        Write-Host ('Error processing {0}: {1}' -f $Item.Source, $_.Exception.Message) -ForegroundColor Red
    }
}

while ($true) {
    Show-Menu

    $choice = Read-Host 'Select a file to humanize'

    if ($choice -eq '0') {
        Write-Host 'Exiting...' -ForegroundColor Green
        exit 0
    }

    $parsed = 0
    if (-not [int]::TryParse($choice, [ref]$parsed)) {
        Write-Host 'Invalid input. Please enter a number.' -ForegroundColor Red
        Start-Sleep -Seconds 1
        continue
    }

    $selected = $script:menuItems | Where-Object { $_.Index -eq $parsed } | Select-Object -First 1

    if (-not $selected) {
        Write-Host 'Selection out of range.' -ForegroundColor Red
        Start-Sleep -Seconds 1
        continue
    }

    Convert-ToHumanJson -Item $selected

    Write-Host ''
    Read-Host 'Press Enter to continue'
}
