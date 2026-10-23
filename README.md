# python-desktop-tools

桌面小工具。

### 启动

```bash
pip install -r requirements.txt
python -m launcher
```

主界面用网格列出图片压缩、JSON 格式化、SVGA 预览、动图播放、时间戳转换、颜色转换、ADB 和 APK 信息，点击卡片进入对应工具，点「返回」回到主界面。

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

把 JSON 排成缩进文本，或压成一行。中文不会被转成 `\u` 转义。输入和结果里的键名、字符串、数字、布尔、null 会分色。

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

## 颜色转换

在 Android 色值之间互转，并预览带透明度的颜色。

### 启动

```bash
python -m color_converter
```

输入支持 `#RGB`、`#ARGB`、`#RRGGBB`、`#AARRGGBB`（可以不写 `#`）、`0x` 加 6 位或 8 位、`Color.parseColor("...")`、`rgb()` 和 `rgba()`。短写按 Android 规则展开：`#RGB` 透明度为 `FF`，`#ARGB` 每位数字重复一次。`rgba` 的透明度写 `50%` 按百分比，带小数点按 0 到 1，大于 1 的整数按 0 到 255。

结果给出 `#AARRGGBB`、`#RRGGBB`、`Color.parseColor`、`0xAARRGGBB`、RGB 分量和透明度，可以分别复制。色块叠在棋盘格上，能看出透明度。拖动滑块或在输入框填写 0 到 100 的整数后回车，只改透明度。

## ADB

查看已连接 Android 手机的屏幕密度，并标出资源目录档位。320 dpi 显示为 xh（xhdpi），480 dpi 显示为 xxh（xxhdpi）。手机改过显示大小时，以覆盖密度为准。

### 启动

```bash
python -m screen_density
```

需要本机能运行 `adb`。程序会依次查找 PATH、`ANDROID_HOME`、`ANDROID_SDK_ROOT`，以及 `%LOCALAPPDATA%\Android\Sdk\platform-tools`。手机需打开 USB 调试并授权。

进入页面或点「刷新」后查询。多台手机时从下拉框切换，名称后面带设备 ID。结果包含当前档位、目录名、当前密度、物理密度、倍数、分辨率、宽高 dp、型号、Android 版本和设备 ID。当前密度和设备 ID 可以复制。把文件拖进手机卡片里的框：apk 会覆盖安装到当前这台手机，其他文件复制到该手机的 `/sdcard` 并保留原文件名。一次拖入多个文件时按顺序处理，失败则停止。点「截屏」把当前画面保存为 PNG。点「录屏」开始录制，再点「停止」结束；最长 3 分钟，到时会自动结束。截屏和录屏都保存到 `D:\adb`，文件名带时间，同一秒重名时追加 `_2`。未连接、未授权或找不到 adb 时，底部状态栏会说明原因。从主界面进入时，点「返回」会丢弃尚未返回的查询结果，已经开始的安装或复制会继续，只是不再更新界面。正在进行的录屏会停止，并把视频保存到 `D:\adb`；界面已经关闭时不再更新状态。

## APK 信息

查看 apk 的应用名、包名、版本、minSdk、targetSdk、ABI 和权限。

### 启动

```bash
python -m apk_info
```

点「打开 APK」或把 apk 拖进窗口。一次拖入多个文件时，只读取第一个 apk。需要本机的 aapt。程序会依次查找 PATH 里的 aapt2、aapt，以及 `ANDROID_HOME`、`ANDROID_SDK_ROOT`、`%LOCALAPPDATA%\Android\Sdk` 和 adb 所在 SDK 的 `build-tools`，用其中版本最高的一档。应用名优先用简体中文。没有原生库时 ABI 显示「无原生库」。`uses-permission-sdk-23` 会在权限名后注明 API 23+。各字段可以分别复制。找不到 aapt 或文件不是 apk 时，底部状态栏会说明原因。
