// ==UserScript==
// @name         Bilibili 视频转文字助手
// @namespace    local.auto-tran-video
// @version      0.1.1
// @description  在 B 站页面批量选择视频，提交到本地 auto-tran-video 服务转文字、翻译和总结。
// @match        http://*.bilibili.com/*
// @match        https://*.bilibili.com/*
// @include      https://www.bilibili.com
// @include      https://www.bilibili.com/
// @match        https://www.bilibili.com/*
// @match        https://search.bilibili.com/*
// @match        https://space.bilibili.com/*
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// ==/UserScript==

(function () {
  "use strict";

  console.info("[auto-tran-video] userscript loaded", location.href);

  const API_BASE = "http://127.0.0.1:8765";
  const state = {
    videos: [],
    selected: new Set(),
    lastJobId: null,
    pollTimer: null,
    serviceOk: false,
  };

  const presetMap = {
    fastest: { asr_model_size: "tiny", asr_device: "cpu", asr_compute_type: "int8", asr_beam_size: 1 },
    balanced: { asr_model_size: "small", asr_device: "cpu", asr_compute_type: "int8", asr_beam_size: 1 },
    quality: { asr_model_size: "medium", asr_device: "cpu", asr_compute_type: "int8", asr_beam_size: 3 },
  };

  const panel = document.createElement("section");
  panel.id = "atv-panel";
  panel.innerHTML = `
    <style>
      #atv-panel {
        position: fixed;
        top: 88px;
        right: 18px;
        width: 360px;
        max-height: calc(100vh - 110px);
        z-index: 999999;
        background: #ffffff;
        color: #202124;
        border: 1px solid #d7dce2;
        border-radius: 8px;
        box-shadow: 0 14px 36px rgba(15, 23, 42, 0.18);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        font-size: 13px;
        overflow: hidden;
      }
      #atv-panel.atv-collapsed { width: 168px; }
      #atv-panel.atv-collapsed .atv-body { display: none; }
      #atv-panel * { box-sizing: border-box; }
      .atv-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        padding: 10px 12px;
        background: #f6f8fa;
        border-bottom: 1px solid #d7dce2;
      }
      .atv-title { font-weight: 700; }
      .atv-status { font-size: 12px; color: #6b7280; }
      .atv-body {
        max-height: calc(100vh - 160px);
        overflow: auto;
        padding: 10px 12px 12px;
      }
      .atv-row { display: flex; gap: 8px; margin-bottom: 8px; }
      .atv-row > * { flex: 1; }
      .atv-field { margin-bottom: 8px; }
      .atv-field label { display: block; margin-bottom: 4px; color: #374151; font-size: 12px; }
      .atv-field input,
      .atv-field select {
        width: 100%;
        min-height: 30px;
        border: 1px solid #cfd6df;
        border-radius: 6px;
        padding: 5px 7px;
        background: #fff;
        color: #111827;
      }
      .atv-check-row {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 8px;
      }
      .atv-check-row input { width: auto; }
      .atv-btn {
        border: 1px solid #cfd6df;
        border-radius: 6px;
        padding: 6px 8px;
        background: #fff;
        color: #111827;
        cursor: pointer;
      }
      .atv-btn:hover { background: #f1f5f9; }
      .atv-primary {
        background: #00a1d6;
        border-color: #00a1d6;
        color: #fff;
      }
      .atv-primary:hover { background: #008fc1; }
      .atv-small { flex: 0 0 auto; padding: 4px 7px; }
      .atv-list {
        display: grid;
        gap: 6px;
        max-height: 220px;
        overflow: auto;
        margin: 8px 0 10px;
        padding: 0;
      }
      .atv-item {
        display: grid;
        grid-template-columns: auto 1fr;
        gap: 7px;
        align-items: start;
        padding: 7px;
        border: 1px solid #e3e8ef;
        border-radius: 6px;
        background: #fbfcfe;
      }
      .atv-item input { margin-top: 2px; }
      .atv-item-title {
        line-height: 1.35;
        word-break: break-word;
      }
      .atv-item-url {
        margin-top: 3px;
        font-size: 11px;
        color: #6b7280;
        word-break: break-all;
      }
      .atv-log {
        min-height: 48px;
        max-height: 130px;
        overflow: auto;
        white-space: pre-wrap;
        border: 1px solid #e3e8ef;
        border-radius: 6px;
        padding: 7px;
        background: #fbfcfe;
        color: #374151;
      }
    </style>
    <div class="atv-head">
      <div>
        <div class="atv-title">视频转文字助手</div>
        <div class="atv-status" id="atv-service">检查服务中...</div>
      </div>
      <button class="atv-btn atv-small" id="atv-toggle">收起</button>
    </div>
    <div class="atv-body">
      <div class="atv-row">
        <button class="atv-btn" id="atv-scan">扫描当前页</button>
        <button class="atv-btn" id="atv-select-all">全选</button>
        <button class="atv-btn" id="atv-clear">清空</button>
      </div>

      <div class="atv-field">
        <label>任务模式</label>
        <select id="atv-workflow">
          <option value="transcribe">中文视频转写</option>
          <option value="summarize" selected>转写并总结</option>
          <option value="english_cn">英文视频中文化</option>
        </select>
      </div>

      <div class="atv-row">
        <div class="atv-field">
          <label>速度/质量</label>
          <select id="atv-preset">
            <option value="fastest">最快</option>
            <option value="balanced" selected>均衡</option>
            <option value="quality">高质量</option>
          </select>
        </div>
        <div class="atv-field">
          <label>切块分钟</label>
          <input id="atv-chunk" type="number" min="1" max="120" step="1" value="10">
        </div>
      </div>

      <div class="atv-row">
        <div class="atv-field">
          <label>ASR 模型</label>
          <input id="atv-asr-model" value="small">
        </div>
        <div class="atv-field">
          <label>Beam</label>
          <input id="atv-beam" type="number" min="1" max="10" step="1" value="1">
        </div>
      </div>

      <div class="atv-row">
        <div class="atv-field">
          <label>ASR 设备</label>
          <select id="atv-device">
            <option value="cpu" selected>cpu</option>
            <option value="cuda">cuda</option>
            <option value="auto">auto</option>
          </select>
        </div>
        <div class="atv-field">
          <label>计算类型</label>
          <select id="atv-compute">
            <option value="int8" selected>int8</option>
            <option value="float16">float16</option>
            <option value="float32">float32</option>
          </select>
        </div>
      </div>

      <div class="atv-field">
        <label>总结模型</label>
        <input id="atv-summary-model" value="qwen-summary:1.5b">
      </div>
      <div class="atv-field">
        <label>翻译模型</label>
        <input id="atv-translate-model" value="qwen3:8b">
      </div>
      <div class="atv-row">
        <div class="atv-field">
          <label>输出目录</label>
          <input id="atv-output-dir" value="output">
        </div>
        <div class="atv-field">
          <label>缓存目录</label>
          <input id="atv-cache-dir" value="cache">
        </div>
      </div>
      <div class="atv-field">
        <label>cookies.txt 路径</label>
        <input id="atv-cookies" placeholder="可选，例如 D:\\cookies.txt">
      </div>
      <div class="atv-field">
        <label>从浏览器读取 cookies</label>
        <input id="atv-cookies-browser" placeholder="可选：chrome / edge / firefox">
      </div>
      <label class="atv-check-row">
        <input id="atv-force" type="checkbox">
        <span>强制重跑已有结果</span>
      </label>
      <label class="atv-check-row">
        <input id="atv-clean-cache" type="checkbox">
        <span>成功后清理音频缓存</span>
      </label>

      <div class="atv-row">
        <button class="atv-btn atv-primary" id="atv-submit">提交选中视频</button>
        <button class="atv-btn" id="atv-refresh">刷新任务</button>
      </div>
      <div id="atv-count">尚未扫描视频</div>
      <div class="atv-list" id="atv-list"></div>
      <div class="atv-log" id="atv-log">先启动本地服务：.\\.venv\\Scripts\\python.exe main.py serve</div>
    </div>
  `;

  mountPanel();

  const $ = (id) => panel.querySelector(id);
  const serviceEl = $("#atv-service");
  const listEl = $("#atv-list");
  const countEl = $("#atv-count");
  const logEl = $("#atv-log");

  $("#atv-toggle").addEventListener("click", () => {
    panel.classList.toggle("atv-collapsed");
    $("#atv-toggle").textContent = panel.classList.contains("atv-collapsed") ? "展开" : "收起";
  });
  $("#atv-scan").addEventListener("click", scanVideos);
  $("#atv-select-all").addEventListener("click", () => {
    state.selected = new Set(state.videos.map((video) => video.url));
    renderVideos();
  });
  $("#atv-clear").addEventListener("click", () => {
    state.selected.clear();
    renderVideos();
  });
  $("#atv-submit").addEventListener("click", submitSelected);
  $("#atv-refresh").addEventListener("click", refreshLastJob);
  $("#atv-preset").addEventListener("change", applyPreset);

  checkService();
  setTimeout(scanVideos, 800);
  setInterval(checkService, 10000);
  setInterval(ensurePanelMounted, 3000);

  function mountPanel() {
    const target = document.body || document.documentElement;
    if (!target) {
      setTimeout(mountPanel, 200);
      return;
    }
    if (!document.getElementById("atv-panel")) {
      target.appendChild(panel);
      console.info("[auto-tran-video] panel mounted");
    }
  }

  function ensurePanelMounted() {
    if (!document.getElementById("atv-panel")) {
      mountPanel();
    }
  }

  function applyPreset() {
    const preset = presetMap[$("#atv-preset").value] || presetMap.balanced;
    $("#atv-asr-model").value = preset.asr_model_size;
    $("#atv-device").value = preset.asr_device;
    $("#atv-compute").value = preset.asr_compute_type;
    $("#atv-beam").value = String(preset.asr_beam_size);
  }

  function scanVideos() {
    const found = new Map();
    collectCurrentVideo(found);
    document.querySelectorAll('a[href*="/video/BV"]').forEach((link) => {
      if (!isVisible(link)) return;
      const video = parseVideoLink(link.href, link);
      if (video) found.set(video.url, video);
    });
    state.videos = Array.from(found.values());
    state.selected = new Set(state.videos.map((video) => video.url));
    renderVideos();
    log(`扫描到 ${state.videos.length} 个视频。`);
  }

  function collectCurrentVideo(found) {
    const match = location.href.match(/BV[0-9A-Za-z]+/);
    if (!match) return;
    const bvid = match[0];
    const titleEl = document.querySelector("h1") || document.querySelector("[title]");
    const title = cleanTitle((titleEl && (titleEl.getAttribute("title") || titleEl.textContent)) || document.title || bvid);
    found.set(`https://www.bilibili.com/video/${bvid}/`, {
      bvid,
      title,
      url: `https://www.bilibili.com/video/${bvid}/`,
    });
  }

  function parseVideoLink(href, link) {
    const match = href.match(/BV[0-9A-Za-z]+/);
    if (!match) return null;
    const bvid = match[0];
    const title = cleanTitle(
      link.getAttribute("title") ||
        link.getAttribute("aria-label") ||
        link.textContent ||
        findNearbyText(link) ||
        bvid
    );
    return {
      bvid,
      title,
      url: `https://www.bilibili.com/video/${bvid}/`,
    };
  }

  function findNearbyText(link) {
    let node = link.parentElement;
    for (let i = 0; i < 3 && node; i += 1) {
      const text = cleanTitle(node.textContent || "");
      if (text && text.length > 4) return text;
      node = node.parentElement;
    }
    return "";
  }

  function cleanTitle(value) {
    return String(value).replace(/\s+/g, " ").trim().slice(0, 120);
  }

  function isVisible(element) {
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function renderVideos() {
    countEl.textContent = `已扫描 ${state.videos.length} 个视频，已选 ${state.selected.size} 个。`;
    listEl.innerHTML = "";
    state.videos.forEach((video) => {
      const row = document.createElement("label");
      row.className = "atv-item";
      row.innerHTML = `
        <input type="checkbox">
        <div>
          <div class="atv-item-title"></div>
          <div class="atv-item-url"></div>
        </div>
      `;
      const checkbox = row.querySelector("input");
      checkbox.checked = state.selected.has(video.url);
      row.querySelector(".atv-item-title").textContent = video.title || video.bvid;
      row.querySelector(".atv-item-url").textContent = video.url;
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) state.selected.add(video.url);
        else state.selected.delete(video.url);
        countEl.textContent = `已扫描 ${state.videos.length} 个视频，已选 ${state.selected.size} 个。`;
      });
      listEl.appendChild(row);
    });
  }

  async function checkService() {
    try {
      await api("GET", "/api/health");
      state.serviceOk = true;
      serviceEl.textContent = "本地服务已连接";
      serviceEl.style.color = "#15803d";
      await loadSettings();
    } catch (error) {
      state.serviceOk = false;
      serviceEl.textContent = "本地服务未连接";
      serviceEl.style.color = "#b91c1c";
    }
  }

  async function loadSettings() {
    if (loadSettings.loaded) return;
    const response = await api("GET", "/api/settings");
    const settings = response.settings || {};
    setValue("#atv-workflow", settings.workflow);
    setValue("#atv-preset", settings.speed_preset);
    setValue("#atv-asr-model", settings.asr_model_size);
    setValue("#atv-device", settings.asr_device);
    setValue("#atv-compute", settings.asr_compute_type);
    setValue("#atv-beam", settings.asr_beam_size);
    setValue("#atv-chunk", settings.chunk_minutes);
    setValue("#atv-summary-model", settings.summary_model);
    setValue("#atv-translate-model", settings.translate_model);
    setValue("#atv-output-dir", settings.output_dir);
    setValue("#atv-cache-dir", settings.cache_dir);
    setValue("#atv-cookies", settings.cookies || "");
    setValue("#atv-cookies-browser", settings.cookies_from_browser || "");
    $("#atv-force").checked = Boolean(settings.force);
    $("#atv-clean-cache").checked = Boolean(settings.clean_cache);
    loadSettings.loaded = true;
  }

  function setValue(selector, value) {
    if (value === undefined || value === null) return;
    const element = $(selector);
    if (element) element.value = String(value);
  }

  function collectSettings() {
    const workflow = $("#atv-workflow").value;
    return {
      workflow,
      speed_preset: $("#atv-preset").value,
      asr_model_size: $("#atv-asr-model").value.trim(),
      asr_device: $("#atv-device").value,
      asr_compute_type: $("#atv-compute").value,
      asr_language: workflow === "english_cn" ? "en" : "zh",
      asr_beam_size: Number($("#atv-beam").value || 1),
      chunk_minutes: Number($("#atv-chunk").value || 10),
      force: $("#atv-force").checked,
      clean_cache: $("#atv-clean-cache").checked,
      summary_model: $("#atv-summary-model").value.trim(),
      translate_model: $("#atv-translate-model").value.trim(),
      translate_to: workflow === "english_cn" ? "Chinese" : null,
      cookies: $("#atv-cookies").value.trim() || null,
      cookies_from_browser: $("#atv-cookies-browser").value.trim() || null,
      output_dir: $("#atv-output-dir").value.trim() || "output",
      cache_dir: $("#atv-cache-dir").value.trim() || "cache",
    };
  }

  async function submitSelected() {
    const urls = Array.from(state.selected);
    if (!urls.length) {
      log("没有选中的视频。");
      return;
    }
    try {
      const settings = collectSettings();
      await api("POST", "/api/settings", settings);
      const response = await api("POST", "/api/jobs", { urls, settings });
      state.lastJobId = response.job.id;
      log(`已提交任务 ${state.lastJobId}，共 ${response.job.counts.total} 个视频。`);
      startPolling();
    } catch (error) {
      log(`提交失败：${error.message}`);
    }
  }

  function startPolling() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    refreshLastJob();
    state.pollTimer = setInterval(refreshLastJob, 3000);
  }

  async function refreshLastJob() {
    if (!state.lastJobId) {
      log("还没有提交过任务。");
      return;
    }
    try {
      const response = await api("GET", `/api/jobs/${state.lastJobId}`);
      const job = response.job;
      const counts = job.counts || {};
      const lines = [
        `任务 ${job.id}: ${job.status}`,
        `总数 ${counts.total || 0}，完成 ${counts.done || 0}，失败 ${counts.failed || 0}，取消 ${counts.cancelled || 0}`,
      ];
      job.items.forEach((item, index) => {
        lines.push(`${index + 1}. ${item.status} ${item.title || item.url}${item.error ? ` - ${item.error}` : ""}`);
      });
      log(lines.join("\n"));
      if (["done", "failed", "cancelled"].includes(job.status) && state.pollTimer) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
      }
    } catch (error) {
      log(`刷新失败：${error.message}`);
    }
  }

  function api(method, path, body) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method,
        url: `${API_BASE}${path}`,
        headers: { "Content-Type": "application/json" },
        data: body === undefined ? undefined : JSON.stringify(body),
        timeout: 8000,
        onload: (response) => {
          if (response.status < 200 || response.status >= 300) {
            reject(new Error(response.responseText || `HTTP ${response.status}`));
            return;
          }
          try {
            resolve(response.responseText ? JSON.parse(response.responseText) : {});
          } catch (error) {
            reject(error);
          }
        },
        onerror: () => reject(new Error("无法连接本地服务，请先运行 main.py serve")),
        ontimeout: () => reject(new Error("连接本地服务超时")),
      });
    });
  }

  function log(message) {
    logEl.textContent = message;
  }
})();
