@echo off
REM Copyright (c) 2026 Chamiseul. All rights reserved.
REM ---------------------------------------------------------------------------
REM Installs the Cascadeur plugin from THIS folder.
REM
REM Self-contained on purpose: the source is wherever this file sits, so the
REM folder can be unzipped anywhere and this still works. Cascadeur lives under
REM Program Files, which needs Administrator to write into, so it asks for
REM elevation and explains why.
REM
REM Each file is compared before it is written. "18 unchanged, 0 written" after
REM an edit is the useful case - it says the edit never reached here.
REM ---------------------------------------------------------------------------
if not "%~1"=="__run__" (
    cmd /k ""%~f0" __run__ %*"
    exit /b
)
setlocal enabledelayedexpansion
pushd "%~dp0"
title Cascadeur plugin installer

set "SRC=%~dp0"
if "%SRC:~-1%"=="\" set "SRC=%SRC:~0,-1%"

set "ALREADY_ELEVATED="
set "NO_PAUSE="
set "CASCADEUR_ROOT="
:args
if "%~2"=="" goto :args_done
if /I "%~2"=="--elevated" set "ALREADY_ELEVATED=1"
REM Called from install.bat, where a "Press any key" would stop the
REM whole install dead on a screen nobody is watching.
if /I "%~2"=="--no-pause" set "NO_PAUSE=1"
if /I "%~2"=="--cascadeur-root" set "CASCADEUR_ROOT=%~3"
if /I "%~2"=="--engine-root" set "ENGINE_ROOT=%~3"
shift
goto :args
:args_done

echo ==========================================================
echo   Cascadeur plugin installer
echo   ARDY Live, MetaArKit, Rig Quadruped (auto)
echo ==========================================================
echo.

REM -- where Cascadeur is ------------------------------------------------------
set "DEFAULT_CASCADEUR=C:\Program Files\Cascadeur"
if not defined CASCADEUR_ROOT (
    if exist "%DEFAULT_CASCADEUR%\cascadeur.exe" (
        set "CASCADEUR_ROOT=%DEFAULT_CASCADEUR%"
    ) else (
        set /p "CASCADEUR_ROOT=Cascadeur folder [%DEFAULT_CASCADEUR%]: "
        if "!CASCADEUR_ROOT!"=="" set "CASCADEUR_ROOT=%DEFAULT_CASCADEUR%"
    )
)
for %%I in ("%CASCADEUR_ROOT%") do set "CASCADEUR_ROOT=%%~fI"

if not exist "%CASCADEUR_ROOT%\cascadeur.exe" (
    echo ERROR: no cascadeur.exe in
    echo        %CASCADEUR_ROOT%
    echo.
    echo        Re-run with the right folder:
    echo          install_plugin.bat --cascadeur-root "D:\Games\Cascadeur"
    goto :fail
)
echo   Cascadeur: %CASCADEUR_ROOT%

set "SCRIPT_DEST=%CASCADEUR_ROOT%\resources\scripts\python\commands\animation_scripts"
set "MODELS_DEST=%CASCADEUR_ROOT%\resources\scripts\python\models"
set "SAMPLES_DEST=%CASCADEUR_ROOT%\samples"

REM -- Administrator, only if it is actually needed -----------------------------
REM Tested by writing, not by asking whether we are admin: an install into a
REM folder the user owns needs no elevation, and demanding it would be rude.
REM The probe is EXPECTED to fail on a normal account, so its complaint must
REM not reach the screen - "Access is denied" above a line saying everything
REM is fine reads as a broken installer. cmd prints a redirection failure
REM before the command's own 2>nul takes effect, so the whole thing is
REM parenthesised and the group redirected instead.
set "PROBE=%SCRIPT_DEST%\.write_probe"
if not exist "%SCRIPT_DEST%" (mkdir "%SCRIPT_DEST%") >nul 2>&1
(break > "%PROBE%") >nul 2>&1
if exist "%PROBE%" (
    del "%PROBE%" >nul 2>&1
) else (
    if defined ALREADY_ELEVATED (
        echo ERROR: cannot write into
        echo        %SCRIPT_DEST%
        echo        even as Administrator. Check the folder's permissions.
        goto :fail
    )
    echo.
    echo   Administrator rights are needed to write into Cascadeur's folder.
    echo   Accept the prompt that appears.
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "Start-Process -FilePath '%~f0' -ArgumentList '__run__ --elevated --cascadeur-root \"%CASCADEUR_ROOT%\"' -Verb RunAs"
    if errorlevel 1 (
        echo.
        echo   The Administrator prompt was refused, so nothing was installed.
        goto :fail
    )
    echo   Continuing in the elevated window.
    popd
    endlocal
    exit /b 0
)

if not exist "%MODELS_DEST%" mkdir "%MODELS_DEST%" 2>nul
if not exist "%SAMPLES_DEST%" mkdir "%SAMPLES_DEST%" 2>nul

REM -- where the engine folder is, for ARDY Live, Kimodo and MotionBricks -------------------------------
REM Only ARDY Live needs it. MetaArKit and Rig Quadruped do not, so a blank
REM answer still installs everything - ARDY Live simply says it cannot find
REM the engine until this is filled in.
if not defined ENGINE_ROOT if exist "%SRC%\..\..\start_ardy.bat" for %%I in ("%SRC%\..\..") do set "ENGINE_ROOT=%%~fI"
if not defined ENGINE_ROOT (
    if exist "%SCRIPT_DEST%\kimodo_engine.ini" (
        for /f "usebackq tokens=1,* delims== " %%A in (`findstr /B /C:"kimodo_root" "%SCRIPT_DEST%\kimodo_engine.ini"`) do set "ENGINE_ROOT=%%B"
    )
)
if not defined ENGINE_ROOT (
    echo.
    echo   The panels need the engine folder - the one holding
    echo   start_ardy.bat. Leave this blank if you only want MetaArKit
    echo   and Rig Quadruped.
    set /p "ENGINE_ROOT=Engine folder (optional): "
)
if defined ENGINE_ROOT for %%I in ("%ENGINE_ROOT%") do set "ENGINE_ROOT=%%~fI"
if defined ENGINE_ROOT (
    if exist "!ENGINE_ROOT!\start_ardy.bat" (
        echo   Engine folder: !ENGINE_ROOT!
    ) else (
        echo   NOTE: no start_ardy.bat in !ENGINE_ROOT! - the panels will not
        echo         find the engine. The other tools are unaffected.
    )
) else (
    echo   Engine folder: not set - ARDY Live will ask you to install it.
)

set "_SAME=0"
set "_WROTE=0"

REM -- the commands ------------------------------------------------------------
echo.
echo   [1/5] Animation Scripts commands...
for %%F in (ardy_live.py ardy_apply.py soma_apply.py kimodo_roundtrip.py ardy_stream.py cascadeur_receiver.py live_link.py
            metaarkit.py savgol.py quadruped_autorig.py quadruped_presets.py) do (
    if exist "%SRC%\%%F" call :copyfile "%SRC%\%%F" "%SCRIPT_DEST%\%%F"
)

REM -- the panels --------------------------------------------------------------
echo   [2/5] Panels...
if exist "%SRC%\models\__init__.py" call :copyfile "%SRC%\models\__init__.py" "%MODELS_DEST%\__init__.py"
for %%P in (ardy_live kimodo_roundtrip metaarkit quadruped_autorig) do (
    if not exist "%MODELS_DEST%\%%P" mkdir "%MODELS_DEST%\%%P" 2>nul
    for %%F in (__init__.py model.py view.qml) do (
        if exist "%SRC%\models\%%P\%%F" call :copyfile "%SRC%\models\%%P\%%F" "%MODELS_DEST%\%%P\%%F"
    )
)

REM -- presets and the sample character ----------------------------------------
echo   [3/5] Presets and sample scene...
if exist "%SRC%\quadruped_presets" (
    if not exist "%SCRIPT_DEST%\quadruped_presets" mkdir "%SCRIPT_DEST%\quadruped_presets" 2>nul
    for %%F in ("%SRC%\quadruped_presets\*.json") do (
        call :copyfile "%%~fF" "%SCRIPT_DEST%\quadruped_presets\%%~nxF"
    )
)
if exist "%SRC%\ARDY.casc" call :copyfile "%SRC%\ARDY.casc" "%SAMPLES_DEST%\ARDY.casc"
if exist "%SRC%\Kimodo.casc" call :copyfile "%SRC%\Kimodo.casc" "%SAMPLES_DEST%\Kimodo.casc"

REM -- where the engine is, for the ARDY Live panel -----------------------------
REM Written fresh rather than copied: a drive letter in a shipped file is a
REM path that only works on the machine it was typed on.
echo   [4/5] Recording the engine location...
(
  echo [paths]
  echo kimodo_root = !ENGINE_ROOT!
  echo cascadeur_root = %CASCADEUR_ROOT%
  echo workspace_root = %%TEMP%%\KimodoEngine
) > "%SCRIPT_DEST%\kimodo_engine.ini"
(
  echo [paths]
  echo kimodo_root = !ENGINE_ROOT!
  echo cascadeur_root = %CASCADEUR_ROOT%
  echo kimodo_scene = %SAMPLES_DEST%\Kimodo.casc
  echo workspace_root = %%TEMP%%\KimodoEngine
  echo.
  echo [defaults]
  echo prompt =
  echo samples_num = 1
  echo sample_index = 0
  echo seed = -1
  echo denoising_steps = 20
  echo cfg_enabled = True
  echo text_weight = 2.0
  echo constraint_weight = 2.0
  echo dataset = RP
) > "%SCRIPT_DEST%\kimodo_roundtrip.ini"

REM -- what is verified --------------------------------------------------------
echo   [5/5] Checking...
set "MISSING="
for %%F in (ardy_live.py kimodo_roundtrip.py metaarkit.py quadruped_autorig.py) do (
    if not exist "%SCRIPT_DEST%\%%F" set "MISSING=!MISSING! %%F"
)
if defined MISSING (
    echo ERROR: these did not arrive:!MISSING!
    goto :fail
)

echo.
echo ==========================================================
echo   Installed. %_SAME% unchanged, %_WROTE% written.
echo.
echo   Restart Cascadeur, then look in Animation Scripts:
echo     ARDY Live             - generate motion from a list of takes
echo     Kimodo Roundtrip      - keyposes in, a Kimodo motion through them out
echo     MetaArKit             - ARKit facial capture onto blendshapes
echo     Rig Quadruped (auto)  - fill the Quick Rigging Tool for a quadruped
echo     Receive Poses         - listen for a Blender add-on
echo.
echo   (c) 2026 Chamiseul. All rights reserved.
echo ==========================================================
echo.
popd
endlocal
if not defined NO_PAUSE pause
exit /b 0

REM ---------------------------------------------------------------------------
:copyfile
REM %1 source, %2 destination. Writes only when the bytes differ, so a re-run
REM is cheap and the count at the end means something.
fc /B "%~1" "%~2" >nul 2>&1
if not errorlevel 1 (
    set /a _SAME+=1
    exit /b 0
)
copy /Y "%~1" "%~2" >nul
if errorlevel 1 (
    echo ERROR: could not write %~2
    exit /b 1
)
set /a _WROTE+=1
exit /b 0

:fail
echo.
echo   Nothing was installed. Scroll up for the first ERROR line.
echo.
popd
endlocal
if not defined NO_PAUSE pause
exit /b 1
