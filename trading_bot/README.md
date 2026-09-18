# Trading Bot — Hyperliquid Perpetuos (autónomo, capital pequeño)

Bot de trading algorítmico para futuros perpetuos en [Hyperliquid](https://hyperliquid.xyz),
pensado para arrancar con capital pequeño (por defecto $50) sin liquidarse
en la primera vela mala. Es un proyecto **independiente** dentro de este
repositorio — no comparte código ni dependencias con `jarvis.py`.

> **Antes de leer nada más: esto no es dinero gratis.** Un bot de reglas
> fijas (RSI + MACD + Bandas de Bollinger) con $50 y apalancamiento puede
> perder el capital igual de rápido que ganarlo. Todo este proyecto está
> diseñado para que sea **difícil perderlo todo de golpe**, no para
> garantizar ganancias. Lee la sección "Riesgos" al final.

## Estructura del proyecto

```
trading_bot/
├── bot/
│   ├── config.py                  # Configuración: .env (secretos) + settings.yaml (estrategia)
│   ├── logging_setup.py           # Logging consola+archivo, heartbeat, ledger de trades (JSONL)
│   ├── exchange/
│   │   ├── base.py                # Interfaz ExchangeClient (contrato común dry-run / real)
│   │   ├── dry_run.py             # Cliente simulado: sin red, sin dinero real, con fees/SL/TP
│   │   ├── hyperliquid_client.py  # Cliente real (hyperliquid-python-sdk) + datos públicos
│   │   └── errors.py
│   ├── analysis/
│   │   ├── indicators.py          # RSI, MACD, Bandas de Bollinger (pandas puro, sin TA-Lib)
│   │   └── signals.py             # Lógica de señal LONG/SHORT/NONE (reglas, no ML)
│   ├── risk/
│   │   ├── position_sizing.py     # Tamaño de posición + apalancamiento a partir del % de riesgo
│   │   └── risk_manager.py        # Kill-switch por pérdida diaria, límite de posiciones simultáneas
│   ├── core/
│   │   ├── engine.py              # Loop principal: fetch -> señal -> sizing -> orden -> log
│   │   ├── control.py             # Canal de pausa/resume/stop (archivo, multiplataforma)
│   │   └── state.py               # Persistencia de estado para `cli.py status`
│   └── testing/
│       └── synthetic_feed.py      # Feed sintético de respaldo si no hay red (nunca en LIVE)
├── cli.py                         # Punto de entrada: start / status / pause / resume / stop
├── config/settings.yaml           # Parámetros de estrategia/riesgo (versionable, sin secretos)
├── scripts/verify_hyperliquid_connection.py  # Chequeo de conectividad antes de ir en vivo
├── tests/                         # 50 tests (pytest) — ver sección Tests
├── .env.example                   # Plantilla de variables de entorno/secretos
└── requirements.txt
```

## Instalación

```bash
cd trading_bot
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edita `.env` (nunca lo subas a git, ya está en `.gitignore`):

```env
HL_NETWORK=testnet
DRY_RUN=true
HYPERLIQUID_PRIVATE_KEY=
```

Con `DRY_RUN=true` (el valor por defecto) el bot **nunca envía órdenes
reales**, sin importar qué más digas en la configuración.

## Configuración de estrategia (`config/settings.yaml`)

Todo lo que se puede ajustar sin tocar código vive ahí, comentado línea por
línea: capital inicial, % de riesgo por operación, apalancamiento máximo,
stop-loss/take-profit, símbolos, timeframe, indicadores, intervalos del
loop. Los valores por defecto son deliberadamente conservadores para una
cuenta de $50:

| Parámetro | Valor por defecto | Qué significa |
|---|---|---|
| `risk_per_trade_pct` | 2.0% | Con $50, arriesga $1 por operación |
| `max_leverage` | 3x | Techo duro (no configurable por encima de 20x, ver más abajo) |
| `liquidation_safety_factor` | 0.5 | El stop-loss debe activarse a la mitad (o menos) de la distancia estimada de liquidación |
| `stop_loss_pct` / `risk_reward_ratio` | 1.5% / 1.5 | Take-profit = 2.25% |
| `max_daily_loss_pct` | 6% | Kill-switch: para de abrir operaciones el resto del día UTC |
| `max_concurrent_positions` | 1 | Una operación a la vez |

`bot/config.py` impone además **techos absolutos** que ningún archivo
YAML puede saltarse (`ABSOLUTE_MAX_LEVERAGE=20`,
`ABSOLUTE_MAX_RISK_PER_TRADE_PCT=10%`, etc.) — protección contra un typo
en la configuración, no contra ti mismo si decides subirlos a propósito.

## Cómo funciona el sizing (para que no sea una caja negra)

1. `risk_amount = equity × risk_per_trade_pct`
2. `notional_objetivo = risk_amount / stop_loss_pct` (el tamaño que hace que
   el stop-loss, si se activa, pierda exactamente `risk_amount`)
3. Se calcula un **apalancamiento máximo seguro** a partir de
   `liquidation_safety_factor / stop_loss_pct` — así el stop siempre se
   activa mucho antes de la liquidación estimada.
4. Si el capital disponible no alcanza para el `notional_objetivo` al
   apalancamiento máximo permitido, el bot **reduce el tamaño de la
   operación** (arriesga menos de lo planeado) en vez de saltarse
   cualquiera de los dos límites anteriores.
5. Si el resultado queda por debajo de `min_order_notional_usd`, la
   operación se rechaza en vez de forzarla.

Este algoritmo está cubierto por `tests/test_position_sizing.py`,
incluyendo una prueba que recorre una grilla de combinaciones capital ×
stop × riesgo verificando que el margen usado **nunca** exceda el
capital disponible y que el apalancamiento **nunca** exceda el límite de
seguridad.

## Comandos

```bash
python cli.py start                                   # arranca en foreground (Ctrl+C para cortar)
python cli.py start --yes-i-understand-live-trading    # requerido además si DRY_RUN=false
python cli.py status                                   # snapshot desde otra terminal
python cli.py pause                                     # deja de abrir operaciones nuevas
python cli.py resume
python cli.py stop                                       # apagado ordenado
```

`pause`/`resume`/`stop` se comunican con el proceso en ejecución a través
de un archivo de control en `runtime/` (no señales Unix, para que
funcione igual en Windows) — tardan hasta un `loop_interval_seconds` en
aplicarse. Para dejarlo corriendo en segundo plano usa `nohup`, `tmux`,
`screen`, o un servicio del sistema — ver `PROMPT.md`.

## Dry-run: ¿de dónde salen los precios?

En `DRY_RUN=true`, el bot primero intenta usar datos **públicos reales**
de Hyperliquid (no requiere API key, son endpoints de solo lectura). Si no
hay red disponible, cae automáticamente a un feed sintético (random walk)
y lo deja bien claro en el log — nunca fallará por falta de internet, pero
tampoco te va a mentir diciendo que son precios reales cuando no lo son.

## Ir a real (mainnet, dinero real)

1. Corre primero en **testnet** un tiempo (`HL_NETWORK=testnet`, cuentas
   de prueba con fondos de un faucet, cero riesgo real) y revisa
   `logs/trades.jsonl`.
2. `python scripts/verify_hyperliquid_connection.py --network mainnet --with-account`
   — valida conectividad y que tu cuenta tenga saldo, sin enviar ninguna
   orden.
3. En `.env`: `HL_NETWORK=mainnet`, `DRY_RUN=false`,
   `HYPERLIQUID_PRIVATE_KEY=` con la clave de una **API wallet** generada
   desde Hyperliquid (Settings → API) — no la clave de tu wallet
   principal — fondeada solo con lo que estás dispuesto a arriesgar.
4. `python cli.py start --yes-i-understand-live-trading`. El bot además
   espera 5 segundos imprimiendo una advertencia antes de arrancar, para
   poder abortar con Ctrl+C.

## Tests

```bash
pip install -r requirements.txt   # incluye pytest
python -m pytest -v
```

50 tests: indicadores (RSI/MACD/Bollinger vs. fórmulas de referencia),
lógica de señales (con valores de indicador controlados a mano), sizing
de posición, kill-switch/límite de concurrencia, validación de
configuración, simulación de fills/SL/TP en `DryRunExchangeClient`, y dos
ciclos completos de motor (abrir → tocar stop-loss → registrar en el
ledger; pausa bloqueando entradas nuevas). El parseo de velas de
`hyperliquid_client.py` está además verificado contra el **docstring real
del SDK instalado** (`Info.candles_snapshot`), no contra una suposición.

## Logging

- `logs/bot.log`: log rotativo (5MB × 5 backups), consola + archivo, mismo
  formato en ambos.
- `logs/trades.jsonl`: una línea JSON por operación cerrada (timestamp,
  símbolo, lado, entrada, salida, tamaño, apalancamiento, notional,
  margen, comisiones, PnL realizado, equity resultante, motivo de
  cierre).
- Heartbeat cada `heartbeat_interval_seconds` con el estado completo
  aunque no haya operaciones.

**Limitación conocida:** en modo LIVE, si el bot detecta que una posición
se cerró (SL/TP ejecutado en el exchange) sin haber implementado todavía
una consulta al histórico de fills, el PnL se infiere por la diferencia
de equity entre polls (preciso), pero `exit_price`/`fees_paid_usd` quedan
en 0 y `exit_reason="detected_closed_approximate"` en el ledger. En
`DRY_RUN` esos campos sí son exactos (la simulación los conoce). Si
necesitas el precio de cierre exacto en LIVE, se puede extender
`HyperliquidClient` con una consulta a fills por rango de tiempo.

## Riesgos (léelo en serio)

- **$50 con apalancamiento es capital de aprendizaje, no una inversión.**
  Las comisiones y el *funding rate* de un perpetuo se comen una cuenta
  chica más rápido de lo que parece.
- El stop-loss aquí es una **orden en el exchange** (`trigger` order), no
  una promesa: en gaps de precio muy violentos puede ejecutarse peor que
  el precio configurado (slippage). El `liquidation_safety_factor` existe
  justamente para dejar margen ante eso.
- Ninguna combinación de RSI/MACD/Bollinger predice el mercado. La
  estrategia por defecto es intencionalmente conservadora (exige
  confirmación triple) para operar poco y mal, no mucho y peor — pero
  sigue siendo un conjunto de reglas simples, no una ventaja estadística
  probada.
- Revisa las leyes/impuestos sobre trading de derivados en tu
  jurisdicción; esto es responsabilidad tuya, no del bot.
