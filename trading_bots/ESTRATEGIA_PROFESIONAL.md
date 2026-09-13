# Estrategia profesional aplicada a las 3 granjas + auditor diario

Este documento resume: (1) qué prácticas de trading sistemático profesional
se aplicaron y por qué, (2) un modelo honesto de cuánto podría generar una
estrategia según su capital — **con las advertencias que corresponden**, (3)
cómo desplegar y correr todo en el servidor real, y (4) las limitaciones de
lo que se hizo en esta sesión de trabajo.

## 0. Limitación que hay que decir de frente

Este código se escribió y se probó en un entorno de desarrollo aislado cuya
política de red **bloquea explícitamente** `api.binance.com` y
`query1.finance.yahoo.com` (confirmado con un 403 de política, no un error
técnico). Por lo tanto:

- **No pude descargar datos históricos ni en vivo reales** para correr un
  backtest o una simulación con precios de mercado de verdad.
- Todo lo probado aquí (`demo_sintetico.py`) usa precios **sintéticos**
  generados con semilla fija, únicamente para verificar que la mecánica del
  código (apertura, cierre por stop/take real, trailing stop, circuit
  breaker, métricas) funciona como se diseñó — **no para estimar
  rentabilidad**.
- `backtest_runner.py` sí descarga datos reales y corre la misma lógica
  contra ellos, pero **tiene que ejecutarse en el servidor** (`165.227.165.146`)
  u otro entorno con salida a Internet real, no en este sandbox.

Cualquier cifra de "cuánto generaría esto en dólares" que no venga de
`backtest_runner.py` corriendo contra datos reales sería una cifra
inventada. Prefiero decir esto claramente a maquillar un número.

## 1. Prácticas profesionales aplicadas (y por qué)

Implementadas en `risk_engine.py` / `posiciones.py` y usadas por las 3
granjas:

| Práctica | Qué hace | Por qué la usan los sistemas rentables |
|---|---|---|
| Riesgo fijo por operación (1% granjas swing, 0.5% scalping) | Nunca se arriesga más de ese % del capital en una operación, sin importar cuán "segura" parezca la señal | Es la regla de supervivencia nº1: con 1% de riesgo por operación hacen falta ~20 pérdidas SEGUIDAS para perder el 20% del capital |
| Stop-loss/take-profit por ATR (volatilidad real) | El stop se aleja o acerca según la volatilidad reciente del activo, no un % fijo arbitrario | Un stop del 1% es enorme en un activo tranquilo y ruido puro en uno volátil |
| Ratio riesgo:beneficio ≥ 1:2 (1:1.5 en scalping) | El objetivo de ganancia es 2x (o 1.5x) la distancia al stop | Con R:R 1:2 basta acertar >33% de las veces para tener expectativa positiva |
| Filtro de tendencia (SMA50/EMA50) | Solo se opera a favor de la tendencia de fondo | Operar en contra de la tendencia dominante es la forma más común de perder con un simple cruce de medias |
| Trailing stop | El stop sube una vez que la operación avanza a favor | Deja correr las ganancias en vez de cerrar siempre en el primer objetivo |
| Circuit breaker de pérdida diaria + drawdown acumulado | Bloquea nuevas operaciones si el día pierde demasiado o el drawdown acumulado supera el límite | Una expectativa positiva por operación igual puede arruinar la cuenta sin un límite agregado |
| Límite de exposición total (60-80% del capital) | Nunca se compromete el 100% del capital en posiciones simultáneas | Diversificación / control de concentración |
| Filtro de "edge sobre costos" (solo scalping) | Descarta señales cuyo objetivo de ganancia no supera 3x la comisión de ida y vuelta | En timeframes cortos, la comisión es la principal causa de que una señal "técnicamente correcta" sea, en la práctica, una pérdida |
| Límite de operaciones/día + cooldown tras stop (solo scalping) | Tope de operaciones por símbolo por día y pausa tras una pérdida | El sobre-trading y el "revenge trading" son las causas nº1 de pérdida en scalping discrecional/algorítmico amateur |
| Métricas reales desde la bitácora (win-rate, profit factor, expectancy, max drawdown, Sharpe aproximado) | Se calculan siempre sobre operaciones YA CERRADAS y registradas en CSV | Mirar solo "cuánto subió el capital" esconde sesgos y rachas de suerte |

Ninguna de estas prácticas garantiza rentabilidad. Son condiciones
**necesarias, no suficientes**: convierten una idea de señal en un sistema
donde una racha de pérdidas razonable no destruye la cuenta, y donde el
resultado se puede medir con honestidad — no convierten automáticamente una
mala señal en una buena.

## 2. Modelo de capital vs. rentabilidad (ilustrativo, NO una promesa)

La única forma honesta de responder "¿cuánto generaría y en cuánto tiempo?"
es: **corre `backtest_runner.py` en el servidor con datos reales y mide la
expectancy y frecuencia de operaciones reales de cada estrategia.** Lo que
sigue es el modelo matemático que usarás para interpretar ese resultado, con
números de ejemplo (no reales) para ilustrar el método:

```
Expectancy en R = (win_rate × R:R) − (1 − win_rate)
Expectancy en USD/operación = Expectancy en R × (riesgo_pct × capital)
Ganancia esperada en N operaciones = N × Expectancy en USD/operación
```

Ejemplo **ilustrativo** (no observado, solo para mostrar el método) con
R:R=2, riesgo=1%:

| Win-rate | Expectancy en R | Con capital $140.81 (riesgo=$1.41/op) | Con capital $200 (riesgo=$2.00/op) |
|---|---|---|---|
| 40% | +0.20R | +$0.28/operación | +$0.40/operación |
| 50% | +0.50R | +$0.71/operación | +$1.00/operación |
| 60% | +0.80R | +$1.13/operación | +$1.60/operación |

Lecturas importantes de esta tabla:

1. **El capital pequeño no cambia el % de retorno, cambia el monto absoluto
   en dólares.** Con $140.81 y 1% de riesgo, cada operación arriesga ~$1.41
   — el sistema puede tener exactamente el mismo % de rentabilidad que uno
   con $10,000, pero en dólares generará ~70 veces menos por operación. Si
   la meta es "vivir de esto", el capital tiene que crecer primero (vía
   aportes o reinversión), no solo el win-rate.
2. **Por debajo de cierto capital, las comisiones dominan.** Con $140.81 y
   posiciones de $14-28 (10-20% del capital), la comisión de Binance
   (0.1%×2 = 0.2%) es $0.03-0.06 por operación — pequeña en términos
   absolutos pero no despreciable frente a una expectancy de $0.71-1.13.
   Para el scalping esto es más crítico: por eso existe el filtro de "edge
   sobre costos".
3. **La cantidad de operaciones por período importa tanto como la
   expectancy por operación.** Una estrategia swing (Minigranja, Granja
   Global) puede dar 1-5 señales por semana entre 5-29 activos; una de
   scalping puede dar varias por día. `backtest_runner.py` te dirá la
   frecuencia REAL observada en el historial — sin eso, cualquier
   proyección de "cuánto en cuánto tiempo" es una suposición sin base.

### ¿Cuánto capital hace falta para "buena rentabilidad"?

No hay una cifra universal — depende de la expectancy y frecuencia reales
(que solo el backtest con datos reales puede dar), y de qué se considere
"buena": un fondo institucional llamaría bueno un CAGR de 15-20% anual con
drawdown controlado; un trader retail suele (equivocadamente) esperar mucho
más. Como referencia de la industria, no como objetivo garantizado:

- Sistemas sistemáticos retail con gestión de riesgo disciplinada (1-2%
  riesgo/operación, R:R≥1:2, win-rate 45-55%) que se consideran "buenos" en
  la práctica suelen rondar 20-40% de retorno anual con drawdowns máximos de
  15-25% — **no 20-40% mensual**, una expectativa común y casi siempre
  producto de sobreajuste (curve-fitting) al backtest.
- Con capital de $140-200, ese rango de retorno anual (20-40%) representa
  aproximadamente $28-56/año sobre los $140.81 de la Mini-Granja y
  $40-80/año sobre los $200 de la Granja Global — montos pequeños en
  términos absolutos que solo se vuelven significativos con capital mayor o
  con reinversión sostenida durante años.
- El capital para que la gestión de riesgo funcione bien en la práctica
  (que el 1% de riesgo no sea una fracción de centavo, y que las comisiones
  no dominen) suele ubicarse a partir de ~$500-1,000 para cripto (fees más
  bajos, sin mínimos de lote) y más para acciones (algunos brokers tienen
  comisión mínima por operación, no solo %).

**Repito la advertencia:** esto es un marco de referencia de la industria,
no una proyección de lo que hará tu estrategia. Corre `backtest_runner.py`
en tu servidor y reemplaza cada supuesto con el número real observado.

## 3. Cómo correr todo en el servidor real

```bash
# En 165.227.165.146, dentro del repo:
pip install -r trading_bots/requirements.txt

# Backtest con datos reales (una vez, para tener una primera medida):
python3 trading_bots/backtest_runner.py --universo minigranja --dias 180
python3 trading_bots/backtest_runner.py --universo granja --dias 365
python3 trading_bots/backtest_runner.py --universo scalping --dias 30

# Bots en vivo con capital simulado (una screen por bot):
screen -dmS mg_real   python3 trading_bots/bot_produccion_minigranja.py
screen -dmS mg_paper  python3 trading_bots/paper_trading_minigranja.py
screen -dmS granja    python3 trading_bots/paper_trading_granja.py
screen -dmS scalping  python3 trading_bots/scalping_bot.py

# Auditor diario (cron, ejemplo a las 06:00 UTC):
0 6 * * * cd /ruta/al/repo && python3 trading_bots/auditor_granjas.py >> trading_bots/auditor_data/cron.log 2>&1
```

Cada bot escribe su propio `estado.json` (capital + posiciones), `trades.csv`
(bitácora de operaciones) y un `.log` rotado en su propia carpeta de datos —
revisa esas carpetas para ver progreso real.

## 4. Qué hace el auditor diario y qué NO hace

Ver el docstring de `auditor_granjas.py` para el detalle completo. En
resumen: corre un autotest de regresión (para detectar si una edición futura
rompió `risk_engine.py`/`posiciones.py` antes de confiar en el resultado),
valida la integridad de cada `estado.json`, calcula métricas reales desde
cada bitácora, y **reduce el riesgo automáticamente solo en bots simulados**
si el drawdown se acerca al límite — nunca en la Mini-Granja real, donde
solo deja una recomendación para que la apruebes tú.

No inventa estrategias nuevas ni te promete rentabilidad: la sección 5 de su
docstring detalla exactamente el alcance (y el límite) de su búsqueda de
parámetros.

## 5. Siguientes pasos recomendados

1. Correr `backtest_runner.py` en el servidor para los 3 universos y con al
   menos 2 ventanas de tiempo distintas (in-sample / out-of-sample), igual
   que ya hiciste con la validación Walk-Forward original de la Mini-Granja.
2. Dejar correr los bots de paper trading (incluido el nuevo de $140 y el de
   scalping) varias semanas antes de considerar ejecución con dinero real en
   cualquiera de las granjas nuevas.
3. Revisar el reporte diario del auditor (`auditor_data/reporte_*.md`) y
   tratar cualquier "RECOMENDACION" o "SUGERENCIA" como información para
   decidir, no como una acción ya tomada sobre dinero real.
4. Si se quiere ejecutar el scalping con datos de un bróker de acciones en
   vez de cripto, se necesitará una cuenta y API key de un bróker con datos
   en tiempo real (Alpaca, Interactive Brokers) — Binance se usó aquí porque
   no requiere credenciales para leer precios.
