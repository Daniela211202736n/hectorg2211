"""
Gestión genérica de posiciones y cuenta, compartida por las 3 granjas
(Minigranja/paper $140, Paper Trading 29 activos $200, Scalping).

Encapsula lo que antes estaba duplicado/ad-hoc en cada script: apertura y
cierre de posiciones con comisiones sobre el nocional, verificación de
stop-loss/take-profit contra el rango real (high/low) de cada vela en vez
de solo el cierre (evita "hindsight" de que la mecha nunca tocó el stop),
trailing stop, límites de exposición, y persistencia atómica en disco.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import risk_engine as rk


@dataclass
class Posicion:
    symbol: str
    precio_entrada: float
    monto_usd: float
    stop_loss: float
    take_profit: float
    distancia_riesgo: float
    fecha_apertura: str


@dataclass
class EstadoCuenta:
    capital: float
    posiciones: dict[str, Posicion] = field(default_factory=dict)
    capital_pico: float = 0.0
    pausado_por_drawdown: bool = False

    def __post_init__(self) -> None:
        if self.capital_pico <= 0:
            self.capital_pico = self.capital

    def exposicion_actual(self) -> float:
        return sum(p.monto_usd for p in self.posiciones.values())

    def gestor_riesgo(self, perdida_diaria_max_pct: float, drawdown_max_pct: float) -> rk.GestorRiesgo:
        gr = rk.GestorRiesgo(
            capital_pico=self.capital_pico,
            perdida_diaria_max_pct=perdida_diaria_max_pct,
            drawdown_max_pct=drawdown_max_pct,
            pausado_por_drawdown=self.pausado_por_drawdown,
        )
        return gr

    def sincronizar_gestor_riesgo(self, gr: rk.GestorRiesgo) -> None:
        self.capital_pico = gr.capital_pico
        self.pausado_por_drawdown = gr.pausado_por_drawdown

    @classmethod
    def cargar(cls, path: Path, capital_default: float) -> "EstadoCuenta":
        if not path.exists():
            return cls(capital=capital_default)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            posiciones = {s: Posicion(**p) for s, p in data.get("posiciones", {}).items()}
            return cls(
                capital=data.get("capital", capital_default),
                posiciones=posiciones,
                capital_pico=data.get("capital_pico", data.get("capital", capital_default)),
                pausado_por_drawdown=data.get("pausado_por_drawdown", False),
            )
        except (json.JSONDecodeError, OSError, TypeError):
            return cls(capital=capital_default)

    def guardar(self, path: Path) -> None:
        tmp = path.with_suffix(".tmp")
        data = {
            "capital": self.capital,
            "capital_pico": self.capital_pico,
            "pausado_por_drawdown": self.pausado_por_drawdown,
            "posiciones": {s: asdict(p) for s, p in self.posiciones.items()},
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass


def registrar_operacion(trades_path: Path, symbol: str, tipo: str, precio: float, monto: float, pnl: float, capital: float, motivo: str = "") -> None:
    trades_path.parent.mkdir(parents=True, exist_ok=True)
    nuevo = not trades_path.exists()
    with trades_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if nuevo:
            w.writerow(["timestamp", "symbol", "tipo", "motivo", "precio", "monto_usd", "pnl_usd", "capital_resultante"])
        w.writerow([datetime.now(timezone.utc).isoformat(), symbol, tipo, motivo, f"{precio:.6f}", f"{monto:.2f}", f"{pnl:.4f}", f"{capital:.2f}"])


def leer_pnl_operaciones_cerradas(trades_path: Path) -> list[float]:
    if not trades_path.exists():
        return []
    pnls = []
    with trades_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["tipo"] == "CIERRE":
                pnls.append(float(row["pnl_usd"]))
    return pnls


def evaluar_cierre_por_rango(pos: Posicion, high: float, low: float) -> Optional[tuple[float, str]]:
    """Comprueba si el rango (high/low) de la vela tocó el stop-loss o el
    take-profit. Si ambos se tocaron en la misma vela, se asume el
    escenario más conservador (el stop se ejecuta primero) — es la
    convención estándar en backtesting para no sobreestimar resultados."""
    if low <= pos.stop_loss:
        return pos.stop_loss, "STOP_LOSS"
    if high >= pos.take_profit:
        return pos.take_profit, "TAKE_PROFIT"
    return None


def abrir_posicion(
    estado: EstadoCuenta,
    symbol: str,
    precio_entrada: float,
    atr: float,
    trades_path: Path,
    fee_rate: float,
    riesgo_pct: float,
    stop_atr_mult: float,
    ratio_riesgo_beneficio: float,
    tope_posicion_pct: float,
    tope_exposicion_pct: float,
    capital_referencia: float,
) -> bool:
    if symbol in estado.posiciones:
        return False

    exposicion_actual = estado.exposicion_actual()
    tope_exposicion = capital_referencia * tope_exposicion_pct
    if exposicion_actual >= tope_exposicion:
        return False

    plan = rk.calcular_plan_operacion(
        capital=capital_referencia,
        precio_entrada=precio_entrada,
        atr=atr,
        riesgo_pct=riesgo_pct,
        stop_atr_mult=stop_atr_mult,
        ratio_riesgo_beneficio=ratio_riesgo_beneficio,
        tope_posicion_pct=tope_posicion_pct,
    )
    monto = min(plan.monto_usd, tope_exposicion - exposicion_actual)
    if monto <= 0:
        return False

    comision_entrada = monto * fee_rate
    estado.capital -= comision_entrada
    estado.posiciones[symbol] = Posicion(
        symbol=symbol,
        precio_entrada=precio_entrada,
        monto_usd=monto,
        stop_loss=plan.stop_loss,
        take_profit=plan.take_profit,
        distancia_riesgo=plan.distancia_riesgo,
        fecha_apertura=datetime.now(timezone.utc).isoformat(),
    )
    registrar_operacion(trades_path, symbol, "APERTURA", precio_entrada, monto, -comision_entrada, estado.capital, "SEÑAL")
    return True


def leer_multiplicador_riesgo(nombre_bot: str, auditor_dir: Path) -> float:
    """Lee `<nombre_bot>_risk_override.json` escrito por `auditor_granjas.py`
    cuando detecta que el drawdown de un bot simulado se acerca a su
    límite, y devuelve el multiplicador a aplicar sobre `riesgo_pct` en el
    ciclo actual (1.0 si no hay override o el archivo no existe/es
    ilegible). Se relee en cada ciclo para que la reducción de riesgo del
    auditor tenga efecto el mismo día en que se detecta, sin reiniciar el
    proceso del bot."""
    override_path = auditor_dir / f"{nombre_bot}_risk_override.json"
    if not override_path.exists():
        return 1.0
    try:
        data = json.loads(override_path.read_text(encoding="utf-8"))
        return float(data.get("riesgo_pct_multiplicador", 1.0))
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return 1.0


def cerrar_posicion(estado: EstadoCuenta, symbol: str, precio_salida: float, motivo: str, trades_path: Path, fee_rate: float) -> float:
    pos = estado.posiciones.pop(symbol)
    variacion = (precio_salida - pos.precio_entrada) / pos.precio_entrada
    pnl_bruto = pos.monto_usd * variacion
    comision_salida = pos.monto_usd * fee_rate
    pnl_neto = pnl_bruto - comision_salida
    estado.capital += pnl_neto
    registrar_operacion(trades_path, symbol, "CIERRE", precio_salida, pos.monto_usd, pnl_neto, estado.capital, motivo)
    return pnl_neto
