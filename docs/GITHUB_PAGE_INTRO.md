# Bilibili YouTube AI Summary（B站 YouTube AI总结助手）页面介绍

## 仓库简介

Bilibili YouTube AI Summary（B站 YouTube AI总结助手）是一个专门用于 AI 总结 B 站和 YouTube 视频内容的本地工具。它支持 Bilibili、YouTube 和本地视频文件，通过 `yt-dlp` 获取音频或字幕，用 `faster-whisper` 在本机完成语音识别，并调用 Ollama 本地模型生成中文翻译、标题翻译和视频总结。

## 一句话介绍

本地运行的 Bilibili / YouTube 视频 AI 总结、字幕提取、批量转写和翻译助手。

## GitHub About 描述

Bilibili YouTube AI Summary: local AI video summarizer for Bilibili and YouTube, with subtitle extraction, transcription, translation, Ollama summaries, CLI and browser extension.

## Topics

```text
bilibili
youtube
ai-summary
video-summary
video-summarizer
speech-to-text
whisper
faster-whisper
ollama
yt-dlp
video-transcription
browser-extension
local-first
```

## 推荐仓库名

```text
bilibili-youtube-ai-summary
```

## 适合写在发布页的介绍

这个项目面向需要快速理解视频内容的人：可以在 Bilibili 或 YouTube 页面直接扫描当前视频，批量加入本地队列，自动优先读取已有字幕，没有字幕时再使用本地 `faster-whisper` 转写。转写完成后，可以继续交给本地 Ollama 模型做中文翻译、AI 总结、标题翻译和笔记整理。

项目默认只在本机运行，不需要 API Key，不上传音频或转写内容到第三方服务。缓存、输出、虚拟环境和本地模型文件都已被 `.gitignore` 排除，适合把源码公开到 GitHub。

## GitHub Pages 设置建议

可以把 GitHub Pages 设置为：

```text
Source: Deploy from a branch
Branch: main
Folder: /root
```

这样根目录 `index.html` 会成为项目展示页。

注意：在 GitHub 仓库里直接点 `index.html` 看到的是 HTML 源码。必须打开 GitHub Pages 生成的网址，或者在本地双击 `index.html`，才会看到渲染后的展示页。
