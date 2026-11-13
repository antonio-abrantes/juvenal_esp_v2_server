const WS_URL = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`;
let ws = null;
let currentAgentMsg = null;
let reconnectTimer = null;

const conversation = document.getElementById("conversation");
const statusBadge  = document.getElementById("status-badge");
const partialText  = document.getElementById("partial-text");
const listenAnim   = document.getElementById("listening-anim");
const wakeAnim     = document.getElementById("wake-word-anim");
const connDot      = document.getElementById("connection-dot");

const STATE_LABELS = {
  idle:       { text: 'Aguardando "juvehnal"', cls: "idle" },
  wake:       { text: "Wake word detetada! 🔔", cls: "wake" },
  listening:  { text: "Ouvindo... 🎙️", cls: "listening" },
  processing: { text: "Processando... 🧠", cls: "processing" },
  speaking:   { text: "Respondendo... 🔊", cls: "speaking" },
};

function setUIState(state) {
  const s = STATE_LABELS[state] || STATE_LABELS.idle;
  statusBadge.textContent = s.text;
  statusBadge.className = `badge ${s.cls}`;

  wakeAnim.classList.toggle("hidden", state !== "wake");
  listenAnim.classList.toggle("hidden", state !== "listening");

  if (state !== "listening") partialText.textContent = "";
}

function addMessage(role, text) {
  // Remover empty state se presente
  const empty = conversation.querySelector(".empty-state");
  if (empty) empty.remove();

  const div = document.createElement("div");
  div.className = `message ${role}`;
  div.textContent = text;
  conversation.appendChild(div);
  while (conversation.children.length > 100) conversation.firstElementChild.remove();
  conversation.scrollTop = conversation.scrollHeight;
  return div;
}

function connect() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    connDot.className = "dot connected";
    clearTimeout(reconnectTimer);
    console.log("✅ WebSocket conectado");
  };

  ws.onclose = () => {
    connDot.className = "dot disconnected";
    console.log("🔌 WebSocket desconectado. Reconectando em 3s...");
    reconnectTimer = setTimeout(connect, 3000);
  };

  ws.onerror = (e) => console.error("WebSocket erro:", e);

  ws.onmessage = ({ data }) => {
    const msg = JSON.parse(data);
    handleEvent(msg);
  };
}

function handleEvent(msg) {
  switch (msg.event) {
    case "connected":
      setUIState(msg.state || "idle");
      break;

    case "wake_word":
      setUIState("wake");
      setTimeout(() => setUIState("listening"), 800);
      break;

    case "state_change":
      setUIState(msg.state);
      break;

    case "transcript_partial":
      partialText.textContent = `"${msg.text}"`;
      break;

    case "transcript_final":
      partialText.textContent = "";
      break;

    case "user_message":
      addMessage("user", msg.text);
      break;

    case "assistant_start":
      currentAgentMsg = addMessage("agent", "");
      currentAgentMsg.classList.add("streaming");
      break;

    case "assistant_token":
      if (currentAgentMsg) {
        currentAgentMsg.textContent += msg.token;
        conversation.scrollTop = conversation.scrollHeight;
      }
      break;

    case "assistant_end":
      if (currentAgentMsg) {
        currentAgentMsg.classList.remove("streaming");
        if (msg.status === "failed") currentAgentMsg.textContent += " [Resposta incompleta]";
        currentAgentMsg = null;
      }
      break;

    case "busy":
      addMessage("system", "Juvenal está ocupado. Aguarde a interação atual terminar.");
      break;

    case "error":
      addMessage("system", `Não foi possível concluir a operação: ${msg.code || "erro"}.`);
      break;

    case "delivery_result":
      if (msg.status === "failed") {
        addMessage("system", `Falha na entrega (${msg.error}). Nenhum reenvio automático foi feito.`);
      }
      break;

    case "history_cleared":
      currentAgentMsg = null;
      conversation.innerHTML = `
        <div class="empty-state">
          <p>💬 Diga <strong>"juvehnal"</strong> para começar</p>
          <small>Histórico limpo</small>
        </div>`;
      break;
  }
}

function clearHistory() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ action: "clear_history" }));
  }
}

// Iniciar
connect();
