#!/usr/bin/env python3
"""
El oído y la voz de Jarvis.

Escucha el micrófono todo el tiempo, detecta cuando dices "Jarvis" (tolera
"Yarvis" y errores parecidos, porque el reconocimiento de Windows/Google a veces
transcribe mal el nombre), graba tu orden, se la pasa al cerebro (cerebro.py) y
responde en voz alta.

Reconocimiento de voz: usa la API gratuita de Google (a través de la librería
`speech_recognition`), sin necesidad de ninguna clave.

Voz de respuesta (texto a voz): por defecto usa `pyttsx3`, que es gratis, offline
y sin ningún límite de uso. Si además configuras ELEVENLABS_API_KEY y
ELEVENLABS_VOICE_ID en el .env, se usa ElevenLabs (mejor calidad) en su lugar.
"""

from __future__ import annotations

import difflib
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

log = logging.getLogger("voz")

SAMPLE_RATE = 16000  # 16kHz reconoce mejor con el servicio de voz de Google
BLOCK_MS = 30
CHANNELS = 1

# Sensibilidad para detectar que hay alguien hablando (súbelo si capta ruido de
# fondo como voz; bájalo si no te detecta hablando).
UMBRAL_VOZ = float(os.environ.get("JARVIS_UMBRAL_VOZ", "0.015"))
SILENCIO_PARA_CORTAR_S = 0.9  # cuánto silencio sostenido cierra una frase
MAX_DURACION_FRASE_S = 12.0
MIN_DURACION_FRASE_S = 0.35

IDIOMA_RECONOCIMIENTO = os.environ.get("JARVIS_IDIOMA", "es-CO")

PALABRAS_ACTIVACION = ["jarvis", "yarvis", "harvis", "jarbis", "arvis"]
UMBRAL_COINCIDENCIA = 0.72


def _similitud(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def detectar_activacion(texto: str) -> tuple[bool, str]:
    """Busca la palabra de activación al inicio de lo transcrito (tolerando errores).

    Devuelve (encontrada, resto_del_texto_sin_la_palabra) — así "Jarvis, qué hora
    es" ejecuta la orden de una vez, sin tener que llamarlo y esperar.
    """
    texto = (texto or "").strip().lower()
    if not texto:
        return False, ""
    palabras = texto.split()
    mejor = 0.0
    corte = 0
    for n in (1, 2):  # "jarvis" u "oye jarvis"
        candidata = " ".join(palabras[:n])
        for objetivo in PALABRAS_ACTIVACION:
            s = _similitud(candidata, objetivo)
            if s > mejor:
                mejor = s
                corte = n
    if mejor >= UMBRAL_COINCIDENCIA:
        resto = " ".join(palabras[corte:]).strip(" ,.:;")
        return True, resto
    return False, ""


def _tono(frecuencias: list[float], duracion_s: float = 0.12) -> None:
    t = np.linspace(0, duracion_s, int(SAMPLE_RATE * duracion_s), endpoint=False)
    onda = np.concatenate([0.2 * np.sin(2 * np.pi * f * t) for f in frecuencias]).astype(
        np.float32
    )
    try:
        sd.play(onda, SAMPLE_RATE)
        sd.wait()
    except Exception:  # noqa: BLE001 - un pitido fallido no debe tumbar nada
        pass


def sonido_te_escucho() -> None:
    _tono([660.0, 880.0])  # dos notas ascendentes


def sonido_no_escuche() -> None:
    _tono([440.0, 300.0])  # dos notas descendentes


_tts_lock = threading.Lock()


def hablar(texto: str) -> None:
    """Voz por defecto: pyttsx3 (offline, gratis, sin límite). Usa ElevenLabs en su
    lugar solo si dejaste su clave configurada (mejor calidad, opcional)."""
    texto = (texto or "").strip()
    if not texto:
        return
    log.info("Jarvis: %s", texto)

    clave_el = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    voz_el = (os.environ.get("ELEVENLABS_VOICE_ID") or "").strip()
    if clave_el and voz_el and _hablar_elevenlabs(texto, clave_el, voz_el):
        return

    with _tts_lock:
        try:
            import pyttsx3

            motor = pyttsx3.init()
            for voz in motor.getProperty("voices"):
                if "spanish" in voz.name.lower() or "español" in voz.name.lower():
                    motor.setProperty("voice", voz.id)
                    break
            motor.setProperty("rate", 175)
            motor.say(texto)
            motor.runAndWait()
        except Exception as e:  # noqa: BLE001
            log.warning("No pude hablar con pyttsx3: %s", e)


def _hablar_elevenlabs(texto: str, api_key: str, voice_id: str) -> bool:
    try:
        from elevenlabs.client import ElevenLabs

        modelo = (os.environ.get("ELEVENLABS_MODEL_ID") or "eleven_multilingual_v2").strip()
        cliente = ElevenLabs(api_key=api_key)
        chunks = cliente.text_to_speech.convert(voice_id=voice_id, text=texto, model_id=modelo)
        audio = b"".join(chunks)
        if not audio:
            return False
        pcm = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        sd.play(pcm, 24000)
        sd.wait()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("ElevenLabs falló, uso la voz local: %s", e)
        return False


def _rms(bloque: np.ndarray) -> float:
    if bloque.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(bloque.astype(np.float64) ** 2)))


def _pcm16_bytes(audio_f32: np.ndarray) -> bytes:
    recortado = np.clip(audio_f32, -1.0, 1.0)
    return (recortado * 32767).astype(np.int16).tobytes()


def transcribir(audio_f32: np.ndarray) -> str:
    """Convierte un fragmento de audio (float32 mono a SAMPLE_RATE) a texto con el
    reconocimiento gratuito de Google (sin clave; mismo servicio que el dictado de
    Windows, pero aquí filtramos mejor la palabra de activación)."""
    import speech_recognition as sr

    reconocedor = sr.Recognizer()
    datos = sr.AudioData(_pcm16_bytes(audio_f32), SAMPLE_RATE, 2)
    try:
        return reconocedor.recognize_google(datos, language=IDIOMA_RECONOCIMIENTO)
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as e:
        log.warning("El reconocimiento de voz falló (¿sin internet?): %s", e)
        return ""


class EscuchaJarvis:
    """Escucha continua del micrófono: detecta 'Jarvis', graba la orden, la pasa al
    cerebro y responde en voz alta. Corre en su propio hilo (no bloquea nada más)."""

    def __init__(
        self,
        cerebro,
        on_estado: Callable[[str], None] | None = None,
        on_mensaje: Callable[[str, str], None] | None = None,
    ) -> None:
        self._cerebro = cerebro
        self._on_estado = on_estado or (lambda estado: None)
        self._on_mensaje = on_mensaje or (lambda rol, texto: None)
        self._detener = threading.Event()
        self._hilo: threading.Thread | None = None

    def iniciar(self) -> None:
        if self._hilo and self._hilo.is_alive():
            return
        self._detener.clear()
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._hilo.start()

    def detener(self) -> None:
        self._detener.set()

    # -- internos ------------------------------------------------------------ #

    def _grabar_frase(self, blocksize: int, stream) -> np.ndarray | None:
        """Graba desde que detecta voz hasta un silencio sostenido. None si nunca
        hubo suficiente voz (para no transcribir puro silencio/ruido)."""
        bloques: list[np.ndarray] = []
        silencio_acumulado = 0.0
        hubo_voz = False

        while not self._detener.is_set():
            datos, _ = stream.read(blocksize)
            nivel = _rms(datos)
            bloques.append(datos.copy())

            if nivel >= UMBRAL_VOZ:
                hubo_voz = True
                silencio_acumulado = 0.0
            else:
                silencio_acumulado += blocksize / SAMPLE_RATE

            duracion = len(bloques) * blocksize / SAMPLE_RATE
            if hubo_voz and silencio_acumulado >= SILENCIO_PARA_CORTAR_S:
                break
            if duracion >= MAX_DURACION_FRASE_S:
                break

        if not hubo_voz:
            return None
        audio = np.concatenate(bloques, axis=0).reshape(-1)
        if audio.shape[0] / SAMPLE_RATE < MIN_DURACION_FRASE_S:
            return None
        return audio

    def _bucle(self) -> None:
        blocksize = max(1, int(SAMPLE_RATE * BLOCK_MS / 1000))
        log.info("Jarvis escuchando... di 'Jarvis' seguido de tu orden.")
        self._on_estado("inactivo")

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                blocksize=blocksize,
            ) as stream:
                while not self._detener.is_set():
                    frase = self._grabar_frase(blocksize, stream)
                    if frase is None:
                        continue

                    texto = transcribir(frase)
                    if not texto:
                        continue

                    activado, resto = detectar_activacion(texto)
                    if not activado:
                        continue

                    if resto:
                        orden = resto
                    else:
                        sonido_te_escucho()
                        self._on_estado("escuchando")
                        frase_orden = self._grabar_frase(blocksize, stream)
                        if frase_orden is None:
                            sonido_no_escuche()
                            self._on_estado("inactivo")
                            continue
                        orden = transcribir(frase_orden)
                        if not orden:
                            sonido_no_escuche()
                            self._on_estado("inactivo")
                            continue

                    self._on_estado("pensando")
                    self._on_mensaje("usuario", orden)
                    respuesta = self._cerebro.procesar(orden)
                    self._on_mensaje("jarvis", respuesta)
                    self._on_estado("hablando")
                    hablar(respuesta)
                    self._on_estado("inactivo")
        except sd.PortAudioError as e:
            log.error("Error de audio: %s", e)
            self._on_estado("error")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from cerebro import Cerebro

    jarvis_cerebro = Cerebro(hablar_fn=hablar)
    escucha = EscuchaJarvis(jarvis_cerebro)
    escucha.iniciar()
    print("Escuchando. Di 'Jarvis' seguido de tu orden. Ctrl+C para salir.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        escucha.detener()
