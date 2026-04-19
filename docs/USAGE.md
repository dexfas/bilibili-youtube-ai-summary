# 使用说明

这份说明面向日常使用：怎么启动、怎么用浏览器插件批量处理视频、怎么用命令行、输出文件在哪里，以及遇到问题怎么排查。

## 1. 每次使用前先启动本地服务

打开命令行，进入项目目录：

```powershell
cd C:\Users\devil\Documents\Ai\auto-tran-video
```

启动服务：

```powershell
.\.venv\Scripts\python.exe main.py serve
```

看到类似下面的信息就说明启动成功：

```text
Uvicorn running on http://127.0.0.1:8765
```

这个窗口不要关。插件、标题翻译、批量任务、打开输出目录都依赖这个本地服务。

## 2. 浏览器插件使用流程

打开 Edge 或 Chrome 的扩展管理页：

```text
edge://extensions/
chrome://extensions/
```

第一次安装：

1. 打开“开发人员模式”
2. 点击“加载解压缩的扩展”
3. 选择项目里的 `extension` 文件夹

以后代码更新后：

1. 打开扩展管理页
2. 找到“视频转文字工作台”
3. 点击“重新加载”
4. 刷新 Bilibili 或 YouTube 页面

## 3. 插件处理视频的基本步骤

1. 先启动本地服务：

```powershell
.\.venv\Scripts\python.exe main.py serve
```

2. 打开 Bilibili 或 YouTube 页面。

3. 页面右侧会出现一个小悬浮窗，点“展开”。

4. 等它自动扫描当前页面视频，或者点“扫描当前页”。

5. 勾选要处理的视频。

6. 选择任务模式：

```text
只转写
转写并总结
英文视频中文化
只转音频
下载视频 MP4
```

7. 选择速度/质量：

```text
最快：tiny + cpu + int8
均衡：small + cpu + int8
高质量：medium + cpu + int8
```

8. 点“提交选中视频”。

9. 在任务区查看进度，完成后点“打开输出文件夹”。

## 4. 常用任务模式说明

### 只转写

生成：

```text
transcript.txt
transcript.srt
transcript.json
```

适合只想拿文字稿和字幕文件。

### 转写并总结

生成：

```text
transcript.txt
transcript.srt
summary.md
```

适合中文视频、课程、访谈、解说、资料整理。

### 英文视频中文化

生成：

```text
transcript.txt        英文原稿
transcript.srt        英文字幕
translation.zh.md     中文翻译
summary.md            中文总结
```

这个模式会优先基于中文翻译生成总结，中文表达更自然。

### 只转音频

生成：

```text
audio.m4a
audio.wav
audio.mp3
```

插件里可以选择音频格式。适合只想保存音频。

### 下载视频 MP4

生成：

```text
video.mp4
metadata.json
```

只下载当前账号权限下可获取的视频，不绕过会员、付费、年龄或地区限制。

## 5. 输出文件在哪里

浏览器插件提交的批量任务默认在：

```text
output/
  browser_batches/
    YYYYMMDD_HHMMSS_jobid/
      视频ID_视频标题/
        transcript.txt
        transcript.srt
        transcript.json
        translation.zh.md
        summary.md
        metadata.json
        state.json
```

在插件里可以点“选择目录”修改输出根目录。

任务完成后可以点：

```text
打开批次目录
打开输出文件夹
```

## 6. 字幕来源怎么看

插件会显示字幕来源：

```text
已有字幕
自动字幕
ASR 转写
来源判断中
来源未知
```

含义：

```text
已有字幕：平台提供的人工字幕
自动字幕：平台提供的自动字幕
ASR 转写：没有可用字幕，使用本地 faster-whisper 转写
来源判断中：任务刚开始，还在检查字幕
来源未知：旧任务或异常情况下没有记录来源
```

如果能拿到平台字幕，速度会比 ASR 快很多。

## 7. YouTube 标题翻译

在 YouTube 页面扫描出视频后，可以点：

```text
翻译标题
```

标题翻译使用本地 Ollama 模型，默认：

```text
qwen3.5:2b
```

它只改变插件里显示的标题，不会改变视频链接、输出目录或任务 metadata。

可以点：

```text
显示中文 / 显示英文
```

在原始标题和中文标题之间切换。

## 8. 模型怎么选

推荐先这样用：

```text
ASR 模型：small
ASR 设备：cpu
计算类型：int8
Beam：1
总结模型：qwen-summary:1.5b
标题翻译模型：qwen3.5:2b
正文翻译模型：qwen3:8b
```

如果机器比较慢：

```text
ASR 模型改 tiny
速度/质量选 最快
总结模型用 1.5b 或 2b
```

如果你有 NVIDIA 显卡，并且 CUDA 环境正常：

```text
ASR 设备可以试 cuda
计算类型可以试 float16
```

如果出现 `cublas64_12.dll is not found`，说明 CUDA 依赖没配好。此时先改回：

```text
设备：cpu
计算类型：int8
```

## 9. 并发怎么设置

默认推荐：

```text
稳定：下载 1，ASR 1，Ollama 1
```

想快一点可以选：

```text
加速：下载 3，ASR 1，Ollama 1
```

不要一开始就把 ASR 和 Ollama 并发开高。本地模型很吃 CPU、内存或显存，开太高反而可能更慢，甚至卡死。

## 10. 命令行用法

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

从 txt 文件读取链接：

```powershell
.\.venv\Scripts\python.exe main.py --summarize batch --file ".\urls.txt"
```

本地视频：

```powershell
.\.venv\Scripts\python.exe main.py local "D:\videos\demo.mp4"
```

总结已有文字稿：

```powershell
.\.venv\Scripts\python.exe main.py summarize "output\某个视频目录"
```

## 11. cookies 怎么用

有些视频需要登录、会员权限、年龄验证或地区权限。这类视频需要你自己的 cookies。

插件高级设置里可以填：

```text
cookies.txt 路径
```

或者：

```text
cookies-from-browser: edge
cookies-from-browser: chrome
cookies-from-browser: firefox
```

如果浏览器正在运行，cookies 数据库可能被锁。关闭浏览器后再试更稳。

## 12. 常见问题

### 插件显示本地服务未连接

确认服务窗口还开着：

```powershell
.\.venv\Scripts\python.exe main.py serve
```

然后刷新网页或重新加载扩展。

### 标题翻译超时

标题翻译走本地 Ollama。确认 Ollama 正在运行：

```powershell
ollama list
```

如果模型太慢，可以换小一点的标题翻译模型。

### 总结为空或失败

常见原因：

```text
Ollama 没启动
模型名填错
模型太弱或响应为空
上下文太长
```

建议先用：

```text
qwen-summary:1.5b
```

或者换成你本机确认能正常对话的模型。

### 下载视频报权限问题

项目不会绕过平台权限。需要登录的视频请配置 cookies。

### 不想上传 output

不用担心，下面这些已经被 `.gitignore` 排除：

```text
output/
cache/
.venv/
models/
```

正常使用 `git add .` 不会把它们上传。

## 13. 更新到 GitHub

修改代码或文档后：

```powershell
cd C:\Users\devil\Documents\Ai\auto-tran-video
git status
git add .
git commit -m "Update project"
git push
```

如果显示：

```text
nothing to commit, working tree clean
```

说明没有新改动需要上传。

