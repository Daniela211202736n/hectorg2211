"""
Motor de riesgo y métricas compartido por las 3 granjas (Minigranja, Paper
Trading multiactiva y Scalping).

Concentra las prácticas que separan a un trader/sistema sistemático rentable
de uno que solo "detecta señales":

  1. Riesgo fijo por operación (fixed-fractional sizing): nunca arriesgar más
     de un % pequeño y constante del capital en una sola operación,
     dimensionando el tamaño según la distancia real al stop-loss (no según
     una convicción subjetiva). Es la regla nº1 de gestión de capital de
     cualquier mesa de trading sistemático — evita que una racha de pérdidas
     razonable destruya la cuenta.
  2. Stop-loss y take-profit basados en volatilidad real del activo (ATR),
     no en un porcentaje arbitrario fijo — un stop del 1% es enorme en un
     activo de baja volatilidad y minúsculo (ruido) en uno de alta.
  3. Relación riesgo:beneficio mínima (por defecto 1:2) — para ser rentable
     no hace falta acertar más del 50% de las veces si cada ganancia es al
     menos el doble de cada pérdida.
  4. Filtro de tendencia (regime filter): solo operar a favor de la
     tendencia de un marco temporal superior (SMA larga). Operar en contra
     de la tendencia dominante es la forma más común de perder de forma
     sistemática con una señal de cruce de medias.
  5. Trailing stop: dejar correr las ganancias moviendo el stop a favor del
     precio en vez de cerrar siempre al primer objetivo fijo.
  6. Circuit breakers de pérdida diaria Y de drawdown acumulado: un sistema
     rentable en expectativa igual puede arruinarse por gestión de riesgo
     agregada deficiente (demasiadas posiciones correlacionadas a la vez).
  7. Métricas de desempeño estándar (win-rate, profit factor, expectancy en
     R, máximo drawdown, Sharpe) calculadas SIEMPRE sobre la bitácora real
     de operaciones — nunca solo "cuánto subió el capital", que puede
     esconder una racha de suerte o un sesgo de cálculo.

Nada de esto garantiza rentabilidad futura: son las condiciones necesarias
(no suficientes) que separan un sistema con expectativa matemática positiva
de uno que solo parece bueno en una muestra pequeña o con sesgo de
look-ahead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Indicadores de volatilidad y tendencia
# ---------------------------------------------------------------------------

def calcular_atr(df: pd.DataFrame, periodo: int = 14) -> pd.Series:
    """Average True Range de Wilder. Requiere columnas high/low/close."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / periodo, min_periods=periodo, adjust=False).mean()


def filtro_tendencia_alcista(df: pd.DataFrame, periodo: int = 50) -> pd.Series:
    """True donde el precio de cierre está por encima de su media móvil
    larga: solo se toman señales de compra a favor de la tendencia
    dominante, evitando comprar cruces alcistas dentro de una tendencia
    bajista de fondo (el error más común al usar solo cruces EMA cortos)."""
    sma_larga = df["close"].rolling(window=periodo, min_periods=periodo).mean()
    return df["close"] > sma_larga


# ---------------------------------------------------------------------------
# Tamaño de posición y stops
# ---------------------------------------------------------------------------

@dataclass
class PlanOperacion:
    monto_usd: float
    stop_loss: float
    take_profit: float
    distancia_riesgo: float  # precio_entrada - stop_loss, en unidades de precio


def calcular_plan_operacion(
    capital: float,
    precio_entrada: float,
    atr: float,
    riesgo_pct: float = 0.01,
    stop_atr_mult: float = 1.5,
    ratio_riesgo_beneficio: float = 2.0,
    tope_posicion_pct: float = 0.10,
) -> PlanOperacion:
    """Dimensiona la operación por riesgo fijo: se arriesga `riesgo_pct` del
    capital si el precio llega al stop, nunca más, independientemente de lo
    "segura" que parezca la señal. `tope_posicion_pct` es un segundo límite
    duro (nunca comprometer más de ese % del capital en un solo activo,
    aunque el stop esté muy cerca y el sizing por riesgo sugiera más)."""
    if atr <= 0 or capital <= 0:
        return PlanOperacion(0.0, precio_entrada, precio_entrada, 0.0)

    distancia_riesgo = max(atr * stop_atr_mult, precio_entrada * 0.001)
    riesgo_usd = capital * riesgo_pct
    monto_por_riesgo = (riesgo_usd / distancia_riesgo) * precio_entrada
    monto_usd = min(monto_por_riesgo, capital * tope_posicion_pct, capital)

    stop_loss = precio_entrada - distancia_riesgo
    take_profit = precio_entrada + distancia_riesgo * ratio_riesgo_beneficio
    return PlanOperacion(monto_usd, stop_loss, take_profit, distancia_riesgo)


def actualizar_trailing_stop(
    precio_actual: float,
    precio_entrada: float,
    stop_actual: float,
    distancia_riesgo: float,
    activar_en_r: float = 1.0,
    trailing_atr_mult: float = 1.0,
) -> float:
    """Una vez que el precio avanzó `activar_en_r` veces la distancia de
    riesgo original a favor, el stop se mueve detrás del precio (nunca hacia
    atrás) para proteger ganancias en vez de devolver todo el movimiento si
    el precio revierte. No garantiza capturar el máximo, pero evita que una
    ganancia abierta grande se convierta en pérdida."""
    avance = precio_actual - precio_entrada
    if avance < activar_en_r * distancia_riesgo:
        return stop_actual  # aún no alcanza el umbral de activación

    nuevo_stop = precio_actual - distancia_riesgo * trailing_atr_mult
    return max(stop_actual, nuevo_stop)  # el stop solo puede subir, nunca bajar


# ---------------------------------------------------------------------------
# Gestión de riesgo agregado (circuit breakers)
# ---------------------------------------------------------------------------

@dataclass
class GestorRiesgo:
    """Controla el riesgo a nivel de CUENTA, no solo por operación. Un
    sistema con expectativa positiva por operación igual puede arruinarse si
    no hay un límite agregado de pérdida diaria y de drawdown acumulado."""

    capital_pico: float
    perdida_diaria_max_pct: float = 0.05
    drawdown_max_pct: float = 0.20
    pausado_por_drawdown: bool = False

    def actualizar_pico(self, capital_actual: float) -> None:
        self.capital_pico = max(self.capital_pico, capital_actual)

    def drawdown_actual_pct(self, capital_actual: float) -> float:
        if self.capital_pico <= 0:
            return 0.0
        return (self.capital_pico - capital_actual) / self.capital_pico

    def permite_nuevas_operaciones(self, capital_actual: float, pnl_dia: float) -> tuple[bool, str]:
        self.actualizar_pico(capital_actual)
        dd = self.drawdown_actual_pct(capital_actual)
        if dd >= self.drawdown_max_pct:
            self.pausado_por_drawdown = True
            return False, f"drawdown acumulado {dd*100:.1f}% >= limite {self.drawdown_max_pct*100:.1f}%"

        if self.pausado_por_drawdown and dd < self.drawdown_max_pct * 0.5:
            # Solo se reanuda si el drawdown se recupera a menos de la mitad
            # del límite (histéresis: evita reanudar y volver a pausar cada
            # ciclo si el capital oscila justo en el límite).
            self.pausado_por_drawdown = False

        if self.pausado_por_drawdown:
            return False, "pausado por drawdown, esperando recuperacion parcial"

        perdida_maxima_usd = self.capital_pico * self.perdida_diaria_max_pct
        if pnl_dia <= -perdida_maxima_usd:
            return False, f"perdida del dia {pnl_dia:+.2f} alcanzo el limite diario ({-perdida_maxima_usd:.2f})"

        return True, "ok"


# ---------------------------------------------------------------------------
# Métricas de desempeño (siempre calculadas sobre la bitácora real)
# ---------------------------------------------------------------------------

@dataclass
class Metricas:
    num_operaciones: int
    win_rate: float
    profit_factor: Optional[float]
    expectancy_usd: float
    max_drawdown_pct: float
    sharpe_aprox: Optional[float]
    capital_final: float
    retorno_total_pct: float


def calcular_metricas(pnl_por_operacion: list[float], capital_inicial: float) -> Metricas:
    """`pnl_por_operacion` es la lista de PnL neto (ya con comisiones) de
    cada operación CERRADA, en orden cronológico. No incluye posiciones
    abiertas (no realizadas)."""
    n = len(pnl_por_operacion)
    if n == 0:
        return Metricas(0, 0.0, None, 0.0, 0.0, None, capital_inicial, 0.0)

    ganancias = [p for p in pnl_por_operacion if p > 0]
    perdidas = [p for p in pnl_por_operacion if p < 0]
    win_rate = len(ganancias) / n

    bruto_ganado = sum(ganancias)
    bruto_perdido = -sum(perdidas)
    profit_factor = (bruto_ganado / bruto_perdido) if bruto_perdido > 0 else (math.inf if bruto_ganado > 0 else None)

    expectancy_usd = sum(pnl_por_operacion) / n

    equity = [capital_inicial]
    for p in pnl_por_operacion:
        equity.append(equity[-1] + p)
    pico = equity[0]
    max_dd = 0.0
    for e in equity:
        pico = max(pico, e)
        if pico > 0:
            max_dd = max(max_dd, (pico - e) / pico)

    if n >= 2:
        media = sum(pnl_por_operacion) / n
        varianza = sum((p - media) ** 2 for p in pnl_por_operacion) / (n - 1)
        desviacion = math.sqrt(varianza)
        # Aproximación simple (no anualizada): media/desviación por
        # operación. Sirve para comparar variantes entre sí, no como cifra
        # de Sharpe anualizado estándar sin conocer la frecuencia real de
        # operaciones por año.
        sharpe_aprox = (media / desviacion) if desviacion > 0 else None
    else:
        sharpe_aprox = None

    capital_final = equity[-1]
    retorno_total_pct = (capital_final - capital_inicial) / capital_inicial * 100 if capital_inicial > 0 else 0.0

    return Metricas(
        num_operaciones=n,
        win_rate=win_rate,
        profit_factor=profit_factor,
        expectancy_usd=expectancy_usd,
        max_drawdown_pct=max_dd * 100,
        sharpe_aprox=sharpe_aprox,
        capital_final=capital_final,
        retorno_total_pct=retorno_total_pct,
    )
