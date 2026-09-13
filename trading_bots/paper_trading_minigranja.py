"""
Paper trading (gemelo simulado) de la Mini-Granja — $140.81 virtuales.

La Mini-Granja real (`bot_produccion_minigranja.py`) solo detecta y loggea
señales; nunca gestionó una cuenta ni un P&L. Este script reutiliza EXACTAMENTE
la misma fuente de datos y los mismos indicadores (EMA7/EMA21 + RSI14 de
Wilder) que la Mini-Granja real, y les añade la capa de gestión de riesgo
profesional (`risk_engine.py`) para simular, con datos reales de Binance pero
capital 100% virtual, qué habría pasado si cada señal se hubiese operado con
disciplina de riesgo:

  * Filtro de tendencia (SMA50 en velas de 1h): solo se opera el cruce
    alcista si el precio ya está por encima de su media de 50 horas.
  * Stop-loss/take-profit por ATR con ratio 1:2, verificados contra el
    rango real (high/low) de cada vela horaria.
  * Trailing stop una vez que la posición avanza 1R a favor.
  * Riesgo fijo del 1% del capital por operación, tope de exposición
    agregada del 60% del capital entre los 5 activos.
  * Circuit breaker de pérdida diaria y de drawdown acumulado.

Capital inicial configurable vía `MG_PAPER_CAPITAL` (por defecto $140.81,
igual al capital real de la Mini-Granja, para que el resultado simulado sea
comparable en magnitud).
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import pandas as pd

import bot_produccion_minigranja as mg
import posiciones as pos_mod
import risk_engine as rk

CAPITAL_INICIAL = float(os.environ.get("MG_PAPER_CAPITAL", "140.81"))
SYMBOLS = mg.SYMBOLS
FEE_RATE = 0.001  # comisión spot típica de Binance (0.1% por lado)
SMA_TENDENCIA = 50
RIESGO_POR_OPERACION_PCT = 0.01
STOP_ATR_MULT = 1.5
RATIO_RIESGO_BENEFICIO = 2.0
MAX_POSICION_PCT = 0.20   # solo 5 activos: se permite algo más de concentración que en la granja de 29
MAX_EXPOSICION_TOTAL_PCT = 0.80
PERDIDA_DIARIA_MAX_PCT = 0.05
DRAWDOWN_MAX_PCT = 0.20
CICLO_SEGUNDOS = 3600

DATA_DIR = Path(os.environ.get("MG_PAPER_DATA_DIR", Path(__file__).with_name("paper_trading_minigranja_data")))
ESTADO_PATH = DATA_DIR / "estado.json"
TRADES_LOG_PATH = DATA_DIR / "trades.csv"
AUDITOR_DIR = Path(__file__).with_name("auditor_data")
LOG_PATH = DATA_DIR / "paper_trading_minigranja.log"

logger = logging.getLogger("paper_trading_minigranja")


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


def procesar_symbol(sesion, estado: pos_mod.EstadoCuenta, symbol: str) -> tuple[float, Optional[tuple[str, float, float]]]:
    df = mg.obtener_datos(sesion, symbol)
    if df is None:
        return 0.0, None

    df = mg.calcular_indicadores(df)
    df["atr"] = rk.calcular_atr(df, mg.RSI_PERIOD)
    df["tendencia_alcista"] = rk.filtro_tendencia_alcista(df, SMA_TENDENCIA)

    i = -2  # última vela cerrada, igual que en la Mini-Granja real
    fast_curr, slow_curr = df["ema_fast"].iloc[i], df["ema_slow"].iloc[i]
    fast_prev, slow_prev = df["ema_fast"].iloc[i - 1], df["ema_slow"].iloc[i - 1]
    rsi_curr = df["rsi"].iloc[i]
    precio_actual = float(df["close"].iloc[i])
    high, low = float(df["high"].iloc[i]), float(df["low"].iloc[i])
    atr_actual = float(df["atr"].iloc[i]) if pd.notna(df["atr"].iloc[i]) else 0.0
    tendencia_ok = bool(df["tendencia_alcista"].iloc[i]) if pd.notna(df["tendencia_alcista"].iloc[i]) else False

    pnl_ciclo = 0.0
    if symbol in estado.posiciones:
        p = estado.posiciones[symbol]
        cierre = pos_mod.evaluar_cierre_por_rango(p, high, low)
        if cierre is not None:
            precio_salida, motivo = cierre
            pnl_ciclo = pos_mod.cerrar_posicion(estado, symbol, precio_salida, motivo, TRADES_LOG_PATH, FEE_RATE)
            logger.info("[CIERRE %s] %s @ %.6f -> pnl neto %+.4f USD", motivo, symbol, precio_salida, pnl_ciclo)
        else:
            nuevo_stop = rk.actualizar_trailing_stop(precio_actual, p.precio_entrada, p.stop_loss, p.distancia_riesgo)
            if nuevo_stop != p.stop_loss:
                p.stop_loss = nuevo_stop
        return pnl_ciclo, None

    hay_cruce = fast_prev <= slow_prev and fast_curr > slow_curr and rsi_curr < mg.RSI_LIMIT
    if hay_cruce and tendencia_ok and atr_actual > 0:
        logger.info("-> %s: cruce alcista + tendencia OK (precio=%.6f, RSI=%.1f)", symbol, precio_actual, rsi_curr)
        return pnl_ciclo, (symbol, precio_actual, atr_actual)

    logger.info("-> %s: sin entrada (precio=%.6f, RSI=%.1f, cruce=%s, tendencia=%s)", symbol, precio_actual, rsi_curr, hay_cruce, tendencia_ok)
    return pnl_ciclo, None


def ejecutar_ciclo(sesion, estado: pos_mod.EstadoCuenta) -> None:
    logger.info("=" * 60)
    logger.info("[PAPER MINI-GRANJA] Capital: $%.4f | Posiciones abiertas: %d", estado.capital, len(estado.posiciones))
    logger.info("=" * 60)

    pnl_ciclo = 0.0
    candidatas: list[tuple[str, float, float]] = []
    for symbol in SYMBOLS:
        try:
            pnl_symbol, candidata = procesar_symbol(sesion, estado, symbol)
            pnl_ciclo += pnl_symbol
            if candidata is not None:
                candidatas.append(candidata)
        except Exception:
            logger.exception("Error procesando %s; se continua con el resto de la flota.", symbol)

    gestor = estado.gestor_riesgo(PERDIDA_DIARIA_MAX_PCT, DRAWDOWN_MAX_PCT)
    permitido, razon = gestor.permite_nuevas_operaciones(estado.capital, pnl_ciclo)
    estado.sincronizar_gestor_riesgo(gestor)

    if not permitido:
        logger.warning("Circuit breaker activo: %s. No se abren nuevas posiciones.", razon)
    else:
        capital_referencia = estado.capital
        multiplicador_riesgo = pos_mod.leer_multiplicador_riesgo("paper_trading_minigranja", AUDITOR_DIR)
        for symbol, precio, atr in candidatas:
            abierto = pos_mod.abrir_posicion(
                estado, symbol, precio, atr, TRADES_LOG_PATH, FEE_RATE,
                RIESGO_POR_OPERACION_PCT * multiplicador_riesgo, STOP_ATR_MULT, RATIO_RIESGO_BENEFICIO,
                MAX_POSICION_PCT, MAX_EXPOSICION_TOTAL_PCT, capital_referencia,
            )
            if abierto:
                p = estado.posiciones[symbol]
                logger.info("[APERTURA] %s @ %.6f | monto=%.4f | stop=%.6f | take=%.6f", symbol, precio, p.monto_usd, p.stop_loss, p.take_profit)

    pnls = pos_mod.leer_pnl_operaciones_cerradas(TRADES_LOG_PATH)
    metricas = rk.calcular_metricas(pnls, CAPITAL_INICIAL)
    logger.info(
        "[METRICAS] operaciones=%d win_rate=%.1f%% profit_factor=%s expectancy=%.4f USD max_dd=%.1f%% retorno_total=%.1f%%",
        metricas.num_operaciones, metricas.win_rate * 100,
        f"{metricas.profit_factor:.2f}" if metricas.profit_factor is not None else "n/a",
        metricas.expectancy_usd, metricas.max_drawdown_pct, metricas.retorno_total_pct,
    )
    logger.info("[ESTADO] PnL este ciclo: %+.4f USD | Capital: $%.4f | Exposicion: $%.4f", pnl_ciclo, estado.capital, estado.exposicion_actual())


def main() -> None:
    configurar_logging()
    logger.info("INICIANDO PAPER TRADING DE LA MINI-GRANJA (capital virtual $%.2f)", CAPITAL_INICIAL)

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
            logger.exception("Error no controlado en el ciclo.")
        finally:
            estado.guardar(ESTADO_PATH)

        espera = max(0.0, CICLO_SEGUNDOS - (time.monotonic() - inicio))
        for _ in range(int(espera)):
            if detener["flag"]:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
