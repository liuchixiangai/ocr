; Inno Setup script for the ScoreSight Windows installer (Inno Setup 6.3 or newer).
;
; Build the app with PyInstaller first (dist\scoresight\), then:
;   iscc scoresight.iss
; which writes dist\scoresight-<version>-windows-x64-setup.exe. The version is
; read from src\version.py; CI passes it explicitly instead:
;   iscc /DAppVersion=0.2.0 /DAppVersionNumeric=0.2.0.0 scoresight.iss
; (AppVersionNumeric is the four-part Windows file version; it defaults to the
; X.Y.Z of AppVersion plus ".0"). /DSignTool=<name> signs Setup and the
; uninstaller with a Sign Tool defined by /S<name>=<command> (see [Setup]).
;
; The installer asks whether to install for all users (the default, needs
; administrator rights) or only for the current user. Unattended installs use
; /ALLUSERS or /CURRENTUSER, e.g. setup.exe /VERYSILENT /CURRENTUSER.

#define AppName "ScoreSight"
#define AppPublisher "Roy Shilkrot"
#define AppURL "https://github.com/royshil/scoresight"
#define AppExeName "scoresight.exe"

#ifndef AppVersion
  ; __version__ = "X.Y.Z" in src\version.py
  #define VersionFile AddBackslash(SourcePath) + "src\version.py"
  #define VersionHandle FileOpen(VersionFile)
  #if !VersionHandle
    #error Cannot open src\version.py; pass /DAppVersion=X.Y.Z to iscc
  #endif
  #define VersionLine ""
  #define Line ""
  #sub ReadVersionLine
    #expr Line = FileRead(VersionHandle)
    #if Pos("__version__", Line) == 1
      #expr VersionLine = Line
    #endif
  #endsub
  #for {0; VersionLine == "" && !FileEof(VersionHandle); 0} ReadVersionLine
  #expr FileClose(VersionHandle)
  #if VersionLine == ""
    #error No __version__ in src\version.py; pass /DAppVersion=X.Y.Z to iscc
  #endif
  #define VersionStart Pos('"', VersionLine) + 1
  #define AppVersion Copy(VersionLine, VersionStart, RPos('"', VersionLine) - VersionStart)
#endif

#ifndef AppVersionNumeric
  ; X.Y.Z-rc.1 -> X.Y.Z.0
  #define VersionCoreEnd Pos("-", AppVersion)
  #if VersionCoreEnd
    #define AppVersionNumeric Copy(AppVersion, 1, VersionCoreEnd - 1) + ".0"
  #else
    #define AppVersionNumeric AppVersion + ".0"
  #endif
#endif

[Setup]
; Never change AppId: it identifies the installation for upgrades and the
; uninstaller. 0.1.x installers had no AppId, so Inno Setup used AppName; keeping
; that value lets this installer upgrade them in place.
AppId={#AppName}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersionNumeric}
VersionInfoProductTextVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Qt 6 needs Windows 10 version 1809 or newer.
MinVersion=10.0.17763
OutputDir=dist
OutputBaseFilename=scoresight-{#AppVersion}-windows-x64-setup
SetupIconFile=icons\scoresight.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
#ifdef SignTool
; Sign Setup and the uninstaller with the Sign Tool of this name, which ISCC's
; /S option defines. Signed release builds (build.yml) pass
;   /DSignTool=artifactsigning
;   "/Sartifactsigning=pwsh -NoProfile -NonInteractive -File $q<repository>\scripts\ci\sign_windows.ps1$q $f"
SignTool={#SignTool}
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Remove the previous version's libraries first, so files that a newer
; PyInstaller or Qt no longer ships can't linger and get loaded.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\scoresight\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; after ScoreSight's Update now: the new version, as the user (not elevated)
Filename: "{app}\{#AppExeName}"; Flags: nowait runasoriginaluser; Check: RelaunchAfterUpdate

[Code]
// ScoreSight's Update now (src\app_update.py) runs Setup with
//   /SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS
//   /UPDATEREADY=<file>  written once Setup runs with the rights it needs (after
//                        the UAC prompt): ScoreSight then closes
//   /UPDATEMUTEX=<name>  held by ScoreSight until it has quit: Setup waits for it
//                        before replacing the files
//   /RELAUNCH=1          start the new version once installed
// Without them (a manual install) none of this happens.

// how long Setup waits for ScoreSight to quit, then closes it (CloseApplications)
const
  UpdateWaitMs = 60000;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ReadyFile, Mutex: String;
  Waited: Integer;
begin
  Result := '';
  ReadyFile := ExpandConstant('{param:UPDATEREADY|}');
  Mutex := ExpandConstant('{param:UPDATEMUTEX|}');
  if ReadyFile <> '' then
    if not SaveStringToFile(ReadyFile, 'installing', False) then
      Log('Could not write ' + ReadyFile);
  if Mutex <> '' then
  begin
    Waited := 0;
    while CheckForMutexes(Mutex) and (Waited < UpdateWaitMs) do
    begin
      Sleep(250);
      Waited := Waited + 250;
    end;
    if Waited >= UpdateWaitMs then
      Log('ScoreSight did not quit in time')
    else
      // the mutex goes with the process, its files a moment later
      Sleep(1000);
  end;
end;

function RelaunchAfterUpdate: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:RELAUNCH|0}') = '1');
end;
