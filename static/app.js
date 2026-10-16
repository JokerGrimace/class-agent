// --- State ---
let currentSessionId = null;
let isStreaming = false;
let currentStreamAbortController = null;
const currentAuthToken = getAuthToken();
const CURRENT_SESSION_STORAGE_KEY = "openclaw_current_session_id";
const PENDING_WORKFLOW_RESUME_STORAGE_KEY = "openclaw_pending_workflow_resume";

// --- DOM ---
const $sidebar = document.getElementById("sidebar");
const $chatMain = document.getElementById("chat-main");
const $messages = document.getElementById("messages");
const $input = document.getElementById("input");
const $sendBtn = document.getElementById("send-btn");
const $sessionList = document.getElementById("session-list");
const $banner = document.getElementById("banner-area");

// --- Init ---
marked.setOptions({
  highlight: (code, lang) => {
    const language = hljs.getLanguage(lang) ? lang : "plaintext";
    return hljs.highlight(code, { language }).value;
  },
  breaks: true,
});

document.getElementById("toggle-sidebar").onclick = () => {
  $sidebar.classList.toggle("collapsed");
  $chatMain.classList.toggle("full");
};

document.getElementById("nav-new-chat").onclick = newSession;
document.getElementById("new-chat-btn").onclick = newSession;

$input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

$input.addEventListener("input", () => {
  $input.style.height = "auto";
  $input.style.height = Math.min($input.scrollHeight, 200) + "px";
  $sendBtn.disabled = !$input.value.trim() || isStreaming;
});

$sendBtn.onclick = sendMessage;

// --- Session Management ---

async function loadSessions() {
  try {
    const res = await fetch("/v1/sessions", {
      headers: getAuthHeaders(),
    });
    const data = await res.json();
    const sessions = data.sessions || [];
    renderSessionList(sessions);
    if (!currentSessionId) {
      const rememberedSessionId = getRememberedSessionId();
      if (rememberedSessionId && sessions.some((item) => item.session_id === rememberedSessionId)) {
        await switchToSession(rememberedSessionId);
      } else if (rememberedSessionId) {
        clearRememberedSessionId();
      }
    }
  } catch { /* ok */ }
}

function renderSessionList(sessions) {
  $sessionList.innerHTML = "";
  sessions.forEach((s) => {
    const el = document.createElement("div");
    el.className = "session-item" + (s.session_id === currentSessionId ? " active" : "");
    el.dataset.id = s.session_id;

    el.innerHTML = `
      <div class="title">${s.session_id.slice(0, 12)}</div>
      <div class="preview">${escapeHtml(s.preview || "")}</div>
      <div class="time">${formatTime(s.updated_at)}</div>
      <button class="delete-btn">&times;</button>
    `;

    el.addEventListener("click", (e) => {
      if (e.target.classList.contains("delete-btn")) return;
      switchToSession(s.session_id);
    });

    el.querySelector(".delete-btn").addEventListener("click", async (e) => {
      e.stopPropagation();
      await fetch(`/v1/sessions/${s.session_id}`, {
        method: "DELETE",
        headers: getAuthHeaders(),
      });
      clearPendingWorkflowResume(s.session_id);
      if (currentSessionId === s.session_id) await newSession();
      else loadSessions();
    });

    $sessionList.appendChild(el);
  });
}

async function newSession() {
  try {
    const res = await fetch("/v1/sessions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...getAuthHeaders(),
      },
    });
    const data = await res.json();
    await switchToSession(data.session_id);
    await loadSessions();
  } catch { showBanner("创建会话失败", "error"); }
}

async function switchToSession(id) {
  currentSessionId = id;
  rememberCurrentSessionId(id);
  await refreshSessionView(id);
  await checkWorkflowResume(id);
  scrollToBottom();
}

async function refreshSessionView(id) {
  $messages.innerHTML = '<div class="welcome"><div class="welcome-logo">OpenClaw</div><p>你的 AI 助手</p></div>';
  $banner.innerHTML = "";

  document.querySelectorAll(".session-item").forEach((el) => el.classList.remove("active"));
  const target = document.querySelector(`.session-item[data-id="${id}"]`);
  if (target) target.classList.add("active");

  try {
    const res = await fetch(`/v1/sessions/${id}`, {
      headers: getAuthHeaders(),
    });
    const session = await res.json();
    $messages.innerHTML = "";
    (session.messages || []).forEach((m) => renderMessage(m, false));
    if (!session.messages || session.messages.length === 0) {
      $messages.innerHTML = '<div class="welcome"><div class="welcome-logo">OpenClaw</div><p>你的 AI 助手</p></div>';
    }
  } catch { /* ok */ }
  scrollToBottom();
}

// --- Send ---

async function sendMessage() {
  const text = $input.value.trim();
  if (!text || isStreaming) return;

  if (!currentSessionId) await newSession();
  if (!currentSessionId) return;
  const sendingSessionId = currentSessionId;

  renderMessage({ role: "user", content: text }, false);
  $input.value = "";
  $input.style.height = "auto";
  $sendBtn.disabled = true;
  isStreaming = true;
  scrollToBottom();

  const aiRow = createMessageRow("ai", true);
  let fullText = "";
  let reasoningEl = null;
  let citations = [];
  const pendingWorkflowResume = getPendingWorkflowResumeForSession(currentSessionId);
  const shouldResumeWorkflow = Boolean(pendingWorkflowResume);
  currentStreamAbortController = new AbortController();

  try {
    const res = await fetch("/v1/agent/send", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...getAuthHeaders(),
      },
      signal: currentStreamAbortController.signal,
      body: JSON.stringify({
        session_id: currentSessionId,
        message: text,
        stream: true,
        resume_workflow: shouldResumeWorkflow,
        workflow_id: pendingWorkflowResume ? pendingWorkflowResume.workflow_id : null,
      }),
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    if (shouldResumeWorkflow) {
      clearPendingWorkflowResume(currentSessionId, pendingWorkflowResume.workflow_id);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, "\n");

      while (true) {
        const idx = buffer.indexOf("\n\n");
        if (idx === -1) break;

        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const event = parseSSE(chunk);
        if (!event) continue;

        switch (event.type) {
          case "reasoning":
            if (!reasoningEl) reasoningEl = createReasoningBox(aiRow);
            reasoningEl.querySelector(".content").textContent += event.data;
            scrollToBottom();
            break;
          case "reasoning_end":
            if (reasoningEl) reasoningEl.open = false;
            break;
          case "text":
            fullText += event.data;
            renderMessageContent(aiRow, fullText);
            scrollToBottom();
            break;
          case "tool_call": {
            const payload = parseToolCallPayload(event.data);
            aiRow.querySelector(".bubble").appendChild(
              createToolCard(payload.tool_name || "", payload.tool_call_id || "")
            );
            break;
          }
          case "tool_result": {
            const payload = parseToolResultPayload(event.data);
            removeToolCard(aiRow, payload.tool_call_id || "");
            citations = mergeCitations(citations, extractCitations(payload.meta || {}));
            renderCitationToggle(aiRow, citations);
            break;
          }
          case "permission_asked": {
            const payload = parsePermissionAskedPayload(event.data);
            await handlePermissionAsked(payload);
            break;
          }
          case "warning":
            showBanner(event.data, "warning");
            break;
          case "done":
            clearToolCards(aiRow);
            break;
        }
      }
    }
  } catch (err) {
    const errorMessage = String(err && err.message ? err.message : err || "");
    if (errorMessage.includes("Permission request expired")) {
      showBanner("Permission request expired. You can send a new message now.", "warning");
      return;
    }
    showBanner("请求失败: " + errorMessage, "error");
  } finally {
    currentStreamAbortController = null;
    isStreaming = false;
    $sendBtn.disabled = !$input.value.trim();
    aiRow.classList.remove("streaming");
    clearToolCards(aiRow);
    renderCitationToggle(aiRow, citations);
    if (currentSessionId === sendingSessionId) {
      await refreshSessionView(sendingSessionId);
      await loadSessions();
    }
  }
}

function parseSSE(chunk) {
  const lines = chunk.split("\n");
  let type = "message";
  let data = "";
  let hasFields = false;
  for (const line of lines) {
    if (!line.length) continue;
    if (line.startsWith("event: ")) {
      type = line.slice(7).trim();
      hasFields = true;
    } else if (line.startsWith("data: ")) {
      if (data) data += "\n";
      data += line.slice(6);
      hasFields = true;
    }
  }
  return hasFields ? { type, data } : null;
}

function parseToolResultPayload(raw) {
  try {
    return JSON.parse(raw);
  } catch {
    return { meta: {} };
  }
}

function parseToolCallPayload(raw) {
  try {
    return JSON.parse(raw);
  } catch {
    const [tool_name, tool_call_id] = String(raw || "").split(":");
    return { tool_name, tool_call_id };
  }
}

function parsePermissionAskedPayload(raw) {
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function extractCitations(meta) {
  const extracted = [];
  const citations = Array.isArray(meta.citations) ? meta.citations : [];

  citations.forEach((item) => {
    if (!item || !item.url) return;
    extracted.push({
      title: item.title || item.url,
      url: item.url,
    });
  });

  if (!extracted.length) {
    const fallbackUrl = meta.final_url || meta.url;
    if (fallbackUrl) {
      extracted.push({
        title: meta.title || fallbackUrl,
        url: fallbackUrl,
      });
    }
  }

  return extracted;
}

function getRenderableCitations(citations) {
  return (Array.isArray(citations) ? citations : []).filter(
    (item) => item && typeof item.url === "string" && item.url.trim()
  );
}

function mergeCitations(existing, incoming) {
  const merged = [...existing];
  const seen = new Set(existing.map((item) => item.url));

  incoming.forEach((item) => {
    if (!item || !item.url || seen.has(item.url)) return;
    seen.add(item.url);
    merged.push(item);
  });

  return merged;
}

function removeToolCard(row, toolCallId) {
  const cards = Array.from(row.querySelectorAll(".tool-card"));
  const target = toolCallId
    ? cards.find((card) => card.dataset.toolCallId === toolCallId)
    : null;
  (target || cards[cards.length - 1])?.remove();
}

function clearToolCards(row) {
  row.querySelectorAll(".tool-card").forEach((card) => card.remove());
}

// --- Render ---

function createMessageRow(role, streaming) {
  const row = document.createElement("div");
  row.className = "msg-row " + (role === "user" ? "user" : "ai");
  if (streaming) row.classList.add("streaming");

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  const content = document.createElement("div");
  content.className = "content";
  bubble.appendChild(content);
  row.appendChild(bubble);

  $messages.querySelector(".welcome")?.remove();
  $messages.appendChild(row);
  return row;
}

function renderMessageContent(row, text) {
  row.querySelector(".content").innerHTML = marked.parse(text || "");
}

function renderMessage(msg, animate) {
  const row = createMessageRow(msg.role, false);
  renderMessageContent(row, msg.content || "");
  if (msg.role === "assistant") {
    renderCitationToggle(row, getRenderableCitations(msg.citations));
  }
  if (animate) row.style.animation = "fadeIn 0.3s ease";
}

function createToolCard(name, id) {
  const el = document.createElement("details");
  el.className = "tool-card";
  if (id) el.dataset.toolCallId = id;
  el.open = true;
  el.innerHTML = `<summary>🔧 ${escapeHtml(name)}</summary><div class="tool-result">等待结果...</div>`;
  return el;
}

function renderCitationToggle(row, citations) {
  row.querySelector(".citation-toggle")?.remove();
  const renderableCitations = getRenderableCitations(citations);
  if (!renderableCitations.length) return;

  const bubble = row.querySelector(".bubble");
  const el = document.createElement("details");
  el.className = "citation-toggle";
  el.innerHTML = `
    <summary title="查看来源链接">🔗</summary>
    <div class="citation-menu"></div>
  `;

  const menu = el.querySelector(".citation-menu");
  renderableCitations.forEach((item) => {
    const link = document.createElement("a");
    link.className = "citation-link";
    link.href = item.url;
    link.target = "_blank";
    link.rel = "noreferrer noopener";
    link.innerHTML = `链接: ${escapeHtml(item.title || item.url)}`;
    menu.appendChild(link);
  });

  bubble.appendChild(el);
}

function createReasoningBox(row) {
  const el = document.createElement("details");
  el.className = "reasoning-box";
  el.open = true;
  el.innerHTML = "<summary>思考中...</summary><div class='content'></div>";
  const bubble = row.querySelector(".bubble");
  bubble.insertBefore(el, bubble.firstChild);
  return el;
}

// --- Banner ---

function showBanner(text, type) {
  const el = document.createElement("div");
  el.className = "banner " + type;
  el.textContent = text;
  $banner.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.remove(), 300); }, 4000);
}

// --- Helpers ---

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str || "";
  return div.innerHTML;
}

function formatTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const diff = now - d;
  if (diff < 60000) return "刚刚";
  if (diff < 3600000) return Math.floor(diff / 60000) + "分钟前";
  if (d.toDateString() === now.toDateString())
    return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    $messages.scrollTop = $messages.scrollHeight;
  });
}

function getAuthToken() {
  return window.localStorage.getItem("openclaw_auth_token") || "";
}

function getAuthHeaders() {
  return currentAuthToken
    ? { Authorization: `Bearer ${currentAuthToken}` }
    : {};
}

function rememberCurrentSessionId(sessionId) {
  if (!sessionId) return;
  window.localStorage.setItem(CURRENT_SESSION_STORAGE_KEY, sessionId);
}

function getRememberedSessionId() {
  return window.localStorage.getItem(CURRENT_SESSION_STORAGE_KEY) || "";
}

function clearRememberedSessionId() {
  window.localStorage.removeItem(CURRENT_SESSION_STORAGE_KEY);
}

function loadPendingWorkflowResume() {
  try {
    const raw = window.localStorage.getItem(PENDING_WORKFLOW_RESUME_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    if (typeof parsed.session_id !== "string" || !parsed.session_id) return null;
    if (typeof parsed.workflow_id !== "string" || !parsed.workflow_id) return null;
    return parsed;
  } catch {
    return null;
  }
}

function savePendingWorkflowResume(state) {
  if (!state || typeof state !== "object") return;
  window.localStorage.setItem(
    PENDING_WORKFLOW_RESUME_STORAGE_KEY,
    JSON.stringify(state)
  );
}

function getPendingWorkflowResumeForSession(sessionId) {
  const state = loadPendingWorkflowResume();
  if (!state) return null;
  if (state.session_id !== sessionId) return null;
  return state;
}

function clearPendingWorkflowResume(sessionId, workflowId) {
  const state = loadPendingWorkflowResume();
  if (!state) return;
  if (sessionId && state.session_id !== sessionId) return;
  if (workflowId && state.workflow_id !== workflowId) return;
  window.localStorage.removeItem(PENDING_WORKFLOW_RESUME_STORAGE_KEY);
}

async function checkWorkflowResume(sessionId) {
  if (!sessionId) return;

  try {
    const res = await fetch(
      `/v1/agent/workflow/check?session_id=${encodeURIComponent(sessionId)}`,
      {
        headers: getAuthHeaders(),
      }
    );
    if (!res.ok) return;

    const data = await res.json();
    if (!data || data.has_incomplete_workflow !== true) {
      clearPendingWorkflowResume(sessionId);
      return;
    }

    const existingPendingResume = getPendingWorkflowResumeForSession(sessionId);
    if (
      existingPendingResume &&
      existingPendingResume.workflow_id === data.workflow_id
    ) {
      return;
    }

    const promptText = buildWorkflowResumePrompt(data);
    const shouldResume = window.confirm(promptText);

    if (!shouldResume) {
      await operateWorkflow("cancel", data.workflow_id);
      clearPendingWorkflowResume(sessionId, data.workflow_id);
      showBanner("Cancelled the unfinished workflow.", "warning");
      return;
    }

    await operateWorkflow("resume", data.workflow_id);
    savePendingWorkflowResume({
      session_id: sessionId,
      workflow_id: data.workflow_id,
      status: data.status,
      current_step_id: data.current_step_id,
    });
    showBanner(buildWorkflowResumeBanner(data.status), "warning");
  } catch {
    showBanner("Failed to check the unfinished workflow.", "error");
  }
}

async function operateWorkflow(action, workflowId) {
  const res = await fetch("/v1/agent/workflow/operate", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...getAuthHeaders(),
    },
    body: JSON.stringify({
      action,
      workflow_id: workflowId,
    }),
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
}

async function handlePermissionAsked(payload) {
  const requestId = payload.request_id || "";
  if (!requestId) {
    showBanner("Received an invalid permission request.", "error");
    return;
  }

  const shouldApprove = window.confirm(buildPermissionPrompt(payload));
  const action = shouldApprove ? "once" : "reject";

  try {
    await replyPermissionRequest(requestId, action);
    showBanner(
      shouldApprove ? "Permission approved. Continuing..." : "Permission rejected.",
      shouldApprove ? "warning" : "warning"
    );
  } catch (err) {
    const errorMessage = String(err && err.message ? err.message : err || "");
    showBanner("Failed to reply to the permission request: " + errorMessage, "error");
    if (errorMessage.includes("HTTP 404") || errorMessage.includes("Permission request not found")) {
      abortCurrentStream("Permission request expired");
    }
  }
}

async function replyPermissionRequest(requestId, action) {
  const res = await fetch(`/v1/agent/permissions/${encodeURIComponent(requestId)}/reply`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...getAuthHeaders(),
    },
    body: JSON.stringify({
      action,
      edited_fields: {},
      comment: "",
    }),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string" && body.detail) {
        detail = body.detail;
      }
    } catch {
      try {
        const text = await res.text();
        if (text) detail = text;
      } catch {
        // ignore
      }
    }
    throw new Error(detail);
  }
  return res.json();
}

function abortCurrentStream(reason) {
  if (currentStreamAbortController) {
    currentStreamAbortController.abort();
  }
  currentStreamAbortController = null;
  isStreaming = false;
  $sendBtn.disabled = !$input.value.trim();
  showBanner(reason || "Current stream stopped.", "warning");
}

function buildPermissionPrompt(payload) {
  const parts = ["A permission request was raised."];
  if (payload.tool_name) {
    parts.push(`tool_name: ${payload.tool_name}`);
  }
  if (payload.tool_action) {
    parts.push(`tool_action: ${payload.tool_action}`);
  }
  if (payload.summary) {
    parts.push(`summary: ${payload.summary}`);
  }
  const visibleArguments = payload.visible_arguments || {};
  const visibleEntries = Object.entries(visibleArguments);
  if (visibleEntries.length) {
    parts.push("visible_arguments:");
    visibleEntries.forEach(([key, value]) => {
      parts.push(`- ${key}: ${String(value)}`);
    });
  }
  parts.push("Approve this operation?");
  return parts.join("\n");
}

function buildWorkflowResumePrompt(data) {
  const parts = [
    "An unfinished workflow was found in this session.",
    `workflow_id: ${data.workflow_id || ""}`,
  ];
  if (data.workflow_name) {
    parts.push(`workflow_name: ${data.workflow_name}`);
  }
  if (data.status) {
    parts.push(`status: ${data.status}`);
  }
  if (data.current_step_id) {
    parts.push(`current_step_id: ${data.current_step_id}`);
  }
  parts.push("Resume it now?");
  return parts.join("\n");
}

function buildWorkflowResumeBanner(status) {
  if (status === "waiting_user_input") {
    return "Workflow resume is ready. Send your next message as the required input.";
  }
  return "Workflow resume is ready. Send your next message to continue the unfinished workflow.";
}

// --- Init ---
loadSessions();
$input.dispatchEvent(new Event("input"));
