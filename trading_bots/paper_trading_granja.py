"""
Granja Global / Radar Multiactiva — Paper trading con prácticas
profesionales de gestión de riesgo (v2).

Cambios frente a la v1 endurecida (ver AUDITORIA.md y ESTRATEGIA_PROFESIONAL.md
para el detalle):

  * Entrada filtrada por TENDENCIA (solo se opera a favor de la SMA50 diaria)
    en vez de reaccionar a cualquier variación diaria >1.5% sin contexto.
  * Stop-loss y take-profit dimensionados por ATR (volatilidad real de cada
    activo) con ratio riesgo:beneficio 1:2, verificados contra el rango
    (high/low) real de cada vela — no solo el cierre — para no sobreestimar
    resultados que la mecha del precio habría stopeado.
  * Trailing stop: una vez que la posición avanza 1R a favor, el stop sube
    para proteger ganancia.
  * Tamaño de posición por riesgo fijo (1% del capital por operación) en vez
    de exponer un % arbitrario de forma pareja.
  * Circuit breaker de pérdida diaria Y de drawdown acumulado (antes solo
    había pérdida diaria).
  * Métricas de desempeño (win-rate, profit factor, expectancy, max
    drawdown) recalculadas cada ciclo desde la bitácora real de operaciones.

Sigue usando datos de Yahoo Finance vía `yfinance` y capital enteramente
simulado — no envía órdenes reales.
"""

from __future__ import annotations

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
import yfinance as yf

import posiciones as pos_mod
import risk_engine as rk

# ==========================================
# CONFIGURACIÓN
# ==========================================
CAPITAL_INICIAL = float(os.environ.get("PT_CAPITAL_INICIAL", "200.00"))
SYMBOLS = [
    "^GDAXI", "^N225", "ZN=F", "QQQ", "IWM", "XLE", "AAPL", "MSFT", "NVDA", "TSLA", "EWZ",
    "GC=F", "CL=F", "EURUSD=X", "BTC-USD", "ETH-USD", "SPY", "AMZN", "GOOGL", "META",
    "NFLX", "AMD", "JNJ", "JPM", "V", "PG", "XOM", "DIS", "INTC"
]
FEE_RATE = 0.001
UMBRAL_ENTRADA_PCT = 0.015
SMA_TENDENCIA = 50
ATR_PERIODO = 14
RIESGO_POR_OPERACION_PCT = 0.01
STOP_ATR_MULT = 1.5
RATIO_RIESGO_BENEFICIO = 2.0
MAX_POSICION_PCT = 0.10
MAX_EXPOSICION_TOTAL_PCT = 0.60
PERDIDA_DIARIA_MAX_PCT = 0.05
DRAWDOWN_MAX_PCT = 0.20
HISTORIA_DIAS = "6mo"

REINTENTOS_YFINANCE = 3
PAUSA_ENTRE_SYMBOLS = 1.0
CICLO_SEGUNDOS = 86400
MIN_VELAS = SMA_TENDENCIA + 5

DATA_DIR = Path(os.environ.get("PT_DATA_DIR", Path(__file__).with_name("paper_trading_data")))
ESTADO_PATH = DATA_DIR / "estado.json"
TRADES_LOG_PATH = DATA_DIR / "trades.csv"
AUDITOR_DIR = Path(__file__).with_name("auditor_data")
LOG_PATH = DATA_DIR / "paper_trading.log"

logger = logging.getLogger("paper_trading")


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


def obtener_historial(symbol: str) -> Optional[pd.DataFrame]:
    for intento in range(1, REINTENTOS_YFINANCE + 1):
        try:
            df = yf.Ticker(symbol).history(period=HISTORIA_DIAS, interval="1d")
            if df is None or df.empty or len(df) < MIN_VELAS:
                logger.warning("Historial insuficiente para %s (intento %d/%d).", symbol, intento, REINTENTOS_YFINANCE)
            else:
                df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close"})
                return df[["open", "high", "low", "close"]].astype(float)
        except Exception as exc:
            logger.warning("Error descargando %s (intento %d/%d): %s", symbol, intento, REINTENTOS_YFINANCE, exc)
        if intento < REINTENTOS_YFINANCE:
            time.sleep(2 ** intento)
    logger.error("No se pudo obtener historial de %s tras %d intentos.", symbol, REINTENTOS_YFINANCE)
    return None


def procesar_symbol(estado: pos_mod.EstadoCuenta, symbol: str) -> tuple[float, Optional[tuple[str, float, float]]]:
    """Devuelve (pnl_realizado_hoy, candidata_entrada) donde candidata_entrada
    es (symbol, precio, atr) o None."""
    df = obtener_historial(symbol)
    if df is None:
        return 0.0, None

    df["atr"] = rk.calcular_atr(df, ATR_PERIODO)
    df["tendencia_alcista"] = rk.filtro_tendencia_alcista(df, SMA_TENDENCIA)

    hoy = df.iloc[-1]
    ayer = df.iloc[-2]
    precio_actual, high, low = float(hoy["close"]), float(hoy["high"]), float(hoy["low"])
    atr_actual = float(hoy["atr"]) if pd.notna(hoy["atr"]) else 0.0
    cambio_pct = (precio_actual - float(ayer["close"])) / float(ayer["close"])

    logger.info("-> %-10s | Cierre: %10.2f | Variacion: %+.2f%% | Tendencia alcista: %s", symbol, precio_actual, cambio_pct * 100, bool(hoy["tendencia_alcista"]))

    pnl_hoy = 0.0
    if symbol in estado.posiciones:
        p = estado.posiciones[symbol]
        cierre = pos_mod.evaluar_cierre_por_rango(p, high, low)
        if cierre is not None:
            precio_salida, motivo = cierre
            pnl_hoy = pos_mod.cerrar_posicion(estado, symbol, precio_salida, motivo, TRADES_LOG_PATH, FEE_RATE)
            logger.info("[CIERRE %s] %s @ %.4f -> pnl neto %+.2f USD", motivo, symbol, precio_salida, pnl_hoy)
        else:
            nuevo_stop = rk.actualizar_trailing_stop(precio_actual, p.precio_entrada, p.stop_loss, p.distancia_riesgo)
            if nuevo_stop != p.stop_loss:
                logger.info("[TRAILING] %s stop %.4f -> %.4f", symbol, p.stop_loss, nuevo_stop)
                p.stop_loss = nuevo_stop
        return pnl_hoy, None

    if cambio_pct > UMBRAL_ENTRADA_PCT and bool(hoy["tendencia_alcista"]) and atr_actual > 0:
        return pnl_hoy, (symbol, precio_actual, atr_actual)

    return pnl_hoy, None


def ejecutar_paper_trading(estado: pos_mod.EstadoCuenta) -> None:
    logger.info("=" * 60)
    logger.info("[PAPER TRADING] Capital: $%.2f | Posiciones abiertas: %d", estado.capital, len(estado.posiciones))
    logger.info("=" * 60)

    pnl_dia = 0.0
    candidatas: list[tuple[str, float, float]] = []

    for symbol in SYMBOLS:
        try:
            pnl_symbol, candidata = procesar_symbol(estado, symbol)
            pnl_dia += pnl_symbol
            if candidata is not None:
                candidatas.append(candidata)
        except Exception:
            logger.exception("Error procesando %s; se continua con el resto de la flota.", symbol)
        time.sleep(PAUSA_ENTRE_SYMBOLS)

    gestor = estado.gestor_riesgo(PERDIDA_DIARIA_MAX_PCT, DRAWDOWN_MAX_PCT)
    permitido, razon = gestor.permite_nuevas_operaciones(estado.capital, pnl_dia)
    estado.sincronizar_gestor_riesgo(gestor)

    if not permitido:
        logger.warning("Circuit breaker activo: %s. No se abren nuevas posiciones este ciclo.", razon)
    else:
        capital_referencia = estado.capital
        # El auditor diario (auditor_granjas.py) puede reducir este
        # multiplicador si detecta que el drawdown se acerca a su limite;
        # se relee cada ciclo para que el ajuste tenga efecto de inmediato.
        multiplicador_riesgo = pos_mod.leer_multiplicador_riesgo("paper_trading_granja", AUDITOR_DIR)
        for symbol, precio, atr in candidatas:
            abierto = pos_mod.abrir_posicion(
                estado, symbol, precio, atr, TRADES_LOG_PATH, FEE_RATE,
                RIESGO_POR_OPERACION_PCT * multiplicador_riesgo, STOP_ATR_MULT, RATIO_RIESGO_BENEFICIO,
                MAX_POSICION_PCT, MAX_EXPOSICION_TOTAL_PCT, capital_referencia,
            )
            if abierto:
                p = estado.posiciones[symbol]
                logger.info("[APERTURA] %s @ %.4f | monto=%.2f | stop=%.4f | take=%.4f", symbol, precio, p.monto_usd, p.stop_loss, p.take_profit)

    pnls_historicos = pos_mod.leer_pnl_operaciones_cerradas(TRADES_LOG_PATH)
    metricas = rk.calcular_metricas(pnls_historicos, CAPITAL_INICIAL)
    logger.info(
        "[METRICAS] operaciones=%d win_rate=%.1f%% profit_factor=%s expectancy=%.3f USD max_dd=%.1f%% retorno_total=%.1f%%",
        metricas.num_operaciones, metricas.win_rate * 100,
        f"{metricas.profit_factor:.2f}" if metricas.profit_factor not in (None,) else "n/a",
        metricas.expectancy_usd, metricas.max_drawdown_pct, metricas.retorno_total_pct,
    )
    logger.info("[ESTADO] PnL realizado hoy: %+.2f USD | Capital: $%.2f | Exposicion abierta: $%.2f",
                pnl_dia, estado.capital, estado.exposicion_actual())


def main() -> None:
    configurar_logging()
    logger.info("INICIANDO PAPER TRADING DE LA GRANJA GLOBAL (v2 - riesgo profesional)")

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
            ejecutar_paper_trading(estado)
        except Exception:
            logger.exception("Error no controlado en el ciclo de paper trading.")
        finally:
            estado.guardar(ESTADO_PATH)

        espera = max(0.0, CICLO_SEGUNDOS - (time.monotonic() - inicio))
        logger.info("Esperando %.0f segundos para el siguiente ciclo...", espera)
        for _ in range(int(espera)):
            if detener["flag"]:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
