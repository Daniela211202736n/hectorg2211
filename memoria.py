#!/usr/bin/env python3
"""
Memoria persistente: pendientes/recordatorios que sobreviven a que cierres y
vuelvas a abrir el programa (a diferencia del historial de conversación, que
vive solo en memoria mientras corre `servidor.py`).

Se guarda en un archivo JSON fuera de la carpeta del proyecto (en
Documentos/Luna, junto a los informes), para que actualizar el código nunca
borre tus pendientes.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any


def _carpeta_datos() -> Path:
    base = Path(os.environ.get("USERPROFILE") or Path.home())
    carpeta = base / "Documents" / "Luna"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def _ruta_memoria() -> Path:
    return _carpeta_datos() / "memoria.json"


_ESTRUCTURA_VACIA = {"pendientes": [], "ultimo_saludo": None}

_lock = threading.Lock()


def _cargar() -> dict:
    ruta = _ruta_memoria()
    if not ruta.is_file():
        return dict(_ESTRUCTURA_VACIA)
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, json.JSONDecodeError):
        return dict(_ESTRUCTURA_VACIA)
    datos.setdefault("pendientes", [])
    datos.setdefault("ultimo_saludo", None)
    return datos


def _guardar(datos: dict) -> None:
    ruta = _ruta_memoria()
    tmp = ruta.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
    tmp.replace(ruta)


def agregar_pendiente(
    texto: str, vence: str | None = None, tipo: str = "recordatorio"
) -> dict[str, Any]:
    """vence: fecha/hora en formato ISO ('2026-09-15T15:00:00') o None si no
    tiene hora fija (solo aparece en el resumen de pendientes, sin aviso de voz
    a una hora exacta)."""
    with _lock:
        datos = _cargar()
        item = {
            "id": uuid.uuid4().hex[:8],
            "texto": texto.strip(),
            "tipo": tipo,
            "creado": datetime.now().isoformat(timespec="seconds"),
            "vence": vence,
            "completado": False,
            "avisado": False,
        }
        datos["pendientes"].append(item)
        _guardar(datos)
        return item


def listar_pendientes(incluir_completados: bool = False) -> list[dict[str, Any]]:
    with _lock:
        datos = _cargar()
        pendientes = datos["pendientes"]
        if incluir_completados:
            return list(pendientes)
        return [p for p in pendientes if not p["completado"]]


def completar_pendiente(id_o_texto: str) -> dict[str, Any] | None:
    """Marca como completado por id exacto, o por coincidencia parcial del texto
    (para que se pueda decir "ya llamé al contador" sin saber el id)."""
    with _lock:
        datos = _cargar()
        objetivo = None
        for p in datos["pendientes"]:
            if p["completado"]:
                continue
            if p["id"] == id_o_texto:
                objetivo = p
                break
        if objetivo is None:
            aguja = id_o_texto.strip().lower()
            for p in datos["pendientes"]:
                if not p["completado"] and aguja and aguja in p["texto"].lower():
                    objetivo = p
                    break
        if objetivo is None:
            return None
        objetivo["completado"] = True
        _guardar(datos)
        return objetivo


def pendientes_por_avisar(ahora: datetime | None = None) -> list[dict[str, Any]]:
    """Recordatorios con hora fija ya cumplida, no completados y no avisados
    todavía (para que el hilo de recordatorios los anuncie por voz una sola
    vez)."""
    ahora = ahora or datetime.now()
    with _lock:
        datos = _cargar()
        resultado = []
        for p in datos["pendientes"]:
            if p["completado"] or p["avisado"] or not p["vence"]:
                continue
            try:
                vence = datetime.fromisoformat(p["vence"])
            except ValueError:
                continue
            if vence <= ahora:
                resultado.append(p)
        return resultado


def marcar_avisado(id_: str) -> None:
    with _lock:
        datos = _cargar()
        for p in datos["pendientes"]:
            if p["id"] == id_:
                p["avisado"] = True
                break
        _guardar(datos)


def necesita_saludo_hoy() -> bool:
    with _lock:
        datos = _cargar()
        return datos.get("ultimo_saludo") != date.today().isoformat()


def registrar_saludo_hoy() -> None:
    with _lock:
        datos = _cargar()
        datos["ultimo_saludo"] = date.today().isoformat()
        _guardar(datos)


def resumen_pendientes_hoy() -> str:
    """Texto listo para hablar/mostrar con los pendientes actuales."""
    pendientes = listar_pendientes()
    if not pendientes:
        return "No tienes pendientes guardados."
    lineas = [f"Tienes {len(pendientes)} pendiente(s):"]
    for p in pendientes:
        cuando = ""
        if p["vence"]:
            try:
                cuando = " (" + datetime.fromisoformat(p["vence"]).strftime("%d/%m %H:%M") + ")"
            except ValueError:
                pass
        lineas.append(f"- {p['texto']}{cuando}")
    return "\n".join(lineas)


if __name__ == "__main__":
    import sys

    logging_ok = True
    print("Archivo de memoria:", _ruta_memoria())
    if len(sys.argv) > 1 and sys.argv[1] == "listar":
        for p in listar_pendientes(incluir_completados=True):
            print(p)
    else:
        print(resumen_pendientes_hoy())
