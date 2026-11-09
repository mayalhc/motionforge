@echo off
REM Stops the ARDY service. It frees its port and the GPU itself.
setlocal
set "PY=%~dp0python\python.exe"
if not exist "%PY%" set "PY=py -3"
%PY% -I -c "import urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:9553/shutdown', data=b'{}', method='POST'), timeout=10); print('ARDY service stopped.')" 2>nul || echo ARDY service is not running.
