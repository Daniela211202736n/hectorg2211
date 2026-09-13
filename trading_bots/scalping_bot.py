"""
Scalping Bot — Binance (cripto, datos reales de mercado), capital simulado.

Bróker/fuente de datos: Binance Spot API pública (mismo endpoint que la
Mini-Granja). Se eligió Binance porque su API de mercado es real, gratuita y
no requiere cuenta ni KYC solo para leer precios — a diferencia de un bróker
de acciones (Alpaca, Interactive Brokers), que exige credenciales propias del
usuario para servir datos en tiempo real. Si más adelante se quiere ejecutar
órdenes reales, Binance también permite spot trading con comisiones bajas
(0.1% por lado) y un entorno de pruebas (Binance Testnet) para validar antes
de arriesgar capital real.

Por qué el scalping es distinto de las otras dos granjas (y por qué la
mayoría de bots de scalping amateur pierden dinero):

  * Opera en velas de 5 minutos con objetivos de ganancia pequeños — las
    comisiones (0.1% + 0.1% = 0.2% ida y vuelta en Binance) se comen una
    fracción mucho mayor de cada operación que en un swing de días. Por eso
    este bot exige que el take-profit supere las comisiones por un margen
    mínimo (`MIN_EDGE_SOBRE_COSTOS`) antes de considerar la operación viable
    — si el "edge" esperado no cubre costos con margen, NO se opera, aunque
    haya una señal técnica válida.
  * Limita operaciones por símbolo por día (`MAX_OPERACIONES_DIA_SYMBOL`):
    el sobre-trading (perseguir cada micro-movimiento) es la causa nº1 de
    que el scalping discrecional/algorítmico amateur pierda por comisiones
    incluso con una tasa de acierto razonable.
  * Cooldown tras un stop-loss (`COOLDOWN_MINUTOS`): evita "revenge trading"
    (reabrir de inmediato en el mismo activo tras una pérdida, un patrón
    estadísticamente perdedor documentado en literatura de trading
    conductual).
  * Igual que las otras granjas: filtro de tendencia, stop/take por ATR,
    riesgo fijo por operación, circuit breaker de pérdida diaria/drawdown.

Solo opera BTCUSDT y ETHUSDT: son los pares con mayor liquidez y spreads más
ajustados de Binance — el scalping en pares de baja liquidez pierde por
slippage antes que por la estrategia.
"""

from __future__ import annotations

import csv
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import pandas as pd

import bot_produccion_minigranja as mg  # reutiliza obtener_datos/crear_sesion_http
import posiciones as pos_mod
import risk_engine as rk

CAPITAL_INICIAL = float(os.environ.get("SC_CAPITAL_INICIAL", "100.00"))
SYMBOLS = ["BTCUSDT", "ETHUSDT"]
INTERVAL = "5m"
FAST_EMA, SLOW_EMA, TENDENCIA_EMA = 9, 21, 50
RSI_PERIOD, RSI_LIMIT = 14, 70
ATR_PERIODO = 14

FEE_RATE = 0.001  # 0.1% por lado en Binance spot (verificar tu nivel VIP real)
STOP_ATR_MULT = 0.8          # stops más ajustados que en swing (coherente con timeframe corto)
RATIO_RIESGO_BENEFICIO = 1.5  # en scalping se prioriza win-rate alto sobre R:R grande
RIESGO_POR_OPERACION_PCT = 0.005  # 0.5%: más conservador por el mayor número de operaciones/día
MAX_POSICION_PCT = 0.30
MAX_EXPOSICION_TOTAL_PCT = 0.60
PERDIDA_DIARIA_MAX_PCT = 0.04
DRAWDOWN_MAX_PCT = 0.15

MIN_EDGE_SOBRE_COSTOS = 3.0   # el take-profit debe superar 3x el costo de ida y vuelta
MAX_OPERACIONES_DIA_SYMBOL = 8
COOLDOWN_MINUTOS = 30

CICLO_SEGUNDOS = 60  # sondea cada minuto; solo actua sobre la ultima vela de 5m ya cerrada

DATA_DIR = Path(os.environ.get("SC_DATA_DIR", Path(__file__).with_name("scalping_data")))
ESTADO_PATH = DATA_DIR / "estado.json"
TRADES_LOG_PATH = DATA_DIR / "trades.csv"
LOG_PATH = DATA_DIR / "scalping.log"
AUDITOR_DIR = Path(__file__).with_name("auditor_data")

logger = logging.getLogger("scalping")


def configurar_logging() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
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
        logger.warning("No se pudo abrir el log %s: %s", LOG_PATH, exc)


def obtener_datos_scalping(sesion, symbol: str) -> Optional[pd.DataFrame]:
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": INTERVAL, "limit": 150}
    import requests
    try:
        response = sesion.get(url, params=params, timeout=10)
    except requests.exceptions.RequestException as exc:
        logger.error("Fallo de red consultando %s: %s", symbol, exc)
        return None
    if response.status_code != 200:
        logger.error("Binance respondio %s para %s", response.status_code, symbol)
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, list) or len(data) < TENDENCIA_EMA + RSI_PERIOD:
        return None
    df = pd.DataFrame(data, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'trades',
        'taker_buy_base', 'taker_buy_quote', 'ignore'
    ])
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    return df


def calcular_indicadores_scalping(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['ema_fast'] = df['close'].ewm(span=FAST_EMA, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=SLOW_EMA, adjust=False).mean()
    df['ema_tendencia'] = df['close'].ewm(span=TENDENCIA_EMA, adjust=False).mean()

    delta = df['close'].diff()
    ganancia = delta.clip(lower=0)
    perdida = -delta.clip(upper=0)
    avg_gain = ganancia.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
    avg_loss = perdida.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    df['rsi'] = (100 - (100 / (1 + rs))).fillna(100)

    df['atr'] = rk.calcular_atr(df, ATR_PERIODO)
    return df


def operaciones_hoy(symbol: str) -> int:
    if not TRADES_LOG_PATH.exists():
        return 0
    hoy = datetime.now(timezone.utc).date().isoformat()
    n = 0
    with TRADES_LOG_PATH.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["symbol"] == symbol and row["tipo"] == "APERTURA" and row["timestamp"].startswith(hoy):
                n += 1
    return n


def en_cooldown(symbol: str) -> bool:
    if not TRADES_LOG_PATH.exists():
        return False
    ahora = datetime.now(timezone.utc)
    with TRADES_LOG_PATH.open(newline="", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    for row in reversed(filas):
        if row["symbol"] == symbol and row["tipo"] == "CIERRE" and row["motivo"] == "STOP_LOSS":
            ts = datetime.fromisoformat(row["timestamp"])
            return (ahora - ts).total_seconds() < COOLDOWN_MINUTOS * 60
        if row["symbol"] == symbol and row["tipo"] == "CIERRE":
            return False  # el cierre mas reciente de este simbolo no fue por stop
    return False


def procesar_symbol(sesion, estado: pos_mod.EstadoCuenta, symbol: str) -> tuple[float, Optional[tuple[str, float, float]]]:
    df = obtener_datos_scalping(sesion, symbol)
    if df is None:
        return 0.0, None
    df = calcular_indicadores_scalping(df)

    i = -2
    fast_curr, slow_curr = df['ema_fast'].iloc[i], df['ema_slow'].iloc[i]
    fast_prev, slow_prev = df['ema_fast'].iloc[i - 1], df['ema_slow'].iloc[i - 1]
    rsi_curr = df['rsi'].iloc[i]
    precio_actual = float(df['close'].iloc[i])
    high, low = float(df['high'].iloc[i]), float(df['low'].iloc[i])
    atr_actual = float(df['atr'].iloc[i]) if pd.notna(df['atr'].iloc[i]) else 0.0
    tendencia_ok = precio_actual > float(df['ema_tendencia'].iloc[i])

    pnl_ciclo = 0.0
    if symbol in estado.posiciones:
        p = estado.posiciones[symbol]
        cierre = pos_mod.evaluar_cierre_por_rango(p, high, low)
        if cierre is not None:
            precio_salida, motivo = cierre
            pnl_ciclo = pos_mod.cerrar_posicion(estado, symbol, precio_salida, motivo, TRADES_LOG_PATH, FEE_RATE)
            logger.info("[CIERRE %s] %s @ %.4f -> pnl neto %+.4f USD", motivo, symbol, precio_salida, pnl_ciclo)
        else:
            nuevo_stop = rk.actualizar_trailing_stop(precio_actual, p.precio_entrada, p.stop_loss, p.distancia_riesgo, activar_en_r=0.7)
            if nuevo_stop != p.stop_loss:
                p.stop_loss = nuevo_stop
        return pnl_ciclo, None

    hay_cruce = fast_prev <= slow_prev and fast_curr > slow_curr and rsi_curr < RSI_LIMIT
    if not (hay_cruce and tendencia_ok and atr_actual > 0):
        return pnl_ciclo, None

    if operaciones_hoy(symbol) >= MAX_OPERACIONES_DIA_SYMBOL:
        logger.info("-> %s: limite diario de operaciones alcanzado, se omite señal.", symbol)
        return pnl_ciclo, None
    if en_cooldown(symbol):
        logger.info("-> %s: en cooldown tras stop-loss reciente, se omite señal.", symbol)
        return pnl_ciclo, None

    plan = rk.calcular_plan_operacion(
        capital=estado.capital, precio_entrada=precio_actual, atr=atr_actual,
        riesgo_pct=RIESGO_POR_OPERACION_PCT, stop_atr_mult=STOP_ATR_MULT,
        ratio_riesgo_beneficio=RATIO_RIESGO_BENEFICIO, tope_posicion_pct=MAX_POSICION_PCT,
    )
    costo_round_trip_pct = 2 * FEE_RATE
    ganancia_potencial_pct = (plan.take_profit - precio_actual) / precio_actual
    if plan.monto_usd <= 0 or ganancia_potencial_pct < costo_round_trip_pct * MIN_EDGE_SOBRE_COSTOS:
        logger.info(
            "-> %s: señal descartada, el take-profit (%.3f%%) no cubre costos con margen suficiente (minimo %.3f%%).",
            symbol, ganancia_potencial_pct * 100, costo_round_trip_pct * MIN_EDGE_SOBRE_COSTOS * 100,
        )
        return pnl_ciclo, None

    logger.info("-> %s: señal de scalping valida (precio=%.4f, RSI=%.1f)", symbol, precio_actual, rsi_curr)
    return pnl_ciclo, (symbol, precio_actual, atr_actual)


def ejecutar_ciclo(sesion, estado: pos_mod.EstadoCuenta) -> None:
    pnl_ciclo = 0.0
    candidatas: list[tuple[str, float, float]] = []
    for symbol in SYMBOLS:
        try:
            pnl_symbol, candidata = procesar_symbol(sesion, estado, symbol)
            pnl_ciclo += pnl_symbol
            if candidata is not None:
                candidatas.append(candidata)
        except Exception:
            logger.exception("Error procesando %s.", symbol)

    gestor = estado.gestor_riesgo(PERDIDA_DIARIA_MAX_PCT, DRAWDOWN_MAX_PCT)
    permitido, razon = gestor.permite_nuevas_operaciones(estado.capital, pnl_ciclo)
    estado.sincronizar_gestor_riesgo(gestor)

    if not permitido:
        logger.warning("Circuit breaker activo: %s.", razon)
    else:
        capital_referencia = estado.capital
        multiplicador_riesgo = pos_mod.leer_multiplicador_riesgo("scalping_bot", AUDITOR_DIR)
        for symbol, precio, atr in candidatas:
            abierto = pos_mod.abrir_posicion(
                estado, symbol, precio, atr, TRADES_LOG_PATH, FEE_RATE,
                RIESGO_POR_OPERACION_PCT * multiplicador_riesgo, STOP_ATR_MULT, RATIO_RIESGO_BENEFICIO,
                MAX_POSICION_PCT, MAX_EXPOSICION_TOTAL_PCT, capital_referencia,
            )
            if abierto:
                p = estado.posiciones[symbol]
                logger.info("[APERTURA] %s @ %.4f | monto=%.2f | stop=%.4f | take=%.4f", symbol, precio, p.monto_usd, p.stop_loss, p.take_profit)

    if pnl_ciclo != 0.0 or candidatas:
        pnls = pos_mod.leer_pnl_operaciones_cerradas(TRADES_LOG_PATH)
        metricas = rk.calcular_metricas(pnls, CAPITAL_INICIAL)
        logger.info(
            "[METRICAS] operaciones=%d win_rate=%.1f%% profit_factor=%s expectancy=%.4f USD max_dd=%.1f%% capital=$%.2f",
            metricas.num_operaciones, metricas.win_rate * 100,
            f"{metricas.profit_factor:.2f}" if metricas.profit_factor is not None else "n/a",
            metricas.expectancy_usd, metricas.max_drawdown_pct, estado.capital,
        )


def main() -> None:
    configurar_logging()
    logger.info("INICIANDO SCALPING BOT (Binance, 5m, capital virtual $%.2f)", CAPITAL_INICIAL)

    sesion = mg.crear_sesion_http()
    estado = pos_mod.EstadoCuenta.cargar(ESTADO_PATH, CAPITAL_INICIAL)
    detener = {"flag": False}

    def _manejar_senal(signum, _frame):
        logger.info("Señal %s recibida, deteniendo tras el ciclo actual.", signum)
        detener["flag"] = True

    signal.signal(signal.SIGINT, _manejar_senal)
    signal.signal(signal.SIGTERM, _manejar_senal)

    while not detener["flag"]:
        inicio = time.monotonic()
        try:
            ejecutar_ciclo(sesion, estado)
        except Exception:
            logger.exception("Error no controlado en el ciclo de scalping.")
        finally:
            estado.guardar(ESTADO_PATH)

        espera = max(0.0, CICLO_SEGUNDOS - (time.monotonic() - inicio))
        for _ in range(int(espera)):
            if detener["flag"]:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
