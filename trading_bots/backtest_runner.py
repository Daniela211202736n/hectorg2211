"""
Backtest con DATOS HISTÓRICOS REALES — para correr en un entorno con salida
a Internet (el servidor de producción, `165.227.165.146`, NO este sandbox
de desarrollo, cuya política de red bloquea Binance y Yahoo Finance).

Aplica exactamente la misma lógica de señal + gestión de riesgo
(`risk_engine.py` / `posiciones.py`) que los bots en vivo, pero recorriendo
velas históricas ya cerradas en vez de esperar el reloj real. Es la única
forma honesta de estimar qué habría generado la estrategia: nadie —ni este
script, ni un trader humano— puede predecir cuánto va a ganar un sistema en
el futuro, pero SÍ se puede medir con rigor cuánto habría ganado o perdido
con esta misma lógica sobre precios que ya ocurrieron.

Uso (en el servidor, con `pip install -r requirements.txt`):

    python3 backtest_runner.py --universo minigranja --dias 180
    python3 backtest_runner.py --universo granja --dias 365
    python3 backtest_runner.py --universo scalping --dias 30

Imprime, para el universo elegido, las métricas reales (`risk_engine.Metricas`)
resultantes de aplicar la estrategia sobre el historial descargado, y guarda
el detalle en `backtest_data/<universo>_<fecha>.csv`.

IMPORTANTE — lo que este backtest NO puede decirte:
  * No proyecta el futuro: un resultado histórico bueno (o malo) en el
    período elegido no es una garantía de lo que pasará después. Repetir el
    backtest sobre varios períodos y ventanas out-of-sample (como ya hizo la
    validación Walk-Forward original de la Mini-Granja) es obligatorio antes
    de confiar en el número.
  * No modela slippage más allá de asumir que el stop/take se ejecuta
    exactamente en el nivel calculado — en mercados reales, sobre todo en
    scalping, el precio de ejecución real suele ser ligeramente peor.
  * Los resultados de la Granja Global (29 activos) y la Mini-Granja no son
    comparables entre sí en % porque compiten con universos, timeframes y
    tamaños de capital distintos.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

import posiciones as pos_mod
import risk_engine as rk

OUT_DIR = Path(__file__).with_name("backtest_data")

FEE_RATE = 0.001
RIESGO_POR_OPERACION_PCT = 0.01
STOP_ATR_MULT = 1.5
RATIO_RIESGO_BENEFICIO = 2.0
MAX_POSICION_PCT = 0.15
MAX_EXPOSICION_TOTAL_PCT = 0.60
SMA_TENDENCIA = 50
ATR_PERIODO = 14


def descargar_binance(symbol: str, interval: str, dias: int) -> pd.DataFrame:
    """Descarga velas de Binance paginando hacia atrás (limite de 1000 velas
    por llamada). Requiere salida real a api.binance.com."""
    ms_por_vela = {"1h": 3_600_000, "5m": 300_000, "1m": 60_000}[interval]
    fin = int(time.time() * 1000)
    inicio = fin - dias * 24 * 3600 * 1000
    velas = []
    cursor = inicio
    while cursor < fin:
        resp = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "startTime": cursor, "limit": 1000},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        velas.extend(data)
        cursor = data[-1][0] + ms_por_vela
        time.sleep(0.3)  # respeta el limite de peso de la API
    df = pd.DataFrame(velas, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time',
        'qav', 'trades', 'tbb', 'tbq', 'ignore'
    ])
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    return df[["timestamp", "open", "high", "low", "close"]]


def descargar_yfinance(symbol: str, dias: int) -> pd.DataFrame:
    periodo = f"{max(dias, 400)}d"  # margen extra para SMA50/ATR14 al inicio
    df = yf.Ticker(symbol).history(period=periodo, interval="1d")
    df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close"})
    return df[["open", "high", "low", "close"]].astype(float).reset_index(drop=True)


def backtest_universo(nombre: str, symbols: list[str], obtener_df, capital_inicial: float, riesgo_pct: float, tope_posicion_pct: float, tope_exposicion_pct: float) -> None:
    estado = pos_mod.EstadoCuenta(capital=capital_inicial)
    OUT_DIR.mkdir(exist_ok=True)
    trades_path = OUT_DIR / f"{nombre}_{datetime.now(timezone.utc).date().isoformat()}.csv"
    if trades_path.exists():
        trades_path.unlink()

    datos: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        print(f"Descargando historial real de {symbol}...")
        df = obtener_df(symbol)
        df["atr"] = rk.calcular_atr(df, ATR_PERIODO)
        df["tendencia_alcista"] = rk.filtro_tendencia_alcista(df, SMA_TENDENCIA)
        datos[symbol] = df

    n_velas = min(len(df) for df in datos.values())
    for i in range(SMA_TENDENCIA + 5, n_velas):
        pnl_ciclo = 0.0
        candidatas = []
        for symbol, df in datos.items():
            fila = df.iloc[i]
            fila_prev = df.iloc[i - 1]
            precio, high, low = float(fila["close"]), float(fila["high"]), float(fila["low"])
            atr = float(fila["atr"]) if pd.notna(fila["atr"]) else 0.0

            if symbol in estado.posiciones:
                p = estado.posiciones[symbol]
                cierre = pos_mod.evaluar_cierre_por_rango(p, high, low)
                if cierre:
                    pnl_ciclo += pos_mod.cerrar_posicion(estado, symbol, cierre[0], cierre[1], trades_path, FEE_RATE)
                else:
                    p.stop_loss = rk.actualizar_trailing_stop(precio, p.precio_entrada, p.stop_loss, p.distancia_riesgo)
                continue

            cambio_pct = (precio - float(fila_prev["close"])) / float(fila_prev["close"])
            if cambio_pct > 0.005 and bool(fila["tendencia_alcista"]) and atr > 0:
                candidatas.append((symbol, precio, atr))

        gestor = estado.gestor_riesgo(perdida_diaria_max_pct=0.05, drawdown_max_pct=0.25)
        permitido, _ = gestor.permite_nuevas_operaciones(estado.capital, pnl_ciclo)
        estado.sincronizar_gestor_riesgo(gestor)
        if permitido:
            capital_ref = estado.capital
            for symbol, precio, atr in candidatas:
                pos_mod.abrir_posicion(
                    estado, symbol, precio, atr, trades_path, FEE_RATE,
                    riesgo_pct, STOP_ATR_MULT, RATIO_RIESGO_BENEFICIO,
                    tope_posicion_pct, tope_exposicion_pct, capital_ref,
                )

    pnls = pos_mod.leer_pnl_operaciones_cerradas(trades_path)
    m = rk.calcular_metricas(pnls, capital_inicial)
    print(f"\n=== Resultado backtest real — {nombre} ({n_velas} velas, {len(symbols)} activos) ===")
    print(f"Capital inicial: ${capital_inicial:.2f} | Capital final: ${m.capital_final:.2f} | Retorno total: {m.retorno_total_pct:+.1f}%")
    print(f"Operaciones cerradas: {m.num_operaciones} | Win-rate: {m.win_rate*100:.1f}% | Profit factor: {m.profit_factor}")
    print(f"Expectancy por operacion: ${m.expectancy_usd:+.4f} | Max drawdown: {m.max_drawdown_pct:.1f}%")
    print(f"Detalle de operaciones guardado en: {trades_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest con datos historicos reales.")
    parser.add_argument("--universo", choices=["minigranja", "granja", "scalping"], required=True)
    parser.add_argument("--dias", type=int, default=180)
    parser.add_argument("--capital", type=float, default=None)
    args = parser.parse_args()

    if args.universo == "minigranja":
        symbols = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "AVAXUSDT", "LINKUSDT"]
        backtest_universo(
            "minigranja", symbols, lambda s: descargar_binance(s, "1h", args.dias),
            args.capital or 140.81, riesgo_pct=0.01, tope_posicion_pct=0.20, tope_exposicion_pct=0.80,
        )
    elif args.universo == "scalping":
        symbols = ["BTCUSDT", "ETHUSDT"]
        backtest_universo(
            "scalping", symbols, lambda s: descargar_binance(s, "5m", args.dias),
            args.capital or 100.0, riesgo_pct=0.005, tope_posicion_pct=0.30, tope_exposicion_pct=0.60,
        )
    else:
        symbols = [
            "^GDAXI", "^N225", "ZN=F", "QQQ", "IWM", "XLE", "AAPL", "MSFT", "NVDA", "TSLA", "EWZ",
            "GC=F", "CL=F", "EURUSD=X", "BTC-USD", "ETH-USD", "SPY", "AMZN", "GOOGL", "META",
            "NFLX", "AMD", "JNJ", "JPM", "V", "PG", "XOM", "DIS", "INTC",
        ]
        backtest_universo(
            "granja", symbols, lambda s: descargar_yfinance(s, args.dias),
            args.capital or 200.0, riesgo_pct=0.01, tope_posicion_pct=0.10, tope_exposicion_pct=0.60,
        )


if __name__ == "__main__":
    main()
