var
  RuntimeRestartRequired: Boolean;

function VCRuntimeReady: Boolean;
var
  Installed: Cardinal;
  Version, DLLVersion: String;
  Packed: Int64;
begin
  Result := False;
  if not RegQueryDWordValue(HKLM64,
    'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed) then Exit;
  if Installed <> 1 then Exit;
  if not RegQueryStringValue(HKLM64,
    'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Version', Version) then Exit;
  if Copy(Version, 1, 1) = 'v' then Delete(Version, 1, 1);
  if not StrToVersion(Version, Packed) then Exit;
  if ComparePackedVersion(Packed, PackVersionComponents(14, 50, 35719, 0)) < 0 then Exit;
  if not GetVersionNumbersString(ExpandConstant('{sys}\vcruntime140.dll'), DLLVersion) then Exit;
  if not StrToVersion(DLLVersion, Packed) then Exit;
  if ComparePackedVersion(Packed, PackVersionComponents(14, 50, 35719, 0)) < 0 then Exit;
  if not FileExists(ExpandConstant('{sys}\vcruntime140_1.dll')) then Exit;
  if not FileExists(ExpandConstant('{sys}\msvcp140.dll')) then Exit;
  Result := True;
end;

function EnsureVCRuntime: String;
var
  ResultCode: Integer;
  RuntimeLog: String;
begin
  Result := '';
  if RuntimeRestartRequired or VCRuntimeReady then
  begin
    Log('FishManager: Visual C++ x64 runtime already available.');
    Exit;
  end;
  RuntimeLog := ExpandConstant('{localappdata}\XianyuSeller\logs\vc-runtime-install.log');
  if not ForceDirectories(ExtractFileDir(RuntimeLog)) then
  begin
    Result := '无法创建运行库安装日志目录，请检查当前用户的文件夹权限。';
    Exit;
  end;
  ExtractTemporaryFile('vc_redist.x64.exe');
  WizardForm.StatusLabel.Caption := '正在补齐内置数据库所需的运行库，请允许 Windows 管理员确认。';
  if not ShellExec('runas', ExpandConstant('{tmp}\vc_redist.x64.exe'),
    '/install /quiet /norestart /log "' + RuntimeLog + '"',
    ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    Result := '运行库未能安装。请重试并在 Windows 管理员确认中选择“是”；本机经营数据不会删除。';
    Exit;
  end;
  Log(Format('FishManager: Visual C++ installer exit code %d.', [ResultCode]));
  if (ResultCode = 3010) or (ResultCode = 1641) then
  begin
    RuntimeRestartRequired := True;
    Exit;
  end;
  if ((ResultCode = 0) or (ResultCode = 1638)) and VCRuntimeReady then Exit;
  Result := Format('运行库安装未完成（错误码 %d）。请保留日志：%s', [ResultCode, RuntimeLog]);
end;

function NeedRestart: Boolean;
begin
  Result := RuntimeRestartRequired;
end;

function CanLaunchDesktop: Boolean;
begin
  Result := not RuntimeRestartRequired;
end;
