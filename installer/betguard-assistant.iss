; Inno Setup script for Betguard Assistant v0.5.34-beta

#define MyAppName "Betguard Assistant"
#define MyAppVersion "0.5.34-beta"
#define MyAppPublisher "Betguard"
#define MyAppURL "http://127.0.0.1:8765"
#define MyAppExeName "BetguardAssistant.exe"

[Setup]
AppId={{BETGUARD-ASSIST-000001}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DisableDirPage=no
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\release
OutputBaseFilename=Betguard-Assistant-v0.5.34-beta-Setup
Compression=lzma2
SolidCompression=no
WizardStyle=modern
UninstallDisplayName={#MyAppName} {#MyAppVersion}
DisableProgramGroupPage=auto
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑"; GroupDescription: "額外捷徑:"
Name: "launch"; Description: "啟動 Betguard Assistant"; GroupDescription: "安裝完成後:"

[Files]
Source: "..\dist\BetguardAssistant\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "playwright-browsers\*"
Source: "..\dist\BetguardAssistant\_internal\playwright-browsers\*"; DestDir: "{app}\_internal\playwright-browsers"; Flags: ignoreversion recursesubdirs createallsubdirs nocompression

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{autoprograms}\解除安裝 {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "啟動 Betguard Assistant"; Flags: nowait postinstall skipifsilent; Tasks: launch

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function ShouldKeepUserData: Boolean;
begin
  Result := (MsgBox('是否保留使用者資料 (牌單、審核記錄)？', mbConfirmation, MB_YESNO or MB_DEFBUTTON1) = IDYES);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if ShouldKeepUserData then
      MsgBox('使用者資料已保留在 Documents\Betguard Assistant Data', mbInformation, MB_OK)
    else
      DelTree(ExpandConstant('{userdocs}\Betguard Assistant Data'), True, True, True);
  end;
end;