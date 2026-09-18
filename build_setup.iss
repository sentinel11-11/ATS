#define MyAppName "АТС Диспетчер"
#define MyAppExeName "ATS_Web_Dispatcher.exe"
[Setup]
AppId={{4AEFC951-EB99-4FA6-A5FA-ATS-DISPATCHER}}
AppName={#MyAppName}
AppVersion=1.0
DefaultDirName={autopf}\ATS Dispatcher
DefaultGroupName=АТС Диспетчер
OutputDir=installer_output
OutputBaseFilename=ATS_Dispatcher_Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "data\*"; DestDir: "{app}\data"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "static\*"; DestDir: "{app}\static"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "xp_bridge\*"; DestDir: "{app}\xp_bridge"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "shared\*"; DestDir: "{app}\shared"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "README_RU.txt"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{group}\АТС Диспетчер"; Filename: "{app}\{#MyAppExeName}"
Name: "{commondesktop}\АТС Диспетчер"; Filename: "{app}\{#MyAppExeName}"
[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить АТС Диспетчер"; Flags: nowait postinstall skipifsilent
