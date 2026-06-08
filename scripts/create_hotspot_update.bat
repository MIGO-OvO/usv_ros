@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM Create and send a usv_ros git bundle to Jetson over the USV hotspot.
REM Usage:
REM   create_hotspot_update.bat [jetson_target] [remote_bundle]
REM Defaults:
REM   jetson_target  = jetson@10.42.0.1
REM   remote_bundle  = ~/usv_ros_update.bundle

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_DIR=%%~fI"
set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=jetson@10.42.0.1"
set "REMOTE_BUNDLE=%~2"
if "%REMOTE_BUNDLE%"=="" set "REMOTE_BUNDLE=~/usv_ros_update.bundle"

for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%T"
set "OUT_DIR=%REPO_DIR%\.usv_updates"
set "BUNDLE=%OUT_DIR%\usv_ros_update_%STAMP%.bundle"

echo [usv-hotspot-update] repo=%REPO_DIR%
echo [usv-hotspot-update] target=%TARGET%
echo [usv-hotspot-update] remote=%REMOTE_BUNDLE%

where git >nul 2>nul
if errorlevel 1 (
    echo [usv-hotspot-update] ERROR: git not found in PATH
    exit /b 1
)
where scp >nul 2>nul
if errorlevel 1 (
    echo [usv-hotspot-update] ERROR: scp not found in PATH
    echo Install OpenSSH Client or copy the bundle manually from %OUT_DIR%.
    exit /b 1
)

cd /d "%REPO_DIR%" || exit /b 1
for /f "delims=" %%H in ('git rev-parse --short HEAD') do set "HEAD_SHORT=%%H"
for /f "delims=" %%B in ('git branch --show-current') do set "BRANCH=%%B"
if "%BRANCH%"=="" set "BRANCH=detached"

echo [usv-hotspot-update] branch=%BRANCH% head=%HEAD_SHORT%
mkdir "%OUT_DIR%" >nul 2>nul

echo [usv-hotspot-update] creating bundle...
git bundle create "%BUNDLE%" HEAD
if errorlevel 1 exit /b 1

echo [usv-hotspot-update] verifying bundle...
git bundle verify "%BUNDLE%"
if errorlevel 1 exit /b 1

echo [usv-hotspot-update] sending bundle over hotspot...
scp "%BUNDLE%" "%TARGET%:%REMOTE_BUNDLE%"
if errorlevel 1 (
    echo [usv-hotspot-update] ERROR: scp failed
    echo Bundle kept at: %BUNDLE%
    echo You can copy it manually, then run on Jetson:
    echo   usvimport %REMOTE_BUNDLE%
    exit /b 1
)

echo.
echo [usv-hotspot-update] done
echo Local bundle : %BUNDLE%
echo Jetson import:
echo   usvoff
echo   usvimport %REMOTE_BUNDLE%
echo   usvbuild
echo   usvon
exit /b 0
