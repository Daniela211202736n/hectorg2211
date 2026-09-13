# Guía para correr las granjas en Windows (cmd)

Esto reemplaza las instrucciones de servidor Linux (`screen`/`cron`) por
equivalentes de Windows. Todo se corre desde la carpeta `trading_bots\` de
este repo, en tu computador.

## 0. Requisito: Python instalado

Si no tienes Python:
1. Descárgalo de https://www.python.org/downloads/ (versión 3.10 o superior).
2. Durante la instalación, **marca la casilla "Add python.exe to PATH"**
   (si no la marcas, nada de esto va a funcionar).
3. Verifica abriendo `cmd` y escribiendo `python --version`.

## 1. Descarga el código

Si tienes `git` instalado, en `cmd`:
```
git clone -b claude/dreamy-lovelace-83985w https://github.com/Daniela211202736n/hectorg2211.git
cd hectorg2211\trading_bots
```
Si no tienes `git`, descarga el `.zip` que te envié en el chat, descomprímelo,
y abre `cmd` dentro de la carpeta `trading_bots` (escribe `cd ` y arrastra la
carpeta a la ventana de cmd, luego Enter).

## 2. Doble clic (o correr desde cmd) en este orden

| Archivo | Qué hace | Cuándo correrlo |
|---|---|---|
| `windows_instalar.bat` | Crea el entorno de Python e instala las librerías necesarias | Una sola vez al principio |
| `windows_backtest.bat` | Descarga historial REAL de Binance/Yahoo Finance y prueba las 3 estrategias contra él | Antes de dejar nada corriendo, y cada vez que quieras revalidar |
| `windows_iniciar_bots.bat` | Abre 4 ventanas de cmd, una por cada bot, corriendo con dinero simulado (o señales, en el caso de la Mini-Granja real) | Cuando decidas dejarlos corriendo |
| `windows_detener_bots.bat` | Cierra las 4 ventanas de los bots | Cuando quieras pausar todo |
| `windows_auditoria.bat` | Corre la auditoría diaria manualmente | Cuando quieras revisar el estado sin esperar a la tarea programada |
| `windows_programar_auditoria.bat` | Crea una tarea de Windows para que la auditoría corra sola todos los días a las 06:00 | Una sola vez |

Puedes correrlos con doble clic desde el explorador de archivos, o desde
`cmd` escribiendo su nombre (ej: `windows_instalar.bat`).

## 3. Qué vas a ver

- `windows_backtest.bat` imprime en la misma ventana los resultados de los
  3 backtests (win-rate, profit factor, drawdown, retorno). Esa es la
  respuesta real a "¿sirve o no?" — con datos de mercado reales.
- `windows_iniciar_bots.bat` abre 4 ventanas tituladas "Mini-Granja REAL",
  "Paper Mini-Granja $140.81", "Paper Granja Global $200" y "Scalping
  BTC/ETH". Cada una imprime su propio log en vivo. **Esta PC tiene que
  quedar encendida** (revisa que no se suspenda por inactividad ni al
  cerrar la tapa, si es laptop) para que sigan corriendo.
- Cada bot también guarda su historial en su propia carpeta dentro de
  `trading_bots\` (por ejemplo `scalping_data\trades.csv`,
  `paper_trading_minigranja_data\trades.csv`) — ahí puedes abrir el CSV en
  Excel para revisar operación por operación.
- `auditor_data\reporte_YYYY-MM-DD.md` es el reporte diario del auditor.

## 4. Diferencias frente a correrlo en un servidor Linux

- No se usa `screen`: cada bot vive en su propia ventana de cmd en vez de
  una sesión de terminal remota. Si cierras la ventana, ese bot se detiene
  (su estado queda guardado, no se pierde el historial).
- No se usa `cron`: se usa el **Programador de tareas de Windows**
  (`windows_programar_auditoria.bat` lo configura automáticamente).
- Si más adelante quieres que esto corra 24/7 sin depender de que tu PC
  esté prendida, la alternativa es volver a un servidor (como el
  `165.227.165.146` que ya tenías) o un VPS barato — dímelo y adapto de
  vuelta las instrucciones de `screen`/`cron` que ya están en
  `ESTRATEGIA_PROFESIONAL.md`.
