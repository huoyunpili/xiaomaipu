#ifndef SourceRoot
  #error SourceRoot must point to the prepared installer staging directory
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

[Setup]
AppId={{6B9201EF-7FA7-47CC-8E97-8ACB452F2A3F}
AppName=鱼管家
AppVersion=0.7.0
AppPublisher=阿栋
AppPublisherURL=https://github.com/huoyunpili/xiaomaipu
LicenseFile={#SourceRoot}\LICENSE
DefaultDirName={localappdata}\Programs\FishManager
DefaultGroupName=鱼管家
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
OutputDir={#OutputDir}
OutputBaseFilename=鱼管家-0.7.0-安装程序
UninstallDisplayIcon={app}\FishManager.ico
SetupLogging=yes
CloseApplications=no
RestartApplications=no

[Files]
Source: "{#SourceRoot}\scripts\windows_release.ps1"; DestName: "fish-manager-maintenance.ps1"; Flags: dontcopy
Source: "{#SourceRoot}\*"; DestDir: "{app}"; Excludes: "__pycache__,*.pyc,requirements.txt"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userdesktop}\鱼管家"; Filename: "{app}\FishManager.exe"; WorkingDir: "{app}"; IconFilename: "{app}\FishManager.ico"; AppUserModelID: "cn.fishmanager.desktop"
Name: "{group}\鱼管家"; Filename: "{app}\FishManager.exe"; WorkingDir: "{app}"; IconFilename: "{app}\FishManager.ico"; AppUserModelID: "cn.fishmanager.desktop"
Name: "{userstartup}\鱼管家"; Filename: "{app}\FishManager.exe"; Parameters: "--background"; WorkingDir: "{app}"; IconFilename: "{app}\FishManager.ico"

[InstallDelete]
Type: files; Name: "{group}\停止鱼管家.lnk"
Type: files; Name: "{group}\重新启动鱼管家.lnk"
Type: files; Name: "{group}\配置闲管家 API.lnk"

[Run]
Filename: "{app}\FishManager.exe"; WorkingDir: "{app}"; Flags: nowait runasoriginaluser
[UninstallRun]
Filename: "{app}\FishManager.exe"; Parameters: "--quit"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "StopDesktop"
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\windows_release.ps1"" -Action Stop -AppRoot ""{app}"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "StopFishManager"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  InstallPath: String;
  I: Integer;
begin
  Result := '';
  InstallPath := ExpandConstant('{app}');
  for I := 1 to Length(InstallPath) do
    if Ord(InstallPath[I]) > 127 then
    begin
      Result := '内置数据库暂不支持含中文或其他非英文字符的程序目录。请返回并选择英文路径，例如 D:\FishManager。';
      Exit;
    end;
  if FileExists(ExpandConstant('{app}\FishManager.exe')) then
  begin
    if not Exec(ExpandConstant('{app}\FishManager.exe'), '--quit',
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    begin
      Result := '请先从鱼管家托盘菜单选择“彻底退出”，再继续安装。';
      Exit;
    end;
    if ResultCode <> 0 then
    begin
      Result := '鱼管家尚未安全退出，请稍候再试。';
      Exit;
    end;
  end;
  if FileExists(ExpandConstant('{app}\scripts\windows_release.ps1')) then
  begin
    ExtractTemporaryFile('fish-manager-maintenance.ps1');
    if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      ExpandConstant('-NoProfile -ExecutionPolicy Bypass -File "{tmp}\fish-manager-maintenance.ps1" -Action Stop -AppRoot "{app}" -NoErrorDialog'),
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      Result := '无法停止旧版鱼管家，请关闭后重试。'
    else if ResultCode <> 0 then
      Result := '旧版鱼管家未能安全停止，请重新启动电脑后再安装。';
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := MsgBox(
    '卸载只会删除鱼管家程序，经营数据、视频和备份会保留在本机 AppData 目录中。' + #13#10 + #13#10 +
    '确定继续卸载吗？', mbConfirmation, MB_YESNO) = IDYES;
end;
