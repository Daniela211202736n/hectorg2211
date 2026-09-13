"""
Granja Global / Radar Multiactiva - Paper trading endurecido.

Versión revisada de paper_trading_granja.py. Cambios principales frente al
script original (detalle completo en trading_bots/AUDITORIA.md):

  * Elimina el sesgo de "look-ahead" del original: la versión anterior leía
    la variación YA OCURRIDA de un día completo y, si superaba el umbral,
    anotaba una ganancia fija (+0.8%) EN EL MISMO INSTANTE, sin abrir ni
    cerrar ninguna posición real en el tiempo. Eso hace que la simulación no
    pueda perder nunca: no es una cuenta de resultados, es una fórmula que
    siempre suma. Aquí una señal de momentum de HOY abre una posición que se
    liquida en el CICLO SIGUIENTE al precio real de mercado, así que el
    resultado puede ser positivo o negativo como en la vida real.
  * Gestión de riesgo: tamaño de posición limitado por símbolo y exposición
    total limitada como % del capital (el original "invertía" el 100% del
    capital en cada uno de los 29 activos simultáneamente sin límite de
    concentración, generando ganancias/pérdidas compuestas irreales).
  * Circuit breaker de pérdida diaria: si las pérdidas del día superan un
    umbral, se detiene la apertura de nuevas posiciones en ese ciclo.
  * Persistencia en disco (capital, posiciones abiertas y bitácora de
    operaciones) en vez de vivir solo en la variable global `CAPITAL_VIRTUAL`
    en memoria de proceso: un reinicio del bot en el servidor ya no borra
    el historial ni resetea el capital a 200.00.
  * Comisión aplicada sobre el importe nocional de cada operación (entrada y
    salida), no sobre el capital total acumulado como hacía el original.
  * Manejo de errores por símbolo con reintentos y pausa entre solicitudes a
    Yahoo Finance (yfinance limita/bloquea IPs que golpean la API sin pausas,
    y el original no tenía backoff ni reintentos).
  * Logging a archivo + consola en lugar de solo `print`.
"""

from __future__ import annotations

import csv
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

import yfinance as yf

# ==========================================
# CONFIGURACIÓN DE PAPER TRADING (SIMULACIÓN EN VIVO)
# ==========================================
CAPITAL_INICIAL = float(os.environ.get("PT_CAPITAL_INICIAL", "200.00"))
SYMBOLS = [
    "^GDAXI", "^N225", "ZN=F", "QQQ", "IWM", "XLE", "AAPL", "MSFT", "NVDA", "TSLA", "EWZ",
    "GC=F", "CL=F", "EURUSD=X", "BTC-USD", "ETH-USD", "SPY", "AMZN", "GOOGL", "META",
    "NFLX", "AMD", "JNJ", "JPM", "V", "PG", "XOM", "DIS", "INTC"
]
FEE_RATE = 0.001
UMBRAL_ENTRADA_PCT = 0.015  # variación diaria mínima para considerar entrada

# Gestión de riesgo / exposición.
MAX_POSICION_PCT = 0.10       # tope por símbolo: 10% del capital
MAX_EXPOSICION_TOTAL_PCT = 0.60  # tope agregado: 60% del capital invertido a la vez
PERDIDA_DIARIA_MAX_PCT = 0.05    # circuit breaker: no abrir más posiciones si el día ya perdió 5%

REINTENTOS_YFINANCE = 3
PAUSA_ENTRE_SYMBOLS = 1.0  # segundos, para no golpear yfinance sin control
CICLO_SEGUNDOS = 86400

DATA_DIR = Path(os.environ.get("PT_DATA_DIR", Path(__file__).with_name("paper_trading_data")))
ESTADO_PATH = DATA_DIR / "estado.json"
TRADES_LOG_PATH = DATA_DIR / "trades.csv"
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
        logger.warning("No se pudo abrir el archivo de log %s: %s", LOG_PATH, exc)


@dataclass
class Posicion:
    symbol: str
    precio_entrada: float
    monto_usd: float
    fecha_apertura: str


@dataclass
class EstadoCuenta:
    """Estado persistente de la cuenta de paper trading. En el script
    original todo esto vivía en la variable global `CAPITAL_VIRTUAL`, sin
    guardarse nunca a disco: cualquier reinicio del proceso (caída de la
    sesión de `screen`, redeploy, reboot del servidor) volvía el capital a
    $200.00 y borraba cualquier posición u operación en curso."""

    capital: float = CAPITAL_INICIAL
    posiciones: dict[str, Posicion] = field(default_factory=dict)

    @classmethod
    def cargar(cls, path: Path) -> "EstadoCuenta":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            posiciones = {
                symbol: Posicion(**p) for symbol, p in data.get("posiciones", {}).items()
            }
            return cls(capital=data.get("capital", CAPITAL_INICIAL), posiciones=posiciones)
        except (json.JSONDecodeError, OSError, TypeError) as exc:
            logger.warning("Estado corrupto o ilegible en %s (%s); se reinicia con capital base.", path, exc)
            return cls()

    def guardar(self, path: Path) -> None:
        tmp_path = path.with_suffix(".tmp")
        data = {
            "capital": self.capital,
            "posiciones": {
                symbol: {
                    "symbol": p.symbol,
                    "precio_entrada": p.precio_entrada,
                    "monto_usd": p.monto_usd,
                    "fecha_apertura": p.fecha_apertura,
                }
                for symbol, p in self.posiciones.items()
            },
        }
        try:
            tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp_path.replace(path)  # escritura atómica
        except OSError as exc:
            logger.warning("No se pudo persistir el estado en %s: %s", path, exc)

    def exposicion_actual(self) -> float:
        return sum(p.monto_usd for p in self.posiciones.values())


def registrar_operacion(symbol: str, tipo: str, precio: float, monto_usd: float, pnl_usd: float, capital_resultante: float) -> None:
    nuevo = not TRADES_LOG_PATH.exists()
    try:
        with TRADES_LOG_PATH.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if nuevo:
                writer.writerow(["timestamp", "symbol", "tipo", "precio", "monto_usd", "pnl_usd", "capital_resultante"])
            writer.writerow([datetime.now(timezone.utc).isoformat(), symbol, tipo, f"{precio:.6f}", f"{monto_usd:.2f}", f"{pnl_usd:.4f}", f"{capital_resultante:.2f}"])
    except OSError as exc:
        logger.warning("No se pudo escribir la bitácora de operaciones: %s", exc)


def obtener_precio_actual(symbol: str) -> tuple[Optional[float], Optional[float]]:
    for intento in range(1, REINTENTOS_YFINANCE + 1):
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="2d", interval="1d")
            if df is None or df.empty or len(df) < 2 or "Close" not in df:
                logger.warning("Datos insuficientes de yfinance para %s (intento %d/%d).", symbol, intento, REINTENTOS_YFINANCE)
            else:
                precio_actual = float(df['Close'].iloc[-1])
                precio_anterior = float(df['Close'].iloc[-2])
                return precio_actual, precio_anterior
        except Exception as exc:
            logger.warning("Error descargando datos para %s (intento %d/%d): %s", symbol, intento, REINTENTOS_YFINANCE, exc)

        if intento < REINTENTOS_YFINANCE:
            time.sleep(2 ** intento)  # backoff exponencial: 2s, 4s, ...

    logger.error("No se pudo obtener precio de %s tras %d intentos; se omite este ciclo.", symbol, REINTENTOS_YFINANCE)
    return None, None


def cerrar_posiciones_vencidas(estado: EstadoCuenta, precios_hoy: dict[str, float]) -> float:
    """Liquida al precio REAL de hoy toda posición abierta en el ciclo
    anterior. El resultado puede ser positivo o negativo: a diferencia del
    original, aquí sí existe riesgo de pérdida."""
    pnl_dia = 0.0
    for symbol in list(estado.posiciones.keys()):
        precio_actual = precios_hoy.get(symbol)
        if precio_actual is None:
            continue  # sin dato hoy: se mantiene abierta e se reintenta el próximo ciclo
        posicion = estado.posiciones.pop(symbol)
        variacion = (precio_actual - posicion.precio_entrada) / posicion.precio_entrada
        pnl_bruto = posicion.monto_usd * variacion
        comision_salida = posicion.monto_usd * FEE_RATE
        pnl_neto = pnl_bruto - comision_salida
        estado.capital += pnl_neto
        pnl_dia += pnl_neto
        logger.info(
            "[CIERRE] %s: entrada=%.4f salida=%.4f variacion=%+.2f%% pnl_neto=%+.2f USD",
            symbol, posicion.precio_entrada, precio_actual, variacion * 100, pnl_neto,
        )
        registrar_operacion(symbol, "CIERRE", precio_actual, posicion.monto_usd, pnl_neto, estado.capital)
    return pnl_dia


def abrir_nuevas_posiciones(estado: EstadoCuenta, senales: dict[str, float], pnl_dia_hasta_ahora: float) -> None:
    # Se fija el capital de referencia al inicio del ciclo de aperturas: si se
    # recalculara `estado.capital * MAX_*_PCT` en cada iteración, el propio
    # pago de comisiones de las posiciones ya abiertas ESE ciclo iría
    # encogiendo el tope mientras se recorren las señales restantes, sin que
    # eso refleje una reducción real del capital disponible para arriesgar.
    capital_referencia = estado.capital
    perdida_maxima_usd = capital_referencia * PERDIDA_DIARIA_MAX_PCT
    if pnl_dia_hasta_ahora <= -perdida_maxima_usd:
        logger.warning(
            "Circuit breaker activado: pérdida del día (%+.2f USD) alcanzó el límite (%.2f USD). "
            "No se abrirán nuevas posiciones en este ciclo.",
            pnl_dia_hasta_ahora, perdida_maxima_usd,
        )
        return

    tope_exposicion = capital_referencia * MAX_EXPOSICION_TOTAL_PCT
    monto_por_posicion = capital_referencia * MAX_POSICION_PCT

    for symbol, precio_actual in senales.items():
        if symbol in estado.posiciones:
            continue  # ya hay una posición abierta en este activo

        exposicion_actual = estado.exposicion_actual()
        if exposicion_actual >= tope_exposicion:
            logger.info("Exposición total (%.2f) alcanzó el tope (%.2f); no se abren más posiciones este ciclo.", exposicion_actual, tope_exposicion)
            break

        monto = min(monto_por_posicion, tope_exposicion - exposicion_actual)
        if monto <= 0:
            continue

        comision_entrada = monto * FEE_RATE
        estado.capital -= comision_entrada
        estado.posiciones[symbol] = Posicion(
            symbol=symbol,
            precio_entrada=precio_actual,
            monto_usd=monto,
            fecha_apertura=datetime.now(timezone.utc).isoformat(),
        )
        logger.info("[APERTURA] %s: entrada=%.4f monto=%.2f USD (comision=%.4f)", symbol, precio_actual, monto, comision_entrada)
        registrar_operacion(symbol, "APERTURA", precio_actual, monto, -comision_entrada, estado.capital)


def ejecutar_paper_trading(estado: EstadoCuenta) -> None:
    logger.info("=" * 50)
    logger.info("[PAPER TRADING EN VIVO] Capital actual: $%.2f USD | Posiciones abiertas: %d", estado.capital, len(estado.posiciones))
    logger.info("=" * 50)

    precios_hoy: dict[str, float] = {}
    senales_entrada: dict[str, float] = {}

    for symbol in SYMBOLS:
        actual, anterior = obtener_precio_actual(symbol)
        if actual is None or anterior is None:
            continue

        precios_hoy[symbol] = actual
        cambio_pct = (actual - anterior) / anterior
        logger.info("-> %-10s | Precio actual: %10.2f | Variacion: %+.2f%%", symbol, actual, cambio_pct * 100)

        if cambio_pct > UMBRAL_ENTRADA_PCT:
            senales_entrada[symbol] = actual

        time.sleep(PAUSA_ENTRE_SYMBOLS)  # evita golpear yfinance sin pausas (riesgo de bloqueo/rate-limit)

    pnl_dia = cerrar_posiciones_vencidas(estado, precios_hoy)
    abrir_nuevas_posiciones(estado, senales_entrada, pnl_dia)

    logger.info("[ESTADO] Ciclo completado. PnL realizado hoy: %+.2f USD", pnl_dia)
    logger.info("Capital acumulado: $%.2f USD | Exposicion abierta: $%.2f USD", estado.capital, estado.exposicion_actual())


def main() -> None:
    configurar_logging()
    logger.info("INICIANDO MOTOR DE PAPER TRADING PARA LA GRANJA...")

    estado = EstadoCuenta.cargar(ESTADO_PATH)

    detener = {"flag": False}

    def _manejar_senal(signum, _frame):
        logger.info("Señal %s recibida, se detiene tras el ciclo actual.", signum)
        detener["flag"] = True

    signal.signal(signal.SIGINT, _manejar_senal)
    signal.signal(signal.SIGTERM, _manejar_senal)

    while not detener["flag"]:
        ciclo_inicio = time.monotonic()
        try:
            ejecutar_paper_trading(estado)
        except Exception:
            logger.exception("Error no controlado en el ciclo de paper trading.")
        finally:
            estado.guardar(ESTADO_PATH)

        transcurrido = time.monotonic() - ciclo_inicio
        espera = max(0.0, CICLO_SEGUNDOS - transcurrido)
        logger.info("Esperando %.0f segundos para el siguiente ciclo...", espera)
        for _ in range(int(espera // 1)):
            if detener["flag"]:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
