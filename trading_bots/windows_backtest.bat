@echo off
REM Corre los 3 backtests con datos historicos REALES (Binance/Yahoo Finance).
REM Requiere haber corrido windows_instalar.bat antes, y que esta PC tenga
REM salida normal a Internet (no bloqueada como el sandbox de desarrollo).

setlocal
cd /d %~dp0

if not exist venv\Scripts\python.exe (
    echo No se encontro el entorno virtual. Corre primero windows_instalar.bat
    pause
    exit /b 1
)

echo ================================================================
echo  Backtest 1/3: Mini-Granja (BTC/ETH/XRP/AVAX/LINK, 1h, 180 dias)
echo ================================================================
venv\Scripts\python.exe backtest_runner.py --universo minigranja --dias 180

echo.
echo ================================================================
echo  Backtest 2/3: Granja Global (29 activos, diario, 365 dias)
echo ================================================================
venv\Scripts\python.exe backtest_runner.py --universo granja --dias 365

echo.
echo ================================================================
echo  Backtest 3/3: Scalping (BTC/ETH, 5 minutos, 30 dias)
echo ================================================================
venv\Scripts\python.exe backtest_runner.py --universo scalping --dias 30

echo.
echo ================================================================
echo  Listo. Estos numeros (win-rate, profit factor, drawdown, retorno)
echo  son la respuesta real a "si sirve o no" - no estan inventados.
echo  El detalle de cada operacion quedo en la carpeta backtest_data\
echo ================================================================
pause
