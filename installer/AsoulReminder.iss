#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

#ifndef MyVersionInfoVersion
  #define MyVersionInfoVersion "1.0.0.0"
#endif

#ifndef ProjectRoot
  #define ProjectRoot ".."
#endif

#ifndef SourceDir
  #define SourceDir ProjectRoot + "\dist\AsoulReminder"
#endif

#ifndef IncludePrivateConfig
  #define IncludePrivateConfig 0
#endif

#ifndef TestBuild
  #define TestBuild 0
#endif

#define MyAppName "Asoul Reminder"
#define MyAppExeName "AsoulReminder.exe"

[Setup]
#if TestBuild
AppId={{D0ED855F-E27A-448D-A18C-69A90662B664}
#else
AppId={{8F602A15-1F05-4F16-AEFD-8B688ADDD030}
#endif
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Asoul Reminder
AppCopyright=Asoul Reminder
DefaultDirName={code:GetDefaultInstallDir}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
#if TestBuild
PrivilegesRequired=lowest
#else
PrivilegesRequired=admin
#endif
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
WizardStyle=modern dynamic
SetupIconFile={#ProjectRoot}\assets\asoul_tray_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
CloseApplications=yes
CloseApplicationsFilter={#MyAppExeName}
RestartApplications=no
UsePreviousAppDir=yes
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
OutputDir={#ProjectRoot}\dist\installer
OutputBaseFilename=AsoulReminder-Setup
VersionInfoVersion={#MyVersionInfoVersion}
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
SetupLogging=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Dirs]
Name: "{app}"; Permissions: users-modify

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\assets"

[UninstallDelete]
Type: files; Name: "{app}\config.json"
Type: dirifempty; Name: "{app}"
[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "config.json"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ProjectRoot}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\config.example.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\build\version.json"; DestDir: "{app}"; Flags: ignoreversion
#if IncludePrivateConfig
Source: "{#PrivateConfigPath}"; DestDir: "{app}"; DestName: "config.json"; Flags: onlyifdoesntexist
#endif

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\使用说明"; Filename: "{app}\README.md"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
function IsAppRunning(): Boolean;
var
  ResultCode: Integer;
begin
  Exec(
    ExpandConstant('{cmd}'),
    '/C tasklist /FI "IMAGENAME eq {#MyAppExeName}" /NH | find /I "{#MyAppExeName}"',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
  Result := (ResultCode = 0);
end;

procedure CloseAppProcesses();
var
  ResultCode: Integer;
begin
  Exec(
    ExpandConstant('{cmd}'),
    '/C taskkill /F /T /IM {#MyAppExeName}',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
  Sleep(500);
end;

{ 检测正在运行的实例：是=自动关闭并继续，否=手动关闭后重新检测，取消=退出安装程序。 }
function HandleRunningInstance(): Boolean;
var
  Answer: Integer;
begin
  Result := True;
  while IsAppRunning() do begin
    Answer := MsgBox(
      '检测到 Asoul Reminder 当前正在运行。' #13#10 #13#10
      '选择「是」将自动关闭它并继续安装（强制结束进程；状态与配置已实时保存，不会丢失数据）。' #13#10
      '选择「否」在你手动关闭后重新检测；选择「取消」退出安装程序。',
      mbConfirmation,
      MB_YESNOCANCEL
    );
    case Answer of
      IDYES: CloseAppProcesses();
      IDNO: ; { 用户选择手动关闭，循环重新检测 }
    else
      begin
        Result := False;
        exit;
      end;
    end;
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := HandleRunningInstance();
end;

function InitializeUninstall(): Boolean;
begin
  Result := HandleRunningInstance();
end;

function GetDefaultInstallDir(Param: String): String;
begin
  if DirExists('D:\') then
    Result := 'D:\AsoulReminder'
  else
    Result := ExpandConstant('{localappdata}\Programs\AsoulReminder');
end;

#if TestBuild
#else
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RegDeleteValue(
      HKEY_CURRENT_USER,
      'Software\Microsoft\Windows\CurrentVersion\Run',
      'AsoulReminder'
    );
end;
#endif
