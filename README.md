> Bilibili屿汐烛华 vibecoding产物，日程检测基于asoul.love网站礼貌化爬取，私人使用，莫要给网站加压力，公开仓库用于自动更新。

# Asoul Reminder（Windows 桌面端）

本地运行的 ASOUL 日程、B 站动态与直播间状态提醒程序：常驻系统托盘，按 ASOUL 日程在开播窗口内检测直播间，读取成员动态，用 Windows 通知提醒。

- 平台：Windows 10/11 x64
- 技术栈：Python 3.11+、PySide6、winotify、winsound
- 相关仓库：[Asoul Reminder Android](https://github.com/AlortinArciple/Asoul-Reminder-Android)（Kotlin + Compose，业务规则相同，代码独立）

## 功能

- **日程**：每 30 分钟检查一次 ASOUL 日程（`calendar/latest` 返回 403 时改读首页日程），并在间隔上叠加 ±10% 随机抖动（27–33 分钟）避免齐刷刷打站点；检测新增、取消、改期、标题与成员变化；失败后按失败次数退避重试，最长约 2 小时。
- **开播检测**：日程开始前 10 分钟到开始后 2 分钟，每 30 秒检查一次直播间是否真的开播，已取消日程同样参与检测；检测到开播立即通知。
- **下播检测**：确认开播后每 20 分钟检查一次，`live_status` 从 `1` 变成其它值即视为下播（`2` 轮播按未开播处理）。
- **动态**：日常每 10 分钟检查一次启用成员的 B 站动态；首次成功读取只建立基线，不提醒历史内容。确认下播后立即刷新动态，若没有新动态则进入每 3 分钟、最长 1 小时的高频检查。
- **通知**：日程变动、开播、动态三类通知；同一场直播只提醒一次；多人同时开播时按成员 `priority` 决定通知顺序与提示音仲裁。
- **提示音**：直播提醒可为每个成员单独开关、单独指定 `.wav`；检测到全屏游戏/全屏应用等会抑制通知的状态时，自动改用 `winsound` 播放铃声（默认 `Ring01.wav`，可配置循环秒数）。
- **窗口**：桌面窗口 + 系统托盘，关闭窗口只隐藏到托盘；窗口与托盘单实例运行。
- **登录**：设置页「B 站 Cookie → 扫码登录」，用手机 B 站客户端扫码即可写入登录凭证，无需手动复制 Cookie。
- **更新**：启动后静默检查 GitHub Releases（24 小时内不重复），设置页也可手动检查；发现新版本直接下载安装包并自动重启安装，不跳转网页。

## 下载

已编译的安装包与便携包在 [Releases](https://github.com/AlortinArciple/Asoul-Reminder-Desktop-Windows/releases) 页面：

| 文件 | 说明 |
| --- | --- |
| `AsoulReminder-Setup.exe` | 安装包，安装到独立目录并创建快捷方式，可从「应用和功能」卸载 |
| `AsoulReminder-portable.zip` | 便携包，解压即用，首次运行自动从内置模板生成 `config.json` |

两者都**不包含** `config.json` 或 B 站 Cookie；每个 Release 的说明里都附了对应版本的 SHA256。

## 安装与运行

### 安装包

双击 `AsoulReminder-Setup.exe`。默认安装到 `D:\AsoulReminder`，没有 D 盘时回退到 `%LOCALAPPDATA%\Programs\AsoulReminder`。升级安装不会覆盖已有的 `config.json`，卸载不会删除 `%APPDATA%\AsoulReminder` 里的状态与日志。

### 便携包

解压后直接运行：

```text
AsoulReminder.exe
start-window.bat      打开桌面窗口并启动托盘监控
start-tray.bat        只启动后台托盘
install-current-user.ps1  复制到 %LOCALAPPDATA%\AsoulReminder\App 并创建桌面快捷方式
```

### 从源码运行

需要 Python 3.11 或更高版本：

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy config.example.json config.json
python run.py
```

已创建过虚拟环境时可以直接：

```powershell
.\.venv\Scripts\python.exe run.py
```

关闭窗口只会隐藏窗口，托盘和监控继续运行；需要完全退出时在托盘菜单点「退出」。

## 第一次使用

1. 启动程序，打开「设置 → 全局配置 → B 站 Cookie」，点「扫码登录」。
2. 用哔哩哔哩手机客户端扫码并在手机上确认。
3. 程序取回 `SESSDATA`、`bili_jct`、`DedeUserID` 等凭证，弹窗确认后才会写入本机 `config.json`。
4. 在设置页勾选要监控的成员，保存。

也可以手动填写 `config.json` 的 `bilibili_cookie` 字段，见下文「B 站 Cookie 与风控」。

## 桌面窗口

- 左列是启用成员的 B 站直播间状态，中列是今日/本周日程与运行状态，右列是各成员最新动态（按发布时间混排）。
- 顶栏「刷新」重新读取 `config.json` 并绕过半小时节流强制检查日程；「立即检查」立即检查直播间与动态，但日程仍遵守节流。
- 日程卡片支持今日/本周切换；本周视图把星期几放在日期下方，今日视图只显示 `HH:mm`。
- 顶栏齿轮按钮打开设置窗口，可修改全局检查间隔、通知开关、提醒时间、请求超时、ASOUL 地址、提示音与成员配置。
- 设置里的「日程绕过系统代理」默认开启，只影响 ASOUL 日程请求；TUN/全局路由模式下仍需把 `asoul.love` 设为 DIRECT。
- 成员头像、显示名称、优先级、提示音都可以在成员卡里单独配置。

## 提醒规则

### 开播与下播

1. 由 ASOUL 日程确定即将或正在发生的直播。
2. 日程开始前 10 分钟起，每 30 秒检查对应成员直播间；只有 `live_status=1` 才算开播。
3. 到日程开始 2 分钟后仍未开播，就停止该场的开播检测。
4. 确认开播后改为每 20 分钟检查一次下播；检测到下播立即刷新该成员动态。
5. 相关配置：

```json
"live_status_open_check_interval_seconds": 30,
"live_status_close_check_interval_minutes": 20,
"dynamic_high_frequency_interval_minutes": 3,
"dynamic_high_frequency_max_minutes": 60
```

### 动态

- 首次成功读取只建立基线，之后只提醒没见过的动态 ID。
- 下播后的立即刷新没有新动态时，进入该成员专属高频检查（每 3 分钟，最长 1 小时），期间发现新动态即提醒并恢复日常检查。
- 「开播同刻动态过滤/分钟」会跳过同成员在开播时间附近发布的动态，避免直播开始时的开播动态重复弹窗；填 `0` 关闭过滤。
- 「日程新增/变动提醒」只提醒还没结束的日程（开始后 30 分钟宽限），并用持久化记录去重：站点每周重建日程页、把几周前的日程重新放回列表时，不会再把历史日程当成新增提醒一遍。

## 配置

`config.json` 由 `config.example.json` 复制而来，主要字段：

```json
{
  "bilibili_cookie": "",
  "dynamic_fetch_limit": 3,
  "members": [
    {
      "id": "diana",
      "name": "嘉然",
      "enabled": true,
      "monitor_dynamic": true,
      "monitor_live": true,
      "priority": 0,
      "live_sound_enabled": true,
      "live_sound_file": ""
    }
  ]
}
```

- 默认启用嘉然、乃琳、贝拉；心宜、思诺在模板里默认不启用，把 `enabled` 改成 `true` 即可（两人的 `uid` 与 `dynamic_url` 已经填好，打开动态监控无需再补）。
- 旧版本生成的 `config.json` 不用手动改：读取配置时若发现已知成员的 `uid` / `dynamic_url` 为空，会自动补上并写回，且不会覆盖你自己填的值、不改动开关。
- `monitor_dynamic: false` 表示只监控直播，不监控动态。
- `priority` 数字越大越优先（可为 0 或负数，允许同级）：同时开播时低优先级通知先发、高优先级后发，且只有最高优先级成员播放提示音。
- `live_sound_enabled` 控制该成员的直播提醒是否带提示音；`live_sound_file` 留空则跟随全局设置。

## 命令行

```powershell
python run.py --window        # 桌面窗口 + 托盘监控（默认）
python run.py --tray          # 只启动后台托盘
python run.py --status        # 只读本地状态，不启动监控
python run.py --check-once    # 立即检查一次后退出，日程仍守半小时节流
python run.py --dump-asoul    # 打印最近一次日程快照
python run.py --dump-dynamics # 打印最近一次动态快照
```

`--window`、`--tray` 会做单实例检测，其它一次性命令不受限制。

## B 站 Cookie 与风控

B 站动态接口需要登录 Cookie，否则常返回：

```text
code=-412, message=request was banned
code=-352, message=风控校验失败
```

程序对动态接口做了 wbi 签名：从 `x/web-interface/nav` 取每日轮换的签名密钥（本地缓存 30 分钟），对查询参数计算 `w_rid`/`wts` 后再请求；密钥获取失败则回退为不签名请求。这能显著降低 -352 拦截率，但无法完全消除。

扫码登录会自动处理凭证；也可以手动填写：

```json
"bilibili_cookie": "SESSDATA=...; bili_jct=...; DedeUserID=..."
```

手动获取方式：浏览器登录 B 站 → 打开任意动态页 → F12 → 网络面板 → 点一个发往 `api.bilibili.com` 的请求 → 复制请求头里 `Cookie:` 后面的整段内容。

**程序每 30 分钟用 `x/web-interface/nav` 校验一次登录状态**（「立即检查」也会强制校验）。只有返回 `code=-101`（或动态接口返回 -101）才判定失效：标记 `bilibili_cookie_expired`、跳过动态检查并自动弹出扫码窗口；网络异常只记为「未知」，不会误判。同一份失效记录只自动弹一次，重新登录成功后清空标记。

Cookie 等同登录凭证：不要发给别人，不要截图公开，也不要把带 Cookie 的 `config.json` 打进安装包或提交到仓库。

## 检查更新

程序启动 4 秒后静默检查一次最新版本，24 小时内不会重复请求；设置页「程序版本」一行也有「检查更新」按钮。

- 优先读 `https://github.com/AlortinArciple/Asoul-Reminder-Desktop-Windows/releases.atom`，读不到才退回 `api.github.com/repos/.../releases/latest`。`api.github.com` 按出口 IP 限流 60 次/小时，共用代理时经常被他人用光；atom 源与安装包下载都不占用这份额度。
- 只有远端版本严格高于本地 `asoul_reminder/version.py` 的 `APP_VERSION` 才会提示；版本号解析不出来或网络失败时不会误报有新版本。
- 点「立即安装」后直接下载 `AsoulReminder-Setup.exe`（不打开浏览器），显示进度与总大小。
- 下载到 `%APPDATA%\AsoulReminder\updates\`，完成后自动结束监控进程并启动安装程序；安装器会重新拉起新版本。
- 下载中的文件以 `.part` 结尾，取消或出错会删除，不会留下损坏的安装包。

发布新版本时，`asoul_reminder/version.py` 的 `APP_VERSION` 必须与 Release tag（去掉 `v`）一致，否则客户端收不到更新提示。

## 成员头像

设置页成员卡可为每个成员选择头像，支持 `png`/`jpg`/`jpeg`/`webp`/`bmp`/`ico`；也可以把图片放进 `assets\avatars`，默认识别 `diana.png`、`eileen.png`、`bella.png`、`fiona.png`、`gladys.png`。没有图片时使用圆形文字头像。

## 数据与日志

运行时数据在：

```text
%APPDATA%\AsoulReminder
```

- `state.json`：已提醒记录、最新动态 ID、直播后高频检查状态、通知重试队列（outbox）。
- `app.log`：本次运行日志，每次启动覆盖。
- `asoul_events.json`、`dynamic_items.json`：最近一次读取到的日程与动态快照。

状态写入是原子的（临时文件 + 替换），并通过锁保证线程安全。

## 构建与打包

```powershell
.\scripts\build.ps1                  # PyInstaller onedir -> dist\AsoulReminder + 便携包
.\scripts\build-installer.ps1        # Inno Setup -> dist\installer\AsoulReminder-Setup.exe
.\scripts\build-installer.ps1 -IncludePrivateConfig   # 附带本机 config.json 的私有安装包
.\scripts\package-portable.ps1       # 只生成便携包
```

`config.json` 是外部配置文件，不会被编进 exe；私有安装包与 `-IncludePrivateConfig` 便携包可能含 Cookie，只能自己留用。

## 项目结构

```text
asoul_reminder/        程序源码
  app.py               CLI、日志、单实例与依赖组装
  service.py           调度、通知去重、直播与动态检查
  window.py            桌面窗口、设置页、扫码登录对话框
  bilibili_login.py    扫码登录协议、Cookie 合并与 nav 失效校验
  asoul.py             ASOUL 日程解析
  bilibili.py          B 站动态与直播间接口（含 wbi 签名）
  http.py              带超时与代理旁路的 HTTP 封装
  config.py            配置校验与默认值
  state.py             线程安全状态与原子写入
  notifications.py     Windows 通知、提示音与重试队列
  emoji.py             B 站表情（含 GIF）缓存
assets/                图标与成员头像
scripts/               构建、打包、检查、清理脚本
tests/                 单元测试（unittest）
config.example.json    公开的配置模板
run.py                 开发入口
```

`build/`、`dist/`、`.venv/`、`__pycache__/`、`config.json` 均为本地生成或私有文件，已在 `.gitignore` 中排除。

## 测试

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
```

测试不读取私有 `config.json`，也不会触发真实通知；当前共 99 个用例，覆盖签名、解析、Cookie 合并与失效判定、通知去重、日程提醒窗口等规则。

## 系统要求

- Windows 10/11 x64。
- 安装包与便携包自带运行时，无需安装 Python；从源码运行需要 Python 3.11+。

## 免责声明

本项目是非官方的个人工具，与 ASOUL、哔哩哔哩无关联。数据来自 ASOUL 公开日程页与 B 站公开接口，请自行控制请求频率，遵守相关服务条款。程序按「现状」提供，使用风险自负。
