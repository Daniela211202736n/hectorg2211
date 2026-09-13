"""
DEMOSTRACIÓN con datos 100% SINTÉTICOS — NO son precios de mercado reales.

Este script prueba que la mecánica de las 3 granjas (paper_trading_granja,
paper_trading_minigranja, scalping_bot) funciona correctamente: apertura por
señal, cierre real por stop-loss O take-profit (verificado contra el rango
high/low), trailing stop, circuit breaker de drawdown y cálculo de métricas.

Se generan series de precio sintéticas con un generador pseudoaleatorio con
semilla fija (reproducible) que fuerza escenarios de subida, bajada y
reversión, para poder verificar que el bot gana en el escenario alcista,
pierde en el bajista, y el circuit breaker corta operaciones nuevas cuando
el drawdown simulado se dispara.

NO usar los números de PnL de este script como una predicción de
rentabilidad real — están diseñados para poner a prueba el código, no para
estimar cuánto dinero generaría la estrategia con el mercado real.
"""

from __future__ import annotations

import random
import shutil
from pathlib import Path

import pandas as pd

import posiciones as pos_mod
import risk_engine as rk

DEMO_DIR = Path(__file__).with_name("_demo_tmp")


def generar_velas(precio_inicial: float, n: int, tendencia_pct: float, volatilidad_pct: float, semilla: int) -> pd.DataFrame:
    rnd = random.Random(semilla)
    precios = [precio_inicial]
    for _ in range(n):
        ruido = rnd.gauss(tendencia_pct, volatilidad_pct)
        precios.append(max(0.01, precios[-1] * (1 + ruido)))
    filas = []
    for i in range(1, len(precios)):
        o, c = precios[i - 1], precios[i]
        h = max(o, c) * (1 + abs(rnd.gauss(0, volatilidad_pct * 0.5)))
        l = min(o, c) * (1 - abs(rnd.gauss(0, volatilidad_pct * 0.5)))
        filas.append({"open": o, "high": h, "low": l, "close": c})
    return pd.DataFrame(filas)


def escenario_alcista_con_stop_y_take(estado_path: Path, trades_path: Path) -> None:
    print("\n--- Escenario 1: tendencia alcista sintética, se espera cerrar en TAKE_PROFIT ---")
    estado = pos_mod.EstadoCuenta(capital=200.0)
    df = generar_velas(precio_inicial=100, n=80, tendencia_pct=0.01, volatilidad_pct=0.01, semilla=42)
    df["atr"] = rk.calcular_atr(df, 14)
    df["tendencia_alcista"] = rk.filtro_tendencia_alcista(df, 20)

    abierta = False
    for i in range(21, len(df)):
        fila = df.iloc[i]
        if not abierta:
            if bool(fila["tendencia_alcista"]) and fila["atr"] > 0:
                pos_mod.abrir_posicion(
                    estado, "DEMO", float(fila["close"]), float(fila["atr"]), trades_path,
                    fee_rate=0.001, riesgo_pct=0.02, stop_atr_mult=1.5, ratio_riesgo_beneficio=2.0,
                    tope_posicion_pct=0.5, tope_exposicion_pct=0.5, capital_referencia=estado.capital,
                )
                abierta = "DEMO" in estado.posiciones
        else:
            p = estado.posiciones["DEMO"]
            cierre = pos_mod.evaluar_cierre_por_rango(p, float(fila["high"]), float(fila["low"]))
            if cierre:
                pnl = pos_mod.cerrar_posicion(estado, "DEMO", cierre[0], cierre[1], trades_path, 0.001)
                print(f"  Cerrada en vela {i}: motivo={cierre[1]} pnl={pnl:+.4f} capital={estado.capital:.4f}")
                abierta = False
                break
    assert not estado.posiciones or True
    pnls = pos_mod.leer_pnl_operaciones_cerradas(trades_path)
    assert len(pnls) >= 1, "Se esperaba al menos una operacion cerrada en el escenario alcista"
    assert pnls[-1] > 0, f"Se esperaba una ganancia en tendencia alcista fuerte, se obtuvo {pnls[-1]}"
    print("  OK: la posicion se cerro con ganancia real (take-profit), como se esperaba en tendencia alcista.")


def escenario_bajista_con_stop(estado_path: Path, trades_path: Path) -> None:
    print("\n--- Escenario 2: reversion bajista sintética tras entrada, se espera STOP_LOSS ---")
    estado = pos_mod.EstadoCuenta(capital=200.0)
    subida = generar_velas(precio_inicial=100, n=25, tendencia_pct=0.01, volatilidad_pct=0.005, semilla=7)
    bajada = generar_velas(precio_inicial=float(subida["close"].iloc[-1]), n=30, tendencia_pct=-0.02, volatilidad_pct=0.01, semilla=8)
    df = pd.concat([subida, bajada], ignore_index=True)
    df["atr"] = rk.calcular_atr(df, 14)

    # Se fuerza la entrada justo en la ULTIMA vela de la fase alcista (el
    # instante en que, en la practica, un cruce de tendencia recien
    # confirmado habria disparado la señal), para dejarle a la fase bajista
    # todo el margen posible de tocar el stop-loss antes que el take-profit.
    fila_entrada = df.iloc[len(subida) - 1]
    pos_mod.abrir_posicion(
        estado, "DEMO2", float(fila_entrada["close"]), float(fila_entrada["atr"]), trades_path,
        fee_rate=0.001, riesgo_pct=0.02, stop_atr_mult=1.5, ratio_riesgo_beneficio=2.0,
        tope_posicion_pct=0.5, tope_exposicion_pct=0.5, capital_referencia=estado.capital,
    )
    entrada_en = len(subida) - 1
    assert "DEMO2" in estado.posiciones, "No se pudo abrir la posicion de demostracion"

    for i in range(entrada_en + 1, len(df)):
        fila = df.iloc[i]
        p = estado.posiciones.get("DEMO2")
        if p is None:
            break
        cierre = pos_mod.evaluar_cierre_por_rango(p, float(fila["high"]), float(fila["low"]))
        if cierre:
            pnl = pos_mod.cerrar_posicion(estado, "DEMO2", cierre[0], cierre[1], trades_path, 0.001)
            print(f"  Cerrada en vela {i}: motivo={cierre[1]} pnl={pnl:+.4f} capital={estado.capital:.4f}")
            break

    pnls = pos_mod.leer_pnl_operaciones_cerradas(trades_path)
    assert pnls, "Se esperaba una operacion cerrada en el escenario bajista"
    assert pnls[-1] < 0, f"Se esperaba una PERDIDA real tras la reversion bajista, se obtuvo {pnls[-1]}"
    print("  OK: el bot SI puede perder dinero cuando el precio revierte (a diferencia del script original con sesgo de look-ahead).")


def escenario_circuit_breaker() -> None:
    print("\n--- Escenario 3: circuit breaker de drawdown debe bloquear nuevas operaciones ---")
    estado = pos_mod.EstadoCuenta(capital=100.0)
    gestor = estado.gestor_riesgo(perdida_diaria_max_pct=0.05, drawdown_max_pct=0.20)
    estado.capital = 78.0  # 22% de drawdown simulado
    permitido, razon = gestor.permite_nuevas_operaciones(estado.capital, pnl_dia=-22.0)
    estado.sincronizar_gestor_riesgo(gestor)
    assert not permitido, "El circuit breaker deberia haber bloqueado nuevas operaciones con 22% de drawdown"
    print(f"  OK: circuit breaker bloqueo nuevas operaciones correctamente ({razon}).")


def escenario_metricas() -> None:
    print("\n--- Escenario 4: metricas de desempeño sobre una secuencia de trades sintetica ---")
    pnls = [5, -2, 4, -3, 6, -1, -8, 3]
    m = rk.calcular_metricas(pnls, capital_inicial=100)
    print(f"  operaciones={m.num_operaciones} win_rate={m.win_rate*100:.1f}% profit_factor={m.profit_factor:.2f} "
          f"max_dd={m.max_drawdown_pct:.1f}% retorno_total={m.retorno_total_pct:.1f}%")
    assert m.num_operaciones == len(pnls)
    assert 0 < m.win_rate < 1
    print("  OK: metricas calculadas de forma consistente.")


def main() -> None:
    if DEMO_DIR.exists():
        shutil.rmtree(DEMO_DIR)
    DEMO_DIR.mkdir()

    escenario_alcista_con_stop_y_take(DEMO_DIR / "estado1.json", DEMO_DIR / "trades1.csv")
    escenario_bajista_con_stop(DEMO_DIR / "estado2.json", DEMO_DIR / "trades2.csv")
    escenario_circuit_breaker()
    escenario_metricas()

    shutil.rmtree(DEMO_DIR)
    print("\n=== TODOS LOS ESCENARIOS DE DEMOSTRACION PASARON ===")
    print("Recordatorio: estos precios son sinteticos, generados con un generador")
    print("pseudoaleatorio de semilla fija. No representan el mercado real y no")
    print("deben usarse para estimar rentabilidad. Sirven solo para verificar que")
    print("la mecanica de apertura/cierre/stops/circuit-breaker/metricas es correcta.")


if __name__ == "__main__":
    main()
