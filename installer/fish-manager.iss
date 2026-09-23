#ifndef SourceRoot
  #error SourceRoot must point to the prepared installer staging directory
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

[Setup]
AppId={{6B9201EF-7FA7-47CC-8E97-8ACB452F2A3F}
AppName=鱼管家
AppVersion=0.6.0
AppPublisher=阿栋
AppPublisherURL=https://github.com/huoyunpili/xiaomaipu
LicenseFile={#SourceRoot}\LICENSE
DefaultDirName={localappdata}\Programs\FishManager
DefaultGroupName=鱼管家
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
OutputDir={#OutputDir}
OutputBaseFilename=鱼管家-0.6.0-安装程序
UninstallDisplayIcon={app}\runtime\python\python.exe
SetupLogging=yes
CloseApplications=no
RestartApplications=no

[Files]
Source: "{#SourceRoot}\*"; DestDir: "{app}"; Excludes: "__pycache__,*.pyc,requirements.txt"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\鱼管家"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\windows_release.ps1"" -Action Start -AppRoot ""{app}"""; WorkingDir: "{app}"; IconFilename: "{app}\runtime\python\python.exe"
Name: "{group}\停止鱼管家"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\windows_release.ps1"" -Action Stop -AppRoot ""{app}"""; WorkingDir: "{app}"; IconFilename: "{app}\runtime\python\python.exe"
Name: "{group}\重新启动鱼管家"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\windows_release.ps1"" -Action Restart -AppRoot ""{app}"""; WorkingDir: "{app}"; IconFilename: "{app}\runtime\python\python.exe"
Name: "{group}\配置闲管家 API"; Filename: "{sys}\notepad.exe"; Parameters: """{localappdata}\XianyuSeller\config.env"""
Name: "{userstartup}\鱼管家"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\windows_release.ps1"" -Action Start -AppRoot ""{app}"""; WorkingDir: "{app}"; IconFilename: "{app}\runtime\python\python.exe"

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\windows_release.ps1"" -Action Install -AppRoot ""{app}"""; WorkingDir: "{app}"; StatusMsg: "正在初始化鱼管家，首次启动可能需要 1～2 分钟…"; Flags: runhidden waituntilterminated

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\windows_release.ps1"" -Action Stop -AppRoot ""{app}"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "StopFishManager"

[Code]
function InitializeUninstall(): Boolean;
begin
  Result := MsgBox(
    '卸载只会删除鱼管家程序，经营数据、视频和备份会保留在本机 AppData 目录中。' + #13#10 + #13#10 +
    '确定继续卸载吗？', mbConfirmation, MB_YESNO) = IDYES;
end;
