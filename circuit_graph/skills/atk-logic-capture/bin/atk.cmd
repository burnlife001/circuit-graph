@echo off
REM atk.cmd — Windows shim for atk-logic-capture.
REM
REM Two invocation styles:
REM   (preferred) bash shim: "%~dp0atk" %*
REM     - no PATHEXT recursion risk because cmd.exe doesn't auto-resolve
REM       extensionless files via PATHEXT for paths beginning with a drive
REM       letter + backslash when followed by `%*` (treats as literal).
REM   (fallback)  python direct: python "%~dp0atk.py" %*
REM     - works even without git-bash on PATH, but emits a cosmetic
REM       "'M' is not recognized" line under pyenv-win's python.bat shim.
REM       The actual command output is unaffected.

if exist "%~dp0atk.exe" (
    "%~dp0atk.exe" %*
) else (
    python "%~dp0atk.py" %*
)
exit /b %ERRORLEVEL%
