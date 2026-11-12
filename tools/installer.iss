; GameShopSetup.exe: installs Game Shop into Program Files (Inno Setup 6, built by tools\build.ps1).
; It puts GameShop.exe in {app}, adds a Start menu entry, starts Game Shop at sign-in (a choice),
; allows the installed exe through Windows Firewall (the PS5 and PS4 reach it over the network),
; and registers an uninstaller in Settings > Apps. The tray's Check for updates downloads the
; next release's setup and runs it with /SILENT; settings and the library in %LOCALAPPDATA%\GameShop
; are never touched, not even by the uninstaller.
;   ISCC /DAppVersion=0.1.14 /DDist=C:\path\to\folder-with-GameShop.exe /DOutDir=... tools\installer.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef Dist
  #define Dist "..\dist"
#endif
#ifndef OutDir
  #define OutDir "..\dist"
#endif

[Setup]
AppId={{6F1B7C2E-4D8A-4E3B-9C55-2A7E0B5D9F41}
AppName=Game Shop
AppVersion={#AppVersion}
AppVerName=Game Shop {#AppVersion}
AppPublisher=MrRobotto512
AppPublisherURL=https://github.com/MrRobotto512/GameLib
AppUpdatesURL=https://github.com/MrRobotto512/GameLib/releases
DefaultDirName={autopf}\Game Shop
DefaultGroupName=Game Shop
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutDir}
OutputBaseFilename=GameShopSetup
SetupIconFile=gameshop.ico
UninstallDisplayIcon={app}\GameShop.exe
UninstallDisplayName=Game Shop
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
; Closing Game Shop is done in PrepareToInstall below: it has no window for Restart Manager to close.
CloseApplications=no

[Tasks]
Name: autostart; Description: "Start Game Shop when I sign in to Windows"
Name: desktopicon; Description: "Put a Game Shop shortcut on the desktop"; Flags: unchecked

[Files]
Source: "{#Dist}\GameShop.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Game Shop"; Filename: "{app}\GameShop.exe"; Parameters: "--tray --open"; WorkingDir: "{app}"
Name: "{autodesktop}\Game Shop"; Filename: "{app}\GameShop.exe"; Parameters: "--tray --open"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Game Shop"; ValueData: """{app}\GameShop.exe"" --tray"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
; One rule for the installed exe, replacing any left from an earlier install.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Game Shop"""; Flags: runhidden waituntilterminated
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""Game Shop"" dir=in action=allow program=""{app}\GameShop.exe"" enable=yes profile=private,public"; Flags: runhidden waituntilterminated
; The logon task tools\install-autostart.ps1 made for a copy outside Program Files; the Run entry above replaces it.
Filename: "{sys}\schtasks.exe"; Parameters: "/delete /tn ""Game Shop"" /f"; Flags: runhidden waituntilterminated; Tasks: autostart
Filename: "{app}\GameShop.exe"; Parameters: "--tray"; WorkingDir: "{app}"; Description: "Start Game Shop"; Flags: nowait postinstall runasoriginaluser

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im GameShop.exe"; Flags: runhidden waituntilterminated; RunOnceId: "StopGameShop"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Game Shop"""; Flags: runhidden waituntilterminated; RunOnceId: "FirewallRule"

[Code]
{ Ask a running Game Shop to quit the way a second copy does (POST /api/quit on its port), give it
  a few seconds, then end whatever GameShop.exe is left (a copy from Downloads included): the new
  exe cannot be written while the old one runs, and two copies would fight over the port. }
procedure AskGameShopToQuit;
var
  Http: Variant;
begin
  try
    Http := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    Http.SetTimeouts(2000, 2000, 2000, 2000);
    Http.Open('POST', 'http://127.0.0.1:8750/api/quit', False);
    Http.SetRequestHeader('Content-Type', 'application/json');
    Http.Send('{}');
  except
  end;
end;

function GameShopRunning: Boolean;
var
  Code: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/c tasklist /fi "imagename eq GameShop.exe" | find /i "GameShop.exe" >nul',
                 '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  I, Code: Integer;
begin
  Result := '';
  if not GameShopRunning then
    Exit;
  AskGameShopToQuit;
  for I := 1 to 20 do
  begin
    if not GameShopRunning then
      Break;
    Sleep(500);
  end;
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im GameShop.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(1500);
end;
