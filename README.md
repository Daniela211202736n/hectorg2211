# Luna

Un asistente de voz/texto con cerebro real (IA), memoria de pendientes entre
días, dashboard oscuro estilo IA, y un script aparte de bienvenida por doble
clap.

## 1. El cerebro y el dashboard (`servidor.py`)

Esto es lo principal: dices **"Luna"** y te responde, conversa contigo de
cualquier tema, busca en internet, te da el clima, crea informes (PDF, Word,
Excel o texto), abre páginas/apps, te pone recordatorios que sobreviven a que
reinicies el computador, pone temporizadores, lee el portapapeles y controla
el volumen (Windows). También puedes escribirle en el dashboard que se abre en
el navegador.

**El cerebro es gratis**: usa la API gratuita de [Groq](https://console.groq.com)
con modelos abiertos (GPT-OSS). No es necesario pagar nada ni poner tarjeta.

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
4. (Opcional) Cambia `JARVIS_USUARIA=Daniela` por tu nombre si quieres que te
   llame distinto, o `JARVIS_NOMBRE_ASISTENTE=Luna` si quieres otro nombre.

**Nunca subas tu archivo `.env` a GitHub** — ya está en `.gitignore` para evitarlo.

### Ejecutar

```bash
python servidor.py
```

Se abre solo `http://localhost:8790` en tu navegador con el dashboard. Desde ahí:

- Di **"Luna"** en voz alta (el micrófono siempre está escuchando) y, tras el
  pitido ascendente, di tu orden. Si dices todo junto ("Luna, qué hora es") no
  hace falta pausa ni pitido: responde directo.
- Si no reaccionó, oirás dos notas descendentes: dilo otra vez, un poco más cerca
  o más fuerte del micrófono.
- O simplemente escríbele en la caja de texto del dashboard.

### Voz de respuesta y reconocimiento de voz

Por defecto usa:
- **Reconocimiento de voz**: el servicio gratuito de Google (sin clave, sin
  límite práctico para uso personal). El piso de ruido de tu cuarto se mide
  solo al arrancar y se sigue adaptando mientras corre (ventilador, tráfico,
  etc.), en vez de usar un número fijo.
- **Voz de respuesta**: **Edge TTS**, la misma voz neuronal gratuita que usa
  "Leer en voz alta" del navegador Edge — mucho más natural que las voces
  clásicas de Windows, sin clave y sin límite conocido para uso personal.
  Cambia el acento/género con `JARVIS_VOZ_EDGE` en el `.env` (lista completa
  con `edge-tts --list-voices`, busca las que empiezan por `es-`).
- Mientras habla, el micrófono se ignora automáticamente para que no se
  escuche a sí misma ni la respuesta se corte compitiendo por el audio.
- Si no hay internet en ese momento, cae automáticamente a `pyttsx3` (offline,
  más robótica, pero nunca la deja muda).

Si quieres mejor calidad todavía, puedes (opcional, no obligatorio) configurar
[ElevenLabs](https://elevenlabs.io) en el `.env`, que tiene prioridad sobre
Edge TTS cuando está configurado:

```env
ELEVENLABS_API_KEY=tu_clave
ELEVENLABS_VOICE_ID=tu_voz
```

### Memoria: recordatorios y pendientes entre días

A diferencia de la conversación (que se olvida si reinicias el programa), los
pendientes se guardan en un archivo aparte (`Documentos/Luna/memoria.json`) y
sobreviven a que cierres el programa, reinicies el PC, o actualices el código.

- **"Recuérdame llamar al contador mañana a las 3pm"** → lo guarda, y a esa hora
  te avisa por voz automáticamente (aunque no le hayas hablado desde entonces).
- **"¿Qué tengo pendiente?"** → te lista todo lo que falta.
- **"Ya llamé al contador"** → lo marca como hecho.
- **Cada día, la primera vez que hable** (o apenas arranca, si la dejas
  corriendo siempre — ver más abajo) te saluda con un resumen de lo pendiente,
  sin que tengas que preguntar.

Esto no gasta nada del cerebro/Groq: el saludo diario y los avisos de hora
corren solos con un revisor liviano en segundo plano.

### Informes en el formato que pidas

**"Hazme un informe de gastos en Excel"**, **"pásame eso en Word"**,
**"mándamelo en PDF"** — todos se guardan en `Documentos/Luna`:

| Formato | Qué genera |
| --- | --- |
| `pdf` (por defecto) | Documento con título y texto |
| `docx` | Documento de Word con título y párrafos |
| `xlsx` | Excel: cada línea es una fila, las comas separan columnas |
| `txt` | Texto plano simple |

### Herramientas que ya tiene el cerebro

| Herramienta | Qué hace | ¿Necesita clave? |
| --- | --- | --- |
| Fecha y hora | Responde al instante, sin usar el cerebro | No |
| Clima | Clima actual de cualquier ciudad (Open-Meteo) | No |
| Buscar en internet | Resultados actuales (DuckDuckGo) | No |
| Crear documento | PDF, Word, Excel o texto en `Documentos/Luna` | No |
| Recordatorio | Guarda pendientes, avisa a la hora que digas | No |
| Listar/completar pendientes | Consulta o marca como hecho | No |
| Abrir | Páginas web, o en Windows apps/carpetas/archivos | No |
| Temporizador | Avisa por voz al cumplirse el tiempo (no persiste) | No |
| Portapapeles | Lee lo que tengas copiado | No |
| Volumen | Sube/baja/silencia (solo Windows) | No |

### Que arranque sola con Windows (para el saludo diario y los avisos)

Para que te salude con tus pendientes sin que tengas que abrir nada, Luna
necesita quedar corriendo en segundo plano. Es un proceso liviano (nada
comparable a tener muchas pestañas de Chrome abiertas), pero si prefieres que
solo corra cuando tú la abras, sáltate este paso.

1. En la carpeta del proyecto, busca `iniciar_silencioso.vbs`. Pruébalo con
   doble clic: no debería abrir ninguna ventana negra, y a los pocos segundos
   se abre el navegador con el dashboard (igual que `python servidor.py`, pero
   sin consola visible).
2. Clic derecho sobre `iniciar_silencioso.vbs` → **Crear acceso directo**.
3. Presiona **Win + R**, escribe `shell:startup` y Enter — se abre la carpeta
   de Inicio de Windows.
4. Arrastra (o corta y pega) el acceso directo del paso 2 dentro de esa carpeta.

Desde el próximo inicio de sesión de Windows, Luna arranca sola. Puedes
comprobar que está corriendo abriendo `http://localhost:8790` en cualquier
momento, incluso sin haber hecho nada más.

### Ajustes finos (`.env`, todos opcionales)

| Variable | Efecto |
| --- | --- |
| `JARVIS_NOMBRE_ASISTENTE` | Nombre de la IA y palabra de activación (por defecto `Luna`). |
| `GROQ_MODEL` / `GROQ_MODEL_RESPALDO` | Cambiar de modelo si uno está saturado o retirado. |
| `JARVIS_PUERTO` | Puerto del dashboard (por defecto `8790`). |
| `JARVIS_IDIOMA` | Idioma/región del reconocimiento de voz (por defecto `es-CO`). |
| `JARVIS_UMBRAL_VOZ` / `JARVIS_SPIKE_RATIO_VOZ` | Ajuste fino del oído si aun calibrando solo le cuesta escucharte. |
| `JARVIS_INPUT_DEVICE` | Forzar un micrófono específico (índice o nombre). |
| `JARVIS_INTERVALO_RECORDATORIOS` | Cada cuántos segundos revisa pendientes vencidos (por defecto 60). |

### Solución de problemas

- **"Falta GROQ_API_KEY"**: revisa que `.env` exista (no `.env.example`) y tenga la
  clave pegada, y reinicia `python servidor.py`.
- **Un modelo de Groq da "model_not_found"**: Groq a veces retira/renombra
  modelos. Corre `curl https://api.groq.com/openai/v1/models -H "Authorization: Bearer TU_CLAVE"`
  para ver cuáles hay activos ahora y soportan `"tools"`, y pon el que quieras
  en `GROQ_MODEL` dentro del `.env`.
- **No te escucha bien / hay que repetir mucho**: el piso de ruido se calibra
  solo, pero si sigue costando, baja `JARVIS_UMBRAL_VOZ` en el `.env` (por
  ejemplo a `0.004`) y acércate más al micrófono.
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
