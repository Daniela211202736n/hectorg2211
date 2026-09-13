@echo off
REM Corre la auditoria diaria manualmente (o via el Programador de Tareas
REM de Windows, ver windows_programar_auditoria.bat).

setlocal
cd /d %~dp0

if not exist venv\Scripts\python.exe (
    echo No se encontro el entorno virtual. Corre primero windows_instalar.bat
    pause
    exit /b 1
)

venv\Scripts\python.exe auditor_granjas.py
echo.
echo Reporte guardado en la carpeta auditor_data\
pause
