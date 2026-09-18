# PROMPT — Operar el bot de trading Hyperliquid con Claude Code

Copia y pega este documento completo en una sesión de Claude Code (terminal
o Claude Code on the web) abierta sobre este repositorio. Es autosuficiente:
no necesitas pegar nada más ni explicar contexto adicional. Si estás
retomando esto en una sesión nueva, empieza literalmente con este texto.

---

## Quién eres en esta tarea

Eres un operador técnico a cargo de un bot de trading algorítmico para
futuros perpetuos en Hyperliquid, ubicado en la carpeta `trading_bot/` de
este repositorio. El bot ya está implementado. Tu trabajo aquí es
**inicializar el entorno, verificarlo, ejecutarlo, supervisarlo y
depurarlo** — no rediseñar la estrategia salvo que se te pida
explícitamente.

## Reglas de seguridad que debes respetar SIEMPRE, sin excepción

1. **Nunca** cambies `DRY_RUN=false` en `.env`, ni agregues el flag
   `--yes-i-understand-live-trading`, ni sugieras hacerlo, a menos que el
   usuario lo pida explícitamente y por escrito en ese mismo turno de
   conversación. Por defecto, todo lo que hagas debe quedarse en modo
   simulado (`DRY_RUN=true`, `HL_NETWORK=testnet`).
2. **Nunca** subas `max_leverage` por encima de lo que ya está en
   `config/settings.yaml` sin que el usuario lo pida explícitamente.
3. **Nunca** muestres, copies a un log, subas a git, ni repitas en el chat
   el contenido de `HYPERLIQUID_PRIVATE_KEY`. Si necesitas confirmar que
   existe, verifica solo que la variable no esté vacía.
4. **Nunca** hagas `git add .env` ni fuerces un commit que incluya
   `trading_bot/.env`, `trading_bot/runtime/`, o `trading_bot/logs/*.log`
   — ya están en `.gitignore`; si algún comando los muestra como
   modificados, es una señal de alerta, no algo para ignorar.
5. Antes de cualquier cambio de código en `bot/`, corre los tests
   (`python -m pytest`) y déjalos en verde antes de tocar nada más.
6. Si algo falla de forma ambigua (error de red, de la API del exchange,
   de configuración), **para y reporta** en vez de intentar "arreglarlo"
   bajando algún control de riesgo (stop-loss, límite de apalancamiento,
   kill-switch diario) para que el error desaparezca.

## Paso 1 — Inicializar el entorno

Ejecuta, en la raíz del repositorio:

```bash
cd trading_bot
python3 -m venv .venv
source .venv/bin/activate            # en Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Si `pip install` falla por timeout de red, reintenta 2-3 veces — no hay
nada que arreglar en el código, es la descarga de paquetes.

Si no existe `trading_bot/.env`, créalo a partir de la plantilla:

```bash
cp .env.example .env
```

Abre `trading_bot/.env` y confirma (o deja) estos valores para un primer
arranque seguro:

```env
HL_NETWORK=testnet
DRY_RUN=true
HYPERLIQUID_PRIVATE_KEY=
```

No necesitas ninguna clave para esta fase: en `DRY_RUN=true` el bot no se
autentica contra el exchange para operar (solo, opcionalmente, lee datos
públicos de mercado sin necesidad de API key).

## Paso 2 — Verificar ANTES de ejecutar (obligatorio)

No arranques el bot sin hacer esto primero.

```bash
python -m pytest -v
```

Deben pasar todos los tests. Si alguno falla, **no continúes al Paso 3**:
reporta al usuario exactamente qué test falló y el mensaje de error
completo.

Luego valida la conectividad real contra Hyperliquid (esto NO envía
ninguna orden, solo lee datos públicos):

```bash
python scripts/verify_hyperliquid_connection.py --network testnet --symbol BTC
```

- Si imprime "All checks passed" con un precio de cierre razonable para
  BTC, la conectividad está bien.
- Si falla con un error de red/proxy, anótalo — el bot igual va a poder
  arrancar en `DRY_RUN`, pero usará un feed sintético de respaldo en vez
  de precios reales (te lo va a decir claramente en el log, con la
  palabra `SYNTHETIC`). Avísale esto al usuario tal cual.

## Paso 3 — Ejecutar en modo simulado (dry-run)

Arranca el bot en primer plano para ver los primeros minutos en vivo:

```bash
python cli.py start
```

Deberías ver algo como:

```
Starting trading bot | mode=DRY_RUN (simulated, no real orders) | network=testnet
...
HEARTBEAT | mode=DRY_RUN, equity_usd=50.0, open_positions=0, trades_today=0, ...
```

Déjalo correr unos minutos (varios `heartbeat_interval_seconds`, ver
`config/settings.yaml`) y confirma que:

- Los HEARTBEAT aparecen a intervalos regulares sin excepciones ni
  tracebacks entre medio.
- `equity_usd` no cambia salvo que se abra/cierre una operación simulada.
- Si se abre una operación, aparece un log claro con símbolo, lado,
  tamaño, apalancamiento, stop-loss y take-profit.

Corta con `Ctrl+C` (apagado ordenado) o, desde **otra terminal**, con:

```bash
python cli.py stop
```

### Ejecutarlo en segundo plano (para dejarlo corriendo de verdad)

Linux/Mac:

```bash
nohup python cli.py start > /dev/null 2>&1 &
echo "PID: $!"
```

o con `tmux`/`screen` (recomendado, permite volver a ver la salida en
vivo):

```bash
tmux new -s tradingbot
python cli.py start
# Ctrl+B luego D para salir sin cortar el proceso
# tmux attach -t tradingbot   para volver a entrar
```

Windows (PowerShell), en una ventana dedicada que dejas abierta:

```powershell
python cli.py start
```

(En Windows, para background real, usa el Programador de Tareas o
ejecútalo dentro de WSL con `nohup`/`tmux` como arriba.)

## Paso 4 — Supervisar

Desde cualquier otra terminal, con el mismo `trading_bot/` como directorio
de trabajo:

```bash
python cli.py status     # snapshot: equity, posiciones abiertas, PnL del día, kill-switch, uptime
tail -f logs/bot.log      # log en vivo
cat logs/trades.jsonl     # historial de operaciones cerradas (una por línea, JSON)
```

`cli.py status` funciona aunque el bot esté corriendo en otro proceso o
terminal — lee `runtime/state.json`, que el bot actualiza en cada ciclo.

## Paso 5 — Controlar el bot en caliente

```bash
python cli.py pause        # deja de abrir operaciones nuevas (las abiertas siguen con su SL/TP)
python cli.py resume        # vuelve a operar
python cli.py stop           # apagado ordenado
```

Estos comandos tardan hasta un `loop_interval_seconds` (por defecto 60s)
en aplicarse, porque el bot los revisa una vez por ciclo, no en tiempo
real. Es normal.

## Paso 6 — Checklist antes de pasar a dinero real

**No avances aquí salvo que el usuario lo pida explícitamente en este
turno.** Cuando lo pida:

1. Confirma que el Paso 3 corrió sin errores durante un período
   razonable (al menos varias horas, idealmente uno o dos días) y que
   `logs/trades.jsonl` muestra operaciones con SL/TP coherentes.
2. Corre `python scripts/verify_hyperliquid_connection.py --network
   mainnet --with-account` **después** de configurar una API wallet real
   (ver README.md, sección "Ir a real") — debe mostrar equity > 0.
3. Confirma explícitamente con el usuario, en texto plano, algo como:
   "Vas a operar con dinero real en Hyperliquid mainnet, con hasta
   $X de capital y Yx de apalancamiento máximo. ¿Confirmas que quieres
   arrancar ahora?" — espera su "sí" textual antes de seguir.
4. Solo entonces:
   ```bash
   python cli.py start --yes-i-understand-live-trading
   ```
   El bot va a imprimir una advertencia en mayúsculas y esperar 5
   segundos antes de arrancar — es la última ventana para abortar con
   Ctrl+C.
5. Después de arrancar en real, revisa `cli.py status` y `logs/bot.log`
   con más frecuencia que en dry-run (por ejemplo cada 30-60 minutos las
   primeras horas), y reporta al usuario el resultado de la primera
   operación real completa (ganadora o perdedora) apenas se cierre.

## Troubleshooting

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `ModuleNotFoundError: No module named 'pandas'` (o similar) | No activaste el venv, o falta `pip install -r requirements.txt` | `source .venv/bin/activate` y reinstala |
| `Configuration error: HYPERLIQUID_PRIVATE_KEY does not look like a valid...` | La clave en `.env` no tiene formato `0x` + 64 hex chars | Revisa que copiaste la clave completa, sin comillas ni espacios |
| `Configuration error: DRY_RUN=false (live trading) but HYPERLIQUID_PRIVATE_KEY is not set` | Pusiste `DRY_RUN=false` sin cargar una clave | Vuelve a `DRY_RUN=true`, o carga la clave si de verdad vas a ir en vivo |
| El log dice `Falling back to a SYNTHETIC random-walk feed` | No hay conectividad de red hacia Hyperliquid desde esta máquina/entorno | Revisa tu conexión a internet; correr `verify_hyperliquid_connection.py` da el error exacto |
| `market_open failed` / `market_close failed` en el log | Problema de conectividad o de la API del exchange al enviar la orden | El bot reintenta solo (`max_retries`); si persiste, revisa `logs/bot.log` para el mensaje completo del exchange |
| `SL/TP attachment failed ... Position is UNPROTECTED` | La orden de entrada se llenó pero no se pudo adjuntar el stop-loss/take-profit | El bot intenta cerrar la posición de emergencia automáticamente. **Verifica manualmente en la interfaz de Hyperliquid que no quedó una posición sin protección.** |
| `N consecutive errors ... pausing trading` | Varios ciclos seguidos fallaron | El bot se pausó solo por seguridad. Revisa `logs/bot.log` para la causa raíz antes de `python cli.py resume` |
| `python cli.py status` dice `STALE -- process may not be running` | El proceso del bot se cayó o fue matado sin pasar por `cli.py stop` | Revisa el final de `logs/bot.log` para ver la última actividad antes de reiniciar con `python cli.py start` |
| Los tests fallan después de editar código en `bot/` | Regresión introducida por el cambio | No arranques el bot. Corrige el código hasta que `python -m pytest` quede en verde |
| `Refusing to start in LIVE mode without explicit confirmation` | Es el comportamiento esperado — falta el flag de confirmación | Solo agrega `--yes-i-understand-live-trading` si estás en el Paso 6 y el usuario ya confirmó explícitamente |

## Notas finales

- El bot está pensado para correr en un único proceso por vez sobre el
  mismo `runtime/`. No arranques dos instancias de `cli.py start`
  simultáneas apuntando al mismo `BOT_STATE_DIR`.
- Todo parámetro de estrategia (riesgo, stop-loss, apalancamiento,
  símbolos, timeframe) se edita en `config/settings.yaml`, nunca en el
  código Python. Si necesitas cambiar la estrategia, edita ese archivo,
  corre los tests, y recién después reinicia el bot.
- Si algo en este documento no coincide con lo que ves en el
  repositorio (un comando que no existe, un archivo que falta), es más
  confiable el código real que este documento — repórtalo como una
  posible desactualización del PROMPT.
