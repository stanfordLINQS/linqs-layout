; Inno Setup script for the LINQS Layout Windows installer.
;
; Build (after the PyInstaller one-folder app exists in dist\LINQS Layout\):
;   iscc /DMyAppVersion=1.0.9 packaging\windows\installer.iss
; Produces: dist\LINQS-Layout-Setup-<version>.exe
;
; A per-user install (PrivilegesRequired=lowest -> {autopf} = %LocalAppData%\Programs)
; so the in-app updater can download and run this with no UAC prompt; CloseApplications
; lets it replace a running install. The release attaches the produced .exe, which
; viewer/update.py finds (it looks for the newest .exe asset).

#define MyAppName "LINQS Layout"
#define MyAppPublisher "Stanford LINQS"
#define MyAppExeName "LINQS Layout.exe"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{6A9F71B8-38BC-4609-88C0-E2528C67A0E7}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=LINQS-Layout-Setup-{#MyAppVersion}
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked
Name: "dxfassoc"; Description: "Associate .dxf files with {#MyAppName}"; GroupDescription: "File associations:"

[Files]
; The whole PyInstaller one-folder output (exe + runtime + dxfcore.dll).
Source: "..\..\dist\LINQS Layout\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; .dxf association. HKA maps to HKCU under PrivilegesRequired=lowest.
Root: HKA; Subkey: "Software\Classes\.dxf\OpenWithProgids"; ValueType: string; ValueName: "LINQSLayout.dxf"; ValueData: ""; Flags: uninsdeletevalue; Tasks: dxfassoc
Root: HKA; Subkey: "Software\Classes\LINQSLayout.dxf"; ValueType: string; ValueName: ""; ValueData: "DXF Layout"; Flags: uninsdeletekey; Tasks: dxfassoc
Root: HKA; Subkey: "Software\Classes\LINQSLayout.dxf\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"; Tasks: dxfassoc
Root: HKA; Subkey: "Software\Classes\LINQSLayout.dxf\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: dxfassoc

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
