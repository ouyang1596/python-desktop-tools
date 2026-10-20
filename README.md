# python-desktop-tools

桌面小工具。

### 启动

```bash
pip install -r requirements.txt
python -m launcher
```

主界面用网格列出图片压缩、JSON 格式化、SVGA 预览、动图播放和时间戳转换，点击卡片进入对应工具，点「返回」回到主界面。

## 图片压缩

使用 TinyPNG（Tinify）压缩 PNG、JPEG、WebP、AVIF。API Key 保存在本机 `%APPDATA%\python-desktop-tools\tinypng.json`，不会写入仓库。

### 安装

```bash
pip install -r requirements.txt
```

### 启动

```bash
python -m image_compressor
```

在窗口顶部填入 TinyPNG API Key 并保存。免费额度每月 500 次，窗口会显示本月已用次数。

添加图片后点击「开始压缩」。结果写到所选输出目录（默认是第一张图片旁边的 `compressed` 文件夹），文件名保持不变，不会修改原图。输出目录里已有的同名文件会被覆盖。

## JSON 格式化

把 JSON 排成缩进文本，或压成一行。中文不会被转成 `\u` 转义。

### 启动

```bash
python -m json_formatter
```

左侧粘贴或打开 `.json` 文件，选择 2 空格或 4 空格缩进后点「格式化」。解析失败时，底部会标出第几行、第几列。右侧结果可以复制或另存为文件。

## SVGA 预览

播放 SVGA 1.x 和 2.x 动画，并查看画布、帧率、总帧数和图层数。文件里如果带音频，信息栏会注明，预览不播放声音。预览按原始像素显示，只有画面大于预览区时才会缩小到窗口内。

### 启动

```bash
python -m svga_preview
```

点「打开文件」选择 `.svga`。打开后自动播放，可以暂停、拖动帧滑块、开关循环，或把速度调成 0.5x / 2x。透明区域用棋盘格显示。从主界面进入时，点「返回」会暂停播放。

## 动图播放

播放 GIF 和动画 WebP，并查看画布、帧数、总时长和循环次数。单帧时长小于 20 毫秒时按 100 毫秒播放。预览按原始像素显示，只有画面大于预览区时才会缩小到窗口内。

### 启动

```bash
python -m anim_preview
```

点「打开文件」或把 `.gif`、`.webp` 拖进预览区。打开后自动播放，可以暂停、拖动帧滑块、开关循环，或把速度调成 0.5x / 2x。透明区域用棋盘格显示。从主界面进入时，点「返回」会暂停播放。

## 时间戳转换

在 Unix 时间戳和日期时间之间互转，支持秒和毫秒。

### 启动

```bash
python -m timestamp_converter
```

窗口顶部显示当前本地时间，以及对应的秒、毫秒时间戳，每秒刷新。时间戳转日期时可选择自动识别单位，或指定秒、毫秒；整数部分不超过 10 位按秒处理，更长按毫秒处理。日期转时间戳支持 `YYYY-MM-DD HH:MM:SS`、带毫秒、仅日期、`YYYY/MM/DD HH:MM:SS` 和 ISO 8601。时区可选本机或 UTC；字符串里如果写了 `Z` 或 `±HH:MM`，以字符串中的时区为准。结果可以分别复制。从主界面进入时，点「返回」会停止刷新当前时间。
