# Jarvis

Un asistente de voz/texto con cerebro real (IA), dashboard oscuro estilo IA, y un
script aparte de bienvenida por doble clap.

## 1. El cerebro y el dashboard (`servidor.py`)

Esto es lo principal: dices **"Jarvis"** y te responde, conversa contigo de
cualquier tema, busca en internet, te da el clima, crea informes en PDF, abre
páginas/apps, pone temporizadores, lee el portapapeles y controla el volumen
(Windows). También puedes escribirle en el dashboard que se abre en el navegador.

**El cerebro es gratis**: usa la API gratuita de [Groq](https://console.groq.com)
con modelos Llama. No es necesario pagar nada ni poner tarjeta.

### Instalación

```bash
python -m pip install -r requirements.txt
```

### Configuración (obligatoria)

1. Copia `.env.example` como `.env` en esta misma carpeta.
2. Entra a [console.groq.com/keys](https://console.groq.com/keys), crea tu cuenta
   gratis (con Google o correo, sin tarjeta) y pulsa **Create API Key**.
3. Pega esa clave en `.env`:
   ```env
   GROQ_API_KEY=gsk_tu_clave_aqui
   ```
4. (Opcional) Cambia `JARVIS_USUARIA=Daniela` por tu nombre si quieres que Jarvis
   te llame distinto.

**Nunca subas tu archivo `.env` a GitHub** — ya está en `.gitignore` para evitarlo.

### Ejecutar

```bash
python servidor.py
```

Se abre solo `http://localhost:8790` en tu navegador con el dashboard. Desde ahí:

- Di **"Jarvis"** en voz alta (el micrófono siempre está escuchando) y, tras el
  pitido ascendente, di tu orden. Si dices todo junto ("Jarvis, qué hora es") no
  hace falta pausa ni pitido: responde directo.
- Si no reaccionó, oirás dos notas descendentes: dilo otra vez, un poco más cerca
  o más fuerte del micrófono.
- O simplemente escríbele en la caja de texto del dashboard.

### Voz de respuesta y reconocimiento de voz

Por defecto Jarvis usa:
- **Reconocimiento de voz**: el servicio gratuito de Google (sin clave, sin
  límite práctico para uso personal).
- **Voz de respuesta**: `pyttsx3`, offline, gratis y sin ningún límite (usa las
  voces instaladas en Windows).

Si quieres mejor calidad de voz y mejor precisión reconociendo lo que dices,
puedes (opcional, no obligatorio) configurar
[ElevenLabs](https://elevenlabs.io) en el `.env`:

```env
ELEVENLABS_API_KEY=tu_clave
ELEVENLABS_VOICE_ID=tu_voz
```

### Herramientas que ya tiene el cerebro

| Herramienta | Qué hace | ¿Necesita clave? |
| --- | --- | --- |
| Fecha y hora | Responde al instante, sin usar el cerebro | No |
| Clima | Clima actual de cualquier ciudad (Open-Meteo) | No |
| Buscar en internet | Resultados actuales (DuckDuckGo) | No |
| Crear PDF | Guarda informes en `Documentos/Jarvis` | No |
| Abrir | Páginas web, o en Windows apps/carpetas/archivos | No |
| Temporizador | Avisa por voz al cumplirse el tiempo | No |
| Portapapeles | Lee lo que tengas copiado | No |
| Volumen | Sube/baja/silencia (solo Windows) | No |

### Ajustes finos (`.env`, todos opcionales)

| Variable | Efecto |
| --- | --- |
| `GROQ_MODEL` / `GROQ_MODEL_RESPALDO` | Cambiar de modelo si uno está saturado. |
| `JARVIS_PUERTO` | Puerto del dashboard (por defecto `8790`). |
| `JARVIS_IDIOMA` | Idioma/región del reconocimiento de voz (por defecto `es-CO`). |
| `JARVIS_UMBRAL_VOZ` | Sensibilidad del micrófono para detectar que hablas. |
| `JARVIS_INPUT_DEVICE` | Forzar un micrófono específico (índice o nombre). |

### Solución de problemas

- **"Falta GROQ_API_KEY"**: revisa que `.env` exista (no `.env.example`) y tenga la
  clave pegada, y reinicia `python servidor.py`.
- **No te escucha bien / hay que repetir mucho**: baja `JARVIS_UMBRAL_VOZ` en el
  `.env` (por ejemplo a `0.01`) y acércate más al micrófono.
- **El límite gratis de Groq se agotó**: espera al reinicio diario, o pon tu
  propia clave de respaldo cambiando `GROQ_MODEL_RESPALDO`.

## 2. Doble clap de bienvenida (`jarvis.py`)

Script aparte, independiente del cerebro: escucha el micrófono y, al detectar un
**doble clap**, abre Spotify, ventanas de Chrome, Cursor, y dice una frase de
bienvenida fija con ElevenLabs. Ver los comentarios al inicio de `jarvis.py` para
todas las constantes de configuración (canción, apps, monitores, etc.).

```bash
python jarvis.py
```

Usa las mismas variables `ELEVENLABS_API_KEY` / `ELEVENLABS_VOICE_ID` del `.env`.
Sin ellas, sigue haciendo todo menos hablar. Ver también `CLAUDE_CODE_URL`,
`TASARADAR_URL`, `CHROME_NEW_WINDOW_WAIT_S`, `CHROME_WINDOW_WIDTH/HEIGHT` en los
comentarios del propio script.

### Tuning del oído (claps)

Edita las constantes al inicio de `jarvis.py`:

| Constante | Efecto |
| --- | --- |
| `SPIKE_RATIO` | Sube si hay falsos positivos; baja si no detecta los claps. |
| `COOLDOWN_S` | Tiempo mínimo entre dos claps dobles registrados. |
| `BLOCK_MS` | Más grande = menos CPU, un poco menos preciso en el tiempo. |
| `MIN_RMS` | Piso de volumen mínimo (ayuda en cuartos muy silenciosos). |
| `SAMPLE_RATE` | Prueba `48000` si tu dispositivo no acepta `44100`. |
