; Inno Setup script for MyWorkDay (WPF build).
; Expects a self-contained single-file publish to already exist at ..\publish - run
; build-installer.ps1 (which does `dotnet publish` then invokes this script) rather than
; running ISCC directly against a stale/missing publish folder.
#define MyAppName "MyWorkDay"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppExeName "MyWorkDay.exe"
#define MyAppSourceDir "..\publish"

[Setup]
AppId={{7C1E4C7B-9B7B-4A2E-9F5B-3B3B2C6E7A4D}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=MyWorkDay-Setup-{#MyAppVersion}
OutputDir=.\output
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\src\MyWorkDay\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "{#MyAppSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
