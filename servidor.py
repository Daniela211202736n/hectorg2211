#!/usr/bin/env python3
"""
Punto de entrada único de Jarvis: levanta el dashboard (navegador) y, al mismo
tiempo, el oído (voz.py) escuchando "Jarvis" en segundo plano. Los dos caminos
—hablarle o escribirle en el dashboard— usan el mismo cerebro (cerebro.py) y
comparten el mismo historial de conversación.

Uso:
    python servidor.py

Luego abre http://localhost:8790 en el navegador (se abre solo).
"""

from __future__ import annotations

import logging
import os
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

load_dotenv(Path(__file__).resolve().parent / ".env")

from cerebro import Cerebro  # noqa: E402 - tras cargar el .env
import voz  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("servidor")

PUERTO = int(os.environ.get("JARVIS_PUERTO", "8790"))
CARPETA_DASHBOARD = Path(__file__).resolve().parent / "dashboard"

app = Flask(__name__, static_folder=None)

_estado_lock = threading.Lock()
_estado = {"estado": "inactivo", "mensajes": []}

MAX_MENSAJES_DASHBOARD = 200


def _actualizar_estado(nuevo_estado: str) -> None:
    with _estado_lock:
        _estado["estado"] = nuevo_estado


def _agregar_mensaje(rol: str, texto: str) -> None:
    with _estado_lock:
        _estado["mensajes"].append(
            {"rol": rol, "texto": texto, "hora": datetime.now().strftime("%H:%M")}
        )
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


@app.get("/api/estado")
def api_estado():
    with _estado_lock:
        return jsonify(dict(_estado))


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


def _abrir_navegador_diferido(url: str) -> None:
    time.sleep(1.0)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    if not (os.environ.get("GROQ_API_KEY") or "").strip():
        log.warning(
            "No hay GROQ_API_KEY configurada todavía: Jarvis abrirá, pero avisará que "
            "le falta el cerebro hasta que pongas la clave en el archivo .env "
            "(sácala gratis en https://console.groq.com/keys)."
        )

    escucha.iniciar()

    url = f"http://localhost:{PUERTO}"
    log.info("Dashboard de Jarvis en %s", url)
    threading.Thread(target=_abrir_navegador_diferido, args=(url,), daemon=True).start()

    app.run(host="0.0.0.0", port=PUERTO, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
