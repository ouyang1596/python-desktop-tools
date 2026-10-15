# python-desktop-tools

桌面小工具。

### 启动

```bash
pip install -r requirements.txt
python -m launcher
```

主界面用网格列出图片压缩和 JSON 格式化，点击卡片进入对应工具，点「返回」回到主界面。

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
