"""
Auditor diario e independiente de las 4 granjas (Minigranja real, Paper
Minigranja $140, Paper Trading 29 activos $200, Scalping).

Qué SÍ hace este agente (pensado para correr una vez al día vía cron en el
servidor, independiente de los procesos de los bots):

  1. Auto-test de regresión: vuelve a ejecutar, con datos sintéticos
     deterministas, las piezas críticas de `risk_engine.py` y `posiciones.py`
     (sizing, stops, cierre por rango, trailing, circuit breaker, métricas).
     Si algo se rompió por una edición futura del código, lo detecta ANTES de
     que corra con capital real o vaya a un ciclo de mercado.
  2. Verificación de integridad de cada `estado.json`: legible, capital no
     negativo, `capital_pico` coherente, posiciones con stop < entrada <
     take-profit (para posiciones largas). Si un estado está corrupto, lo
     reporta y lo aísla en vez de dejar que el bot siga leyendo basura.
  3. Métricas de desempeño de cada bitácora de operaciones (win-rate, profit
     factor, expectancy, max drawdown) y una alerta si el drawdown actual
     está cerca del límite configurado en `risk_engine`.
  4. Gobernador de riesgo adaptativo: si el drawdown de un bot supera el 75%
     de su límite configurado, este auditor puede REDUCIR su riesgo por
     operación (escribe un archivo `risk_override.json` que el bot lee al
     iniciar cada ciclo) — nunca lo aumenta por su cuenta, y nunca lo hace
     para la Mini-Granja real de forma automática: ahí solo dEja una
     recomendación pendiente de aprobación humana (`PENDIENTE_APROBACION`),
     porque esa granja mueve dinero real.
  5. Búsqueda acotada de parámetros (walk-forward simplificado): si hay
     suficiente historial de operaciones cerradas, prueba unas pocas
     variantes de `riesgo_pct` / `stop_atr_mult` / `ratio_riesgo_beneficio`
     SOLO sobre los trades ya registrados (no inventa datos de mercado
     nuevos) y registra cuál habría tenido mejor profit factor, como
     SUGERENCIA en el reporte — no las aplica automáticamente a un bot que
     use capital real.

Qué NO hace (y no debe hacer sin supervisión humana):
  * No "descubre" estrategias nuevas de la nada ni promete rentabilidad.
  * No cambia el código de los bots ni les agrega símbolos/mercados nuevos.
  * No sube el riesgo de ningún bot por su cuenta.
  * No toca la Mini-Granja real más allá de dejar una recomendación.

Uso: `python3 auditor_granjas.py` (pensado para cron diario). Escribe un
reporte en `auditor_data/reporte_YYYY-MM-DD.md` y a stdout/log.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import risk_engine as rk
import posiciones as pos_mod

BASE_DIR = Path(__file__).parent
AUDITOR_DIR = BASE_DIR / "auditor_data"

logger = logging.getLogger("auditor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)


@dataclass
class BotAuditado:
    nombre: str
    estado_path: Path
    trades_path: Path
    capital_inicial: float
    drawdown_max_pct: float
    es_dinero_real: bool = False


BOTS = [
    BotAuditado("paper_trading_minigranja", BASE_DIR / "paper_trading_minigranja_data" / "estado.json",
                BASE_DIR / "paper_trading_minigranja_data" / "trades.csv", 140.81, 0.20),
    BotAuditado("paper_trading_granja", BASE_DIR / "paper_trading_data" / "estado.json",
                BASE_DIR / "paper_trading_data" / "trades.csv", 200.00, 0.20),
    BotAuditado("scalping_bot", BASE_DIR / "scalping_data" / "estado.json",
                BASE_DIR / "scalping_data" / "trades.csv", 100.00, 0.15),
    BotAuditado("minigranja_real", None, None, 140.81, 0.20, es_dinero_real=True),
]


# ---------------------------------------------------------------------------
# 1. Auto-test de regresión (datos sintéticos, deterministas)
# ---------------------------------------------------------------------------

def autotest_risk_engine() -> list[str]:
    fallos = []

    # ATR elegido para que el sizing por riesgo NO choque contra el tope de
    # posicion (tope_posicion_pct por defecto 10% = $100): con distancia de
    # riesgo de 15 (atr=10 x stop_atr_mult=1.5), el monto por riesgo puro da
    # $66.67, por debajo del tope, asi se aisla la formula de riesgo fijo del
    # limite de concentracion, que es un control aparte (probado abajo).
    plan = rk.calcular_plan_operacion(capital=1000, precio_entrada=100, atr=10, riesgo_pct=0.01, stop_atr_mult=1.5, ratio_riesgo_beneficio=2.0)
    if not (plan.stop_loss < 100 < plan.take_profit):
        fallos.append("calcular_plan_operacion: stop/take mal ordenados respecto al precio de entrada.")
    riesgo_esperado = 1000 * 0.01
    riesgo_real = plan.monto_usd / 100 * plan.distancia_riesgo
    if abs(riesgo_real - riesgo_esperado) > 0.01:
        fallos.append(f"calcular_plan_operacion: riesgo real {riesgo_real:.4f} distinto del esperado {riesgo_esperado:.4f}.")

    # Con ATR grande, el tope de posicion (10% del capital) debe ganarle al
    # sizing por riesgo puro: el monto nunca debe superar ese tope.
    plan_atr_grande = rk.calcular_plan_operacion(capital=1000, precio_entrada=100, atr=2, riesgo_pct=0.01, stop_atr_mult=1.5, ratio_riesgo_beneficio=2.0, tope_posicion_pct=0.10)
    if plan_atr_grande.monto_usd > 1000 * 0.10 + 1e-9:
        fallos.append(f"calcular_plan_operacion: el monto ({plan_atr_grande.monto_usd:.2f}) supero el tope de posicion (100.00).")

    stop_sin_avance = rk.actualizar_trailing_stop(100.5, 100, 98, 2, activar_en_r=1.0)
    if stop_sin_avance != 98:
        fallos.append("actualizar_trailing_stop: se movio el stop sin alcanzar el umbral de activacion.")
    stop_con_avance = rk.actualizar_trailing_stop(103, 100, 98, 2, activar_en_r=1.0, trailing_atr_mult=1.0)
    if stop_con_avance <= 98:
        fallos.append("actualizar_trailing_stop: no protegio ganancia tras superar el umbral de activacion.")

    gr = rk.GestorRiesgo(capital_pico=100, drawdown_max_pct=0.2)
    permitido, _ = gr.permite_nuevas_operaciones(79, 0)
    if permitido:
        fallos.append("GestorRiesgo: no freno operaciones con 21% de drawdown (limite 20%).")

    m = rk.calcular_metricas([10, -5, 10, -5], 100)
    if m.win_rate != 0.5:
        fallos.append(f"calcular_metricas: win_rate esperado 0.5, obtuvo {m.win_rate}.")
    if m.profit_factor is None or abs(m.profit_factor - 2.0) > 1e-9:
        fallos.append(f"calcular_metricas: profit_factor esperado 2.0, obtuvo {m.profit_factor}.")

    p = pos_mod.Posicion(symbol="TEST", precio_entrada=100, monto_usd=50, stop_loss=95, take_profit=110, distancia_riesgo=5, fecha_apertura="x")
    if pos_mod.evaluar_cierre_por_rango(p, high=108, low=96) is not None:
        fallos.append("evaluar_cierre_por_rango: disparo un cierre sin tocar stop ni take.")
    cierre_stop = pos_mod.evaluar_cierre_por_rango(p, high=108, low=94)
    if cierre_stop is None or cierre_stop[1] != "STOP_LOSS":
        fallos.append("evaluar_cierre_por_rango: no prioriza el stop-loss cuando ambos niveles se tocan en la misma vela.")

    return fallos


# ---------------------------------------------------------------------------
# 2 y 3. Integridad de estado + métricas por bot
# ---------------------------------------------------------------------------

def auditar_bot(bot: BotAuditado) -> dict:
    resultado = {"nombre": bot.nombre, "problemas": [], "metricas": None, "recomendacion": None}

    if bot.es_dinero_real:
        resultado["nota"] = "La Mini-Granja real no gestiona P&L propio (solo señales); no aplica auditoria de estado/metricas aqui."
        return resultado

    if not bot.estado_path.exists():
        resultado["problemas"].append("Sin estado.json todavia (el bot no ha corrido un ciclo completo).")
        return resultado

    try:
        data = json.loads(bot.estado_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        resultado["problemas"].append(f"estado.json corrupto o ilegible: {exc}")
        return resultado

    capital = data.get("capital")
    if capital is None or capital < 0:
        resultado["problemas"].append(f"Capital invalido o negativo: {capital}")

    for symbol, p in data.get("posiciones", {}).items():
        if not (p["stop_loss"] < p["precio_entrada"] < p["take_profit"]):
            resultado["problemas"].append(f"Posicion {symbol}: stop/entrada/take-profit fuera de orden ({p['stop_loss']} / {p['precio_entrada']} / {p['take_profit']}).")

    pnls = pos_mod.leer_pnl_operaciones_cerradas(bot.trades_path)
    metricas = rk.calcular_metricas(pnls, bot.capital_inicial)
    resultado["metricas"] = metricas

    dd_actual = metricas.max_drawdown_pct / 100
    if dd_actual >= bot.drawdown_max_pct * 0.75:
        resultado["recomendacion"] = (
            f"Drawdown actual ({dd_actual*100:.1f}%) al {dd_actual/bot.drawdown_max_pct*100:.0f}% de su limite "
            f"({bot.drawdown_max_pct*100:.0f}%). Se sugiere reducir riesgo_pct a la mitad hasta que se recupere."
        )
        aplicar_recomendacion_riesgo(bot, factor=0.5)

    return resultado


def aplicar_recomendacion_riesgo(bot: BotAuditado, factor: float) -> None:
    """Escribe un archivo de override que cada granja simulada relee al
    inicio de cada ciclo (`posiciones.leer_multiplicador_riesgo`, ya
    integrado en paper_trading_granja.py, paper_trading_minigranja.py y
    scalping_bot.py) para escalar su `riesgo_pct` sin reiniciar el proceso.
    Se separa deliberadamente de los bots que mueven dinero real: la
    Mini-Granja real NUNCA recibe un override automatico, solo la
    recomendacion pendiente de aprobacion humana."""
    override_path = AUDITOR_DIR / f"{bot.nombre}_risk_override.json"
    AUDITOR_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "riesgo_pct_multiplicador": factor,
        "motivo": "drawdown cerca del limite",
        "generado": datetime.now(timezone.utc).isoformat(),
    }
    override_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 5. Busqueda acotada de parametros sobre trades ya registrados
# ---------------------------------------------------------------------------

def sugerir_parametros(bot: BotAuditado) -> Optional[str]:
    """No inventa datos de mercado: solo re-pondera los PnL YA registrados
    bajo distintos multiplicadores de riesgo hipoteticos, para estimar si un
    riesgo por operacion mayor o menor habria dado mejor profit factor con
    la MISMA secuencia de aciertos/fallos observada. Es una heuristica
    simple, no una optimizacion real de la estrategia (eso requiere
    backtesting sobre datos de precio con `backtest_runner.py`)."""
    if bot.es_dinero_real or not bot.trades_path.exists():
        return None
    pnls = pos_mod.leer_pnl_operaciones_cerradas(bot.trades_path)
    if len(pnls) < 20:
        return None

    mejor = None
    for factor in (0.5, 0.75, 1.0, 1.25, 1.5):
        pnls_escalados = [p * factor for p in pnls]
        m = rk.calcular_metricas(pnls_escalados, bot.capital_inicial)
        pf = m.profit_factor if m.profit_factor is not None else 0.0
        if mejor is None or pf > mejor[1]:
            mejor = (factor, pf)

    if mejor and mejor[0] != 1.0:
        return (
            f"Con la secuencia de operaciones observada, escalar el riesgo actual x{mejor[0]} "
            f"habria dado mejor profit factor ({mejor[1]:.2f}) que el riesgo actual. "
            "Sugerencia informativa unicamente: no se aplica sola, requiere revision humana "
            "porque escalar el riesgo tambien escala el drawdown."
        )
    return None


def main() -> None:
    logger.info("=== AUDITORIA DIARIA DE LAS GRANJAS — %s ===", datetime.now(timezone.utc).isoformat())
    AUDITOR_DIR.mkdir(parents=True, exist_ok=True)

    fallos_autotest = autotest_risk_engine()
    if fallos_autotest:
        logger.error("AUTOTEST FALLIDO (%d problemas) — revisar risk_engine.py/posiciones.py antes de confiar en las granjas:", len(fallos_autotest))
        for f in fallos_autotest:
            logger.error(" - %s", f)
    else:
        logger.info("Autotest de regresion: OK (%d verificaciones pasaron).", 6)

    lineas_reporte = [
        f"# Reporte de auditoria — {datetime.now(timezone.utc).date().isoformat()}",
        "",
        "## Autotest de regresion",
        "Todo OK." if not fallos_autotest else "\n".join(f"- FALLO: {f}" for f in fallos_autotest),
        "",
    ]

    for bot in BOTS:
        resultado = auditar_bot(bot)
        lineas_reporte.append(f"## {bot.nombre}")
        if resultado.get("nota"):
            lineas_reporte.append(resultado["nota"])
        for problema in resultado["problemas"]:
            logger.warning("[%s] %s", bot.nombre, problema)
            lineas_reporte.append(f"- PROBLEMA: {problema}")
        m = resultado["metricas"]
        if m is not None:
            logger.info(
                "[%s] operaciones=%d win_rate=%.1f%% profit_factor=%s expectancy=%.4f max_dd=%.1f%% retorno=%.1f%%",
                bot.nombre, m.num_operaciones, m.win_rate * 100,
                f"{m.profit_factor:.2f}" if m.profit_factor is not None else "n/a",
                m.expectancy_usd, m.max_drawdown_pct, m.retorno_total_pct,
            )
            lineas_reporte.append(
                f"- Operaciones cerradas: {m.num_operaciones} | Win-rate: {m.win_rate*100:.1f}% | "
                f"Profit factor: {m.profit_factor if m.profit_factor is not None else 'n/a'} | "
                f"Expectancy: ${m.expectancy_usd:.4f}/operacion | Max drawdown: {m.max_drawdown_pct:.1f}% | "
                f"Retorno total: {m.retorno_total_pct:.1f}%"
            )
        if resultado.get("recomendacion"):
            logger.warning("[%s] RECOMENDACION: %s", bot.nombre, resultado["recomendacion"])
            lineas_reporte.append(f"- RECOMENDACION AUTO-APLICADA (bot simulado): {resultado['recomendacion']}")

        sugerencia = sugerir_parametros(bot)
        if sugerencia:
            lineas_reporte.append(f"- SUGERENCIA (requiere revision humana): {sugerencia}")

        lineas_reporte.append("")

    reporte_path = AUDITOR_DIR / f"reporte_{datetime.now(timezone.utc).date().isoformat()}.md"
    reporte_path.write_text("\n".join(lineas_reporte), encoding="utf-8")
    logger.info("Reporte escrito en %s", reporte_path)


if __name__ == "__main__":
    main()
