"""
Mini-Granja - Bot de monitoreo de producción (Binance, cripto).

Versión endurecida de bot_produccion_minigranja.py. Cambios principales frente
al script original (ver trading_bots/AUDITORIA.md para el detalle completo):

  * Manejo de errores por símbolo: un fallo de red/API en un activo ya NO
    aborta el resto del ciclo (el original propagaba la excepción hasta el
    `try` del bucle principal y saltaba los símbolos restantes).
  * `requests.Session` con reintentos/backoff y `timeout` explícito (el
    original podía colgarse indefinidamente y no reintentaba nada).
  * Manejo explícito de rate-limit de Binance (HTTP 429/418 y el header
    `Retry-After`).
  * RSI calculado con el suavizado de Wilder (EMA con alpha=1/período) en
    lugar de una media móvil simple, que es lo que usan la mayoría de
    plataformas y evita señales falsas por una fórmula distinta a la que el
    trader espera.
  * Validación de que hay velas suficientes antes de operar sobre el
    DataFrame (evita NaN silenciosos cuando Binance devuelve menos datos de
    los pedidos).
  * Estado (última señal emitida por símbolo) persistido en disco en vez de
    vivir solo en variables globales de proceso: sobrevive a reinicios del
    bot y evita reenviar la misma señal en cada vela sin cruce nuevo.
  * Sugerencia de tamaño de posición y stop-loss/take-profit basados en
    riesgo (%) del capital y en el rango de la vela (proxy simple de ATR),
    ya que el script original detectaba señales pero no aplicaba ninguna
    gestión de riesgo ni sizing.
  * Logging a archivo rotado + consola en lugar de solo `print`, para que la
    sesión de `screen` no sea la única fuente de verdad histórica.

Este script sigue sin enviar órdenes reales a Binance: detecta y registra
señales, igual que el original. Añadir ejecución real requiere además
gestión de claves de API, control de posiciones abiertas y control de
exposición simultánea entre los 5 activos, que no está implementado aquí a
propósito (ver recomendaciones en AUDITORIA.md).
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ==========================================
# CONFIGURACIÓN DE PRODUCCIÓN - MINI-GRANJA
# ==========================================
CAPITAL_INICIAL = float(os.environ.get("MG_CAPITAL_INICIAL", "140.81"))
SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "AVAXUSDT", "LINKUSDT"]
INTERVAL = "1h"
FAST_EMA = 7
SLOW_EMA = 21
RSI_PERIOD = 14
RSI_LIMIT = 70
KLINES_LIMIT = 100

# Gestión de riesgo (solo informativa: el bot no envía órdenes).
RIESGO_POR_OPERACION_PCT = float(os.environ.get("MG_RIESGO_PCT", "0.01"))  # 1% del capital
STOP_LOSS_ATR_MULT = 1.5
TAKE_PROFIT_ATR_MULT = 3.0

REQUEST_TIMEOUT = 10  # segundos
CICLO_SEGUNDOS = 3600

STATE_PATH = Path(os.environ.get("MG_STATE_PATH", Path(__file__).with_name("minigranja_state.json")))
LOG_PATH = Path(os.environ.get("MG_LOG_PATH", Path(__file__).with_name("minigranja.log")))

logger = logging.getLogger("minigranja")


def configurar_logging() -> None:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    consola = logging.StreamHandler(sys.stdout)
    consola.setFormatter(formatter)
    logger.addHandler(consola)

    try:
        archivo = RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        archivo.setFormatter(formatter)
        logger.addHandler(archivo)
    except OSError as exc:
        logger.warning("No se pudo abrir el archivo de log %s: %s", LOG_PATH, exc)


def crear_sesion_http() -> requests.Session:
    """Sesión con reintentos y backoff exponencial para errores transitorios
    (timeouts, 5xx y el rate-limit de Binance 429/418)."""
    sesion = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=[418, 429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        respect_retry_after_header=True,
    )
    adaptador = HTTPAdapter(max_retries=retry)
    sesion.mount("https://", adaptador)
    sesion.mount("http://", adaptador)
    return sesion


@dataclass
class EstadoBot:
    """Estado persistente entre ciclos/reinicios. Vivir solo en variables
    globales en memoria (como en el script original) implica perder todo el
    historial de señales cada vez que se reinicia el proceso o cae la sesión
    de `screen`."""

    ultima_senal: dict[str, str] = field(default_factory=dict)  # symbol -> timestamp ISO de la última señal emitida

    @classmethod
    def cargar(cls, path: Path) -> "EstadoBot":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(ultima_senal=data.get("ultima_senal", {}))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Estado corrupto o ilegible en %s (%s); se reinicia vacío.", path, exc)
            return cls()

    def guardar(self, path: Path) -> None:
        tmp_path = path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps({"ultima_senal": self.ultima_senal}, indent=2), encoding="utf-8")
            tmp_path.replace(path)  # escritura atómica, evita estado corrupto si el proceso muere a mitad
        except OSError as exc:
            logger.warning("No se pudo persistir el estado en %s: %s", path, exc)


def obtener_datos(sesion: requests.Session, symbol: str) -> Optional[pd.DataFrame]:
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": INTERVAL, "limit": KLINES_LIMIT}
    try:
        response = sesion.get(url, params=params, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.RequestException as exc:
        logger.error("Fallo de red consultando %s: %s", symbol, exc)
        return None

    if response.status_code != 200:
        logger.error("Binance respondió %s para %s: %s", response.status_code, symbol, response.text[:200])
        return None

    try:
        data = response.json()
    except ValueError as exc:
        logger.error("Respuesta no-JSON de Binance para %s: %s", symbol, exc)
        return None

    if not isinstance(data, list) or len(data) < SLOW_EMA + RSI_PERIOD:
        logger.warning("Datos insuficientes de Binance para %s (%s velas recibidas).", symbol, len(data) if isinstance(data, list) else "?")
        return None

    df = pd.DataFrame(data, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'trades',
        'taker_buy_base', 'taker_buy_quote', 'ignore'
    ])
    df['close'] = df['close'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    return df


def calcular_indicadores(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['ema_fast'] = df['close'].ewm(span=FAST_EMA, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=SLOW_EMA, adjust=False).mean()

    delta = df['close'].diff()
    ganancia = delta.clip(lower=0)
    perdida = -delta.clip(upper=0)
    # Suavizado de Wilder (equivalente a un EMA con alpha=1/periodo), el
    # estándar de facto para RSI. La media móvil simple del script original
    # produce un RSI distinto al de la mayoría de gráficos/plataformas.
    avg_gain = ganancia.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
    avg_loss = perdida.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    df['rsi'] = 100 - (100 / (1 + rs))
    df['rsi'] = df['rsi'].fillna(100)  # sin pérdidas recientes -> RSI 100 (evita división por 0)

    df['rango'] = df['high'] - df['low']  # proxy simple de volatilidad para el stop-loss
    return df


def sugerir_gestion_riesgo(capital: float, precio: float, rango_reciente: float) -> tuple[float, float, float]:
    """Devuelve (tamaño_posicion_usd, stop_loss, take_profit) sugeridos según
    riesgo fijo por operación. El script original no calculaba ningún stop ni
    tamaño de posición: solo imprimía la señal, dejando la gestión de riesgo
    enteramente fuera del código."""
    riesgo_usd = capital * RIESGO_POR_OPERACION_PCT
    distancia_stop = max(rango_reciente * STOP_LOSS_ATR_MULT, precio * 0.002)
    tamano_posicion = riesgo_usd / distancia_stop * precio if distancia_stop > 0 else 0.0
    tamano_posicion = min(tamano_posicion, capital)  # nunca sugerir más que el capital disponible
    stop_loss = precio - distancia_stop
    take_profit = precio + distancia_stop * (TAKE_PROFIT_ATR_MULT / STOP_LOSS_ATR_MULT)
    return tamano_posicion, stop_loss, take_profit


def ejecutar_monitoreo(sesion: requests.Session, estado: EstadoBot) -> None:
    logger.info("Monitoreando flota de la Mini-Granja en Binance...")
    for symbol in SYMBOLS:
        try:
            df = obtener_datos(sesion, symbol)
            if df is None:
                continue

            df = calcular_indicadores(df)

            i = -2  # última vela CERRADA; evita repintado sobre la vela en curso
            fast_curr = df['ema_fast'].iloc[i]
            slow_curr = df['ema_slow'].iloc[i]
            fast_prev = df['ema_fast'].iloc[i - 1]
            slow_prev = df['ema_slow'].iloc[i - 1]
            rsi_curr = df['rsi'].iloc[i]
            precio_actual = df['close'].iloc[i]
            rango_reciente = df['rango'].iloc[-10:].mean()
            vela_timestamp = str(df['timestamp'].iloc[i])

            hay_cruce = fast_prev <= slow_prev and fast_curr > slow_curr and rsi_curr < RSI_LIMIT

            if hay_cruce:
                if estado.ultima_senal.get(symbol) == vela_timestamp:
                    logger.info("%s: cruce ya reportado para esta vela, se omite duplicado.", symbol)
                    continue

                tamano, stop_loss, take_profit = sugerir_gestion_riesgo(
                    CAPITAL_INICIAL, precio_actual, rango_reciente
                )
                logger.info(
                    "[SENAL DETECTADA] %s alcista @ %.6f | RSI=%.1f | "
                    "tamano sugerido=%.2f USDT | stop-loss=%.6f | take-profit=%.6f",
                    symbol, precio_actual, rsi_curr, tamano, stop_loss, take_profit,
                )
                estado.ultima_senal[symbol] = vela_timestamp
            else:
                logger.info("-> %s: sin cruce activo (precio: %.6f, RSI: %.1f)", symbol, precio_actual, rsi_curr)

        except Exception:
            # Un error inesperado calculando indicadores de UN símbolo no debe
            # tumbar el ciclo completo ni matar el proceso (a diferencia del
            # original, donde una excepción aquí escapaba hasta el bucle
            # principal y abortaba los símbolos restantes de ese ciclo).
            logger.exception("Error procesando %s; se continúa con el resto de la flota.", symbol)


def main() -> None:
    configurar_logging()
    logger.info("=" * 50)
    logger.info(" BOT DE PRODUCCION - MINI-GRANJA CRIPTO EN VIVO")
    logger.info("=" * 50)

    sesion = crear_sesion_http()
    estado = EstadoBot.cargar(STATE_PATH)

    detener = {"flag": False}

    def _manejar_senal(signum, _frame):
        logger.info("Señal %s recibida, se detiene tras el ciclo actual.", signum)
        detener["flag"] = True

    signal.signal(signal.SIGINT, _manejar_senal)
    signal.signal(signal.SIGTERM, _manejar_senal)

    while not detener["flag"]:
        ciclo_inicio = time.monotonic()
        try:
            ejecutar_monitoreo(sesion, estado)
        except Exception:
            logger.exception("Error no controlado en el ciclo de monitoreo.")
        finally:
            estado.guardar(STATE_PATH)

        transcurrido = time.monotonic() - ciclo_inicio
        espera = max(0.0, CICLO_SEGUNDOS - transcurrido)
        for _ in range(int(espera // 1)):
            if detener["flag"]:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
