; Inno Setup 6/7 — Medhar Install.exe
; Збирається після PyInstaller: dist\Medhar\*
;
; У Inno Setup Compiler: File → Open → desktop\installer.iss → Build → Compile
; Або з терміналу: .\desktop\build.ps1

#define MyAppName "Medhar"
#define MyAppVersion "1.2.22"
#define MyAppPublisher "Medhar"
#define MyAppExeName "Medhar.exe"
#define MyAppURL "https://github.com/vadimglodnyj/medhar"

[Setup]
AppId={{A7C3E9F1-8B2D-4E6A-9C1F-5D8B0A2E4F67}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=
InfoBeforeFile=
OutputDir=..\dist
OutputBaseFilename=Install
SetupIconFile=medhar.ico
UninstallDisplayIcon={app}\medhar.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppName}
; Без підпису SmartScreen може попередити — це очікувано
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
; Якщо є Ukrainian.isl у Inno Setup — розкоментуйте:
; Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"

[Tasks]
Name: "desktopicon"; Description: "Створити ярлик на робочому столі"; GroupDescription: "Додаткові ярлики:"; Flags: checkedonce

[Files]
; Уся onedir-збірка PyInstaller
Source: "..\dist\Medhar\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Окремий .ico для ярликів: Explorer кешує іконку exe за шляхом і після
; перевстановлення часто показує стару.
Source: "medhar.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\medhar.ico"
Name: "{group}\Видалити {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\medhar.ico"; Tasks: desktopicon

[Run]
; Скидання кешу іконок Explorer, інакше ярлик лишається зі старою картинкою.
Filename: "{sys}\ie4uinit.exe"; Parameters: "-show"; Flags: runhidden skipifdoesntexist
Filename: "{app}\{#MyAppExeName}"; Description: "Запустити {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
