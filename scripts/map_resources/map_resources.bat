@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM Interactive offline map resource helper for Windows.
REM Actions: download/export, inspect, import. Packs auto-detected from common dirs.

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "REPO_DIR=%%~fI"
set "PACK_DIR=%REPO_DIR%\.map_packs"
set "DEFAULT_CACHE=%USERPROFILE%\usv_ws\map_cache"
if not exist "%PACK_DIR%" mkdir "%PACK_DIR%" >nul 2>nul

set "PY="
where py >nul 2>nul && set "PY=py -3"
if "%PY%"=="" where python >nul 2>nul && set "PY=python"
if "%PY%"=="" (
    echo [mapres] ERROR: Python not found in PATH.
    exit /b 1
)

:menu
echo.
echo ===== USV Map Resources =====
echo pack dir: %PACK_DIR%
echo 1^) download map pack ^(interactive bbox/zoom^)
echo 2^) inspect available pack
echo 3^) import available pack
echo 4^) export from existing cache
echo q^) quit
set /p "CHOICE=Select: "
if "%CHOICE%"=="" exit /b 0
if /i "%CHOICE%"=="1" goto download
if /i "%CHOICE%"=="2" goto inspect
if /i "%CHOICE%"=="3" goto import
if /i "%CHOICE%"=="4" goto export_cache
if /i "%CHOICE%"=="q" exit /b 0
goto menu

:download
cd /d "%PACK_DIR%" || exit /b 1
%PY% "%SCRIPT_DIR%map_pack_export.py" -i
pause
goto menu

:export_cache
set "CACHE_DIR=%DEFAULT_CACHE%"
set /p "CACHE_DIR=Cache dir [%DEFAULT_CACHE%]: "
if "%CACHE_DIR%"=="" set "CACHE_DIR=%DEFAULT_CACHE%"
for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%T"
set "OUT=%PACK_DIR%\map_cache_%STAMP%.tar"
set /p "OUT=Output pack [%OUT%]: "
if "%OUT%"=="" set "OUT=%PACK_DIR%\map_cache_%STAMP%.tar"
%PY% "%SCRIPT_DIR%map_pack_export.py" --from-cache "%CACHE_DIR%" --out "%OUT%"
pause
goto menu

:inspect
call :select_pack
if errorlevel 1 goto menu
%PY% "%SCRIPT_DIR%map_pack_import.py" "%PICKED_PACK%" --inspect
pause
goto menu

:import
call :select_pack
if errorlevel 1 goto menu
set "CACHE_DIR=%DEFAULT_CACHE%"
set /p "CACHE_DIR=Import cache dir [%DEFAULT_CACHE%]: "
if "%CACHE_DIR%"=="" set "CACHE_DIR=%DEFAULT_CACHE%"
%PY% "%SCRIPT_DIR%map_pack_import.py" "%PICKED_PACK%" --cache-dir "%CACHE_DIR%"
pause
goto menu

:select_pack
set "PICKED_PACK="
set "LIST_FILE=%TEMP%\usv_map_packs_%RANDOM%.txt"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$dirs=@('%PACK_DIR%','%CD%','%USERPROFILE%\Downloads','%USERPROFILE%\usv_ws');" ^
  "$files=@(); foreach($d in $dirs){ if(Test-Path $d){ $files += Get-ChildItem -Path $d -Recurse -File -Include *.tar,*.pack -ErrorAction SilentlyContinue }};" ^
  "$files | Sort-Object LastWriteTime -Descending -Unique | Select-Object -ExpandProperty FullName" > "%LIST_FILE%"
set /a N=0
for /f "usebackq delims=" %%F in ("%LIST_FILE%") do (
    set /a N+=1
    set "PACK_!N!=%%F"
    echo !N!^) %%F
)
if %N% LEQ 0 (
    echo [mapres] No .tar/.pack found. Put packs in %PACK_DIR% or Downloads.
    del "%LIST_FILE%" >nul 2>nul
    exit /b 1
)
set /p "IDX=Pick pack number: "
for /f "tokens=*" %%A in ("!PACK_%IDX%!") do set "PICKED_PACK=%%A"
del "%LIST_FILE%" >nul 2>nul
if "%PICKED_PACK%"=="" (
    echo [mapres] Invalid selection.
    exit /b 1
)
exit /b 0
