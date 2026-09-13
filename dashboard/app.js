const chat = document.getElementById("chat");
const nucleo = document.getElementById("nucleo");
const estadoTexto = document.getElementById("estado-texto");
const reloj = document.getElementById("reloj");
const form = document.getElementById("form-entrada");
const campoTexto = document.getElementById("campo-texto");

const ESTADOS = {
  inactivo: "EN LÍNEA",
  escuchando: "TE ESCUCHO",
  pensando: "PENSANDO…",
  hablando: "RESPONDIENDO",
  error: "ERROR DE AUDIO",
};

let mensajesMostrados = 1; // el saludo inicial ya está en el HTML

function agregarMensaje(rol, texto) {
  const div = document.createElement("div");
  div.className = `mensaje ${rol === "usuario" ? "usuario" : "jarvis"}`;
  const etiqueta = document.createElement("span");
  etiqueta.className = "etiqueta";
  etiqueta.textContent = rol === "usuario" ? "TÚ" : "JARVIS";
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
    agregarMensaje("jarvis", "No pude conectarme con el servidor de Jarvis.");
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

function actualizarReloj() {
  const ahora = new Date();
  reloj.textContent = ahora.toLocaleTimeString("es-CO", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

actualizarReloj();
setInterval(actualizarReloj, 1000 * 15);
consultarEstado();
setInterval(consultarEstado, 1000);
