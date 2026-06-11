; Inno Setup script — build after: pyinstaller main.spec --noconfirm
; Run: ISCC.exe installer\AERenderManager.iss

#define MyAppName "AE Render Manager"
#define MyAppVersion "1.1.1"
#define MyAppPublisher "vatik-vvv"
#define MyAppURL "https://github.com/vatik-vvv/ae-render-manager"
#define MyAppExeName "AERenderManager.exe"

[Setup]
AppId={{A3F8C2E1-9B4D-4A7F-8E2C-1D5A6E7B8C9D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=
OutputDir=..\release
OutputBaseFilename=AERenderManager-{#MyAppVersion}-win64-setup
SetupIconFile=..\AERM_icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\config.example.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\RELEASE_INSTALL.txt"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigPath, ExamplePath: String;
begin
  if CurStep = ssPostInstall then
  begin
    ConfigPath := ExpandConstant('{app}\config.json');
    ExamplePath := ExpandConstant('{app}\config.example.json');
    if not FileExists(ConfigPath) and FileExists(ExamplePath) then
      FileCopy(ExamplePath, ConfigPath, False);
  end;
end;
