const chat = document.getElementById("chat");
const nucleo = document.getElementById("nucleo");
const estadoTexto = document.getElementById("estado-texto");
const reloj = document.getElementById("reloj");
const fechaEl = document.getElementById("fecha");
const form = document.getElementById("form-entrada");
const campoTexto = document.getElementById("campo-texto");
const indicadores = document.getElementById("indicadores");
const saludoNombre = document.getElementById("saludo-nombre");

const ESTADOS = {
  inactivo: "EN LÍNEA",
  escuchando: "TE ESCUCHO",
  pensando: "PENSANDO…",
  hablando: "RESPONDIENDO",
  error: "ERROR DE AUDIO",
};

const MICROFONO_TEXTO = {
  inactivo: "activo",
  escuchando: "escuchando",
  pensando: "pensando",
  hablando: "en pausa",
  error: "sin micrófono",
};

let mensajesMostrados = 1; // el saludo inicial ya está en el HTML
let nombreAsistente = "LUNA";

// --------------------------------------------------------------------- //
// Estado (chat + núcleo)
// --------------------------------------------------------------------- //

function agregarMensaje(rol, texto) {
  const div = document.createElement("div");
  div.className = `mensaje ${rol === "usuario" ? "usuario" : "jarvis"}`;
  const etiqueta = document.createElement("span");
  etiqueta.className = "etiqueta";
  etiqueta.textContent = rol === "usuario" ? "TÚ" : nombreAsistente;
  const parrafo = document.createElement("p");
  parrafo.textContent = texto;
  div.appendChild(etiqueta);
  div.appendChild(parrafo);
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

function aplicarEstado(estado) {
  nucleo.className = "nucleo" + (estado && estado !== "inactivo" ? ` ${estado}` : "");
  estadoTexto.textContent = ESTADOS[estado] || "EN LÍNEA";

  const indMic = indicadores.querySelector('[data-clave="microfono"]');
  indMic.querySelector("b").textContent = MICROFONO_TEXTO[estado] || "activo";
  indMic.classList.toggle("alerta", estado === "error");
  indMic.classList.toggle("activo", estado !== "error");
}

async function consultarEstado() {
  try {
    const resp = await fetch("/api/estado");
    if (!resp.ok) return;
    const datos = await resp.json();
    aplicarEstado(datos.estado);

    const mensajes = datos.mensajes || [];
    for (let i = mensajesMostrados; i < mensajes.length; i++) {
      agregarMensaje(mensajes[i].rol, mensajes[i].texto);
    }
    mensajesMostrados = Math.max(mensajesMostrados, mensajes.length);
  } catch (e) {
    // Si el servidor no responde, lo intentamos de nuevo en el próximo ciclo.
  }
}

async function enviarMensaje(texto) {
  agregarMensaje("usuario", texto);
  mensajesMostrados += 1; // ya lo mostramos: no lo dupliques en el próximo sondeo
  aplicarEstado("pensando");

  try {
    await fetch("/api/mensaje", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texto }),
    });
  } catch (e) {
    agregarMensaje("jarvis", `No pude conectarme con el servidor de ${nombreAsistente}.`);
    mensajesMostrados += 1;
  }
  consultarEstado();
}

form.addEventListener("submit", (evento) => {
  evento.preventDefault();
  const texto = campoTexto.value.trim();
  if (!texto) return;
  campoTexto.value = "";
  enviarMensaje(texto);
});

// --------------------------------------------------------------------- //
// Reloj
// --------------------------------------------------------------------- //

function actualizarReloj() {
  const ahora = new Date();
  reloj.textContent = ahora.toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit" });
  fechaEl.textContent = ahora.toLocaleDateString("es-CO", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

// --------------------------------------------------------------------- //
// Configuración (nombre, modelo, voz)
// --------------------------------------------------------------------- //

async function cargarConfig() {
  try {
    const resp = await fetch("/api/config");
    if (!resp.ok) return;
    const cfg = await resp.json();
    nombreAsistente = (cfg.nombre_asistente || "LUNA").toUpperCase();
    document.title = nombreAsistente;
    document.getElementById("marca-texto").textContent = nombreAsistente.split("").join(" ");
    saludoNombre.textContent = cfg.usuaria || "";
    campoTexto.placeholder = `Escríbele a ${cfg.nombre_asistente}… (o di «${cfg.nombre_asistente}, …»)`;

    const indVoz = indicadores.querySelector('[data-clave="voz"]');
    indVoz.querySelector("b").textContent = cfg.voz || "—";
    indVoz.classList.add("activo");

    const indIa = indicadores.querySelector('[data-clave="ia"]');
    const modeloCorto = (cfg.modelo || "").split("/").pop() || cfg.modelo;
    indIa.querySelector("b").textContent = modeloCorto || "—";
    indIa.classList.add("activo");

    indicadores.querySelector('[data-clave="nucleo"]').classList.add("activo");
  } catch (e) {
    // Sin config, el dashboard igual funciona con las etiquetas por defecto.
  }
}

// --------------------------------------------------------------------- //
// Sistema (CPU/RAM/disco/red/batería)
// --------------------------------------------------------------------- //

const CIRCUNFERENCIA = 264;
let redAnterior = null; // { bajada, subida, ts }

function pintarAnillo(clave, porcentaje) {
  const nodo = document.querySelector(`.anillo-stat[data-clave="${clave}"]`);
  if (!nodo) return;
  const pct = Math.max(0, Math.min(100, porcentaje || 0));
  const progreso = nodo.querySelector(".progreso");
  progreso.style.strokeDashoffset = String(CIRCUNFERENCIA * (1 - pct / 100));
  nodo.querySelector(".anillo-valor b").textContent = Math.round(pct);
  nodo.dataset.nivel = pct >= 85 ? "alto" : pct >= 60 ? "medio" : "";
}

function formatoKBs(bytesPorSegundo) {
  if (bytesPorSegundo < 1024) return `${bytesPorSegundo.toFixed(0)} B/s`;
  return `${(bytesPorSegundo / 1024).toFixed(1)} KB/s`;
}

async function consultarSistema() {
  try {
    const resp = await fetch("/api/sistema");
    if (!resp.ok) return;
    const s = await resp.json();

    pintarAnillo("cpu", s.cpu_pct);
    pintarAnillo("ram", s.ram_pct);
    pintarAnillo("disco", s.disco_pct);

    document.getElementById("dato-nucleos").textContent = s.nucleos;
    document.getElementById("dato-memoria").textContent = `${s.ram_usado_gb} / ${s.ram_total_gb} GB`;
    document.getElementById("dato-disco").textContent = `${s.disco_libre_gb} GB`;

    const horas = Math.floor(s.encendido_min / 60);
    const minutos = s.encendido_min % 60;
    document.getElementById("dato-encendido").textContent =
      horas > 0 ? `${horas} h ${minutos} min` : `${minutos} min`;

    const panelBateria = document.getElementById("panel-bateria");
    if (s.bateria_pct === null || s.bateria_pct === undefined) {
      panelBateria.hidden = true;
    } else {
      panelBateria.hidden = false;
      document.getElementById("bateria-relleno").style.width = `${s.bateria_pct}%`;
      document.getElementById("bateria-texto").textContent =
        `${Math.round(s.bateria_pct)}%${s.bateria_cargando ? " · cargando" : ""}`;
    }

    const ahora = Date.now();
    if (redAnterior) {
      const segundos = (ahora - redAnterior.ts) / 1000;
      if (segundos > 0) {
        const bajada = Math.max(0, (s.red_bajada_bytes - redAnterior.bajada) / segundos);
        const subida = Math.max(0, (s.red_subida_bytes - redAnterior.subida) / segundos);
        document.getElementById("red-bajada").textContent = formatoKBs(bajada);
        document.getElementById("red-subida").textContent = formatoKBs(subida);
      }
    }
    redAnterior = { bajada: s.red_bajada_bytes, subida: s.red_subida_bytes, ts: ahora };
  } catch (e) {
    // Reintenta en el próximo ciclo.
  }
}

// --------------------------------------------------------------------- //
// Clima
// --------------------------------------------------------------------- //

const ICONOS_CLIMA = {
  0: "☀️", 1: "🌤️", 2: "⛅", 3: "☁️", 45: "🌫️", 48: "🌫️",
  51: "🌦️", 53: "🌦️", 55: "🌧️", 61: "🌧️", 63: "🌧️", 65: "🌧️",
  71: "🌨️", 73: "🌨️", 75: "❄️", 80: "🌦️", 81: "🌧️", 82: "⛈️",
  95: "⛈️", 96: "⛈️", 99: "⛈️",
};

async function consultarClima() {
  try {
    const resp = await fetch("/api/clima");
    const datos = await resp.json();
    if (datos.error) {
      document.getElementById("clima-desc").textContent = "No disponible";
      return;
    }
    const d = datos.datos || {};
    document.getElementById("clima-lugar").textContent = datos.lugar || "";
    document.getElementById("clima-icono").textContent = ICONOS_CLIMA[d.weather_code] || "⛅";
    document.getElementById("clima-temp").textContent = `${Math.round(d.temperature_2m ?? 0)}°`;
    document.getElementById("clima-desc").textContent = datos.texto || "";
    document.getElementById("clima-humedad").textContent = `${d.relative_humidity_2m ?? "—"}%`;
    document.getElementById("clima-viento").textContent = `${d.wind_speed_10m ?? "—"} km/h`;
  } catch (e) {
    document.getElementById("clima-desc").textContent = "No disponible";
  }
}

// --------------------------------------------------------------------- //
// Pendientes
// --------------------------------------------------------------------- //

async function consultarPendientes() {
  try {
    const resp = await fetch("/api/pendientes");
    if (!resp.ok) return;
    const datos = await resp.json();
    const lista = document.getElementById("lista-pendientes");
    const pendientes = datos.pendientes || [];
    lista.innerHTML = "";
    if (pendientes.length === 0) {
      lista.innerHTML = '<li class="vacio">Sin pendientes por ahora.</li>';
      return;
    }
    for (const p of pendientes) {
      const li = document.createElement("li");
      let html = p.texto;
      if (p.vence) {
        const f = new Date(p.vence);
        const cuando = f.toLocaleString("es-CO", {
          day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
        });
        html += `<span class="cuando">${cuando}</span>`;
      }
      li.innerHTML = html;
      lista.appendChild(li);
    }
  } catch (e) {
    // Reintenta en el próximo ciclo.
  }
}

// --------------------------------------------------------------------- //
// Arranque
// --------------------------------------------------------------------- //

actualizarReloj();
setInterval(actualizarReloj, 1000 * 15);

cargarConfig();
consultarEstado();
setInterval(consultarEstado, 1000);

consultarSistema();
setInterval(consultarSistema, 3000);

consultarClima();
setInterval(consultarClima, 1000 * 60 * 10);

consultarPendientes();
setInterval(consultarPendientes, 1000 * 20);
