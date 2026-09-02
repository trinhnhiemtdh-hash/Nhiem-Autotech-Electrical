// ==========================================================================
// NHIỆM AUTOTECH - LIVE CHAT SUPPORT JAVASCRIPT
// ==========================================================================

const CHAT_STORAGE_KEY = "nhiem_chat_history_v1";
const CHAT_TEASER_DISMISSED_KEY = "nhiem_chat_teaser_dismissed";

function getChatHistory() {
  try {
    const raw = localStorage.getItem(CHAT_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (e) {
    return [];
  }
}

function saveChatHistory(history) {
  try {
    localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(history));
  } catch (e) {}
}

function formatCurrentTime() {
  const d = new Date();
  const h = String(d.getHours()).padStart(2, "0");
  const m = String(d.getMinutes()).padStart(2, "0");
  return `${h}:${m}`;
}

function scrollToBottom() {
  const container = document.getElementById("chatMessages");
  if (container) {
    container.scrollTop = container.scrollHeight;
  }
}

function renderSavedMessages() {
  const container = document.getElementById("dynamicChatMessages");
  if (!container) return;
  
  const history = getChatHistory();
  container.innerHTML = "";
  
  history.forEach((msg) => {
    const isUser = msg.sender === "user";
    const msgEl = document.createElement("div");
    msgEl.className = `chat-msg ${isUser ? "user-msg" : "bot-msg"}`;
    
    if (isUser) {
      msgEl.innerHTML = `
        <div class="msg-bubble-wrap">
          <div class="msg-bubble">${escapeHtml(msg.text)}</div>
          <span class="msg-time">${msg.time || ""}</span>
        </div>
      `;
    } else {
      msgEl.innerHTML = `
        <div class="msg-avatar">
          <img src="/static/images/logo.jpg?v=2" alt="Bot" onerror="this.src='/static/images/logo.png';">
        </div>
        <div class="msg-bubble-wrap">
          <div class="msg-author">Kỹ thuật Nhiệm Autotech</div>
          <div class="msg-bubble">${msg.text}</div>
          <span class="msg-time">${msg.time || ""}</span>
        </div>
      `;
    }
    container.appendChild(msgEl);
  });
  
  scrollToBottom();
}

function escapeHtml(str) {
  if (!str) return "";
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function toggleLiveChat(forceState) {
  const win = document.getElementById("chatWindow");
  const btn = document.getElementById("chatLauncherBtn");
  const badge = document.getElementById("chatUnreadBadge");
  const teaser = document.getElementById("chatTeaser");
  
  if (!win || !btn) return;

  const isOpen = forceState !== undefined ? forceState : !win.classList.contains("active");

  if (isOpen) {
    win.classList.add("active");
    btn.classList.add("active");
    if (badge) badge.style.display = "none";
    if (teaser) teaser.style.display = "none";
    scrollToBottom();
    setTimeout(() => {
      const input = document.getElementById("chatInput");
      if (input) input.focus();
    }, 200);
  } else {
    win.classList.remove("active");
    btn.classList.remove("active");
  }
}

function dismissChatTeaser() {
  const teaser = document.getElementById("chatTeaser");
  if (teaser) {
    teaser.style.opacity = "0";
    teaser.style.transform = "translateY(8px)";
    setTimeout(() => {
      teaser.style.display = "none";
    }, 250);
  }
  sessionStorage.setItem(CHAT_TEASER_DISMISSED_KEY, "true");
}

function handleQuickChip(text) {
  const input = document.getElementById("chatInput");
  if (!input) return;

  if (text.endsWith(": ")) {
    input.value = text;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  } else {
    input.value = text;
    handleSendChat(new Event("submit"));
  }
}

function handleChatInputKeydown(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    handleSendChat(e);
  }
}

async function handleSendChat(e) {
  if (e && e.preventDefault) e.preventDefault();
  
  const input = document.getElementById("chatInput");
  const sendBtn = document.getElementById("chatSendBtn");
  const typing = document.getElementById("chatTypingIndicator");
  const container = document.getElementById("dynamicChatMessages");
  
  if (!input) return;
  const messageText = input.value.trim();
  if (!messageText) return;

  // Clear input and reset height
  input.value = "";
  input.style.height = "auto";
  if (sendBtn) sendBtn.disabled = true;

  const nowTime = formatCurrentTime();

  // 1. Add User message to DOM
  const userMsgEl = document.createElement("div");
  userMsgEl.className = "chat-msg user-msg";
  userMsgEl.innerHTML = `
    <div class="msg-bubble-wrap">
      <div class="msg-bubble">${escapeHtml(messageText)}</div>
      <span class="msg-time">${nowTime}</span>
    </div>
  `;
  container.appendChild(userMsgEl);
  scrollToBottom();

  // 2. Save User message to history
  const history = getChatHistory();
  history.push({ sender: "user", text: messageText, time: nowTime });
  saveChatHistory(history);

  // 3. Show typing indicator
  if (typing) {
    typing.style.display = "flex";
    scrollToBottom();
  }

  // 4. Send request to backend
  try {
    const res = await fetch("/api/chat-support", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        message: messageText,
        page: window.location.pathname + window.location.search,
      }),
    });

    const data = await res.json();
    
    // Slight human-like delay
    setTimeout(() => {
      if (typing) typing.style.display = "none";
      
      const replyText = (data && data.reply) 
        ? data.reply 
        : "Cảm ơn bạn! Nhiệm Autotech đã nhận được tin nhắn và sẽ phản hồi ngay qua SĐT của bạn nhé!";
      const replyTime = formatCurrentTime();

      const botMsgEl = document.createElement("div");
      botMsgEl.className = "chat-msg bot-msg";
      botMsgEl.innerHTML = `
        <div class="msg-avatar">
          <img src="/static/images/logo.jpg?v=2" alt="Bot" onerror="this.src='/static/images/logo.png';">
        </div>
        <div class="msg-bubble-wrap">
          <div class="msg-author">Kỹ thuật Nhiệm Autotech</div>
          <div class="msg-bubble">${replyText}</div>
          <span class="msg-time">${replyTime}</span>
        </div>
      `;
      container.appendChild(botMsgEl);
      scrollToBottom();

      // Save bot reply
      history.push({ sender: "bot", text: replyText, time: replyTime });
      saveChatHistory(history);
    }, 700);

  } catch (err) {
    console.error("Chat error:", err);
    if (typing) typing.style.display = "none";

    const errorMsgEl = document.createElement("div");
    errorMsgEl.className = "chat-msg bot-msg";
    errorMsgEl.innerHTML = `
      <div class="msg-avatar">
        <img src="/static/images/logo.jpg?v=2" alt="Bot" onerror="this.src='/static/images/logo.png';">
      </div>
      <div class="msg-bubble-wrap">
        <div class="msg-author">Kỹ thuật Nhiệm Autotech</div>
        <div class="msg-bubble">Tin nhắn đã được chuyển đến bộ phận kỹ thuật. Bạn cũng có thể liên hệ trực tiếp qua Zalo/Hotline: <a href="tel:0379760905" style="color:#ff7a00;font-weight:bold;">0379 760 905</a> nhé!</div>
        <span class="msg-time">${formatCurrentTime()}</span>
      </div>
    `;
    container.appendChild(errorMsgEl);
    scrollToBottom();
  } finally {
    if (sendBtn) sendBtn.disabled = false;
  }
}

// Auto-grow textarea
document.addEventListener("DOMContentLoaded", () => {
  const chatInput = document.getElementById("chatInput");
  if (chatInput) {
    chatInput.addEventListener("input", () => {
      chatInput.style.height = "auto";
      chatInput.style.height = Math.min(chatInput.scrollHeight, 100) + "px";
    });
  }

  // Set initial welcome time
  const welcomeTimeEl = document.getElementById("welcomeMsgTime");
  if (welcomeTimeEl) {
    welcomeTimeEl.textContent = formatCurrentTime();
  }

  // Render previous session messages
  renderSavedMessages();

  // Show teaser after 3 seconds if not dismissed
  if (!sessionStorage.getItem(CHAT_TEASER_DISMISSED_KEY)) {
    setTimeout(() => {
      const teaser = document.getElementById("chatTeaser");
      const win = document.getElementById("chatWindow");
      if (teaser && (!win || !win.classList.contains("active"))) {
        teaser.classList.add("show");
      }
    }, 2800);
  }
});
