; ============================================================================
;  OneClick SPLAT — Windows Installer
;  Built with Inno Setup 6 (https://jrsoftware.org/isinfo.php)
;
;  Build:  iscc setup.iss   (or double-click build.bat)
;  Output: dist/OneClickSPLAT-Setup.exe
; ============================================================================

#define MyAppName         "OneClick SPLAT"
#define MyAppVersion      "2.27.14"
#define MyAppPublisher    "Oli97430"
#define MyAppURL          "https://github.com/Oli97430/supersplat"
#define MyAppExeName      "OneClickSPLAT.exe"

[Setup]
AppId={{A4F6B3D2-7C28-4E8F-B6D1-9C5E2A8F3D7E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\OneClick SPLAT
DefaultGroupName=OneClick SPLAT
DisableProgramGroupPage=auto
LicenseFile=..\LICENSE
OutputDir=dist
OutputBaseFilename=OneClickSPLAT-Setup-{#MyAppVersion}
SetupIconFile=assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DiskSpanning=no
MinVersion=10.0.17763
UninstallDisplayIcon={app}\assets\icon.ico
; Wizard images are optional — provide assets\wizard-large.bmp (164x314)
; and assets\wizard-small.bmp (55x58) to enable a branded installer UI.
; WizardImageFile=assets\wizard-large.bmp
; WizardSmallImageFile=assets\wizard-small.bmp

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "french";  MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon"; Description: "Start OneClick SPLAT automatically at login"; GroupDescription: "Startup options:"; Flags: unchecked
Name: "downloadml";  Description: "Download ML dependencies now (~5 GB — requires internet)"; GroupDescription: "First-run setup:"; Flags: checkedonce

[Files]
; ── Backend Python source (small, always bundled) ──────────────────────────
Source: "..\server\main.py";         DestDir: "{app}\server"; Flags: ignoreversion
Source: "..\server\pipeline.py";     DestDir: "{app}\server"; Flags: ignoreversion
Source: "..\server\.env.example";    DestDir: "{app}\server"; Flags: ignoreversion

; ── Frontend pre-built bundle (built via npm run build before packaging) ───
Source: "..\dist\*"; DestDir: "{app}\frontend"; Flags: ignoreversion recursesubdirs createallsubdirs

; ── Bundled launchers and scripts ──────────────────────────────────────────
Source: "scripts\launch.ps1";         DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "scripts\serve-frontend.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "scripts\install-deps.ps1";   DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "scripts\check-gpu.ps1";      DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "scripts\OneClickSPLAT.exe.cmd"; DestDir: "{app}"; DestName: "OneClickSPLAT.cmd"; Flags: ignoreversion
Source: "scripts\update.ps1";        DestDir: "{app}\scripts"; Flags: ignoreversion

; ── Assets ─────────────────────────────────────────────────────────────────
Source: "assets\icon.ico";   DestDir: "{app}\assets"; Flags: ignoreversion
Source: "assets\README.txt"; DestDir: "{app}";        Flags: ignoreversion isreadme

[Icons]
Name: "{group}\OneClick SPLAT";       Filename: "{app}\OneClickSPLAT.cmd"; IconFilename: "{app}\assets\icon.ico"; WorkingDir: "{app}"
Name: "{group}\Stop backend";         Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Get-Process python -EA SilentlyContinue | Where-Object {{ $_.MainWindowTitle -match 'uvicorn' -or $_.CommandLine -match 'uvicorn' }} | Stop-Process"""; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\Open install folder";  Filename: "{app}"
Name: "{group}\Uninstall OneClick SPLAT"; Filename: "{uninstallexe}"
Name: "{autodesktop}\OneClick SPLAT"; Filename: "{app}\OneClickSPLAT.cmd"; IconFilename: "{app}\assets\icon.ico"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{commonstartup}\OneClick SPLAT"; Filename: "{app}\OneClickSPLAT.cmd"; IconFilename: "{app}\assets\icon.ico"; WorkingDir: "{app}"; Tasks: startupicon

[Run]
; ── GPU check before heavy install ─────────────────────────────────────────
Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\check-gpu.ps1"""; \
    StatusMsg: "Detecting NVIDIA GPU…"; \
    Flags: runhidden waituntilterminated

; ── Install dependencies (Python venv + torch + nerfstudio + COLMAP + ffmpeg)
; Runs SYNCHRONOUSLY during the install — without this the user could click
; "Finish" before the venv exists and the launcher would crash.
Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\install-deps.ps1"" -AppDir ""{app}"""; \
    StatusMsg: "Installing Python, ML deps, COLMAP, ffmpeg (5-15 min, ~6 GB download)…"; \
    Tasks: downloadml; \
    Flags: waituntilterminated

; ── Optionally launch at end ───────────────────────────────────────────────
Filename: "{app}\OneClickSPLAT.cmd"; \
    Description: "Launch {#MyAppName} now"; \
    Flags: postinstall nowait skipifsilent unchecked

[UninstallRun]
; Stop any running backend before uninstall
Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Get-Process python -EA SilentlyContinue | Where-Object CommandLine -match uvicorn | Stop-Process -Force"""; \
    Flags: runhidden; \
    RunOnceId: "StopBackend"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\venv"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\jobs"
Type: filesandordirs; Name: "{app}\logs"
; Per-user runtime data (logs, jobs cache, PIDs) — opt-in cleanup
Type: filesandordirs; Name: "{localappdata}\OneClickSPLAT"

[Code]
function InitializeSetup(): Boolean;
var
  Version: TWindowsVersion;
begin
  GetWindowsVersionEx(Version);
  if (Version.NTPlatform = False) or (Version.Major < 10) then
  begin
    MsgBox('OneClick SPLAT requires Windows 10 or later.', mbCriticalError, MB_OK);
    Result := False;
    Exit;
  end;
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Could trigger additional post-install logic here
  end;
end;
