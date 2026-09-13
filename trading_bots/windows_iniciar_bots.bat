@echo off
REM Abre 4 ventanas de cmd, una por cada bot, corriendo con dinero simulado
REM (la Mini-Granja real solo detecta senales, no mueve fondos por si sola).
REM Cierra la ventana correspondiente (o Ctrl+C dentro de ella) para detener
REM ese bot en particular; los demas siguen corriendo.

setlocal
cd /d %~dp0

if not exist venv\Scripts\python.exe (
    echo No se encontro el entorno virtual. Corre primero windows_instalar.bat
    pause
    exit /b 1
)

start "Mini-Granja REAL (solo senales)" cmd /k "cd /d %~dp0 && venv\Scripts\python.exe bot_produccion_minigranja.py"
start "Paper Mini-Granja $140.81" cmd /k "cd /d %~dp0 && venv\Scripts\python.exe paper_trading_minigranja.py"
start "Paper Granja Global $200" cmd /k "cd /d %~dp0 && venv\Scripts\python.exe paper_trading_granja.py"
start "Scalping BTC/ETH" cmd /k "cd /d %~dp0 && venv\Scripts\python.exe scalping_bot.py"

echo.
echo Se abrieron 4 ventanas nuevas, una por cada bot.
echo IMPORTANTE: esta PC debe quedar encendida y sin suspenderse para que
echo los bots sigan corriendo (revisa la configuracion de energia de Windows
echo si es una laptop: que no se suspenda al cerrar la tapa ni por inactividad).
pause
