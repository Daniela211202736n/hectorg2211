#!/usr/bin/env python3
"""
Punto de entrada único de Luna: levanta el dashboard (navegador) y, al mismo
tiempo, el oído (voz.py) escuchando "Luna" en segundo plano. Los dos caminos
—hablarle o escribirle en el dashboard— usan el mismo cerebro (cerebro.py) y
comparten el mismo historial de conversación.

Uso:
    python servidor.py

Luego abre http://localhost:8790 en el navegador (se abre solo).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

import psutil
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

load_dotenv(Path(__file__).resolve().parent / ".env")

from cerebro import (  # noqa: E402 - tras cargar el .env
    Cerebro,
    GROQ_MODEL,
    NOMBRE_ASISTENTE,
    NOMBRE_USUARIA,
    herramienta_clima,
)
import memoria  # noqa: E402
import voz  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("servidor")

PUERTO = int(os.environ.get("JARVIS_PUERTO", "8790"))
CARPETA_DASHBOARD = Path(__file__).resolve().parent / "dashboard"
INTERVALO_RECORDATORIOS_S = int(os.environ.get("JARVIS_INTERVALO_RECORDATORIOS", "60"))
CIUDAD_CLIMA = (os.environ.get("JARVIS_CIUDAD_CLIMA") or "Buga, Colombia").strip()
CLIMA_CACHE_S = 600  # el clima no cambia rápido: cachea 10 min para no golpear la API

app = Flask(__name__, static_folder=None)

_estado_lock = threading.Lock()
_estado = {"estado": "inactivo", "mensajes": [], "interacciones": 0}

MAX_MENSAJES_DASHBOARD = 200


def _actualizar_estado(nuevo_estado: str) -> None:
    with _estado_lock:
        _estado["estado"] = nuevo_estado


def _agregar_mensaje(rol: str, texto: str) -> None:
    with _estado_lock:
        _estado["mensajes"].append(
            {"rol": rol, "texto": texto, "hora": datetime.now().strftime("%H:%M")}
        )
        if rol == "usuario":
            _estado["interacciones"] += 1
        if len(_estado["mensajes"]) > MAX_MENSAJES_DASHBOARD:
            _estado["mensajes"] = _estado["mensajes"][-MAX_MENSAJES_DASHBOARD:]


cerebro = Cerebro(hablar_fn=voz.hablar)
escucha = voz.EscuchaJarvis(cerebro, on_estado=_actualizar_estado, on_mensaje=_agregar_mensaje)


@app.get("/")
def index():
    return send_from_directory(CARPETA_DASHBOARD, "index.html")


@app.get("/<path:ruta>")
def estaticos(ruta: str):
    return send_from_directory(CARPETA_DASHBOARD, ruta)


@app.after_request
def _sin_cache(respuesta):
    # El dashboard cambia seguido mientras lo seguimos mejorando: sin esto, el
    # navegador podía quedarse con una versión vieja del HTML/JS/CSS aunque el
    # archivo en disco ya estuviera actualizado.
    respuesta.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return respuesta


@app.get("/api/estado")
def api_estado():
    with _estado_lock:
        datos = dict(_estado)
    datos["nivel_mic"] = escucha.nivel_mic()
    datos["umbral_mic"] = escucha.umbral_actual()
    datos["activaciones_voz"] = escucha.activaciones()
    return jsonify(datos)


def _saludar(forzado: bool = False) -> bool:
    """Habla y muestra el resumen de pendientes. Con forzado=True lo hace aunque
    ya se haya saludado hoy (para el botón "Repetir saludo" del dashboard)."""
    if not forzado and not memoria.necesita_saludo_hoy():
        return False
    resumen = memoria.resumen_pendientes_hoy()
    saludo = f"Buenos días, {NOMBRE_USUARIA}. {resumen}"
    _agregar_mensaje("jarvis", saludo)
    voz.hablar(saludo)
    memoria.registrar_saludo_hoy()
    return True


@app.post("/api/repetir_saludo")
def api_repetir_saludo():
    _saludar(forzado=True)
    return jsonify({"ok": True})


@app.get("/api/config")
def api_config():
    return jsonify(
        {
            "nombre_asistente": NOMBRE_ASISTENTE,
            "usuaria": NOMBRE_USUARIA,
            "modelo": GROQ_MODEL,
            "voz": _motor_voz_activo(),
            "ciudad_clima": CIUDAD_CLIMA,
        }
    )


def _motor_voz_activo() -> str:
    if (os.environ.get("ELEVENLABS_API_KEY") or "").strip() and (
        os.environ.get("ELEVENLABS_VOICE_ID") or ""
    ).strip():
        return "ElevenLabs"
    return "Edge TTS"


# psutil.cpu_percent necesita una primera llamada de "referencia" al arrancar;
# si no, la primera lectura real del dashboard siempre daría 0%.
psutil.cpu_percent(interval=None)


@app.get("/api/sistema")
def api_sistema():
    memoria_ram = psutil.virtual_memory()
    disco = psutil.disk_usage(str(Path.home()))
    bateria = psutil.sensors_battery()
    red = psutil.net_io_counters()
    segundos_encendido = max(0, time.time() - psutil.boot_time())

    return jsonify(
        {
            "cpu_pct": psutil.cpu_percent(interval=None),
            "ram_pct": memoria_ram.percent,
            "ram_usado_gb": round(memoria_ram.used / 1e9, 1),
            "ram_total_gb": round(memoria_ram.total / 1e9, 1),
            "disco_pct": disco.percent,
            "disco_libre_gb": round(disco.free / 1e9, 1),
            "disco_total_gb": round(disco.total / 1e9, 1),
            "nucleos": psutil.cpu_count(logical=True) or 0,
            "encendido_min": round(segundos_encendido / 60),
            "bateria_pct": bateria.percent if bateria else None,
            "bateria_cargando": bool(bateria.power_plugged) if bateria else None,
            "red_bajada_bytes": red.bytes_recv,
            "red_subida_bytes": red.bytes_sent,
        }
    )


@app.get("/api/pendientes")
def api_pendientes():
    return jsonify({"pendientes": memoria.listar_pendientes()})


_clima_cache: dict = {"datos": None, "hasta": 0.0}
_clima_lock = threading.Lock()


@app.get("/api/clima")
def api_clima():
    ciudad = (request.args.get("ciudad") or CIUDAD_CLIMA).strip()
    with _clima_lock:
        ahora = time.time()
        if _clima_cache["datos"] is not None and _clima_cache["hasta"] > ahora:
            return jsonify(_clima_cache["datos"])

        try:
            resultado = herramienta_clima(ciudad)
        except Exception as e:  # noqa: BLE001 - nunca tumbar el endpoint por la red
            log.warning("Clima falló: %s", e)
            resultado = {"error": f"No pude obtener el clima ahora mismo: {e}"}
        if "error" not in resultado:
            _clima_cache["datos"] = resultado
            _clima_cache["hasta"] = ahora + CLIMA_CACHE_S
        return jsonify(resultado)


@app.post("/api/mensaje")
def api_mensaje():
    cuerpo = request.get_json(silent=True) or {}
    texto = (cuerpo.get("texto") or "").strip()
    if not texto:
        return jsonify({"error": "Falta el texto del mensaje."}), 400

    _agregar_mensaje("usuario", texto)
    _actualizar_estado("pensando")
    respuesta = cerebro.procesar(texto)
    _agregar_mensaje("jarvis", respuesta)
    _actualizar_estado("inactivo")
    return jsonify({"respuesta": respuesta})


def _navegador_para_app() -> str | None:
    """Busca Chrome o Edge para abrir el dashboard como una ventana de app
    (sin pestañas ni barra de direcciones) en vez de una pestaña normal."""
    if sys.platform == "win32":
        candidatos = [
            (os.environ.get("ProgramFiles", r"C:\Program Files"), "Google", "Chrome", "Application", "chrome.exe"),
            (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Google", "Chrome", "Application", "chrome.exe"),
            (os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
            (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Microsoft", "Edge", "Application", "msedge.exe"),
            (os.environ.get("ProgramFiles", r"C:\Program Files"), "Microsoft", "Edge", "Application", "msedge.exe"),
        ]
        for base, *partes in candidatos:
            if not base:
                continue
            ruta = os.path.join(base, *partes)
            if os.path.isfile(ruta):
                return ruta
        return None
    import shutil

    return shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("microsoft-edge")


def _abrir_navegador_diferido(url: str) -> None:
    time.sleep(1.0)
    navegador = _navegador_para_app()
    if navegador:
        try:
            popen_kw: dict = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if sys.platform == "win32":
                popen_kw["creationflags"] = subprocess.CREATE_NO_WINDOW
            subprocess.Popen(
                [navegador, f"--app={url}", "--window-size=1440,860"],
                **popen_kw,
            )
            return
        except OSError:
            pass  # si falla, cae al navegador normal de abajo
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass


def _hilo_recordatorios() -> None:
    """Revisa cada rato (sin usar el cerebro/Groq, así que no gasta nada):
    - si es la primera vez que se habla hoy, saluda y resume los pendientes.
    - si algún pendiente con hora ya se cumplió, avisa por voz una sola vez.
    """
    while True:
        try:
            _saludar()

            for pendiente in memoria.pendientes_por_avisar():
                aviso = f"Recordatorio: {pendiente['texto']}"
                _agregar_mensaje("jarvis", aviso)
                voz.hablar(aviso)
                memoria.marcar_avisado(pendiente["id"])
        except Exception:  # noqa: BLE001 - este hilo nunca debe morir
            log.exception("Fallo revisando recordatorios")
        time.sleep(INTERVALO_RECORDATORIOS_S)


def main() -> int:
    if not (os.environ.get("GROQ_API_KEY") or "").strip():
        log.warning(
            "No hay GROQ_API_KEY configurada todavía: Luna abrirá, pero avisará que "
            "le falta el cerebro hasta que pongas la clave en el archivo .env "
            "(sácala gratis en https://console.groq.com/keys)."
        )

    escucha.iniciar()
    threading.Thread(target=_hilo_recordatorios, daemon=True).start()

    url = f"http://localhost:{PUERTO}"
    log.info("Dashboard de %s en %s", NOMBRE_ASISTENTE, url)
    threading.Thread(target=_abrir_navegador_diferido, args=(url,), daemon=True).start()

    app.run(host="0.0.0.0", port=PUERTO, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
