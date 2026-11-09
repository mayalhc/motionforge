@echo off
REM Starts the ARDY service for Cascadeur's ARDY Live, on the engine MotionForge uses.
REM
REM Cascadeur's panel starts this itself, in its own window, and never waits on
REM it: a blocking launch from inside Cascadeur crashed it. Closing this window
REM stops the service; so does stop_ardy.bat.
setlocal
pushd "%~dp0"

set "PY=%~dp0python\python.exe"
if not exist "%PY%" (
    where py >nul 2>nul && set "PY=py -3"
)
if not exist "%~dp0ardy\bin\ardy-server.exe" (
    echo ARDY is not installed in %~dp0
    echo Press Install Engines in MotionForge's preferences first.
    popd
    pause
    exit /b 1
)

echo ======================================================================
echo  ARDY service for Cascadeur  -  same engine as MotionForge
echo  Leave this window open while you use ARDY Live.
echo  The first start loads the model onto the GPU.
echo ======================================================================
echo.

%PY% -I -u "%~dp0plugins\cascadeur\engine_service.py" --engine "%~dp0." --port 9553

popd
pause
