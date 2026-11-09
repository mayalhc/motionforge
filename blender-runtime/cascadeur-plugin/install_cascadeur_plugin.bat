@echo off
REM Installs the Cascadeur plug-in that Install Engines downloaded into this folder.
REM It asks for Administrator, because Cascadeur lives under Program Files.
setlocal
set "ENGINE_ROOT=%~dp0."
if not exist "%~dp0plugins\cascadeur\install_plugin.bat" (
    echo The Cascadeur plug-in is not in %~dp0plugins\cascadeur yet.
    echo Press Install Engines in Blender's Preferences - Add-ons - MotionForge first.
    pause
    exit /b 1
)
call "%~dp0plugins\cascadeur\install_plugin.bat"
