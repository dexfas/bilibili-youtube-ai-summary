(() => {
  "use strict";

  if (window.__AUTO_TRAN_VIDEO_EXTENSION_LOADED__) return;
  window.__AUTO_TRAN_VIDEO_EXTENSION_LOADED__ = true;

  const isTopFrame = window.top === window;
  const PANEL_ID = "atv-ext-panel";
  const MESSAGE_SOURCE = "auto-tran-video-extension";

  const presetMap = {
    fastest: { asr_model_size: "tiny", asr_device: "cpu", asr_compute_type: "int8", asr_beam_size: 1 },
    balanced: { asr_model_size: "small", asr_device: "cpu", asr_compute_type: "int8", asr_beam_size: 1 },
    quality: { asr_model_size: "medium", asr_device: "cpu", asr_compute_type: "int8", asr_beam_size: 3 },
  };

  if (!isTopFrame) {
    window.addEventListener("message", (event) => {
      if (!event.data || event.data.source !== MESSAGE_SOURCE || event.data.type !== "scan-request") return;
      postFrameVideos(event.data);
    });
    setTimeout(() => postFrameVideos({}), 1200);
    return;
  }

  const state = {
    videos: [],
    selected: new Set(),
    itemByUrl: new Map(),
    lastJobId: null,
    lastBatchDir: null,
    pollTimer: null,
    modelsLoaded: false,
    userClearedSelection: false,
    clearUntil: 0,
    autoScanTimer: null,
    lastPageKey: makePageKey(),
    lastHref: normalizedPageHref(),
    scanToken: 0,
    pageEpoch: 0,
    titleMode: "source",
    titleTranslations: new Map(),
    youtubeNavigatingUntil: 0,
    longRequestActive: false,
    longRequestGraceUntil: 0,
  };

  const panel = document.createElement("section");
  panel.id = PANEL_ID;
  panel.className = "atv-collapsed";
  panel.innerHTML = `
    <div class="atv-head" id="atv-drag-handle" title="拖动面板">
      <div class="atv-head-main">
        <div class="atv-title">视频转文字工作台</div>
        <div class="atv-sub">
          <span id="atv-service">检查服务中...</span>
          <span id="atv-scan-status">等待扫描</span>
        </div>
      </div>
      <div class="atv-head-actions">
        <button class="atv-btn atv-small atv-save-btn" id="atv-save-settings">保存设置</button>
        <button class="atv-btn atv-small" id="atv-toggle">展开</button>
      </div>
    </div>
    <div class="atv-body">
      <div class="atv-workspace">
        <section class="atv-pane atv-video-pane">
          <div class="atv-pane-title">
            <span>视频工作区</span>
            <span id="atv-count">0 个视频，已选 0</span>
          </div>
          <div class="atv-toolbar">
            <button class="atv-btn" id="atv-scan">扫描当前页</button>
            <button class="atv-btn" id="atv-select-all">全选</button>
            <button class="atv-btn" id="atv-clear-selection">清空选择</button>
            <button class="atv-btn" id="atv-clear-results">清空结果</button>
          </div>
          <div class="atv-title-tools">
            <button class="atv-btn" id="atv-translate-titles">翻译标题</button>
            <button class="atv-btn" id="atv-toggle-title-language">显示中文</button>
          </div>
          <div class="atv-list" id="atv-list"></div>
        </section>

        <section class="atv-pane atv-side-pane">
          <div class="atv-pane-title">
            <span>设置与任务</span>
            <span id="atv-action-preview">将生成 transcript.txt + summary.md</span>
          </div>

          <div class="atv-actions">
            <button class="atv-btn atv-primary" id="atv-submit">提交选中视频</button>
            <button class="atv-btn" id="atv-pause">暂停队列</button>
            <button class="atv-btn" id="atv-resume">继续队列</button>
            <button class="atv-btn" id="atv-stop-current">停止当前项</button>
            <button class="atv-btn" id="atv-cancel">取消未开始</button>
            <button class="atv-btn" id="atv-open-batch">打开批次目录</button>
            <button class="atv-btn" id="atv-resummarize">重新总结</button>
          </div>

          <details class="atv-section" open>
            <summary>常用设置</summary>
            <div class="atv-row">
              <div class="atv-field">
                <label>任务模式</label>
                <select id="atv-workflow">
                  <option value="transcribe">只转写</option>
                  <option value="summarize" selected>转写并总结</option>
                  <option value="english_cn">英文视频中文化</option>
                  <option value="audio_only">只转音频</option>
                  <option value="video_download">下载视频 MP4</option>
                </select>
              </div>
              <div class="atv-field" id="atv-audio-format-field">
                <label>音频格式</label>
                <select id="atv-audio-format">
                  <option value="m4a" selected>m4a</option>
                  <option value="wav">wav 16k</option>
                  <option value="mp3">mp3 128k</option>
                </select>
              </div>
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
                <label>并发模式</label>
                <select id="atv-concurrency-preset">
                  <option value="stable" selected>稳定</option>
                  <option value="fast">加速</option>
                  <option value="custom">自定义</option>
                </select>
              </div>
            </div>

            <div class="atv-field">
              <label>输出根目录</label>
              <div class="atv-path-row">
                <input id="atv-output-dir" value="output">
                <button class="atv-btn" id="atv-pick-output">选择目录</button>
              </div>
            </div>
            <div class="atv-field">
              <label>批次目录名</label>
              <input id="atv-batch-name" placeholder="留空自动生成">
            </div>

            <div class="atv-model-tools">
              <button class="atv-btn" id="atv-refresh-models">刷新 Ollama 模型</button>
              <span id="atv-model-status">未读取模型列表</span>
            </div>
            <div class="atv-row">
              <div class="atv-field">
                <label>总结模型</label>
                <select id="atv-summary-select"><option value="">手动输入 / 默认</option></select>
                <input id="atv-summary-model" value="qwen-summary:1.5b">
              </div>
              <div class="atv-field">
                <label>翻译模型</label>
                <select id="atv-translate-select"><option value="">手动输入 / 默认</option></select>
                <input id="atv-translate-model" value="qwen3:8b">
              </div>
              <div class="atv-field">
                <label>标题翻译模型</label>
                <select id="atv-title-translate-select"><option value="">手动输入 / 默认</option></select>
                <input id="atv-title-translate-model" value="qwen3.5:2b">
              </div>
            </div>
          </details>

          <details class="atv-section">
            <summary>高级设置</summary>
            <div class="atv-row atv-custom-concurrency" id="atv-custom-concurrency">
              <div class="atv-field">
                <label>下载并发</label>
                <input id="atv-download-concurrency" type="number" min="1" max="5" step="1" value="1">
              </div>
              <div class="atv-field">
                <label>转写并发</label>
                <input id="atv-asr-concurrency" type="number" min="1" max="2" step="1" value="1">
              </div>
              <div class="atv-field">
                <label>总结/翻译并发</label>
                <input id="atv-ollama-concurrency" type="number" min="1" max="2" step="1" value="1">
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
            <div class="atv-row">
              <div class="atv-field">
                <label>切块分钟</label>
                <input id="atv-chunk" type="number" min="1" max="120" step="1" value="10">
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
            <label class="atv-check-row"><input id="atv-auto-scan" type="checkbox" checked><span>页面变化后自动扫描</span></label>
            <label class="atv-check-row"><input id="atv-auto-select-new" type="checkbox" checked><span>新增视频自动勾选</span></label>
            <label class="atv-check-row"><input id="atv-prefer-subtitles" type="checkbox" checked><span>优先使用已有字幕，没字幕再转写</span></label>
            <label class="atv-check-row"><input id="atv-force" type="checkbox"><span>强制重跑已有结果</span></label>
            <label class="atv-check-row"><input id="atv-clean-cache" type="checkbox"><span>成功后清理音频缓存</span></label>
          </details>

          <div class="atv-job" id="atv-job">
            <div class="atv-job-empty">还没有提交任务。</div>
          </div>
          <div class="atv-log" id="atv-log">先启动本地服务：.\\.venv\\Scripts\\python.exe main.py serve</div>
        </section>
      </div>
    </div>
    <div class="atv-resize" id="atv-resize" title="拖动调整大小"></div>
  `;

  mountPanel();

  const $ = (selector) => panel.querySelector(selector);
  const serviceEl = $("#atv-service");
  const scanStatusEl = $("#atv-scan-status");
  const listEl = $("#atv-list");
  const countEl = $("#atv-count");
  const logEl = $("#atv-log");
  const previewEl = $("#atv-action-preview");
  const modelStatusEl = $("#atv-model-status");
  const jobEl = $("#atv-job");

  restorePanelState();
  enableDrag();
  enableResize();
  bindEvents();
  setupAutoScan();
  checkService();
  scheduleScan("initial", 800);
  setInterval(checkService, 10000);
  setInterval(ensurePanelMounted, 3000);
  updateActionPreview();
  renderVideos();

  function bindEvents() {
    $("#atv-toggle").addEventListener("click", () => setCollapsed(!panel.classList.contains("atv-collapsed")));
    $("#atv-scan").addEventListener("click", () => scanVideos({ reason: "manual", reset: true }));
    $("#atv-select-all").addEventListener("click", () => {
      state.userClearedSelection = false;
      state.selected = new Set(state.videos.map((video) => video.url));
      renderVideos();
      updateActionPreview();
    });
    $("#atv-clear-selection").addEventListener("click", () => {
      state.userClearedSelection = true;
      state.selected.clear();
      renderVideos();
      updateActionPreview();
    });
    $("#atv-clear-results").addEventListener("click", () => {
      clearScannedVideos({ userClear: true });
      log("已清除本次扫描结果。");
    });
    $("#atv-submit").addEventListener("click", submitSelected);
    $("#atv-pause").addEventListener("click", () => controlJob("pause", "已请求暂停队列"));
    $("#atv-resume").addEventListener("click", () => controlJob("resume", "已继续队列"));
    $("#atv-stop-current").addEventListener("click", () => controlJob("stop-current", "已请求停止当前项"));
    $("#atv-cancel").addEventListener("click", () => controlJob("cancel", "已取消未开始项目"));
    $("#atv-resummarize").addEventListener("click", resummarizeLastJob);
    $("#atv-open-batch").addEventListener("click", () => openOutputPath(state.lastBatchDir));
    $("#atv-translate-titles").addEventListener("click", translateVisibleTitles);
    $("#atv-toggle-title-language").addEventListener("click", toggleTitleLanguage);
    $("#atv-save-settings").addEventListener("click", () => saveSettings(true));
    $("#atv-pick-output").addEventListener("click", pickOutputDir);
    $("#atv-refresh-models").addEventListener("click", loadModels);
    $("#atv-preset").addEventListener("change", () => {
      applyPreset();
      updateActionPreview();
    });
    $("#atv-workflow").addEventListener("change", updateActionPreview);
    $("#atv-audio-format").addEventListener("change", updateActionPreview);
    $("#atv-concurrency-preset").addEventListener("change", updateConcurrencyUI);
    $("#atv-summary-select").addEventListener("change", () => applyModelSelect("#atv-summary-select", "#atv-summary-model"));
    $("#atv-translate-select").addEventListener("change", () => applyModelSelect("#atv-translate-select", "#atv-translate-model"));
    $("#atv-title-translate-select").addEventListener("change", () => applyModelSelect("#atv-title-translate-select", "#atv-title-translate-model"));
    window.addEventListener("message", handleFrameVideos);
  }

  function postFrameVideos(request) {
    const videos = collectVisibleVideosFromDocument(document);
    window.top.postMessage(
      {
        source: MESSAGE_SOURCE,
        type: "frame-videos",
        videos,
        pageKey: request && request.pageKey,
        token: request && request.token,
        pageEpoch: request && request.pageEpoch,
      },
      "*"
    );
  }

  function mountPanel() {
    const target = document.body || document.documentElement;
    if (target && !document.getElementById(PANEL_ID)) target.appendChild(panel);
  }

  function ensurePanelMounted() {
    if (!document.getElementById(PANEL_ID)) mountPanel();
  }

  function restorePanelState() {
    chrome.storage.local.get(["atvPanelPosition", "atvPanelSize"], (result) => {
      const size = result.atvPanelSize;
      if (size) {
        panel.style.width = `${clamp(size.width, 430, window.innerWidth - 24)}px`;
        panel.style.height = `${clamp(size.height, 360, window.innerHeight - 24)}px`;
      }
      const position = result.atvPanelPosition;
      if (position) {
        panel.style.left = `${clamp(position.left, 0, window.innerWidth - 80)}px`;
        panel.style.top = `${clamp(position.top, 0, window.innerHeight - 60)}px`;
        panel.style.right = "auto";
      }
      setCollapsed(true);
      updatePanelLayout();
    });
  }

  function setCollapsed(collapsed) {
    panel.classList.toggle("atv-collapsed", collapsed);
    $("#atv-toggle").textContent = collapsed ? "展开" : "收起";
    updatePanelLayout();
  }

  function enableDrag() {
    const handle = $("#atv-drag-handle");
    let dragging = false;
    let offsetX = 0;
    let offsetY = 0;

    handle.addEventListener("mousedown", (event) => {
      if (event.target.closest("button")) return;
      dragging = true;
      const rect = panel.getBoundingClientRect();
      offsetX = event.clientX - rect.left;
      offsetY = event.clientY - rect.top;
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
      event.preventDefault();
    });

    function onMove(event) {
      if (!dragging) return;
      const left = clamp(event.clientX - offsetX, 0, window.innerWidth - Math.min(panel.offsetWidth, 120));
      const top = clamp(event.clientY - offsetY, 0, window.innerHeight - 60);
      panel.style.left = `${left}px`;
      panel.style.top = `${top}px`;
      panel.style.right = "auto";
    }

    function onUp() {
      dragging = false;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      const rect = panel.getBoundingClientRect();
      chrome.storage.local.set({ atvPanelPosition: { left: Math.round(rect.left), top: Math.round(rect.top) } });
    }
  }

  function enableResize() {
    const handle = $("#atv-resize");
    let resizing = false;
    let startX = 0;
    let startY = 0;
    let startWidth = 0;
    let startHeight = 0;

    handle.addEventListener("mousedown", (event) => {
      if (panel.classList.contains("atv-collapsed")) return;
      resizing = true;
      const rect = panel.getBoundingClientRect();
      startX = event.clientX;
      startY = event.clientY;
      startWidth = rect.width;
      startHeight = rect.height;
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
      event.preventDefault();
    });

    function onMove(event) {
      if (!resizing) return;
      const width = clamp(startWidth + event.clientX - startX, 430, window.innerWidth - 24);
      const height = clamp(startHeight + event.clientY - startY, 360, window.innerHeight - 24);
      panel.style.width = `${width}px`;
      panel.style.height = `${height}px`;
      updatePanelLayout();
    }

    function onUp() {
      resizing = false;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      const rect = panel.getBoundingClientRect();
      chrome.storage.local.set({ atvPanelSize: { width: Math.round(rect.width), height: Math.round(rect.height) } });
    }
  }

  function updatePanelLayout() {
    if (!panel || panel.classList.contains("atv-collapsed")) {
      panel.classList.remove("atv-narrow");
      return;
    }
    panel.classList.toggle("atv-narrow", panel.getBoundingClientRect().width < 660);
  }

  function setupAutoScan() {
    const observer = new MutationObserver((mutations) => {
      if (mutations.every((mutation) => panel.contains(mutation.target))) return;
      const changed = detectPageContextChange();
      scheduleScan(changed ? "context" : "dom", changed ? 250 : 900);
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });

    const originalPush = history.pushState;
    const originalReplace = history.replaceState;
    history.pushState = function (...args) {
      const result = originalPush.apply(this, args);
      handleUrlMaybeChanged();
      return result;
    };
    history.replaceState = function (...args) {
      const result = originalReplace.apply(this, args);
      handleUrlMaybeChanged();
      return result;
    };
    window.addEventListener("popstate", handleUrlMaybeChanged);
    window.addEventListener("resize", updatePanelLayout);
    window.addEventListener("yt-navigate-start", handleYoutubeNavigateStart);
    document.addEventListener("yt-navigate-start", handleYoutubeNavigateStart, true);
    window.addEventListener("yt-navigate-finish", () => {
      state.youtubeNavigatingUntil = 0;
      handleUrlMaybeChanged();
      scheduleScan("youtube", 500);
    });
    document.addEventListener(
      "yt-navigate-finish",
      () => {
        state.youtubeNavigatingUntil = 0;
        handleUrlMaybeChanged();
        scheduleScan("youtube", 500);
      },
      true
    );
    window.addEventListener("yt-page-type-changed", handleUrlMaybeChanged);
    document.addEventListener("yt-page-type-changed", handleUrlMaybeChanged, true);
    window.addEventListener("yt-page-data-updated", () => scheduleScan("youtube", 650));
    document.addEventListener("yt-page-data-updated", () => scheduleScan("youtube", 650), true);
    document.addEventListener("click", handlePossibleYoutubeLinkClick, true);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) scheduleScan("visible", 700);
    });
    setInterval(handleUrlMaybeChanged, 600);
  }

  function handleYoutubeNavigateStart() {
    if (!location.hostname.includes("youtube.com")) return;
    state.youtubeNavigatingUntil = Date.now() + 2400;
    state.pageEpoch += 1;
    state.scanToken += 1;
    clearScannedVideos({ pageChange: true });
    scanStatusEl.textContent = "页面切换中...";
  }

  function handlePossibleYoutubeLinkClick(event) {
    if (!location.hostname.includes("youtube.com")) return;
    const link = event.target && event.target.closest ? event.target.closest("a[href]") : null;
    if (!link) return;
    const href = link.href || "";
    if (!href || (!href.includes("youtube.com") && !href.includes("youtu.be") && !href.startsWith("/"))) return;
    setTimeout(handleUrlMaybeChanged, 0);
    setTimeout(handleUrlMaybeChanged, 250);
    setTimeout(handleUrlMaybeChanged, 800);
  }

  function handleUrlMaybeChanged() {
    if (detectPageContextChange()) scheduleScan("url", 700);
  }

  function detectPageContextChange() {
    const key = makePageKey();
    const href = normalizedPageHref();
    if (state.lastPageKey === key && state.lastHref === href) return false;
    state.lastPageKey = key;
    state.lastHref = href;
    state.pageEpoch += 1;
    state.scanToken += 1;
    clearScannedVideos({ pageChange: true });
    return true;
  }

  function scheduleScan(reason, delay) {
    if (Date.now() < state.clearUntil) return;
    if (reason !== "initial" && reason !== "manual" && !$("#atv-auto-scan").checked) return;
    clearTimeout(state.autoScanTimer);
    state.autoScanTimer = setTimeout(() => scanVideos({ reason }), delay);
  }

  function scanVideos({ reason, reset = false } = {}) {
    if (Date.now() < state.clearUntil) return;
    if (reason !== "manual" && Date.now() < state.youtubeNavigatingUntil) return;
    if (reset) {
      state.lastPageKey = makePageKey();
      state.lastHref = normalizedPageHref();
      state.pageEpoch += 1;
      state.scanToken += 1;
      clearScannedVideos({ pageChange: true });
    } else {
      detectPageContextChange();
    }
    state.scanToken += 1;
    const token = state.scanToken;
    const pageEpoch = state.pageEpoch;
    const pageKey = state.lastPageKey;
    const scanHref = location.href;
    const found = reset ? new Map() : new Map(state.videos.map((video) => [video.url, video]));
    scanDocument(document, found);
    scanOpenShadowRoots(document, found);
    document.querySelectorAll("iframe").forEach((frame) => {
      try {
        if (frame.contentDocument) scanDocument(frame.contentDocument, found);
        if (frame.contentWindow) frame.contentWindow.postMessage({ source: MESSAGE_SOURCE, type: "scan-request", pageKey, token, pageEpoch }, "*");
      } catch {
        try {
          frame.contentWindow.postMessage({ source: MESSAGE_SOURCE, type: "scan-request", pageKey, token, pageEpoch }, "*");
        } catch {}
      }
    });
    if (scanHref !== location.href || pageEpoch !== state.pageEpoch || pageKey !== state.lastPageKey) return;
    mergeVideos(Array.from(found.values()), { reason });
    scanStatusEl.textContent = `已扫描 ${state.videos.length} 个`;
  }

  function handleFrameVideos(event) {
    if (!event.data || event.data.source !== MESSAGE_SOURCE || event.data.type !== "frame-videos") return;
    if (event.data.pageKey && event.data.pageKey !== state.lastPageKey) return;
    if (event.data.token && event.data.token !== state.scanToken) return;
    if (event.data.pageEpoch !== undefined && event.data.pageEpoch !== state.pageEpoch) return;
    const videos = Array.isArray(event.data.videos) ? event.data.videos : [];
    if (!videos.length || Date.now() < state.clearUntil) return;
    mergeVideos(videos, { reason: "frame" });
    scanStatusEl.textContent = `已扫描 ${state.videos.length} 个`;
  }

  function mergeVideos(videos, { reason }) {
    const found = new Map(state.videos.map((video) => [video.url, video]));
    const previousUrls = new Set(found.keys());
    videos.forEach((video) => {
      if (!video || !video.url) return;
      const previous = found.get(video.url);
      if (!previous || isBetterVideo(video, previous)) found.set(video.url, { ...previous, ...video });
    });

    const oldSelected = new Set(state.selected);
    state.videos = Array.from(found.values());
    state.selected = new Set(state.videos.filter((video) => oldSelected.has(video.url)).map((video) => video.url));
    const autoSelectNew = $("#atv-auto-select-new").checked && !state.userClearedSelection;
    if ((reason === "initial" || autoSelectNew) && autoSelectNew) {
      state.videos.forEach((video) => {
        if (!previousUrls.has(video.url) || reason === "initial") state.selected.add(video.url);
      });
    }
    renderVideos();
    updateActionPreview();
  }

  function clearScannedVideos({ userClear = false } = {}) {
    state.videos = [];
    state.selected.clear();
    state.itemByUrl.clear();
    state.titleTranslations.clear();
    state.titleMode = "source";
    state.userClearedSelection = userClear;
    if (userClear) state.clearUntil = Date.now() + 1500;
    renderVideos();
    updateActionPreview();
    scanStatusEl.textContent = "等待扫描";
  }

  function collectVisibleVideosFromDocument(doc) {
    const found = new Map();
    scanDocument(doc, found);
    scanOpenShadowRoots(doc, found);
    return Array.from(found.values());
  }

  function scanDocument(doc, found) {
    collectCurrentVideo(doc, found);
    const selector = [
      'a[href*="BV"]',
      'a[href*="/video/"]',
      'a[href*="/watch"]',
      'a[href*="/shorts/"]',
      'a[href*="youtu.be/"]',
      "[data-bvid]",
      "[data-bv]",
      '[data-url*="BV"]',
      '[data-link*="BV"]',
      '[data-href*="BV"]',
      "[data-video-id]",
      'ytd-video-renderer',
      'ytd-rich-item-renderer',
      'ytd-compact-video-renderer',
      'ytd-grid-video-renderer',
      'ytd-reel-item-renderer',
    ].join(",");
    doc.querySelectorAll(selector).forEach((element) => {
      if (isProbablyVisible(element) && !isAdElement(element)) collectVideosFromElement(element, found);
    });
    scanScriptData(doc, found);
  }

  function scanOpenShadowRoots(rootDoc, found) {
    rootDoc.querySelectorAll("*").forEach((element) => {
      if (!element.shadowRoot) return;
      scanDocument(element.shadowRoot, found);
    });
  }

  function scanScriptData(doc, found) {
    doc.querySelectorAll("script").forEach((script) => {
      const text = script.textContent || "";
      if (!text.includes("BV")) return;
      const matches = text.match(/BV[0-9A-Za-z]{8,14}/g) || [];
      matches.slice(0, 80).forEach((bvid) => {
        const index = text.indexOf(bvid);
        const nearby = text.slice(Math.max(0, index - 500), index + 800);
        const title = decodeScriptTitle(nearby);
        if (title && !isNoiseTitle(title)) addVideo(found, { platform: "bilibili", id: bvid, title, strong: false });
      });
    });
  }

  function decodeScriptTitle(text) {
    const match = text.match(/"title"\s*:\s*"([^"]{3,180})"/) || text.match(/"name"\s*:\s*"([^"]{3,180})"/);
    if (!match) return "";
    try {
      return JSON.parse(`"${match[1]}"`);
    } catch {
      return match[1].replace(/\\u([0-9a-fA-F]{4})/g, (_, code) => String.fromCharCode(parseInt(code, 16)));
    }
  }

  function collectCurrentVideo(doc, found) {
    const bvid = (location.href.match(/BV[0-9A-Za-z]{8,14}/) || [])[0];
    if (bvid) {
      const titleEl = doc.querySelector("h1") || doc.querySelector(".video-title") || doc.querySelector("[title]");
      addVideo(found, {
        platform: "bilibili",
        id: bvid,
        title: bestCleanTitle([titleEl && titleEl.getAttribute("title"), titleEl && titleEl.textContent, document.title, bvid]),
        duration: findDurationNear(titleEl || doc.body),
        strong: true,
      });
    }

    const youtube = currentYoutubeId();
    if (youtube) {
      const titleEl = doc.querySelector("ytd-watch-metadata h1") || doc.querySelector("h1") || doc.querySelector("#title h1");
      addVideo(found, {
        platform: "youtube",
        id: youtube.id,
        url: youtube.url,
        title: bestCleanTitle([titleEl && titleEl.textContent, document.title, youtube.id]),
        duration: findDurationNear(doc.querySelector("ytd-player") || doc.body),
        strong: true,
      });
    }
  }

  function collectVideosFromElement(element, found) {
    if (isAdElement(element)) return;
    const values = collectCandidateValues(element);
    values.forEach((value) => {
      const bvids = String(value).match(/BV[0-9A-Za-z]{8,14}/g) || [];
      bvids.forEach((bvid) => {
        addVideo(found, {
          platform: "bilibili",
          id: bvid,
          title: titleFromElement(element, bvid),
          duration: findDurationNear(element),
          strong: hasStrongTitleSource(element),
        });
      });

      extractYoutubeIds(value).forEach((item) => {
        addVideo(found, {
          platform: "youtube",
          id: item.id,
          url: item.url,
          title: titleFromElement(element, item.id),
          duration: findDurationNear(element),
          strong: hasStrongTitleSource(element),
        });
      });
    });
  }

  function collectCandidateValues(element) {
    const values = [];
    for (const attribute of element.attributes || []) values.push(attribute.value);
    if (element.href) values.push(element.href);
    const text = cleanTitle(element.textContent || "");
    if (text.length <= 420) values.push(text);
    return values;
  }

  function addVideo(found, candidate) {
    const platform = candidate.platform || "bilibili";
    const id = normalizeVideoId(platform, candidate.id);
    if (!id) return;
    const url = candidate.url || normalizedVideoUrl(platform, id);
    const title = bestCleanTitle([candidate.title, id]);
    const duration = normalizeDuration(candidate.duration);
    const strong = Boolean(candidate.strong);
    if (platform === "youtube" && isYoutubeAdTitle(title)) return;
    if (!strong && (isNoiseTitle(title) || title === id || /^https?:\/\//i.test(title))) return;

    const previous = found.get(url);
    const video = { platform, id, bvid: platform === "bilibili" ? id : "", title, duration, url };
    if (previous && !isBetterVideo(video, previous)) return;
    found.set(url, video);
  }

  function normalizeVideoId(platform, id) {
    const text = String(id || "");
    if (platform === "youtube") {
      const match = text.match(/[0-9A-Za-z_-]{8,16}/);
      return match ? match[0] : "";
    }
    const match = text.match(/BV[0-9A-Za-z]{8,14}/);
    return match ? match[0] : "";
  }

  function normalizedVideoUrl(platform, id) {
    if (platform === "youtube") return `https://www.youtube.com/watch?v=${id}`;
    return `https://www.bilibili.com/video/${id}/`;
  }

  function currentYoutubeId() {
    try {
      const url = new URL(location.href);
      if (url.hostname.includes("youtu.be")) {
        const id = url.pathname.split("/").filter(Boolean)[0];
        return id ? { id, url: normalizedVideoUrl("youtube", id) } : null;
      }
      if (!url.hostname.includes("youtube.com")) return null;
      if (url.pathname === "/watch" && url.searchParams.get("v")) {
        const id = url.searchParams.get("v");
        return { id, url: normalizedVideoUrl("youtube", id) };
      }
      const shorts = url.pathname.match(/\/shorts\/([0-9A-Za-z_-]{8,16})/);
      if (shorts) return { id: shorts[1], url: `https://www.youtube.com/shorts/${shorts[1]}` };
    } catch {}
    return null;
  }

  function extractYoutubeIds(value) {
    const text = String(value || "");
    const results = [];
    const patterns = [
      /(?:https?:\/\/)?(?:www\.)?youtube\.com\/watch\?[^"'\s<>]*v=([0-9A-Za-z_-]{8,16})/g,
      /(?:https?:\/\/)?(?:www\.)?youtube\.com\/shorts\/([0-9A-Za-z_-]{8,16})/g,
      /(?:https?:\/\/)?youtu\.be\/([0-9A-Za-z_-]{8,16})/g,
      /\/watch\?[^"'\s<>]*v=([0-9A-Za-z_-]{8,16})/g,
      /\/shorts\/([0-9A-Za-z_-]{8,16})/g,
    ];
    patterns.forEach((pattern) => {
      let match;
      while ((match = pattern.exec(text))) {
        const id = match[1];
        const isShort = match[0].includes("/shorts/");
        results.push({ id, url: isShort ? `https://www.youtube.com/shorts/${id}` : normalizedVideoUrl("youtube", id) });
      }
    });
    return results;
  }

  function titleFromElement(element, id) {
    const candidates = [];
    addTitleCandidate(candidates, element.getAttribute("title"), 100);
    addTitleCandidate(candidates, element.getAttribute("aria-label"), 92);
    const card = findVideoCard(element);
    if (card) {
      [
        "#video-title",
        "a#video-title",
        "yt-formatted-string#video-title",
        ".bili-video-card__info--tit",
        ".bili-video-card__info--tit a",
        ".video-card__info-title",
        ".video-title",
        ".bili-dyn-card-video__title",
        ".title",
        ".name",
        "h3",
        "p[title]",
        "a[title]",
      ].forEach((selector, index) => {
        card.querySelectorAll(selector).forEach((node) => {
          addTitleCandidate(candidates, node.getAttribute("title"), 95 - index);
          addTitleCandidate(candidates, node.getAttribute("aria-label"), 86 - index);
          addTitleCandidate(candidates, node.textContent, 82 - index);
        });
      });
      addTitleCandidate(candidates, card.getAttribute("title"), 78);
      addTitleCandidate(candidates, card.getAttribute("aria-label"), 76);
    }
    addTitleCandidate(candidates, element.textContent, 35);

    let node = element.parentElement;
    for (let depth = 0; depth < 4 && node; depth += 1) {
      addTitleCandidate(candidates, node.getAttribute("title"), 70 - depth);
      addTitleCandidate(candidates, node.getAttribute("aria-label"), 68 - depth);
      const text = cleanTitle(node.textContent || "");
      if (text.length < 500) addTitleCandidate(candidates, text, 24 - depth);
      node = node.parentElement;
    }
    const sorted = candidates
      .map((item) => ({ ...item, title: normalizeTitle(item.title, id) }))
      .filter((item) => item.title && !isNoiseTitle(item.title))
      .sort((a, b) => b.score - a.score || scoreTitle(b.title) - scoreTitle(a.title));
    return sorted[0] ? sorted[0].title : id;
  }

  function findVideoCard(element) {
    return (
      element.closest(
        [
          "ytd-video-renderer",
          "ytd-rich-item-renderer",
          "ytd-compact-video-renderer",
          "ytd-grid-video-renderer",
          "ytd-reel-item-renderer",
          "li",
          "article",
          ".bili-video-card",
          ".video-card",
          ".feed-card",
          ".search-card",
          ".card-box",
          ".bili-rich-card",
          ".bili-dyn-card-video",
          "[class*=video]",
          "[class*=card]",
        ].join(",")
      ) ||
      closestMatching(element, (node) => {
        if (!node.querySelector) return false;
        const hasVideo = node.querySelector('a[href*="BV"], a[href*="/video/"], a[href*="/watch"], a[href*="/shorts/"], [data-bvid], [data-bv]');
        if (!hasVideo) return false;
        const text = cleanTitle(node.textContent || "");
        return text.length > 8 && text.length < 1200;
      })
    );
  }

  function closestMatching(element, predicate) {
    let node = element;
    for (let depth = 0; depth < 7 && node && node !== document.body; depth += 1) {
      if (predicate(node)) return node;
      node = node.parentElement;
    }
    return null;
  }

  function hasStrongTitleSource(element) {
    if (element.matches && element.matches("#video-title, [title], [aria-label], .bili-video-card__info--tit, .video-title")) return true;
    const card = findVideoCard(element);
    if (!card) return false;
    return Boolean(
      card.querySelector(
        "#video-title, [id='video-title'], .bili-video-card__info--tit, .video-card__info-title, .video-title, .bili-dyn-card-video__title, h3, a[title], p[title]"
      )
    );
  }

  function findDurationNear(element) {
    if (!element) return "";
    const card = findVideoCard(element) || element;
    const selectors = [
      ".bili-video-card__stats__duration",
      ".bili-cover-card__stat",
      ".bili-video-card__duration",
      ".bili-video-card__mask .duration",
      ".bili-video-card__cover .duration",
      ".bili-cover-card__duration",
      ".bili-watch-card__duration",
      ".video-duration",
      ".video-length",
      ".duration",
      ".length",
      ".video-time",
      ".time",
      "ytd-thumbnail-overlay-time-status-renderer",
      "#time-status",
      ".ytd-thumbnail-overlay-time-status-renderer",
      "span[aria-label*='minute']",
      "span[aria-label*='分钟']",
    ];
    const roots = [card, element, card.parentElement, element.parentElement].filter(Boolean);
    for (const root of roots) {
      for (const selector of selectors) {
        const nodes = root.querySelectorAll ? Array.from(root.querySelectorAll(selector)).slice(0, 8) : [];
        if (root.matches && root.matches(selector)) nodes.unshift(root);
        for (const node of nodes) {
          const duration = durationFromNode(node);
          if (duration) return duration;
        }
      }
    }
    const attributeNodes = [card, element, ...Array.from(card.querySelectorAll ? card.querySelectorAll("[aria-label],[title],[data-duration],[data-time],[data-length],img[alt],a[aria-label]") : []).slice(0, 80)];
    for (const node of attributeNodes) {
      const duration = durationFromNode(node);
      if (duration) return duration;
    }
    return normalizeDuration([element.textContent, card.textContent, card.parentElement && card.parentElement.textContent].filter(Boolean).join(" "));
  }

  function durationFromNode(node) {
    if (!node) return "";
    const values = [
      node.textContent,
      node.getAttribute && node.getAttribute("aria-label"),
      node.getAttribute && node.getAttribute("title"),
      node.getAttribute && node.getAttribute("alt"),
      node.getAttribute && node.getAttribute("data-duration"),
      node.getAttribute && node.getAttribute("data-time"),
      node.getAttribute && node.getAttribute("data-length"),
    ];
    for (const value of values) {
      const duration = normalizeDuration(value);
      if (duration) return duration;
    }
    return "";
  }

  function normalizeDuration(value) {
    const text = cleanTitle(value || "");
    if (!text) return "";
    const colon = text.match(/\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b/);
    if (colon) return colon[0];
    const chinese = text.match(/(?:(\d{1,2})\s*小时)?\s*(?:(\d{1,3})\s*分(?:钟)?)\s*(?:(\d{1,2})\s*秒)?/);
    if (chinese && (chinese[1] || chinese[2] || chinese[3])) {
      return formatDurationSeconds((Number(chinese[1] || 0) * 3600) + (Number(chinese[2] || 0) * 60) + Number(chinese[3] || 0));
    }
    const chineseSecondsOnly = text.match(/\b(\d{1,3})\s*秒\b/);
    if (chineseSecondsOnly) return formatDurationSeconds(Number(chineseSecondsOnly[1]));
    const english = text.match(/(?:(\d{1,2})\s*(?:hours?|hrs?|h))?\s*(?:(\d{1,3})\s*(?:minutes?|mins?|m))\s*(?:(\d{1,2})\s*(?:seconds?|secs?|s))?/i);
    if (english && (english[1] || english[2] || english[3])) {
      return formatDurationSeconds((Number(english[1] || 0) * 3600) + (Number(english[2] || 0) * 60) + Number(english[3] || 0));
    }
    const englishSecondsOnly = text.match(/\b(\d{1,3})\s*(?:seconds?|secs?|s)\b/i);
    if (englishSecondsOnly) return formatDurationSeconds(Number(englishSecondsOnly[1]));
    return "";
  }

  function formatDurationSeconds(totalSeconds) {
    const seconds = Math.max(0, Math.round(totalSeconds || 0));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const rest = seconds % 60;
    if (hours) return `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
    return `${minutes}:${String(rest).padStart(2, "0")}`;
  }

  function addTitleCandidate(candidates, value, score) {
    if (!value) return;
    splitCandidateLines(value).forEach((line) => {
      if (!line || isNoiseTitle(line)) return;
      candidates.push({ title: line, score });
    });
  }

  function splitCandidateLines(value) {
    return String(value)
      .split(/[\n\r|·•]/)
      .map((line) => cleanTitle(line))
      .filter(Boolean)
      .slice(0, 12);
  }

  function bestCleanTitle(values) {
    const candidates = [];
    values.forEach((value, index) => addTitleCandidate(candidates, value, 100 - index));
    const sorted = candidates
      .map((item) => ({ ...item, title: normalizeTitle(item.title) }))
      .filter((item) => item.title && !isNoiseTitle(item.title))
      .sort((a, b) => b.score - a.score || scoreTitle(b.title) - scoreTitle(a.title));
    return sorted[0] ? sorted[0].title : cleanTitle(values.find(Boolean) || "video");
  }

  function normalizeTitle(value, id) {
    let title = cleanTitle(value || "");
    title = title.replace(/BV[0-9A-Za-z]{8,14}/g, "");
    title = title.replace(/[0-9A-Za-z_-]{11}/g, (match) => (match === id ? "" : match));
    if (id) title = title.replace(id, "");
    title = title.replace(/https?:\/\/\S+/g, "");
    title = title.replace(/\s+\d+(\.\d+)?\s*(万|萬)?\s*(播放|观看|views?)\b.*$/i, "");
    title = title.replace(/\s+\d{1,2}:\d{2}(:\d{2})?$/g, "");
    title = title.replace(/\s*(?:\d{1,2}\s*小时)?\s*\d{1,3}\s*分(?:钟)?\s*(?:\d{1,2}\s*秒)?\s*$/g, "");
    title = title.replace(/\s*(?:\d{1,2}\s*(?:hours?|hrs?|h))?\s*\d{1,3}\s*(?:minutes?|mins?|m)\s*(?:\d{1,2}\s*(?:seconds?|secs?|s))?\s*$/gi, "");
    title = title.replace(/\s+[-–]\s+YouTube$/i, "");
    title = title.replace(/_哔哩哔哩_bilibili$/i, "");
    title = title.replace(/^\(\d+\)\s*/, "");
    return cleanTitle(title);
  }

  function isNoiseTitle(value) {
    const title = cleanTitle(value);
    if (!title || title.length < 3) return true;
    if (/^BV[0-9A-Za-z]{8,14}$/.test(title)) return true;
    if (/^[0-9A-Za-z_-]{8,16}$/.test(title)) return true;
    if (/^[\d\s.,:：万萬亿億KmM+-]+$/.test(title)) return true;
    if (/^\d{1,2}:\d{2}(:\d{2})?$/.test(title)) return true;
    if (/^\d{1,2}-\d{1,2}$/.test(title)) return true;
    if (/^(播放|弹幕|点赞|收藏|评论|分享|观看|投币|广告|直播|关注|已关注|稍后再看)$/i.test(title)) return true;
    if (/^(views?|subscribers?|watch|share|save|more|shorts|live|recommended|all|today|yesterday)$/i.test(title)) return true;
    if (/^\d+(\.\d+)?\s*(万|萬)?\s*(播放|观看|views?)$/i.test(title)) return true;
    if (/^\d+\s*(seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s+ago$/i.test(title)) return true;
    return false;
  }

  function isAdElement(element) {
    if (!element || !element.closest) return false;
    const href = String(element.href || element.getAttribute?.("href") || "");
    const youtubeContext =
      location.hostname.includes("youtube.com") ||
      location.hostname.includes("youtu.be") ||
      href.includes("youtube.com") ||
      href.includes("youtu.be") ||
      Boolean(element.closest("ytd-app, ytd-page-manager"));
    if (!youtubeContext) return false;
    const adSelector = [
      "ytd-promoted-video-renderer",
      "ytd-display-ad-renderer",
      "ytd-ad-slot-renderer",
      "ytd-player-legacy-desktop-watch-ads-renderer",
      "ytd-companion-slot-renderer",
      "ytd-action-companion-ad-renderer",
      "ytd-in-feed-ad-layout-renderer",
      "ytd-ad-preview-renderer",
      "google-bottom-bar",
      "[id*='ad-slot']",
      "[class*='ad-showing']",
    ].join(",");
    if (element.closest(adSelector)) return true;
    const card = findVideoCard(element) || element;
    return isYoutubeAdTitle(card.textContent || "");
  }

  function isYoutubeAdTitle(value) {
    const title = cleanTitle(value || "");
    if (!title) return false;
    if (/观看\s*访问网站|访问网站|visit\s+site|sponsored|promoted|广告/i.test(title)) return true;
    if (/^(ad|ads)$/i.test(title)) return true;
    return false;
  }

  function scoreTitle(title) {
    let score = title.length;
    if (/[\u4e00-\u9fff]/.test(title)) score += 20;
    if (/[!?？！。，、【】《》]/.test(title)) score += 4;
    if (isNoiseTitle(title)) score -= 100;
    return score;
  }

  function cleanTitle(value) {
    return String(value).replace(/\s+/g, " ").trim().slice(0, 160);
  }

  function isBetterVideo(candidate, current) {
    if (!current) return true;
    if (candidate.duration && !current.duration) return true;
    if (isNoiseTitle(current.title) && !isNoiseTitle(candidate.title)) return true;
    if (candidate.title && candidate.title.length > (current.title || "").length && !isNoiseTitle(candidate.title)) return true;
    return false;
  }

  function isProbablyVisible(element) {
    if (!element) return false;
    if (isHiddenByAncestor(element)) return false;
    if (isInsideInactiveYoutubePage(element)) return false;
    if (!element.getBoundingClientRect) return true;
    const view = element.ownerDocument && element.ownerDocument.defaultView ? element.ownerDocument.defaultView : window;
    const style = view.getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
    const rect = element.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) return true;
    return false;
  }

  function isHiddenByAncestor(element) {
    const view = element.ownerDocument && element.ownerDocument.defaultView ? element.ownerDocument.defaultView : window;
    let node = element;
    for (let depth = 0; node && depth < 12; depth += 1) {
      if (node.nodeType !== 1) {
        node = node.parentElement;
        continue;
      }
      if (node.hidden || node.hasAttribute("hidden") || node.getAttribute("aria-hidden") === "true") return true;
      const style = view.getComputedStyle(node);
      if (style.display === "none" || style.visibility === "hidden") return true;
      node = node.parentElement;
    }
    return false;
  }

  function isInsideInactiveYoutubePage(element) {
    if (!location.hostname.includes("youtube.com")) return false;
    const view = element.ownerDocument && element.ownerDocument.defaultView ? element.ownerDocument.defaultView : window;
    let node = element;
    while (node && node.parentElement) {
      if (node.parentElement.tagName && node.parentElement.tagName.toLowerCase() === "ytd-page-manager") {
        if (node.hidden || node.hasAttribute("hidden") || node.getAttribute("aria-hidden") === "true") return true;
        const style = view.getComputedStyle(node);
        return style.display === "none" || style.visibility === "hidden";
      }
      node = node.parentElement;
    }
    return false;
  }

  function renderVideos() {
    countEl.textContent = `${state.videos.length} 个视频，已选 ${state.selected.size}`;
    listEl.innerHTML = "";
    state.videos.forEach((video) => {
      const item = state.itemByUrl.get(video.url);
      const progress = item && item.progress ? item.progress : null;
      const percent = progress && Number.isFinite(progress.percent) ? progress.percent : statusPercent(item && item.status);
      const row = document.createElement("div");
      row.className = `atv-item atv-status-${(item && item.status) || "idle"}`;
      row.innerHTML = `
        <label class="atv-item-select"><input type="checkbox"></label>
        <div class="atv-item-main">
          <div class="atv-item-top">
            <span class="atv-item-title"></span>
            <span class="atv-item-status"></span>
          </div>
          <div class="atv-item-meta"></div>
          <div class="atv-progress"><span></span></div>
          <div class="atv-item-message"></div>
          <div class="atv-item-actions"></div>
        </div>
      `;
      const checkbox = row.querySelector("input");
      checkbox.checked = state.selected.has(video.url);
      row.querySelector(".atv-item-title").textContent = displayedVideoTitle(video);
      const sourceLabel = transcriptSourceLabel(item);
      row.querySelector(".atv-item-meta").textContent = [video.platform === "youtube" ? "YouTube" : "Bilibili", video.id, video.duration, sourceLabel].filter(Boolean).join(" · ");
      row.querySelector(".atv-item-status").textContent = statusText(item);
      row.querySelector(".atv-progress span").style.width = `${percent || 0}%`;
      row.querySelector(".atv-item-message").textContent = itemMessage(item);
      checkbox.addEventListener("change", () => {
        state.userClearedSelection = false;
        if (checkbox.checked) state.selected.add(video.url);
        else state.selected.delete(video.url);
        updateActionPreview();
      });
      const actions = row.querySelector(".atv-item-actions");
      if (item && (item.output_dir || item.output)) {
        const open = document.createElement("button");
        open.className = "atv-link-btn";
        open.textContent = "打开输出文件夹";
        open.addEventListener("click", () => openOutputPath(item.output_dir || item.output));
        actions.appendChild(open);
      }
      if (item && ["failed", "cancelled"].includes(item.status)) {
        const retry = document.createElement("button");
        retry.className = "atv-link-btn";
        retry.textContent = "重试此视频";
        retry.addEventListener("click", () => retryItem(video.url));
        actions.appendChild(retry);
      }
      listEl.appendChild(row);
    });
    if (!state.videos.length) {
      const empty = document.createElement("div");
      empty.className = "atv-empty";
      empty.textContent = "当前页还没有扫描到可用视频。搜索页、首页或 YouTube 页面加载完成后会自动扫描。";
      listEl.appendChild(empty);
    }
    updateTitleLanguageButton();
  }

  function displayedVideoTitle(video) {
    if (state.titleMode === "translated" && video.platform === "youtube") {
      return state.titleTranslations.get(video.url) || video.title || video.id;
    }
    return video.title || video.id;
  }

  function transcriptSourceLabel(item) {
    if (!item) return "";
    const source = item.transcript_source || "unknown";
    const map = {
      manual_subtitle: "已有字幕",
      auto_subtitle: "自动字幕",
      asr: "ASR 转写",
    };
    if (map[source]) return map[source];
    if (["done", "failed", "cancelled"].includes(item.status)) return "来源未知";
    return "来源判断中";
  }

  function statusText(item) {
    if (!item) return "";
    const progress = item.progress || {};
    if (item.status === "failed") return "失败";
    if (item.status === "done") return "完成";
    if (item.status === "cancelled") return "已取消";
    return progress.stage || item.status || "";
  }

  function itemMessage(item) {
    if (!item) return "等待提交";
    if (item.error) return `${item.error_stage ? `[${item.error_stage}] ` : ""}${item.error}`;
    const progress = item.progress || {};
    const chunk = progress.chunk_total ? ` (${progress.chunk_index}/${progress.chunk_total})` : "";
    return `${progress.message || item.status || ""}${chunk}`;
  }

  function statusPercent(status) {
    const map = {
      queued: 0,
      starting: 1,
      checking_subtitles: 4,
      using_subtitles: 28,
      downloading: 8,
      downloading_video: 30,
      saving_audio: 70,
      extracting_audio: 20,
      transcribing: 55,
      translating: 78,
      summarizing: 90,
      stopping: 95,
      done: 100,
      failed: 100,
      cancelled: 100,
    };
    return map[status] || 0;
  }

  async function checkService() {
    serviceEl.textContent = "检查服务中...";
    serviceEl.className = "";
    try {
      const health = await api("GET", "/api/health", undefined, 12000);
      serviceEl.textContent = `本地服务已连接 v${health.version || "?"}`;
      serviceEl.className = "atv-ok";
      await loadSettings();
      if (!state.modelsLoaded) await loadModels();
    } catch (error) {
      if (state.longRequestActive || Date.now() < state.longRequestGraceUntil) {
        serviceEl.textContent = "本地服务忙：正在处理长请求";
        serviceEl.className = "atv-ok";
        return;
      }
      serviceEl.textContent = `本地服务未连接：${shortError(error.message)}`;
      serviceEl.className = "atv-bad";
    }
  }

  async function loadSettings() {
    if (loadSettings.loaded) return;
    const response = await api("GET", "/api/settings");
    const settings = response.settings || {};
    setValue("#atv-workflow", settings.workflow);
    setValue("#atv-audio-format", settings.audio_format);
    setValue("#atv-preset", settings.speed_preset);
    setValue("#atv-concurrency-preset", settings.concurrency_preset);
    setValue("#atv-download-concurrency", settings.download_concurrency);
    setValue("#atv-asr-concurrency", settings.asr_concurrency);
    setValue("#atv-ollama-concurrency", settings.ollama_concurrency);
    setValue("#atv-asr-model", settings.asr_model_size);
    setValue("#atv-device", settings.asr_device);
    setValue("#atv-compute", settings.asr_compute_type);
    setValue("#atv-beam", settings.asr_beam_size);
    setValue("#atv-chunk", settings.chunk_minutes);
    setValue("#atv-summary-model", settings.summary_model);
    setValue("#atv-translate-model", settings.translate_model);
    setValue("#atv-title-translate-model", settings.title_translate_model || "qwen3.5:2b");
    setValue("#atv-output-dir", settings.output_dir);
    setValue("#atv-cache-dir", settings.cache_dir);
    setValue("#atv-batch-name", settings.batch_name || "");
    setValue("#atv-cookies", settings.cookies || "");
    setValue("#atv-cookies-browser", settings.cookies_from_browser || "");
    $("#atv-auto-scan").checked = settings.auto_scan !== false;
    $("#atv-auto-select-new").checked = settings.auto_select_new !== false;
    $("#atv-prefer-subtitles").checked = settings.prefer_subtitles !== false;
    $("#atv-force").checked = Boolean(settings.force);
    $("#atv-clean-cache").checked = Boolean(settings.clean_cache);
    loadSettings.loaded = true;
    updateConcurrencyUI();
    updateActionPreview();
  }

  async function saveSettings(showMessage) {
    try {
      const response = await api("POST", "/api/settings", collectSettings());
      if (showMessage) log("设置已保存。");
      return response.settings;
    } catch (error) {
      log(`保存设置失败：${error.message}`);
      throw error;
    }
  }

  async function pickOutputDir() {
    try {
      log("正在打开文件夹选择窗口...");
      const response = await api("POST", "/api/output-dir/pick", undefined, 120000);
      if (response.ok && response.path) {
        $("#atv-output-dir").value = response.path;
        await saveSettings(false);
        log(`输出目录已设置为：${response.path}`);
      } else if (response.cancelled) {
        log("已取消选择输出目录。");
      } else {
        log(`选择目录失败：${response.error || "未知错误"}`);
      }
    } catch (error) {
      log(`选择目录失败：${error.message}`);
    }
  }

  async function loadModels() {
    modelStatusEl.textContent = "读取中...";
    try {
      const response = await api("GET", "/api/ollama/models", undefined, 8000);
      if (!response.ok) {
        modelStatusEl.textContent = `读取失败：${response.error || "Ollama 未响应"}`;
        return;
      }
      fillModelSelect("#atv-summary-select", response.models, $("#atv-summary-model").value);
      fillModelSelect("#atv-translate-select", response.models, $("#atv-translate-model").value);
      fillModelSelect("#atv-title-translate-select", response.models, $("#atv-title-translate-model").value);
      modelStatusEl.textContent = `已读取 ${response.models.length} 个模型`;
      state.modelsLoaded = true;
    } catch (error) {
      modelStatusEl.textContent = `读取失败：${error.message}`;
    }
  }

  function fillModelSelect(selector, models, currentValue) {
    const select = $(selector);
    select.innerHTML = `<option value="">手动输入 / 默认</option>`;
    models.forEach((model) => {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      if (model === currentValue) option.selected = true;
      select.appendChild(option);
    });
  }

  function applyModelSelect(selectSelector, inputSelector) {
    const value = $(selectSelector).value;
    if (value) $(inputSelector).value = value;
  }

  function applyPreset() {
    const preset = presetMap[$("#atv-preset").value] || presetMap.balanced;
    $("#atv-asr-model").value = preset.asr_model_size;
    $("#atv-device").value = preset.asr_device;
    $("#atv-compute").value = preset.asr_compute_type;
    $("#atv-beam").value = String(preset.asr_beam_size);
  }

  function updateConcurrencyUI() {
    const isCustom = $("#atv-concurrency-preset").value === "custom";
    $("#atv-custom-concurrency").style.display = isCustom ? "grid" : "none";
  }

  function collectSettings() {
    const workflow = $("#atv-workflow").value;
    return {
      workflow,
      audio_format: $("#atv-audio-format").value,
      speed_preset: $("#atv-preset").value,
      concurrency_preset: $("#atv-concurrency-preset").value,
      download_concurrency: Number($("#atv-download-concurrency").value || 1),
      asr_concurrency: Number($("#atv-asr-concurrency").value || 1),
      ollama_concurrency: Number($("#atv-ollama-concurrency").value || 1),
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
      title_translate_model: $("#atv-title-translate-model").value.trim() || "qwen3.5:2b",
      translate_to: workflow === "english_cn" ? "Chinese" : null,
      cookies: $("#atv-cookies").value.trim() || null,
      cookies_from_browser: $("#atv-cookies-browser").value.trim() || null,
      output_dir: $("#atv-output-dir").value.trim() || "output",
      cache_dir: $("#atv-cache-dir").value.trim() || "cache",
      batch_name: $("#atv-batch-name").value.trim() || null,
      auto_scan: $("#atv-auto-scan").checked,
      auto_select_new: $("#atv-auto-select-new").checked,
      prefer_subtitles: $("#atv-prefer-subtitles").checked,
      simplified_chinese: true,
    };
  }

  function updateActionPreview() {
    const workflow = $("#atv-workflow").value;
    const count = state.selected.size;
    const audioFormat = $("#atv-audio-format").value;
    const map = {
      transcribe: "将生成 transcript.txt + transcript.srt",
      summarize: "将生成 transcript.txt + summary.md",
      english_cn: "将生成英文稿 + translation.zh.md + summary.md",
      audio_only: `将生成 audio.${audioFormat}`,
      video_download: "将生成 video.mp4",
    };
    previewEl.textContent = `${count} 个视频：${map[workflow] || map.summarize}`;
    $("#atv-audio-format-field").style.display = workflow === "video_download" ? "none" : "block";
  }

  async function translateVisibleTitles() {
    const youtubeVideos = state.videos.filter((video) => video.platform === "youtube" && video.title && video.title !== video.id);
    if (!youtubeVideos.length) {
      log("当前列表里没有可翻译的 YouTube 标题。");
      return;
    }
    const titles = Array.from(new Set(youtubeVideos.map((video) => video.title)));
    const batches = chunkList(titles, 8);
    const model = $("#atv-title-translate-model").value.trim() || "qwen3.5:2b";
    state.longRequestActive = true;
    serviceEl.textContent = "本地服务忙：正在翻译标题";
    serviceEl.className = "atv-ok";
    try {
      log(`正在分 ${batches.length} 批翻译 ${titles.length} 个 YouTube 标题...`);
      for (let index = 0; index < batches.length; index += 1) {
        const response = await api(
          "POST",
          "/api/translate-titles",
          {
            titles: batches[index],
            target_language: "Chinese",
            model,
          },
          100000
        );
        if (!response.ok) {
          log(`标题翻译失败（第 ${index + 1}/${batches.length} 批）：${response.error || "Ollama 未返回结果"}`);
          return;
        }
        const bySource = new Map((response.items || []).map((item) => [item.source, item.translation]));
        youtubeVideos.forEach((video) => {
          const translated = bySource.get(video.title);
          if (translated) state.titleTranslations.set(video.url, translated);
        });
        state.titleMode = "translated";
        renderVideos();
        const currentTranslated = youtubeVideos.filter((video) => state.titleTranslations.has(video.url)).length;
        log(`标题翻译中：${index + 1}/${batches.length} 批，已翻译 ${currentTranslated}/${youtubeVideos.length} 个。`);
      }
      const currentTranslated = youtubeVideos.filter((video) => state.titleTranslations.has(video.url)).length;
      log(`已翻译 ${currentTranslated} 个标题。`);
    } catch (error) {
      log(`标题翻译失败：${error.message}`);
    } finally {
      state.longRequestActive = false;
      state.longRequestGraceUntil = Date.now() + 15000;
      setTimeout(() => {
        if (!state.longRequestActive) void checkService();
      }, 3000);
    }
  }

  function toggleTitleLanguage() {
    state.titleMode = state.titleMode === "translated" ? "source" : "translated";
    renderVideos();
  }

  function updateTitleLanguageButton() {
    const button = $("#atv-toggle-title-language");
    if (!button) return;
    button.textContent = state.titleMode === "translated" ? "显示英文" : "显示中文";
    button.disabled = state.titleTranslations.size === 0;
  }

  async function submitSelected() {
    const videos = state.videos.filter((video) => state.selected.has(video.url));
    if (!videos.length) {
      log("没有选中的视频。");
      return;
    }
    try {
      const settings = collectSettings();
      await api("POST", "/api/settings", settings);
      const response = await api("POST", "/api/jobs", { urls: videos.map((video) => video.url), items: videos, settings });
      state.lastJobId = response.job.id;
      state.lastBatchDir = response.job.batch_dir || null;
      log(`已提交任务 ${state.lastJobId}，共 ${response.job.counts.total} 个视频。`);
      renderJob(response.job);
      startPolling();
    } catch (error) {
      log(`提交失败：${error.message}`);
    }
  }

  async function controlJob(action, okMessage) {
    if (!state.lastJobId) {
      log("还没有可控制的任务。");
      return;
    }
    try {
      const response = await api("POST", `/api/jobs/${state.lastJobId}/${action}`);
      log(okMessage);
      renderJob(response.job);
      startPolling();
    } catch (error) {
      log(`任务控制失败：${error.message}`);
    }
  }

  async function resummarizeLastJob() {
    if (!state.lastJobId) {
      log("还没有可重新总结的任务。");
      return;
    }
    try {
      const response = await api("POST", `/api/jobs/${state.lastJobId}/resummarize`);
      state.lastJobId = response.job.id;
      state.lastBatchDir = response.job.batch_dir || state.lastBatchDir;
      log(`已提交重新总结任务 ${state.lastJobId}。`);
      renderJob(response.job);
      startPolling();
    } catch (error) {
      log(`重新总结失败：${error.message}`);
    }
  }

  async function retryItem(url) {
    if (!state.lastJobId || !url) {
      log("还没有可重试的视频。");
      return;
    }
    try {
      const response = await api("POST", `/api/jobs/${state.lastJobId}/retry-item`, { url });
      log("已将该视频放回队列重试。");
      renderJob(response.job);
      startPolling();
    } catch (error) {
      log(`重试失败：${error.message}`);
    }
  }

  function startPolling() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    refreshLastJob();
    state.pollTimer = setInterval(refreshLastJob, 2500);
  }

  async function refreshLastJob() {
    if (!state.lastJobId) return;
    try {
      const response = await api("GET", `/api/jobs/${state.lastJobId}`);
      const job = response.job;
      state.lastBatchDir = job.batch_dir || state.lastBatchDir;
      state.itemByUrl.clear();
      (job.items || []).forEach((item) => {
        if (item.url) state.itemByUrl.set(item.url, item);
      });
      renderVideos();
      renderJob(job);
      if (["done", "failed", "cancelled"].includes(job.status) && state.pollTimer) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
      }
    } catch (error) {
      log(`刷新任务失败：${error.message}`);
    }
  }

  function renderJob(job) {
    if (!job) {
      jobEl.innerHTML = `<div class="atv-job-empty">还没有提交任务。</div>`;
      return;
    }
    const counts = job.counts || {};
    jobEl.innerHTML = "";
    const header = document.createElement("div");
    header.className = "atv-job-head";
    header.innerHTML = `
      <div><strong>任务 ${escapeHtml(job.id)}</strong> · ${escapeHtml(job.status)}</div>
      <div>总数 ${counts.total || 0}，完成 ${counts.done || 0}，失败 ${counts.failed || 0}，取消 ${counts.cancelled || 0}</div>
      <div class="atv-job-path"></div>
    `;
    header.querySelector(".atv-job-path").textContent = job.batch_dir ? `输出：${job.batch_dir}` : "";
    jobEl.appendChild(header);

    (job.items || []).forEach((item, index) => {
      const card = document.createElement("div");
      card.className = `atv-job-item atv-status-${item.status}`;
      const progress = item.progress || {};
      const percent = Number.isFinite(progress.percent) ? progress.percent : statusPercent(item.status);
      card.innerHTML = `
        <div class="atv-job-title"></div>
        <div class="atv-job-meta"></div>
        <div class="atv-progress"><span></span></div>
        <div class="atv-job-actions"></div>
      `;
      card.querySelector(".atv-job-title").textContent = `${index + 1}. ${item.title || item.id || item.url}`;
      card.querySelector(".atv-job-meta").textContent = item.error
        ? `${transcriptSourceLabel(item)} · 失败 ${item.error_stage ? `[${item.error_stage}] ` : ""}${item.error}`
        : `${transcriptSourceLabel(item)} · ${progress.message || item.status || ""}${progress.chunk_total ? ` (${progress.chunk_index}/${progress.chunk_total})` : ""}`;
      card.querySelector(".atv-progress span").style.width = `${percent || 0}%`;
      const actions = card.querySelector(".atv-job-actions");
      if (item.output_dir || item.output) {
        const open = document.createElement("button");
        open.className = "atv-link-btn";
        open.textContent = "打开输出文件夹";
        open.addEventListener("click", () => openOutputPath(item.output_dir || item.output));
        actions.appendChild(open);
      }
      if (["failed", "cancelled"].includes(item.status)) {
        const retry = document.createElement("button");
        retry.className = "atv-link-btn";
        retry.textContent = "重试此视频";
        retry.addEventListener("click", () => retryItem(item.url));
        actions.appendChild(retry);
      }
      jobEl.appendChild(card);
    });
  }

  async function openOutputPath(path) {
    if (!path) {
      log("还没有可打开的输出目录。");
      return;
    }
    try {
      const response = await api("POST", "/api/open-path", { path });
      if (response.fallback) log(`原目录还不存在，已打开最近的父目录：${response.path}`);
      else log(`已打开目录：${response.path}`);
    } catch (error) {
      log(`打开目录失败：${error.message}`);
    }
  }

  function api(method, path, body, timeoutMs = 20000) {
    return new Promise((resolve, reject) => {
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        reject(new Error(`扩展请求超时：${path}`));
      }, timeoutMs + 2000);

      chrome.runtime.sendMessage({ type: "api", method, path, body, timeoutMs }, (response) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        const runtimeError = chrome.runtime.lastError;
        if (runtimeError) {
          reject(new Error(runtimeError.message));
          return;
        }
        if (!response || !response.ok) {
          reject(new Error((response && response.error) || "扩展后台请求失败"));
          return;
        }
        resolve(response.data || {});
      });
    });
  }

  function setValue(selector, value) {
    if (value === undefined || value === null) return;
    const element = $(selector);
    if (element) element.value = String(value);
  }

  function normalizedPageHref() {
    try {
      const url = new URL(location.href);
      url.hash = "";
      return url.toString();
    } catch {
      return location.href.split("#")[0];
    }
  }

  function makePageKey() {
    try {
      const url = new URL(location.href);
      url.hash = "";
      if (url.hostname.includes("youtube.com") || url.hostname.includes("youtu.be")) {
        const parts = [url.origin, url.pathname];
        ["v", "q", "list"].forEach((name) => {
          const value = url.searchParams.get(name);
          if (value) parts.push(`${name}=${value}`);
        });
        const tab = activePageHint();
        if (tab) parts.push(`tab=${tab}`);
        return parts.join("|");
      }
      return `${url.origin}${url.pathname}${url.search}|${activePageHint()}`;
    } catch {
      return `${location.href.split("#")[0]}|${activePageHint()}`;
    }
  }

  function activePageHint() {
    const selectors = [
      '[aria-selected="true"]',
      '[role="tab"][class*="active"]',
      '.bili-tabs__nav-item--active',
      '.vui_tabs--active',
      '.bili-feed4-tab-item.active',
      '.channel-link--active',
      '.is-active',
      '.selected',
    ];
    for (const selector of selectors) {
      const nodes = Array.from(document.querySelectorAll(selector)).slice(0, 20);
      for (const node of nodes) {
        const existingPanel = document.getElementById(PANEL_ID);
        if (existingPanel && existingPanel.contains(node)) continue;
        if (!isProbablyVisible(node)) continue;
        const text = cleanTitle(node.textContent || node.getAttribute("title") || node.getAttribute("aria-label") || "");
        if (text && text.length <= 40 && !isNoiseTitle(text)) return text;
      }
    }
    return "";
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  }

  function shortError(message) {
    return String(message || "请求失败").replace(/^Error:\s*/i, "").slice(0, 90);
  }

  function chunkList(items, size) {
    const chunks = [];
    for (let index = 0; index < items.length; index += size) {
      chunks.push(items.slice(index, index + size));
    }
    return chunks;
  }

  function log(message) {
    logEl.textContent = message;
  }
})();
