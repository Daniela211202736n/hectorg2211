@echo off
REM Crea una tarea programada de Windows que corre la auditoria diaria todos
REM los dias a las 06:00 (hora de esta PC), equivalente al cron de Linux.
REM Correr UNA SOLA VEZ. Puede pedir permisos de administrador.

setlocal
set TASK_NAME=AuditorGranjasTrading
set SCRIPT_PATH=%~dp0windows_auditoria.bat

schtasks /query /tn "%TASK_NAME%" >nul 2>nul
if not errorlevel 1 (
    echo Ya existe una tarea programada llamada "%TASK_NAME%".
    echo Si quieres cambiar la hora, primero borrala con:
    echo   schtasks /delete /tn "%TASK_NAME%" /f
    pause
    exit /b 0
)

schtasks /create /tn "%TASK_NAME%" /tr "\"%SCRIPT_PATH%\"" /sc daily /st 06:00 /f

if errorlevel 1 (
    echo No se pudo crear la tarea programada. Intenta abrir este .bat
    echo con clic derecho -^> "Ejecutar como administrador".
) else (
    echo.
    echo Tarea programada creada: correra windows_auditoria.bat todos los dias a las 06:00.
    echo Puedes verla en el Programador de tareas de Windows ^(taskschd.msc^).
    echo Para borrarla mas adelante: schtasks /delete /tn "%TASK_NAME%" /f
)
pause
