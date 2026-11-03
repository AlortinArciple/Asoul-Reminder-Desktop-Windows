Asoul Reminder 便携版

使用方式：
1. 解压整个文件夹，不要只复制 AsoulReminder.exe。
2. 双击 AsoulReminder.exe 或 start-window.bat，会打开桌面窗口并启动托盘监控。
3. 双击 start-tray.bat，会只启动托盘，不自动弹出桌面窗口。
4. 关闭桌面窗口只会隐藏窗口，后台托盘会继续运行。
5. 要完全退出，请右键托盘图标，选择“退出”。

配置说明：
- 如果压缩包里没有 config.json，首次启动会自动从 config.example.json 生成。
- B 站 Cookie 属于登录凭证，不要把包含 config.json 的私用包发给别人。
- 日志和状态保存在当前 Windows 用户的 %APPDATA%\AsoulReminder 目录。

头像说明：
- 可以把头像图片放到 assets\avatars。
- 默认识别 diana.png、eileen.png、bella.png、fiona.png、gladys.png。
- 也可以在设置窗口里为每个成员单独选择头像文件。

可选安装：
右键 install-current-user.ps1，选择“使用 PowerShell 运行”，会复制到当前用户的
%LOCALAPPDATA%\AsoulReminder\App，并在桌面创建快捷方式。
