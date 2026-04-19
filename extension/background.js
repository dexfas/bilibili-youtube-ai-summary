const API_BASE = "http://127.0.0.1:8765";
const DEFAULT_TIMEOUT_MS = 15000;
const FAST_TIMEOUT_MS = 5000;

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.type !== "api") {
    return false;
  }

  requestLocalApi(message)
    .then((data) => sendResponse({ ok: true, data }))
    .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));

  return true;
});

async function requestLocalApi(message) {
  const method = message.method || "GET";
  const path = message.path || "/";
  const timeoutMs = Number(message.timeoutMs || (method === "GET" ? FAST_TIMEOUT_MS : DEFAULT_TIMEOUT_MS));
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const options = {
    method,
    headers: {
      "Content-Type": "application/json",
    },
    signal: controller.signal,
  };

  if (message.body !== undefined && method !== "GET") {
    options.body = JSON.stringify(message.body);
  }

  try {
    const response = await fetch(`${API_BASE}${path}`, options);
    const text = await response.text();
    if (!response.ok) {
      throw new Error(text || `HTTP ${response.status}`);
    }
    return text ? JSON.parse(text) : {};
  } catch (error) {
    if (error && error.name === "AbortError") {
      throw new Error(`本地服务请求超时：${path}`);
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
