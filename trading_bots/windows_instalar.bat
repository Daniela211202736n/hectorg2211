@echo off
REM Instala todo lo necesario para correr las granjas en Windows.
REM Doble clic sobre este archivo, o desde cmd: windows_instalar.bat

setlocal
cd /d %~dp0

where python >nul 2>nul
if errorlevel 1 (
    echo No se encontro "python" en el PATH de Windows.
    echo Instala Python 3.10 o superior desde https://www.python.org/downloads/
    echo IMPORTANTE: durante la instalacion, marca la casilla "Add python.exe to PATH".
    pause
    exit /b 1
)

echo Creando entorno virtual en trading_bots\venv ...
python -m venv venv
if errorlevel 1 (
    echo No se pudo crear el entorno virtual. Revisa el mensaje de error de arriba.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo ===============================================
echo  Instalacion completa.
echo  Siguiente paso: corre windows_backtest.bat para
echo  probar las estrategias con datos reales antes
echo  de dejarlas corriendo con dinero simulado.
echo ===============================================
pause
