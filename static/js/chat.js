// ==========================================================================
// NHIỆM AUTOTECH - LIVE CHAT SUPPORT 2-WAY JAVASCRIPT
// ==========================================================================

const CHAT_STORAGE_KEY = "nhiem_chat_history_v1";
const CHAT_SESSION_KEY = "nhiem_chat_session_id";
const CHAT_TEASER_DISMISSED_KEY = "nhiem_chat_teaser_dismissed";

let lastPollMessageId = 0;
let clientPollTimer = null;

function getChatSessionId() {
  let sess = localStorage.getItem(CHAT_SESSION_KEY);
  if (!sess) {
    sess = "sess_" + Date.now().toString(36) + "_" + Math.random().toString(36).substring(2, 9);
    localStorage.setItem(CHAT_SESSION_KEY, sess);
  }
  return sess;
}

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
    if (msg.id && msg.id > lastPollMessageId) {
      lastPollMessageId = msg.id;
    }

    const isUser = msg.sender === "user";
    const isAdmin = msg.sender === "admin";
    const msgEl = document.createElement("div");
    msgEl.className = `chat-msg ${isUser ? "user-msg" : "bot-msg"}`;
    
    if (isUser) {
      msgEl.innerHTML = `
        <div class="msg-bubble-wrap">
          <div class="msg-bubble">${escapeHtml(msg.text)}</div>
          <span class="msg-time">${msg.time || ""}</span>
        </div>
      `;
    } else if (isAdmin) {
      msgEl.innerHTML = `
        <div class="msg-avatar">
          <img src="/static/images/logo.jpg?v=2" alt="Kỹ sư" onerror="this.src='/static/images/logo.png';">
        </div>
        <div class="msg-bubble-wrap">
          <div class="msg-author" style="color:#0284c7;font-weight:700;">⚡ ${escapeHtml(msg.sender_name || "Bộ phận kỹ thuật Nhiệm Autotech")}</div>
          <div class="msg-bubble" style="background:#f0f9ff;border:1px solid #bae6fd;color:#0f172a;box-shadow:0 1px 3px rgba(0,0,0,0.04);">
            ${escapeHtml(msg.text)}
          </div>
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
    if (badge) {
      badge.style.display = "none";
      badge.textContent = "0";
    }
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
  const sessionId = getChatSessionId();

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
        session_id: sessionId
      }),
    });

    const data = await res.json();
    
    if (data && data.message_id) {
      if (data.message_id > lastPollMessageId) {
        lastPollMessageId = data.message_id;
      }
    }

    // Slight human-like delay for initial automated confirmation
    setTimeout(() => {
      if (typing) typing.style.display = "none";
      
      const userMsgCount = history.filter(h => h.sender === "user").length;
      const defaultReply = userMsgCount <= 1
        ? "Cảm ơn bạn! Nhiệm Autotech đã nhận được thông tin. Chúng tôi sẽ phản hồi hoặc liên hệ qua SĐT của bạn ngay nhé!"
        : "Cảm ơn bạn! Nhiệm Autotech đã nhận được tin nhắn. Bộ phận kỹ thuật sẽ phản hồi trực tiếp cho bạn tại đây ngay nhé!";
      const replyText = (data && data.reply) ? data.reply : defaultReply;
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

      history.push({ sender: "bot", text: replyText, time: replyTime });
      saveChatHistory(history);
    }, 600);

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

// ==========================================================================
// 2-WAY POLLING: Nhận câu trả lời từ Admin theo thời gian thực
// ==========================================================================
async function pollAdminMessages() {
  const sessionId = getChatSessionId();
  if (!sessionId) return;

  try {
    const res = await fetch(`/api/chat-poll?session_id=${encodeURIComponent(sessionId)}&after_id=${lastPollMessageId}`);
    const data = await res.json();
    
    if (data.success && data.messages && data.messages.length > 0) {
      const container = document.getElementById("dynamicChatMessages");
      const history = getChatHistory();
      const win = document.getElementById("chatWindow");
      const isOpen = win && win.classList.contains("active");

      let receivedAdminMsg = false;

      data.messages.forEach((m) => {
        if (m.id > lastPollMessageId) {
          lastPollMessageId = m.id;
        }

        // Chỉ xử lý tin nhắn phản hồi từ Admin
        if (m.sender === "admin") {
          const alreadyExists = history.some(h => h.id === m.id);
          if (!alreadyExists) {
            receivedAdminMsg = true;

            const adminItem = {
              id: m.id,
              sender: "admin",
              sender_name: m.sender_name || "Bộ phận kỹ thuật Nhiệm Autotech",
              text: m.message,
              time: m.time || formatCurrentTime()
            };
            history.push(adminItem);
            saveChatHistory(history);

            if (container) {
              const adminMsgEl = document.createElement("div");
              adminMsgEl.className = "chat-msg bot-msg";
              adminMsgEl.innerHTML = `
                <div class="msg-avatar">
                  <img src="/static/images/logo.jpg?v=2" alt="Kỹ sư" onerror="this.src='/static/images/logo.png';">
                </div>
                <div class="msg-bubble-wrap">
                  <div class="msg-author" style="color:#0284c7;font-weight:700;">⚡ ${escapeHtml(adminItem.sender_name)}</div>
                  <div class="msg-bubble" style="background:#f0f9ff;border:1px solid #bae6fd;color:#0f172a;box-shadow:0 1px 3px rgba(0,0,0,0.04);">
                    ${escapeHtml(adminItem.text)}
                  </div>
                  <span class="msg-time">${adminItem.time}</span>
                </div>
              `;
              container.appendChild(adminMsgEl);
              scrollToBottom();
            }

            // Nếu cửa sổ chat đang đóng, hiện badge + tooltip teaser
            if (!isOpen) {
              const badge = document.getElementById("chatUnreadBadge");
              if (badge) {
                const currentCount = parseInt(badge.textContent || "0");
                badge.textContent = currentCount + 1;
                badge.style.display = "flex";
              }
              const teaser = document.getElementById("chatTeaser");
              if (teaser) {
                const teaserSpan = teaser.querySelector(".chat-teaser-text span");
                if (teaserSpan) {
                  teaserSpan.innerHTML = `<strong>Bộ phận kỹ thuật vừa trả lời:</strong> "${escapeHtml(m.message.slice(0, 45))}..." ⚡`;
                }
                teaser.style.display = "flex";
                teaser.classList.add("show");
                teaser.style.opacity = "1";
              }
            }
          }
        }
      });
    }
  } catch (err) {
    // Polling failover silently
  }
}

// Auto-grow textarea & Initialization
document.addEventListener("DOMContentLoaded", () => {
  const chatInput = document.getElementById("chatInput");
  if (chatInput) {
    chatInput.addEventListener("input", () => {
      chatInput.style.height = "auto";
      chatInput.style.height = Math.min(chatInput.scrollHeight, 100) + "px";
    });
  }

  const welcomeTimeEl = document.getElementById("welcomeMsgTime");
  if (welcomeTimeEl) {
    welcomeTimeEl.textContent = formatCurrentTime();
  }

  // Khôi phục lịch sử chat cũ của khách
  renderSavedMessages();

  // Bắt đầu lắng nghe phản hồi của Admin định kỳ 3.5 giây
  pollAdminMessages();
  clientPollTimer = setInterval(pollAdminMessages, 3500);

  // Hiển thị gợi ý sau 3 giây nếu chưa bị đóng
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
