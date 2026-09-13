@echo off
REM Cierra las 4 ventanas de los bots (equivalente a cerrarlas a mano con la X).
REM taskkill /F termina el proceso de golpe (no le da chance de "despedirse"),
REM pero no hay riesgo de perder datos: cada bot guarda su estado.json de
REM forma atomica al FINAL de cada ciclo ya completado, asi que en el peor
REM caso solo se pierde el ciclo que estaba a medias, nunca el historial.

taskkill /FI "WINDOWTITLE eq Mini-Granja REAL*" /T /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq Paper Mini-Granja*" /T /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq Paper Granja Global*" /T /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq Scalping BTC/ETH*" /T /F >nul 2>nul

echo Se enviaron senales de cierre a las 4 ventanas de los bots (si estaban abiertas).
pause
