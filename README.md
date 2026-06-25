# 批量压缩打包

批量压缩 + 蓝奏云上传工具，提供 **命令行（CLI）** 与 **图形界面（GUI）** 两种用法：

- **CLI** —— 跨平台（**Windows / Linux / macOS**），核心打包仅依赖 Python 标准库，无需安装任何第三方库即可使用，适合服务器、自动化脚本、CI。
- **GUI** —— **PyQt6 + QFluentWidgets**（Windows 11 Fluent 设计风格，过渡/涟漪动效、亮/暗主题自动跟随、Mica 云母效果），未引入 WebEngine，发布包保持轻量。内置 WebView2 登录为 Windows 专属。

## 功能

- 现代 Fluent 界面，PC 端优化（高 DPI 自适应、原生拖拽、可滚动页面）
- 支持拖入文件或文件夹，也支持手动选择文件 / 目录
- 按 `100MB` 上限把文件分组，生成多个独立压缩包；**以最终压缩结果严格控制每包 ≤ 100MB**，超限自动二分拆包
- 支持 `zip` / `7z` / `rar` 三种格式（7z/rar 优先用程序目录 `tools` 内置工具）
- 输出到原目录或自定义目录
- 单独选择现成压缩包上传到蓝奏云，带上传进度
- 上传完成后显示分享链接，可一键复制

## 命令行（CLI）

跨平台命令行入口，纯打包零依赖（仅在用到上传时才需要安装上传依赖）。

运行方式（任选其一）：

```bash
python -m pyapp pack ...      # 从源码运行
python -m pyapp.cli pack ...  # 等价
bachpack pack ...             # pip 安装后 / 下载预编译可执行文件
```

子命令：

```bash
# 把若干文件 / 目录按每包 ≤100MB 切分压缩为 zip，输出到 ./out
bachpack pack ./photos ./report.pdf -o ./out -f zip

# 7z 格式，自定义前缀，单包上限改为 50MB
bachpack pack ./data -f 7z -p backup --limit 50

# 手动指定压缩程序路径（覆盖自动检测）
bachpack pack ./data -f 7z --tool-path /usr/bin/7zz

# 打包并自动上传到蓝奏云（Cookie 走环境变量）
export LANZOU_COOKIE='ylogin=...; phpdisk_info=...'
bachpack pack ./data --upload --folder-id 0

# 仅上传现成压缩包，并以 JSON 输出分享链接
bachpack upload out/*.zip --cookie-file cookie.txt --json

# 查看当前检测到的压缩工具
bachpack tools
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `-f, --format {zip,7z,rar}` | 压缩格式，默认 `zip` |
| `-o, --output DIR` | 输出目录，默认放在输入的公共父目录 |
| `-p, --prefix NAME` | 压缩包文件名前缀 |
| `--limit MB` | 单包大小上限（MB），默认 100 |
| `--tool-path PATH` | 手动指定 7z / rar 可执行文件 |
| `--upload` | 打包后自动上传蓝奏云 |
| `--cookie / --cookie-file` | 蓝奏云 Cookie；也可用环境变量 `LANZOU_COOKIE` |
| `--folder-id ID` | 目标文件夹 ID，留空传到根目录 |
| `--json` | 以 JSON 输出结果（便于脚本解析） |
| `-q, --quiet` | 安静模式 |

> **压缩工具**：`zip` 优先用 7-Zip，找不到时回退到 Python 内置 zip（永远可用）；`7z` / `rar` 需要系统装有对应工具，或随程序放到 `tools/` 子目录。Linux/macOS 上 7-Zip 的可执行名通常是 `7zz`（`brew install sevenzip` / `apt install 7zip`）。

## 蓝奏云登录（三种方式）

1. **内置登录（推荐，最稳）**：用系统自带的 WebView2 在程序内打开蓝奏云登录页，登录成功后通过 WebView2 CookieManager 直接抓取 Cookie（含 HttpOnly）。不打包 Chromium，不受浏览器锁库 / 新版加密影响。
2. **从浏览器导入**：浏览器已登录蓝奏云后台时直接读取其 Cookie。支持 Edge / Chrome / Firefox / Brave / Vivaldi / Opera / Opera GX / Chromium / Arc / LibreWolf。注意：**新版 Edge / Chrome 运行时会独占锁住 Cookie 库**，需先完全退出该浏览器或以管理员身份运行本程序。
3. **粘贴 Cookie**：至少包含 `ylogin` 和 `phpdisk_info`（浏览器 F12 → 应用 → Cookie → `pc.woozooo.com`）。

> 内置登录需要系统已安装 **WebView2 运行时**（Windows 11 默认自带；Win10 多数也有，缺失时可从微软官网安装 Evergreen 版）。

> 程序不会在界面上明文显示 Cookie。`目标文件夹 ID` 留空时上传到根目录。

## 启动 GUI

Windows 直接双击：

```text
launch.pyw
```

命令行启动：

```bash
python -m pyapp        # 无参数 = 图形界面
```

> 需要 Python 3.9–3.14（已在 3.14 + PyQt6 6.11 上验证）。GUI 在 Linux/macOS 上需自行安装 PyQt6（内置 WebView2 登录为 Windows 专属，其余登录方式通用）。

## 安装与依赖

CLI 纯打包零依赖，直接 `python -m pyapp.cli pack ...` 即可。需要上传或图形界面时再装对应可选依赖：

```bash
pip install -e .            # 安装 bachpack / bachpack-gui 命令
pip install -e ".[upload]"  # + 蓝奏云上传依赖
pip install -e ".[gui]"     # + 图形界面依赖
```

GUI 依赖也已 vendored 到 `.python_deps`，如需重装：

```powershell
python -m pip install --target .python_deps -r requirements.txt
```

## 打包成可执行文件

```bash
# CLI 单文件（跨平台）
bash scripts/build-cli.sh                                          # Linux / macOS → dist/bachpack
powershell -ExecutionPolicy Bypass -File .\scripts\build-cli.ps1   # Windows → dist/bachpack.exe
```

```powershell
# GUI（Windows）
powershell -ExecutionPolicy Bypass -File .\scripts\build-exe.ps1       # 单文件 exe
powershell -ExecutionPolicy Bypass -File .\scripts\build-portable.ps1  # 便携版 onedir（可随包附带 7z / rar）
```

GitHub Actions（`.github/workflows/build.yml`）会在 Ubuntu / Windows / macOS 上跑测试并构建 CLI；打 `v*` tag 时自动发布三平台预编译产物到 Release。

## 测试

```bash
python -m unittest discover -s tests
```

## 结构

- `pyapp/core.py` —— 打包纯逻辑（扫描、分组装箱、压缩、自动拆包；跨平台工具检测）
- `pyapp/cli.py` —— 命令行接口（pack / upload / tools）
- `pyapp/lanzou_client.py` —— 蓝奏云登录 / 上传 / Cookie 获取
- `pyapp/ui/` —— PyQt6 + QFluentWidgets 界面层
- `pyapp/app.py` —— 程序入口（按参数路由到 CLI 或 GUI）
- `scripts/` —— 各平台构建脚本；`batch-packager-cli.spec` 为 CLI 打包规格
