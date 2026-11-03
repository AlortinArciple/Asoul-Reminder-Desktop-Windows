# ASOUL 直播与动态提醒

这是一个 Windows 11 本地桌面提醒程序，用来监控 ASOUL 日程表和 B 站动态更新，并保留后台托盘模式。

## 功能

- 每 30 分钟检查一次 ASOUL 日程；如果 `calendar/latest` 返回 403，会改读 ASOUL 首页中的日程数据，失败后也至少半小时后再尝试。日程请求默认忽略系统代理。
- 对启用成员的直播，在开播前 10 分钟和 5 分钟各提醒一次。
- 从日程前 10 分钟到开播时间后 2 分钟，每 30 秒检查直播间是否实际开播；检测到开播会立即通知，已取消日程也会检查。
- 检测直播新增、取消、改期、标题变化、成员变化。
- 监控启用成员的 B 站动态。
- 日常每 10 分钟检查一次动态。
- 确认下播后立即刷新该成员动态。
- 如果下播后的立即刷新没有发现新动态，进入该成员专属高频动态检查。
- 高频动态检查每 3 分钟一次，最长持续 1 小时；如果期间发现新动态，提醒后恢复日常检查。
- 含日期的时间统一显示星期几，格式如 `08-29 14:00 星期五`（纯时间不追加），通知文案同步。
- 动态中的 B 站表情支持 GIF 动图渲染（Windows 桌面与 Android）。
- Windows 直播提醒支持按成员开启提示音（默认开启，设置中可关闭），并可为每个成员单独指定音效 .wav 文件（留空跟随全局设置）；正常桌面状态提示音跟随系统通知（与最初版本一致）；检测到全屏游戏/全屏应用等会抑制通知的状态时，自动改由 winsound 播放内置铃声 `C:\Windows\Media\Ring01.wav` 并循环 5 秒。全局设置可自定义提示音 .wav 文件和循环秒数。
- 默认同时启动桌面窗口和后台托盘；关闭窗口只隐藏窗口，监控仍在托盘中运行。
- 桌面窗口/托盘监控为单实例运行，重复启动会直接退出并提示已有实例。
- 心宜、思诺已作为可选成员写入配置，默认不启用。

> 本仓库只包含 Windows 桌面端；Android 端不在本仓库内。

## 下载

已编译的 Windows 安装包与便携包在 [Releases](https://github.com/AlortinArciple/Asoul-Reminder-Desktop-Windows/releases) 页面：

```text
AsoulReminder-Setup.exe
AsoulReminder-portable.zip
```

两者都不包含 `config.json` 或 B 站 Cookie。

## 安装与运行

需要先安装 Python 3.11 或更高版本。

在项目目录运行：

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy config.example.json config.json
python run.py
```

如果你的系统没有 `py` 命令，可以把上面的 `py` 换成 `python`。

如果已经创建过虚拟环境，也可以直接运行：

```powershell
.\.venv\Scripts\python.exe run.py
```

这个命令会打开桌面窗口并启动托盘监控服务。关闭窗口只会隐藏桌面窗口，后台托盘和监控会继续运行；需要完全退出时，在托盘菜单里点 `退出`。

如果只想先启动后台托盘、不自动弹出桌面窗口，可以运行：

```powershell
.\.venv\Scripts\python.exe run.py --tray
```

下面这些命令用于查看状态或执行一次性操作：

```powershell
.\.venv\Scripts\python.exe run.py --status
.\.venv\Scripts\python.exe run.py --check-once
.\.venv\Scripts\python.exe run.py --dump-asoul
.\.venv\Scripts\python.exe run.py --dump-dynamics
.\.venv\Scripts\python.exe run.py --window
```

- `--status`：只读取本地状态，不启动监控。
- `--check-once`：立即检查一次，然后退出；ASOUL 日程仍会遵守半小时节流。
- `--dump-asoul`：打印最近一次 ASOUL 日程快照。
- `--dump-dynamics`：打印最近一次 B 站动态快照。
- `--window`：启动桌面窗口总览，并在后台运行托盘监控。
- `--tray`：启动后台托盘但不自动显示桌面窗口，可以从托盘菜单重新打开窗口。

桌面窗口和托盘监控会做单实例检测；`--status`、`--check-once`、`--dump-asoul`、`--dump-dynamics` 这些一次性命令不受限制。

## 项目结构

```text
asoul_reminder/        程序源码
assets/                图标和成员头像
scripts/               构建、便携包、检查和清理脚本
scripts/portable/      便携包内附带的启动和安装脚本
tests/                 单元测试
config.example.json    可公开的默认配置模板
config.json            当前本机私有配置，可能包含 Cookie，不要公开
run.py                 本地开发入口
```

`build/`、`dist/`、`.venv/`、`__pycache__/` 都是本地生成目录，已经在 `.gitignore` 中排除。

## 维护脚本

常用检查：

```powershell
.\scripts\check.ps1
```

这个脚本会依次执行：

```powershell
.\.venv\Scripts\python.exe -m compileall asoul_reminder run.py
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

清理缓存或构建产物：

```powershell
.\scripts\clean.ps1 -Caches
.\scripts\clean.ps1 -Build
.\scripts\clean.ps1 -Dist
.\scripts\clean.ps1 -All
```

`-Dist` 和 `-All` 会删除已打包的 exe/zip，重新分发前需要再运行 `.\scripts\build.ps1`。

## 桌面窗口

桌面窗口使用 PySide6 实现，采用浅色 neomorphism 风格：暖灰背景、柔和高光/阴影卡片、黄色强调色和彩色成员徽章。

- 左侧显示启用成员的 B 站直播间状态。
- 中间显示今日日程和运行状态；今日日程里不显示已改期条目。
- 日程卡片支持今日/本周切换，刷新图标会绕过半小时节流并立即重新读取日程；托盘菜单也提供 `刷新日程`。
- 本周日程卡中，星期几显示在日期下方（小号灰字）；今日视图仅显示 `HH:mm`。
- 右侧显示单列最新动态，启用成员的动态会按发布时间混排；如果旧快照没有发布时间，界面会显示“发布时间未知”，排序再用最近读取时间兜底。
- 嘉然、乃琳、贝拉默认启用。
- 顶栏齿轮按钮会打开设置窗口，可以修改全局检查间隔、通知开关、提醒时间、请求超时、ASOUL 地址和成员配置；ASOUL 日程检查间隔最低为 30 分钟。
- 设置中的 `日程绕过系统代理` 默认开启，只影响 ASOUL 日程请求，不影响 B 站动态和直播间请求；TUN/全局路由模式仍需在梯子规则中把 `asoul.love` 设为 DIRECT。
- 关闭桌面窗口不会退出程序；托盘菜单中的 `打开桌面窗口` 可以重新显示窗口，托盘图标双击也会唤起窗口。
- 成员头像支持图片替换；如果没有配置图片，会继续使用圆形文字头像。
- 成员配置里的 `显示名称` 可以自定义动态、直播间、通知和状态里显示的中文名；内部英文 id 不会随之改变。
- 成员配置里的 `优先级` 用于同时开播时的通知排序与提示音仲裁：数字越大越优先（可为 0 或负数），允许同级；同时开播时低优先级通知先发、高优先级后发（通知中心里高优先级显示在顶部），且本轮只有最高优先级成员播放提示音，避免提示音互相打断。优先级不影响界面里的成员顺序。
- 成员配置里的 `直播通知音效` 控制该成员的直播提醒（开播前提醒和开播通知）是否带提示音，默认开启；其余通知保持静音。提示音由程序经 winsound 独立播放，可穿过勿扰模式；具体音效文件在全局设置的 `直播提示音文件` 里选择，成员卡的 `音效文件` 可为单个成员指定 .wav（留空跟随全局）。
- 全局设置里的 `直播提示音文件` 可自定义直播提醒的提示音（仅 .wav），`试听`/`停止` 可即时预览；留空使用内置铃声 Ring01.wav。`直播提示音循环/秒` 控制提示音重复时长（0-60，0=只响一次，默认 5）。铃声与循环仅在全屏等系统会抑制通知音效的状态下启用，正常桌面始终用系统通知音。
- `开播同刻动态过滤/分钟` 会跳过同成员在开播时间附近发布的动态提醒，用来避免直播开始时的 B 站直播提醒动态重复弹窗；填 `0` 可关闭过滤。
- Cookie 只显示是否已配置，不在设置窗口里明文展示。
- 设置窗口里的保存操作会写回 `config.json`，当前窗口中的服务配置也会同步刷新。

## 成员头像

可以在设置窗口的成员配置里，为每个成员选择头像图片。支持常见图片格式，例如：

```text
png
jpg
jpeg
webp
bmp
ico
```

也可以直接把图片放到程序目录的：

```text
assets\avatars
```

默认识别这些文件名：

```text
diana.png
eileen.png
bella.png
fiona.png
gladys.png
```

如果使用便携包，解压后同样可以把头像放到 `assets\avatars` 目录。

## 直播结束判断

直播持续时间不固定，所以程序不会只按 ASOUL 日程里的开始时间推算结束。

现在的判断方式是：

1. 根据 ASOUL 日程确认即将或正在发生的直播。
2. 从日程开始前 10 分钟进入开播检测，每 30 秒检查对应成员 B 站直播间状态；已取消日程也会参与这段检测。
3. 如果检测到 `live_status=1`，立即发送“直播已开始”通知。
4. 如果到日程开始 2 分钟后仍未检测到开播，就停止该场日程的开播检测。
5. 确认开播后，改为每 20 分钟检查一次是否关播。
6. 手动检查和程序启动时会立即检查一次。
7. 使用 B 站直播间接口中的 `live_status`：

   ```text
   1 = 直播中
   0 = 未开播/已结束
   2 = 轮播，程序按未开播处理
   ```

8. 如果检测到 `live_status` 从 `1` 变成非 `1`，就认为该场直播已经结束；其中 `2` 也会按未开播处理。
9. 检测到直播结束后，立即刷新该成员动态。
10. 如果这次立即刷新没有发现新动态，进入该成员的高频动态检查：每 3 分钟一次，最长持续 1 小时。
11. 如果立即刷新或高频期间发现新动态，提醒后结束该场直播对应的高频流程，恢复日常动态检查。

相关配置：

```json
"live_status_open_check_interval_seconds": 30,
"live_status_close_check_interval_minutes": 20,
"dynamic_high_frequency_interval_minutes": 3,
"dynamic_high_frequency_max_minutes": 60
```

## 查看实际读取到的数据

程序每次检查后会写出快照文件：

```text
%APPDATA%\AsoulReminder\asoul_events.json
%APPDATA%\AsoulReminder\dynamic_items.json
```

也可以用命令查看：

```powershell
.\.venv\Scripts\python.exe run.py --dump-asoul
.\.venv\Scripts\python.exe run.py --dump-dynamics
```

`--dump-asoul` 会显示从 ASOUL 日程解析出的直播时间、成员、状态、标题、简介。

`--dump-dynamics` 会显示每个成员最近读取到的动态 ID、摘要和跳转链接。

## B 站 Cookie

B 站动态接口通常需要浏览器 Cookie。没有 Cookie 时，B 站可能返回：

```text
code=-412, message=request was banned
```

或者：

```text
code=-352, message=风控校验失败
```

因此程序在没有配置 Cookie 时会跳过 B 站动态检查，只检查 ASOUL 直播时间表。

Windows 与 Android 两端都已实现 B 站 wbi 签名（`w_rid`/`wts`）：程序从 `x/web-interface/nav` 获取每日轮换的签名密钥（本地缓存 30 分钟），对动态接口的查询参数签名后再请求，以减少 -352 风控拦截；获取密钥失败时自动回退为不签名请求，不影响原有行为。

配置 Cookie 后，程序每次会读取最近若干条动态，而不是只读取一条。

首次成功读取时，程序会把当前最近动态全部记录为基线，不提醒历史动态。之后只要发现没见过的动态 ID，就发送通知。

相关配置：

```json
"dynamic_fetch_limit": 3
```

在 `config.json` 中填写：

```json
"bilibili_cookie": "SESSDATA=...; bili_jct=...; DedeUserID=..."
```

注意：Cookie 相当于登录凭证，不要发给别人，也不要截图公开。

## 扫码登录（桌面端）

桌面窗口 → 「设置」→ 全局配置 → 「B 站 Cookie」一行点「扫码登录」：

1. 用哔哩哔哩手机客户端扫码，并在手机上确认登录；
2. 程序取回 `SESSDATA`、`bili_jct`、`DedeUserID` 等凭证，缺少 `buvid3` 时再补一次 `x/frontend/finger/spi`；
3. 写入前弹确认框，只有选择「是」才会写进 `config.json`；
4. 旧 Cookie 里的其他字段（`buvid3`、`b_nut` 等）会保留，减少指纹变化带来的 -352 风控。

程序每 30 分钟用 `x/web-interface/nav` 校验一次登录状态（「立即检查」也会强制校验）。只有返回 `code=-101`（或动态接口返回 -101）才判定失效：写入 `bilibili_cookie_expired`、跳过动态检查，并自动弹出扫码窗口；网络异常只记为「未知」，不会误判。同一份失效记录只自动弹一次，重新登录成功后清空标记。

二维码依赖 `qrcode`（已写入 `requirements.txt`）；缺少时对话框会改为显示可复制的登录链接。

## 如何找到 B 站 Cookie

你截图里的位置已经基本正确。按下面步骤找：

1. 用 Edge 或 Chrome 登录 B 站。
2. 打开一个动态页，例如：

   ```text
   https://space.bilibili.com/672328094/dynamic
   ```

3. 按 `F12` 打开开发者工具。
4. 切换到 `网络` 面板。
5. 建议点上方的 `Fetch/XHR`，这样请求会少一些。
6. 刷新页面。
7. 在请求列表中找和动态相关的接口，常见名称包括：

   ```text
   space
   web-dynamic
   feed
   dynamic
   ```

   也可以直接点任何发往 `api.bilibili.com` 的请求。

8. 点中某个请求后，在右侧或下方打开 `标头` / `Headers`。
9. 找到 `请求标头` / `Request Headers`。
10. 在里面找到 `Cookie:` 这一行。
11. 复制 `Cookie:` 后面的整段内容，不要复制 `Cookie:` 这几个字。
12. 粘贴到 `config.json` 的 `bilibili_cookie` 字段里。
13. 保存 `config.json`，重启程序。

如果你当前页面看不到 `Cookie`：

- 先确认不是点到了图片、字体、脚本等静态资源请求。
- 优先点 `api.bilibili.com` 请求。
- 如果右侧没有详情区域，可以双击某个请求，或者把开发者工具窗口拉宽。
- 如果开启了 `保留日志`，可以先清空列表再刷新页面，避免请求太多。

## 配置成员

默认启用：

- 嘉然
- 乃琳
- 贝拉

默认不启用：

- 心宜
- 思诺

如果要启用心宜或思诺，打开 `config.json`，把对应成员的：

```json
"enabled": false
```

改成：

```json
"enabled": true
```

如果只想监控直播、不监控动态，可以保持：

```json
"monitor_dynamic": false
```

## 开机自启动

托盘菜单里有 `开机自启动` 选项。

它只会写入当前用户的 Windows Run 注册表项，不会修改系统级启动项。

开机自启动会使用 `--tray` 后台托盘模式，不会在登录时自动弹出桌面窗口；登录后可以从托盘菜单打开窗口。

## 托盘图标

程序默认统一使用一个图标：

```text
assets/asoul_tray_icon.ico
```

- `asoul_tray_icon.ico`：后台托盘、通知、窗口和 exe 图标。

## 打包

可以使用：

```powershell
.\scripts\build.ps1
```

打包后程序会输出到 `dist` 目录。

当前打包方式是 `onedir`：

```text
dist\AsoulReminder\AsoulReminder.exe
```

双击 `AsoulReminder.exe` 会同时打开桌面窗口并启动托盘监控；如果只想启动后台托盘、不自动弹窗，可以从命令行运行：

```powershell
.\dist\AsoulReminder\AsoulReminder.exe --tray
```

`config.json` 是外部配置文件，不会被编进 exe。不要公开包含 Cookie 的打包目录。

`build.ps1` 还会生成便携压缩包：

```text
dist\packages\AsoulReminder-portable.zip
```

这个包不包含当前的 `config.json`，适合分发或拿到另一台电脑后重新配置。首次运行时，程序会从内置的 `config.example.json` 自动生成 `config.json`。

如果要迁移当前电脑上的配置，可以额外运行：

```powershell
.\scripts\package-portable.ps1 -IncludePrivateConfig
```

它会生成：

```text
dist\packages\AsoulReminder-portable-with-config.zip
```

这个私用包包含当前 `config.json`，如果里面有 B 站 Cookie，就不要公开分享。
### EXE 安装包

安装包默认安装到独立目录 `D:\AsoulReminder`。如果电脑没有 D 盘，则回退到当前用户的 `%LOCALAPPDATA%\Programs\AsoulReminder`。安装器会创建桌面和开始菜单快捷方式，并提供标准卸载程序。

先运行 `build.ps1` 生成应用目录，再生成不包含当前配置的公开安装包：

```powershell
.\scripts\build-installer.ps1
```

输出文件：

```text
dist\installer\AsoulReminder-Setup.exe
```

如需生成包含当前 `config.json` 的个人迁移安装包：

```powershell
.\scripts\build-installer.ps1 -IncludePrivateConfig
```

输出文件：

```text
dist\installer\AsoulReminder-Setup-with-config.exe
```

个人迁移安装包可能包含 B 站 Cookie，不要公开分享。升级安装不会覆盖目标目录中已经存在的 `config.json`；卸载也不会删除 `%APPDATA%\AsoulReminder` 中的状态、日志和缓存。

便携包解压后可以直接运行：

```text
AsoulReminder.exe
start-window.bat
start-tray.bat
```

也可以运行 `install-current-user.ps1`，把程序复制到当前用户的 `%LOCALAPPDATA%\AsoulReminder\App` 并创建桌面快捷方式。

## 日志与状态

程序运行状态保存在：

```text
%APPDATA%\AsoulReminder
```

其中包括：

- `state.json`：已提醒记录、最新动态 ID、直播后高频检查状态。
- `app.log`：本次运行日志，程序每次启动会刷新覆盖。

## 2026-08 迭代记录与交接

> 本节是当时的交接记录，原样保留；其中提到 Android 端的条目只作背景说明。


本节记录 2026-08-28 至 2026-08-30 的全部改动与本机环境重建过程，供后续维护者和 agent 参考。

### 应用更名：ASOUL Reminder → Asoul Reminder

用户可见显示名已统一为 **Asoul Reminder**：Windows 窗口标题与桌面左上角标题、`QApplication` 应用名、托盘悬浮提示、Windows 通知的 app_id（toast 顶部显示名）、"已在运行"弹窗、安装器显示名/发布者/版权、开始菜单与桌面快捷方式、Android 桌面图标名称（`strings.xml` 的 `app_name`）。

**内部标识符已于 2026-08-30 统一为 `AsoulReminder` 风格**（显示名更名时曾刻意保留全大写 `ASOULReminder`，本次经用户决策调整）。现在这些标识符包括：

- PyInstaller 产物名 `AsoulReminder.exe` 与安装包文件名 `AsoulReminder-Setup*.exe`
- 数据目录 `%APPDATA%\AsoulReminder`（`paths.py` 的 `APP_NAME`；`state.json` 基线与去重、直播窗口状态、日志、表情缓存都依赖它）
- 开机自启注册表值 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\AsoulReminder`（`startup.py`）
- 单实例互斥量与命令事件 `Local\AsoulReminder.*`（`single_instance.py`）；安装器不再使用 Inno 内置 `AppMutex` 弹窗，改为安装/卸载启动时检测进程并提示「自动关闭并继续 / 手动关闭后重新检测 / 取消」
- 安装包默认安装目录 `D:\AsoulReminder`（无 D 盘时回退 `%LOCALAPPDATA%\Programs\AsoulReminder`）

这次是纯大小写调整：Windows 的文件路径、注册表值名和 Win32 命名对象（互斥量/事件）都对大小写不敏感，因此已有数据目录、开机自启注册表值、单实例互斥量和旧安装目录都会继续解析到同一目标，不需要数据迁移。刻意保持不变的只剩：

- 安装器 `AppId`（GUID）：升级/卸载身份，改动会生成独立的卸载条目
- Android 包名 `com.alortinarciple.asoulreminder`：系统级身份，改名等于全新应用，Keystore Cookie、权限、后台任务均不可迁移

### 功能与修复（本迭代）

Windows 与 Android 共同：

- 含日期的时间统一追加星期几（`MM-dd HH:mm 星期五`），通知文案同步；纯时间（如今日日程 `HH:mm`）不追加。星期名硬编码，不依赖系统 locale。
- 动态 GIF 表情：解析器优先读取 B 站 `emoji.gif_url`（动图），缺失或空串时回落 `icon_url`（静态图）。此前只读 `icon_url` 导致表情缓存全是 PNG、动图永远不动画。修复后需刷新一次动态才对新抓取内容生效，旧快照仍是静态图。
- （两端共同）B 站动态接口实现 wbi 签名（`w_rid`/`wts`，算法保持不变：参数按 key 排序、过滤 `!'()*`、encodeURIComponent 编码后与 mixin key 拼接做 MD5），密钥来自 `x/web-interface/nav` 的 `wbi_img`，每日轮换、本地缓存 30 分钟；密钥获取失败回退为不签名请求。用于缓解动态接口 -352 风控拦截（实测 Windows 通知默认音那次改动的同款做法，仅限请求侧）。
- （Android 修复）接口返回空动态列表时保留该成员缓存，不再把成员动态整体清空；JSON 取值改为安全取值，单个异常节点不再炸掉整个成员的解析。Kotlin `?:` 不跳过空串、Python `or` 跳过——两端表现差异的根源就是它。

Windows 专属：

- 安装/卸载程序检测到 Asoul Reminder 正在运行时，提供「自动关闭并继续」（taskkill 强制结束，状态与配置均为原子写入不会丢失）、「手动关闭后重新检测」、「取消」三个选项；移除了 Inno 内置 `AppMutex` 弹窗。
- 成员级"直播通知音效"开关（设置窗口成员卡，默认开启）：覆盖开播前提醒与"直播已开始"通知，提示音通道按系统状态自动切换：正常桌面走 toast 自带音效（与最初一致）；经 `SHQueryUserNotificationState` 检测到全屏游戏/全屏应用等会抑制通知的状态时，toast 静音、改由 winsound 播放内置铃声 `C:\Windows\Media\Ring01.wav`（`live_sound_file` 可换任意 .wav），`live_sound_loop_seconds` 控制循环秒数（默认 5，0=只响一次）——实测 Windows 通知默认音太轻柔，音乐/游戏音频下听不清，铃声加循环解决穿透问题。音效标志随通知重试队列（outbox）持久化。其余通知（日程变动、动态）保持静音。Android 不同步此功能（Android 通知音效由系统渠道控制，按成员分音效需每成员独立渠道）。
- 本周日程卡：星期几显示在日期下方，标题与简介行距与今日视图一致。

Android 专属：

- 版本号升至 0.3.0（versionCode 5）。
- 通知渠道拆分为「悬浮 / 普通」两套：开播提醒（含开播前定时提醒）与动态提醒默认悬浮横幅，日程提醒默认不悬浮，均可在设置页「悬浮通知」小节单独开关；开播前定时提醒改走「开播提醒」渠道。
- 修复直播期间重复弹出「正在直播」通知：直播状态刷新串行化，并以 B 站 `live_time` 作为场次标识去重，同一场直播只通知一次（状态抖动、并发刷新、标题变化都不再重复）。
- （Windows 修复）多人同时开播时通知互相覆盖：winotify 默认 `tag=标题`，同标题通知会互相替换；现在每条通知使用基于去重键的唯一 tag（md5 前 16 位），不同成员/事件的通知在通知中心共存。
- （Windows 新增）成员配置新增 `priority` 优先级字段（数字越大越优先、允许同级）：同时开播时决定通知发送顺序（高优先级后发、显示在顶部），并仲裁提示音（本轮只播最高优先级成员）；无日程开播的提示音也补上了成员自定义音效文件。
- 通知点击优先用 B 站客户端打开（未安装时回退浏览器，构建通知时决定目标，规避 Android 12+ 的通知 trampoline 限制）；
- 设置页新增「哔哩哔哩打开方式」：可显式指定用哪个应用打开动态/直播间（解决双开每次弹选择器），未指定时自动优先 B 站客户端；通知点击与 App 内「打开动态/直播间」使用同一套解析。下播后「正在查找新动态」的常驻通知在找到或超时后自动恢复为「后台监控运行中」。
- 系统闹钟默认设为开播前 10 分钟（Toast 会显示具体闹钟时刻），系统日历默认开始时间设为开播前 1 小时；系统闹钟的公开 API 不支持指定日期与自动删除，日期仍需在时钟 App 中确认。
- 修复动态/开播通知收不到的主因：POST_NOTIFICATIONS 权限此前只在设置页拨动通知开关时才申请，现在启动时主动申请；设置页新增「通知权限」状态行（可重新授权、可跳系统设置），并说明首次抓取仅建立基线的规则。
- 修复"创建系统闹钟"报"没有可用的时钟应用"：根因是 targetSdk 36 包可见性限制，`AndroidManifest.xml` 的 `<queries>` 补充 `ACTION_SET_ALARM` 与日历 `ACTION_INSERT`，并声明 `com.android.alarm.permission.SET_ALARM`。

### 本机开发环境重建实录

- Python 3.13.15：`python -m venv .venv` + `pip install -r requirements.txt`；PyInstaller 6.22 由 `build.ps1` 自动安装。
- JDK 17.0.2（`C:\Program Files\Java\jdk-17.0.2`）。
- Android SDK 36 位于 `%LOCALAPPDATA%\Android\Sdk`（cmdline-tools、platform-tools、platforms;android-36、build-tools 35/36，许可已全部接受）；`android/local.properties` 以 `sdk.dir=C\:/...` 格式指向它（冒号必须转义，否则 lint 报 `PropertyEscape` 错误；且修改该文件后 lint 可能复用旧分析缓存报假错，删除 `android/app/build` 重新分析即可）。
- 网络受限注意：本机直连 `services.gradle.org` 与 Maven Central 会长期停滞。Gradle 8.13 发行包可从腾讯镜像（`mirrors.cloud.tencent.com/gradle/`）预热到 `%USERPROFILE%\.gradle\wrapper\dists\gradle-8.13-bin\<hash>\`；依赖建议通过阿里云镜像的 init 脚本（`--init-script` 传入，或临时放入 `%USERPROFILE%\.gradle\init.d\` 用完删除）。GitHub Releases 直连超时，可经 `ghfast.top` 等加速前缀下载，下载后务必校验数字签名。
- Inno Setup 7.1.0 安装在仓库内 `build-tools\InnoSetup7\`（`build-installer.ps1` 的固定编译器路径，该目录不入库），含 `ChineseSimplified.isl`。
- Git：正式版 Git for Windows 2.55（`%LOCALAPPDATA%\Programs\Git`，已加入用户 PATH）。

### 版本控制与交接事项

- git 仓库于 2026-08-30 初始化（`main` 分支，基线提交 `7f600bc`），仓库级提交身份 `Alort <alort@users.noreply.local>` 为占位邮箱，可随时修改。
- `config.json`、`android/local.properties`、`android/app/src/private/**` 按 `.gitignore` 排除，任何情况下不得入库或外传。
- `scripts/verify-agent-package.ps1` 对本 README 及后续改动文件报 SHA-256 不匹配属预期：清单校验的是迁移包打包当时的状态。
- 尚待真机人工确认项：GIF 动画效果、系统闹钟/日历跳转、直播通知音效的实际听感。
- 无 CI、无依赖锁文件（`requirements.txt` 仅下限约束）、未配置远程仓库；`docs/agent-handoff.md` 为 2026-08-14 历史交接记录，其中"桌面三列布局待确认"已在本次迭代确认并同步 README。


