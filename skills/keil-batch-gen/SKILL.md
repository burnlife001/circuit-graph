---
name: keil-batch-gen
description: Generate __build.ps1 / __download.ps1 next to .uvprojx, update project CLAUDE.md
---

# Keil Batch Scripts Generation

Locate `UV4.exe` (`C:\Keil_v5\UV4\UV4.exe` or `D:\Keil_v5\UV4\UV4.exe`), then copy templates from `scripts/` next to the `.uvprojx` and replace placeholders.

## Steps

1. Find `.uvprojx` and put the scripts in the same directory.
2. Copy from `scripts/`:
   - `__build.ps1` — build only
   - `__download.ps1` — flash only
   - `__build_and_download.ps1` — build then flash (build failure stops before download)
3. Replace placeholders:
   - `{{UV4_PATH}}` — full path to `UV4.exe`
   - `{{PROJECT_NAME}}` — project name without `.uvprojx`
   - `{{OUTPUT_NAME}}` — executable name from Keil "Options for Target → Output" (usually same as project name)

## Result Detection

- **Build**: read `build.log` for `N Error(s)` / `N Warning(s)`. Also checks `.axf` timestamp against source files to catch stale builds.
- **Download**: read `download.log` for `Verify OK`.

## Working Directory

All scripts use `Push-Location $PSScriptRoot` so UV4.exe always runs in the project directory, regardless of where the caller invoked the script. This ensures Keil finds the project file, scatter file, and output paths correctly.

## Note on Scatter File

If `Objects/*.sct` (scatter description file) is missing, the linker fails with `L6031U: Could not open scatter description file`. Do not delete it when cleaning build artifacts — only remove `*.o`, `*.d`, `*.crf`, `*.dep`.
