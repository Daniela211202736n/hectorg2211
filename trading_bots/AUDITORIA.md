# Auditoría — Mini-Granja (producción) y Paper Trading (Granja Global)

Fecha: 2026-09-13
Alcance: `bot_produccion_minigranja.py` y `paper_trading_granja.py` tal como
se ejecutan en `165.227.165.146` bajo `screen`.

Este documento resume los hallazgos de la revisión y remite a las versiones
endurecidas incluidas en esta misma carpeta (`bot_produccion_minigranja.py`
y `paper_trading_granja.py`), que implementan las correcciones descritas
abajo. Los scripts originales **no ejecutan órdenes reales** (el de Binance
solo detecta y loggea señales; el de paper trading es enteramente
simulado), así que ningún hallazgo aquí implica pérdida de fondos ya
ocurrida — el objetivo es que la telemetría y la lógica de riesgo sean
confiables antes de dar el salto a ejecución real.

## 1. Hallazgo crítico: el paper trading no puede perder (sesgo de look-ahead)

`paper_trading_granja.py` original calcula la variación **ya cerrada** del
día (`(actual - anterior) / anterior`) y, si supera +1.5%, anota de
inmediato una ganancia fija de +0.8% sobre el capital total, **en el mismo
instante en que detecta la variación**. Esto no simula una operación en el
tiempo: no hay apertura ni cierre separados, ni posibilidad de que el precio
se mueva en contra después de "comprar". El resultado es una cuenta que
**estructuralmente no puede perder dinero** — cualquier estrategia se ve
rentable si se le permite conocer el resultado antes de "apostar".

Consecuencia práctica: las métricas de rentabilidad de la Granja Global no
son un indicador válido de si la lógica de señales (variación diaria >1.5%)
funcionaría con dinero real. El capital virtual solo puede subir o quedarse
igual, nunca bajar por una operación perdedora.

**Corrección aplicada:** la señal de hoy abre una posición que se liquida en
el ciclo siguiente al precio de mercado real (`cerrar_posiciones_vencidas`),
así que el PnL refleja el movimiento real de precio entre el día de entrada
y el de salida, positivo o negativo.

## 2. Gestión de riesgo ausente

- **Minigranja:** el script detecta cruces EMA7/EMA21 con RSI<70 pero no
  calcula tamaño de posición, stop-loss ni take-profit; toda la gestión de
  riesgo queda fuera del código y depende de que un humano decida a mano
  cuánto arriesgar. No hay límite de exposición simultánea entre los 5
  activos (si los 5 cruzan a la vez, no hay ninguna regla que limite
  cuánto del capital de $140.81 se comprometería).
- **Paper trading:** el original "invierte" el 100% del capital total en
  **cada uno** de los 29 activos que cruce el umbral el mismo día, sin
  ningún límite de concentración ni de exposición agregada. Si 10 activos
  cruzan el umbral el mismo día (plausible en un día de mercado fuerte,
  dado que muchos están correlacionados — índices, ETFs sectoriales,
  cripto), el capital se "reinvertiría" 10 veces esa sesión sin ningún
  control, inflando el resultado de forma irreal.

**Corrección aplicada:** ambos scripts ahora sugieren/aplican tamaño de
posición acotado (riesgo fijo por operación en Minigranja; % del capital
con tope de exposición agregada en paper trading) y el paper trading incluye
un circuit breaker de pérdida diaria (`PERDIDA_DIARIA_MAX_PCT`) que detiene
nuevas aperturas si el día ya perdió más del umbral configurado.

## 3. Variables globales y persistencia de estado

- `CAPITAL_VIRTUAL` en el original es una variable global de proceso
  modificada con `global CAPITAL_VIRTUAL` dentro del bucle. Funciona para un
  script de un solo proceso y un solo hilo, pero:
  - **No persiste.** Un reinicio de la sesión `screen`, un `reboot` del
    droplet, o un simple `kill` del proceso hace que el capital vuelva a
    $200.00 y se pierda todo el historial de "operaciones" — no hay forma
    de reconstruir el rendimiento acumulado real de la simulación.
  - No hay bitácora de operaciones (CSV/DB): todo el rastro vive solo en la
    salida de `screen`, que normalmente tiene un buffer limitado y se
    pierde al reiniciar la sesión.
  - Si en algún momento se paraleliza (por ejemplo, un segundo proceso que
    lea el mismo estado, o se migra a async/threads para paralelizar las
    descargas de yfinance), una variable global mutable sin lock es una
    condición de carrera en potencia.
- Minigranja no tiene variables de estado en absoluto: **cada ciclo es sin
  memoria**, por lo que si el cruce EMA persiste dos velas seguidas (cosa
  común), el bot **reimprime la misma señal cada hora** hasta que el cruce
  desaparece, sin ninguna forma de saber si ya se "actuó" sobre ella.

**Corrección aplicada:** ambos scripts persisten su estado en disco
(`minigranja_state.json`, `paper_trading_data/estado.json`) con escritura
atómica (`os.replace` vía `Path.replace`) para evitar corrupción si el
proceso muere a mitad de escritura, más una bitácora CSV de operaciones en
el caso de paper trading.

## 4. Robustez ante errores de API

### Binance (Minigranja)
- `requests.get(url)` **sin `timeout`**: una API lenta o un problema de red
  puede colgar el proceso indefinidamente (el bucle exterior no avanzaría
  nunca al siguiente ciclo).
- Sin reintentos ni manejo de rate-limit: Binance devuelve 429 (rate limit)
  o 418 (IP baneada temporalmente) cuando se excede el límite de peso de la
  API; el original los trata igual que cualquier otro `status_code != 200`
  y simplemente `continue`, sin backoff — en un escenario de baneo temporal
  seguiría martillando la API cada hora sin esperar el tiempo indicado en
  `Retry-After`.
- Una excepción de red (`ConnectionError`, `Timeout`, DNS) en
  `requests.get()` **no está capturada** dentro de `obtener_datos`, así que
  se propaga hasta el `try/except` genérico del bucle principal, **abortando
  el resto de los símbolos de ese ciclo** — un fallo transitorio en BTCUSDT
  deja sin monitorear también a ETHUSDT, XRPUSDT, AVAXUSDT y LINKUSDT esa
  hora.
- No valida que Binance haya devuelto suficientes velas antes de calcular
  EMA21/RSI14 (con `limit=100` no debería ocurrir, pero no hay guardas si la
  API cambia de comportamiento o recorta la respuesta).

**Corrección aplicada:** `requests.Session` con `Retry` (backoff exponencial,
respeta `Retry-After`, reintenta 429/418/5xx), `timeout` explícito,
try/except por símbolo (un fallo no tumba el ciclo), y validación de
cantidad mínima de velas.

### Yahoo Finance / yfinance (Paper trading)
- Sin `timeout` ni reintentos: `yfinance` internamente puede tardar mucho o
  fallar por rate-limit de Yahoo (frecuente cuando se golpea la API para 29
  símbolos seguidos sin pausas); el original no reintenta ni espera entre
  símbolos.
- El `try/except` genérico solo cubre la llamada a `ticker.history(...)`,
  pero no distingue entre "no hay datos" (feriado, ticker deslistado,
  `ZN=F` fuera de sesión) y un error de red — en ambos casos se pierde el
  ciclo del día para ese símbolo sin reintento.
- No hay pausa entre solicitudes a los 29 símbolos: yfinance/Yahoo son
  conocidos por limitar o bloquear temporalmente IPs que hacen ráfagas de
  solicitudes, lo que puede dejar el radar completo sin datos por horas.

**Corrección aplicada:** reintentos con backoff exponencial por símbolo
(hasta 3 intentos), validación de `DataFrame` vacío/insuficiente antes de
usarlo, y una pausa fija entre símbolos (`PAUSA_ENTRE_SYMBOLS`).

## 5. Lógica contable del paper trading

Además del sesgo de look-ahead (§1):

- **Comisión mal aplicada:** el original resta `CAPITAL_VIRTUAL * FEE_RATE`
  (comisión sobre el capital TOTAL acumulado) en vez de sobre el importe de
  la operación. A medida que el capital crece, la comisión "por operación"
  crece con él aunque el tamaño de la posición no cambie — economía
  inconsistente con cualquier bróker real, donde la comisión es un % del
  monto operado.
- **Sin bitácora por operación:** imposible reconstruir después cuántas
  operaciones se hicieron en qué símbolo, a qué precio, y cuál fue el
  resultado individual — solo se ve el capital acumulado al final de cada
  ciclo de 24h.
- **Sin límite de posiciones duplicadas:** si un símbolo sigue subiendo
  >1.5% varios días seguidos, el original lo "compra" de nuevo cada día
  sumando ganancia fija cada vez, en vez de reconocer que ya hay una
  posición abierta en ese activo.

**Corrección aplicada:** comisión calculada sobre el monto nocional de cada
operación (entrada y salida por separado), bitácora CSV
(`paper_trading_data/trades.csv`) con timestamp/símbolo/tipo/precio/monto/
PnL/capital resultante, y no se abre una posición nueva en un símbolo que ya
tiene una posición abierta.

## 6. Otras observaciones menores

- El RSI del original usa una media móvil simple (`rolling().mean()`) en vez
  del suavizado de Wilder que usan la mayoría de plataformas (TradingView,
  Binance, etc.); numéricamente da valores distintos a los que vería un
  trader comparando contra un gráfico estándar. Corregido en la versión
  endurecida (`ewm(alpha=1/14)`).
- El índice `i = -2` para evaluar el cruce EMA (última vela **cerrada**, no
  la vela en curso) es correcto y se mantuvo — evita "repintado" de la
  señal mientras la vela actual sigue formándose.
- Ninguno de los dos procesos define manejo de señales del sistema
  (`SIGTERM`/`SIGINT`); un `screen -X kill` o un reinicio del servidor no
  tiene garantía de guardar el último estado antes de morir. Corregido:
  ambos capturan la señal y persisten el estado en el `finally` del ciclo
  en curso.
- Todo el logging original es `print()` a stdout, capturado únicamente por
  el buffer de `screen`. Corregido con `logging` a archivo rotado (5 MB x 5
  backups) + consola, para tener histórico auditable independiente de la
  sesión de terminal.

## Siguientes pasos recomendados (no implementados aquí)

1. Antes de conectar Minigranja a órdenes reales de Binance: añadir gestión
   de claves de API vía variables de entorno/secret manager (nunca en el
   código), control de posiciones abiertas reales vía la API de cuenta de
   Binance (no solo señales), y pruebas en **Binance Testnet** primero.
2. Calcular métricas de desempeño reales sobre la bitácora CSV del paper
   trading (Sharpe, máximo drawdown, win-rate) una vez que el modelo ya no
   tenga sesgo de look-ahead — los resultados de la versión original no son
   utilizables para decidir si la estrategia amplia (29 activos, umbral
   1.5%) es viable.
3. Considerar mover el estado de JSON local a una base de datos ligera
   (SQLite) si se van a correlacionar señales entre Minigranja y la Granja
   Global, o si se necesita consultar el historial desde otro proceso.
