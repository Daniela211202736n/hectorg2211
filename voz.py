#!/usr/bin/env python3
"""
El oído y la voz de Luna.

Escucha el micrófono todo el tiempo, detecta cuando dices el nombre de activación
(por defecto "Luna", configurable con JARVIS_NOMBRE_ASISTENTE en el .env) tolerando
errores de transcripción del reconocimiento de voz, graba tu orden, se la pasa al
cerebro (cerebro.py) y responde en voz alta.

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

# El piso de ruido (ventilador, tráfico, etc.) se mide solo al arrancar y se
# adapta mientras corre — igual que el detector de claps de jarvis.py — en vez
# de usar un número fijo que funciona bien en un cuarto y mal en otro.
# Nota: si en pruebas reales sigue sin escucharte, baja este número primero.
SPIKE_RATIO_VOZ = float(os.environ.get("JARVIS_SPIKE_RATIO_VOZ", "2.0"))
# Piso absoluto mínimo: nunca consideramos "voz" algo más flojito que esto,
# aunque el cuarto esté en silencio total. Súbelo si te detecta ruido como voz;
# bájalo si no te detecta hablando.
MIN_RMS_VOZ = float(os.environ.get("JARVIS_UMBRAL_VOZ", "0.0025"))
# Techo del piso de ruido: si la calibración inicial cae justo en un momento
# ruidoso (por ejemplo un sonido de Windows), esto evita que el umbral quede
# clavado tan alto que ya no te detecte hablar en el resto de la sesión.
PISO_RUIDO_MAX = 0.01
NOISE_FLOOR_ALPHA = 0.97  # más cerca de 1 = el piso se adapta más lento
QUIET_GATE_MULT = 1.6  # solo actualiza el piso cuando el audio está por debajo de esto
CALIBRACION_S = 1.2  # segundos de silencio inicial para medir el ruido de fondo
SILENCIO_PARA_CORTAR_S = 1.3  # cuánto silencio sostenido cierra una frase
MAX_DURACION_FRASE_S = 12.0
MIN_DURACION_FRASE_S = 0.35

IDIOMA_RECONOCIMIENTO = os.environ.get("JARVIS_IDIOMA", "es-CO")

# Palabra de activación: sigue el mismo nombre configurado para el cerebro
# (JARVIS_NOMBRE_ASISTENTE en el .env). Por defecto "Luna".
_NOMBRE_ASISTENTE = (os.environ.get("JARVIS_NOMBRE_ASISTENTE") or "Luna").strip().lower()
PALABRAS_ACTIVACION = [_NOMBRE_ASISTENTE]
UMBRAL_COINCIDENCIA = 0.72

# Palabras comunes del español que NUNCA cuentan como activación, aunque su
# parecido con el nombre elegido pase el umbral de coincidencia (p. ej. "una"
# se parece muchísimo a "Luna" para cualquier comparador de texto — es
# literalmente la misma palabra sin la "l" — y "una" aparece todo el tiempo en
# frases normales: "necesito una pregunta", "dame un momento"...).
PALABRAS_EXCLUIDAS = {
    "una", "uno", "un", "la", "el", "lo", "los", "las", "no", "sí", "si", "ya",
    "hay", "y", "de", "que", "a", "en",
}


def _similitud(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def detectar_activacion(texto: str) -> tuple[bool, str]:
    """Busca la palabra de activación entre las dos primeras palabras de lo
    transcrito (tolerando errores de transcripción), por ejemplo "Luna, qué hora
    es" u "oye Luna, qué hora es".

    Devuelve (encontrada, resto_del_texto_sin_la_palabra) — si venía todo junto,
    ejecuta la orden de una vez, sin tener que llamarla y esperar.
    """
    texto = (texto or "").strip().lower()
    if not texto:
        return False, ""
    palabras = texto.split()
    # Compara palabra por palabra (no la frase completa concatenada): así una
    # palabra de activación corta como "Luna" no pierde puntaje solo por venir
    # después de "oye" u otra palabra suelta.
    for i, palabra in enumerate(palabras[:2]):
        if palabra in PALABRAS_EXCLUIDAS:
            continue
        for objetivo in PALABRAS_ACTIVACION:
            if _similitud(palabra, objetivo) >= UMBRAL_COINCIDENCIA:
                resto = " ".join(palabras[i + 1 :]).strip(" ,.:;")
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

# Voz neuronal gratis de Microsoft Edge (sin clave, sin límite conocido para uso
# personal). Mucho más natural que las voces SAPI de Windows que usa pyttsx3.
# Cambia esta en el .env con JARVIS_VOZ_EDGE si prefieres otra voz/acento:
# lista completa con `edge-tts --list-voices` (busca las que empiezan por "es-").
VOZ_EDGE_POR_DEFECTO = "es-CO-SalomeNeural"

# Se activa mientras Luna está hablando, para que el micrófono no se escuche a
# sí misma (evita que la respuesta se "corte" por competir con la grabación y
# evita que se autoactive con su propia voz).
_hablando = threading.Event()


def esta_hablando() -> bool:
    return _hablando.is_set()


def hablar(texto: str) -> None:
    """Voz por defecto: Edge TTS (neuronal, gratis, natural). Si configuraste
    ElevenLabs se usa esa (mejor calidad todavía); si no hay internet, cae a
    pyttsx3 (offline, más robótica, pero nunca la deja muda)."""
    texto = (texto or "").strip()
    if not texto:
        return
    log.info("Luna: %s", texto)

    _hablando.set()
    try:
        clave_el = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
        voz_el = (os.environ.get("ELEVENLABS_VOICE_ID") or "").strip()
        if clave_el and voz_el and _hablar_elevenlabs(texto, clave_el, voz_el):
            return
        if _hablar_edge_tts(texto):
            return
        _hablar_pyttsx3(texto)
    finally:
        _hablando.clear()


def _hablar_edge_tts(texto: str) -> bool:
    import asyncio
    import tempfile

    try:
        import edge_tts
        from playsound import playsound
    except ImportError:
        return False

    voz = (os.environ.get("JARVIS_VOZ_EDGE") or VOZ_EDGE_POR_DEFECTO).strip()
    ruta_tmp = Path(tempfile.gettempdir()) / f"jarvis_voz_{os.getpid()}_{int(time.time() * 1000)}.mp3"
    try:
        async def _generar() -> None:
            comunicador = edge_tts.Communicate(texto, voz)
            await comunicador.save(str(ruta_tmp))

        with _tts_lock:
            asyncio.run(_generar())
            if not ruta_tmp.is_file() or ruta_tmp.stat().st_size == 0:
                return False
            playsound(str(ruta_tmp))
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Edge TTS falló (¿sin internet?), uso la voz local: %s", e)
        return False
    finally:
        ruta_tmp.unlink(missing_ok=True)


def _hablar_pyttsx3(texto: str) -> None:
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
    """Escucha continua del micrófono: detecta 'Luna', graba la orden, la pasa al
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
        self._piso_ruido = 1e-4  # se recalibra al iniciar _bucle
        self._nivel_mic = 0.0  # nivel de audio en vivo, para mostrar en el dashboard
        self._activaciones = 0  # cuántas veces se activó por voz en esta sesión

    def nivel_mic(self) -> float:
        return self._nivel_mic

    def umbral_actual(self) -> float:
        return max(self._piso_ruido * SPIKE_RATIO_VOZ, MIN_RMS_VOZ)

    def activaciones(self) -> int:
        return self._activaciones

    def iniciar(self) -> None:
        if self._hilo and self._hilo.is_alive():
            return
        self._detener.clear()
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._hilo.start()

    def detener(self) -> None:
        self._detener.set()

    # -- internos ------------------------------------------------------------ #

    def _calibrar_piso_ruido(self, blocksize: int, stream) -> None:
        """Mide el ruido de fondo (ventilador, tráfico, etc.) un momento antes de
        empezar a escuchar en serio, para no asumir que el cuarto está en
        silencio total. Usa un percentil bajo (no el promedio) para que un
        sonido pasajero durante la calibración (una notificación, una tos) no
        deje el umbral pegado demasiado alto para el resto de la sesión."""
        muestras: list[float] = []
        bloques_necesarios = max(1, int(CALIBRACION_S * SAMPLE_RATE / blocksize))
        for _ in range(bloques_necesarios):
            datos, _ = stream.read(blocksize)
            muestras.append(_rms(datos))
        piso = float(np.percentile(muestras, 35)) if muestras else 1e-4
        self._piso_ruido = min(max(piso, 1e-5), PISO_RUIDO_MAX)
        log.info("Piso de ruido calibrado: %.5f", self._piso_ruido)

    def _grabar_frase(self, blocksize: int, stream) -> np.ndarray | None:
        """Graba desde que detecta voz hasta un silencio sostenido. None si nunca
        hubo suficiente voz (para no transcribir puro silencio/ruido). El umbral
        de "hay voz" se adapta solo al ruido de fondo mientras escucha."""
        bloques: list[np.ndarray] = []
        silencio_acumulado = 0.0
        hubo_voz = False

        while not self._detener.is_set():
            datos, _ = stream.read(blocksize)
            if esta_hablando():
                # Ignora el audio mientras Luna habla: si no, se escucharía a sí
                # misma (retroalimentación) y podría auto-activarse o cortar su
                # propia respuesta al competir por el dispositivo de audio.
                continue
            nivel = _rms(datos)
            self._nivel_mic = nivel

            umbral = max(self._piso_ruido * SPIKE_RATIO_VOZ, MIN_RMS_VOZ)
            quiet_gate = self._piso_ruido * QUIET_GATE_MULT
            if nivel < quiet_gate:
                self._piso_ruido = (
                    NOISE_FLOOR_ALPHA * self._piso_ruido + (1.0 - NOISE_FLOOR_ALPHA) * nivel
                )
                self._piso_ruido = min(max(self._piso_ruido, 1e-6), PISO_RUIDO_MAX)

            bloques.append(datos.copy())

            if nivel >= umbral:
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
        self._on_estado("inactivo")

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                blocksize=blocksize,
            ) as stream:
                self._calibrar_piso_ruido(blocksize, stream)
                log.info("Luna escuchando... di 'Luna' seguido de tu orden.")
                while not self._detener.is_set():
                    try:
                        self._ciclo_de_escucha(blocksize, stream)
                    except sd.PortAudioError:
                        raise  # error real de dispositivo: que lo maneje el except de abajo
                    except Exception as e:  # noqa: BLE001 - un fallo no debe dejar a Luna muda
                        log.exception("Error inesperado escuchando, sigo intentando: %s", e)
                        self._on_estado("inactivo")
        except sd.PortAudioError as e:
            log.error("Error de audio: %s", e)
            self._on_estado("error")

    def _ciclo_de_escucha(self, blocksize: int, stream) -> None:
        """Una vuelta completa: espera la palabra de activación, graba la orden,
        la procesa y responde. Separado de _bucle para poder envolverlo en un
        try/except amplio sin que un error deje muerto el hilo entero."""
        frase = self._grabar_frase(blocksize, stream)
        if frase is None:
            return

        texto = transcribir(frase)
        if not texto:
            return

        activado, resto = detectar_activacion(texto)
        if not activado:
            return
        self._activaciones += 1

        if resto:
            orden = resto
        else:
            sonido_te_escucho()
            self._on_estado("escuchando")
            frase_orden = self._grabar_frase(blocksize, stream)
            if frase_orden is None:
                sonido_no_escuche()
                self._on_estado("inactivo")
                return
            orden = transcribir(frase_orden)
            if not orden:
                sonido_no_escuche()
                self._on_estado("inactivo")
                return

        self._on_estado("pensando")
        self._on_mensaje("usuario", orden)
        respuesta = self._cerebro.procesar(orden)
        self._on_mensaje("jarvis", respuesta)
        self._on_estado("hablando")
        hablar(respuesta)
        self._on_estado("inactivo")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from cerebro import Cerebro

    jarvis_cerebro = Cerebro(hablar_fn=hablar)
    escucha = EscuchaJarvis(jarvis_cerebro)
    escucha.iniciar()
    print("Escuchando. Di 'Luna' seguido de tu orden. Ctrl+C para salir.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        escucha.detener()
