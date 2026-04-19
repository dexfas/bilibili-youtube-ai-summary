# VidScribe Local（影译笔记）

本地视频转文字、字幕提取、翻译和总结工具。支持 Bilibili、YouTube、本地视频/音频文件，提供命令行和浏览器扩展两种使用方式。

核心目标是：尽量把视频学习、资料整理和批量归档这件事放在本机完成。语音识别使用本地 `faster-whisper`，总结、正文翻译和标题翻译使用本地 Ollama 模型，不需要 API Key。

> 项目英文名：VidScribe Local  
> 项目中文名：影译笔记  
> 命令行包名暂时保留 `auto-tran-video`，避免影响现有用法。

## 功能

- Bilibili / YouTube / 本地文件转写为 `transcript.txt`、`transcript.srt`、`transcript.json`
- 优先读取平台已有字幕，找不到字幕再走本地 ASR 转写
- 英文视频中文化：保留英文原稿，额外输出中文翻译和中文总结
- 使用 Ollama 本地模型生成视频总结、翻译正文、翻译 YouTube 标题
- 长视频切块处理，支持续跑，避免中断后全量重来
- 批量链接处理，单个失败不影响后续任务
- 浏览器扩展扫描 Bilibili / BewlyCat / YouTube 当前页面已加载视频
- 浏览器扩展支持批量选择、输出目录选择、任务进度、失败重试、打开输出文件夹
- 受控并发：下载、ASR、Ollama 分阶段限流，避免本地资源被打满

## 架构

```text
浏览器页面 / CLI
  -> yt-dlp 获取音频、视频或字幕
  -> ffmpeg / imageio-ffmpeg 转换音频
  -> faster-whisper 本地语音识别
  -> OpenCC 统一简体中文
  -> Ollama 本地模型总结/翻译
  -> Markdown / TXT / SRT / JSON 输出
```

主要目录：

```text
auto_tran_video/      Python 核心逻辑
extension/            Edge / Chrome MV3 浏览器扩展
browser/              油猴脚本旧版入口
docs/                 项目介绍和说明文档
output/               本地输出目录，不上传 GitHub
cache/                下载和处理中间缓存，不上传 GitHub
models/               本地模型或临时模型文件，不上传 GitHub
```

## 安装

建议使用 Python 3.12。

```powershell
git clone <your-repo-url>
cd auto-tran-video
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

本项目依赖 `imageio-ffmpeg`，通常不需要单独安装系统 ffmpeg。

## 使用说明

新用户建议先看完整使用说明：

- [docs/USAGE.md](docs/USAGE.md)

项目展示页：

- [docs/index.html](docs/index.html)

## Ollama 模型

先确保 Ollama 已启动：

```powershell
ollama list
```

浏览器扩展里可以选择本机 Ollama 模型。推荐配置：

```text
总结模型：qwen-summary:1.5b
标题翻译：qwen3.5:2b
正文翻译：qwen3:8b 或更强模型
```

如果你的机器没有这些模型，可以在扩展设置里换成自己已有的模型。

## CLI 用法

始终在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe main.py --help
```

单个 Bilibili 或 YouTube 视频：

```powershell
.\.venv\Scripts\python.exe main.py bilibili "https://www.bilibili.com/video/BVxxxx/"
```

转写并总结：

```powershell
.\.venv\Scripts\python.exe main.py --summarize bilibili "https://www.bilibili.com/video/BVxxxx/"
```

英文视频中文化：

```powershell
.\.venv\Scripts\python.exe main.py --english-cn bilibili "https://www.bilibili.com/video/BVxxxx/"
```

批量链接：

```powershell
.\.venv\Scripts\python.exe main.py --summarize batch "https://www.bilibili.com/video/BVxxxx/" "https://www.youtube.com/watch?v=xxxx"
```

从文本文件读取链接：

```powershell
.\.venv\Scripts\python.exe main.py --summarize batch --file ".\urls.txt"
```

本地文件：

```powershell
.\.venv\Scripts\python.exe main.py local "D:\videos\demo.mp4"
```

总结已有转写结果：

```powershell
.\.venv\Scripts\python.exe main.py summarize "output\某个视频目录"
```

## 浏览器扩展

启动本地服务：

```powershell
.\.venv\Scripts\python.exe main.py serve
```

默认服务地址：

```text
http://127.0.0.1:8765
```

安装扩展：

1. 打开 `edge://extensions/` 或 `chrome://extensions/`
2. 打开开发人员模式
3. 点击“加载解压缩的扩展”
4. 选择项目里的 `extension` 目录

更新扩展源码后，需要在扩展管理页点击“重新加载”，然后刷新 Bilibili 或 YouTube 页面。

扩展能力：

- 当前页面可见视频扫描
- 页面切换后清空旧扫描结果
- 下拉加载后自动增量扫描
- YouTube 标题本地翻译
- 批量提交任务
- 暂停队列、继续队列、停止当前项、取消未开始项
- 失败项单独重试
- 显示字幕来源：已有字幕、自动字幕、ASR 转写
- 打开批次目录或单个视频输出目录

## 输出

浏览器扩展提交的批量任务默认输出到：

```text
output/
  browser_batches/
    YYYYMMDD_HHMMSS_jobid/
      BVxxxx_视频标题/
        audio.wav
        transcript.txt
        transcript.srt
        transcript.json
        translation.zh.md
        summary.md
        metadata.json
        state.json
```

`output/`、`cache/`、`.venv/`、`models/` 默认被 `.gitignore` 排除，不会上传到 GitHub。

## 并发

并发采用“批次内并发、按阶段限流”：

```text
稳定：下载 1，ASR 1，Ollama 1
加速：下载 3，ASR 1，Ollama 1
自定义：下载 1-5，ASR 1-2，Ollama 1-2
```

推荐先使用“稳定”或“加速”。ASR 和 Ollama 默认单并发，是为了避免 CPU、显存和内存被同时打满。

## cookies 和权限

项目不会绕过 Bilibili 或 YouTube 的权限限制。需要登录、会员、年龄限制或地区限制的视频，需要用户自己提供 cookies。

支持：

```text
cookies.txt 路径
cookies-from-browser: chrome / edge / firefox
```

## 注意

- “不下载视频”在技术上不能做到 100%。转写必须读取音频数据，但本项目可以只缓存音频，不保存完整视频。
- 批量处理请遵守平台规则和版权要求，建议用于个人学习、检索和资料整理。
- 本项目默认只绑定本机 `127.0.0.1`，浏览器扩展通过本地服务提交任务。
