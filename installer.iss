; PICker — Inno Setup installer script (multi-file build)
; Build: iscc installer.iss
; Expects PyInstaller onedir output in dist/PICker-{version}/

#define MyAppName "PICker"
#define MyAppExeName "PICker.exe"
#define MyAppPublisher "nebula3141"
#define MyAppURL "https://github.com/nebula3141/picker"

#ifndef MyAppVersion
  #define MyAppVersion "4.7.0"
#endif

[Setup]
AppId={{E7B3A2F1-8C4D-4E9A-B5F6-7D0E1F2A3B4C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
LicenseFile=LICENSE
OutputDir=C:\Users\Z\Desktop\picker_installer
OutputBaseFilename=PICker-{#MyAppVersion}-setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UninstallDisplayName={#MyAppName} {#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "fileassoc"; Description: "Register PICker as image file handler (double-click and Open With)"; GroupDescription: "File associations:"; Flags: unchecked
Name: "contextmenu"; Description: "Add ""Browse with PICker"" to folder right-click menu"; GroupDescription: "File associations:"; Flags: unchecked
Name: "addtopath"; Description: "Add PICker to system PATH (use from command line)"; GroupDescription: "Advanced:"; Flags: unchecked

[Files]
; Multi-file PyInstaller build — entire directory
Source: "dist_multi\PICker-{#MyAppVersion}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Icon file for associations
Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icon.ico"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Registry]
; ProgId
Root: HKCU; Subkey: "Software\Classes\PICker.Image"; ValueType: string; ValueData: "PICker Image File"; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\PICker.Image\DefaultIcon"; ValueType: string; ValueData: "{app}\icon.ico,0"; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\PICker.Image\shell"; ValueType: string; ValueData: "open"; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\PICker.Image\shell\open"; ValueType: string; ValueData: "Open with PICker"; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\PICker.Image\shell\open\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Flags: uninsdeletekey; Tasks: fileassoc

; Image extensions
Root: HKCU; Subkey: "Software\Classes\.jpg\OpenWithProgids"; ValueType: none; ValueName: "PICker.Image"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\.jpeg\OpenWithProgids"; ValueType: none; ValueName: "PICker.Image"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\.png\OpenWithProgids"; ValueType: none; ValueName: "PICker.Image"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\.tiff\OpenWithProgids"; ValueType: none; ValueName: "PICker.Image"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\.tif\OpenWithProgids"; ValueType: none; ValueName: "PICker.Image"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\.webp\OpenWithProgids"; ValueType: none; ValueName: "PICker.Image"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\.bmp\OpenWithProgids"; ValueType: none; ValueName: "PICker.Image"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\.cr2\OpenWithProgids"; ValueType: none; ValueName: "PICker.Image"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\.cr3\OpenWithProgids"; ValueType: none; ValueName: "PICker.Image"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\.nef\OpenWithProgids"; ValueType: none; ValueName: "PICker.Image"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\.arw\OpenWithProgids"; ValueType: none; ValueName: "PICker.Image"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\.dng\OpenWithProgids"; ValueType: none; ValueName: "PICker.Image"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\.raf\OpenWithProgids"; ValueType: none; ValueName: "PICker.Image"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\.orf\OpenWithProgids"; ValueType: none; ValueName: "PICker.Image"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\.rw2\OpenWithProgids"; ValueType: none; ValueName: "PICker.Image"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\.pef\OpenWithProgids"; ValueType: none; ValueName: "PICker.Image"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\.srw\OpenWithProgids"; ValueType: none; ValueName: "PICker.Image"; Flags: uninsdeletevalue; Tasks: fileassoc

; Directory context menu
Root: HKCU; Subkey: "Software\Classes\Directory\shell\PICker"; ValueType: string; ValueData: "Browse with PICker"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\shell\PICker"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\icon.ico"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\shell\PICker\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%V"""; Flags: uninsdeletekey; Tasks: contextmenu

; Directory background
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\PICker"; ValueType: string; ValueData: "Browse with PICker"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\PICker"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\icon.ico"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\PICker\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%V"""; Flags: uninsdeletekey; Tasks: contextmenu

; SystemFileAssociations
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\image\shell\PICker"; ValueType: string; ValueData: "Open with PICker"; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\image\shell\PICker"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\icon.ico"; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\image\shell\PICker\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Flags: uninsdeletekey; Tasks: fileassoc

; App Paths
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#MyAppExeName}"; ValueType: string; ValueData: "{app}\{#MyAppExeName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#MyAppExeName}"; ValueType: string; ValueName: "Path"; ValueData: "{app}"; Flags: uninsdeletekey

[Code]
const
  SHCNE_ASSOCCHANGED = $08000000;
  SHCNF_IDLIST = $0000;

procedure SHChangeNotify(wEventId, uFlags: Cardinal; dwItem1, dwItem2: Cardinal); external 'SHChangeNotify@shell32.dll stdcall';

procedure AddToPath();
var
  OldPath, AppDir: string;
begin
  AppDir := ExpandConstant('{app}');
  if RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OldPath) then
  begin
    if Pos(Uppercase(AppDir), Uppercase(OldPath)) = 0 then
      RegWriteStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OldPath + ';' + AppDir);
  end
  else
    RegWriteStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', AppDir);
end;

procedure RemoveFromPath();
var
  OldPath, AppDir, NewPath: string;
  P: Integer;
begin
  AppDir := ExpandConstant('{app}');
  if RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OldPath) then
  begin
    P := Pos(';' + Uppercase(AppDir), Uppercase(OldPath));
    if P > 0 then
    begin
      NewPath := Copy(OldPath, 1, P - 1) + Copy(OldPath, P + Length(AppDir) + 1, MaxInt);
      RegWriteStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', NewPath);
    end
    else begin
      P := Pos(Uppercase(AppDir) + ';', Uppercase(OldPath));
      if P > 0 then
      begin
        NewPath := Copy(OldPath, 1, P - 1) + Copy(OldPath, P + Length(AppDir) + 1, MaxInt);
        RegWriteStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', NewPath);
      end
      else if Uppercase(OldPath) = Uppercase(AppDir) then
        RegDeleteValue(HKEY_CURRENT_USER, 'Environment', 'Path');
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, 0, 0);
    if IsTaskSelected('addtopath') then
      AddToPath();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, 0, 0);
    RemoveFromPath();
  end;
end;

function InitializeUninstall(): Boolean;
var
  MsgResult: Integer;
begin
  Result := True;
  MsgResult := MsgBox('Do you want to remove PICker settings and library data?'#13#13 +
    'Click Yes to delete all data, No to keep settings for future installs.',
    mbConfirmation, MB_YESNOCANCEL);
  if MsgResult = IDCANCEL then
    Result := False
  else if MsgResult = IDYES then
    DelTree(ExpandConstant('{userappdata}\PICker'), True, True, True);
end;
