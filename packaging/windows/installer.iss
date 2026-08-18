; Video Compressor Windows Setup script (Inno Setup)
;
; This manifest is compiled by scripts/build_setup.py, which validates the
; staged Nuitka standalone directory and injects the release version, version
; info and architecture defines below. Keep this file declarative: the Python
; builder owns argument validation, version normalization and artifact naming,
; while this file describes the installation behavior.
;
; v1.6.1 is the installer-backend bridge release: it migrates legacy per-user
; WiX/MSI installs through the Windows Installer before installing this Inno
; version (see the Code section at the end of this file). AppId is the stable
; identity for all future Inno releases; never change it.
[Setup]
AppId={{4478BF58-30E3-5232-AE83-3E33254B3385}
AppName=Video Compressor
AppVersion={#ReleaseVersion}
AppVerName=Video Compressor {#ReleaseVersion}
AppPublisher=starfield17
AppPublisherURL=https://github.com/starfield17/Video_compress_Encoder_gui
AppUpdatesURL=https://github.com/starfield17/Video_compress_Encoder_gui/releases

VersionInfoVersion={#VersionInfo}
VersionInfoProductName=Video Compressor
VersionInfoCompany=starfield17

; Per-user installation only: no UAC, no Program Files, no system components.
PrivilegesRequired=lowest

; Previous MSI installs used "%LOCALAPPDATA%\Programs\starfield17 Video
; Compressor". New installs use the clean directory name below.
DefaultDirName={localappdata}\Programs\Video Compressor
DefaultGroupName=Video Compressor

; Stable per-user Add/Remove Programs registration (no custom registry keys).
UninstallDisplayName=Video Compressor
UninstallDisplayIcon={app}\video-compressor.exe
OutputBaseFilename=video-compressor-setup

SetupIconFile={#SetupIcon}
Compression=lzma2/ultra
SolidCompression=yes

; x86_64 builds may also install through the Windows 11 ARM64 x64 emulation
; layer; native ARM64 builds restrict to native ARM64.
ArchitecturesAllowed={#ArchitecturesAllowed}
ArchitecturesInstallIn64BitMode={#ArchitecturesInstallIn64BitMode}

; SignTool stays disabled so Inno does not require a certificate at compile
; time; sign_windows.ps1 signs Setup.exe after compilation. Enable both lines
; to sign Setup.exe and its generated uninstaller during compilation instead.
; SignTool=signtool $f
; SignedUninstaller=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; No default Desktop shortcut, matching the previous MSI behavior.

[Files]
; Install the complete Nuitka standalone tree (application, DLLs, Qt, FFmpeg)
; recursively. Do not enumerate individual payload files here.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.pyc,__pycache__"

[Icons]
Name: "{group}\Video Compressor"; Filename: "{app}\video-compressor.exe"; WorkingDir: "{app}"

[Code]
const
  VC_MSI_DISPLAY_NAME = 'Video Compressor';
  VC_MSI_PUBLISHER = 'starfield17';
  VC_UNINSTALL_KEY = 'Software\Microsoft\Windows\CurrentVersion\Uninstall';

procedure ReadVersionPart(var Remaining: String; var Value: Integer);
var
  Dot: Integer;
  Part: String;
begin
  Dot := Pos('.', Remaining);
  if Dot = 0 then
  begin
    Part := Remaining;
    Remaining := '';
  end
  else
  begin
    Part := Copy(Remaining, 1, Dot - 1);
    Delete(Remaining, 1, Dot);
  end;
  Value := StrToIntDef(Part, 0);
end;

procedure ParseVersion(const Version: String; var Major, Minor, Patch: Integer);
var
  Remaining: String;
begin
  Remaining := Version;
  Major := 0;
  Minor := 0;
  Patch := 0;
  ReadVersionPart(Remaining, Major);
  if Remaining <> '' then
    ReadVersionPart(Remaining, Minor);
  if Remaining <> '' then
    ReadVersionPart(Remaining, Patch);
end;

{ Returns True only for a version older than the first Inno release (1.6.1),
  tolerating both 3-part and 4-part MSI display versions such as "1.6.0.0". }
function IsLegacyMsiVersion(const Version: String): Boolean;
var
  Major, Minor, Patch: Integer;
begin
  ParseVersion(Version, Major, Minor, Patch);
  Result := (Major < 1)
    or ((Major = 1) and (Minor < 6))
    or ((Major = 1) and (Minor = 6) and (Patch < 1));
end;

function QueryMsiRegistration(const RootKey: Integer; const Subkey: String;
  out ProductCode: String; out MsiVersion: String): Boolean;
var
  Names: TArrayOfString;
  I: Integer;
  CurrentKey: String;
  CurrentName: String;
  CurrentPublisher: String;
  CurrentVersion: String;
  VersionIsWindowsInstaller: Cardinal;
begin
  Result := False;
  ProductCode := '';
  MsiVersion := '';
  if not RegGetSubkeyNames(RootKey, Subkey, Names) then
    Exit;

  for I := 0 to GetArrayLength(Names) - 1 do
  begin
    CurrentKey := Subkey + '\' + Names[I];

    { Strictly match only the Video Compressor Windows Installer registration. }
    if not RegQueryStringValue(RootKey, CurrentKey, 'DisplayName', CurrentName) then
      Continue;
    if CurrentName <> VC_MSI_DISPLAY_NAME then
      Continue;
    if not RegQueryStringValue(RootKey, CurrentKey, 'DisplayVersion', CurrentVersion) then
      Continue;
    if not IsLegacyMsiVersion(CurrentVersion) then
      Continue;
    if not RegQueryStringValue(RootKey, CurrentKey, 'Publisher', CurrentPublisher) then
      Continue;
    if CurrentPublisher <> VC_MSI_PUBLISHER then
      Continue;
    if not RegQueryDWordValue(RootKey, CurrentKey, 'WindowsInstaller', VersionIsWindowsInstaller) then
      Continue;
    if VersionIsWindowsInstaller <> 1 then
      Continue;

    ProductCode := Names[I];
    MsiVersion := CurrentVersion;
    Result := True;
    Exit;
  end;
end;

function IsLegacyMsiInstalled(out ProductCode: String; out MsiVersion: String): Boolean;
const
  HKCU = $80000001;
  HKLM = $80000002;
begin
  Result := False;
  ProductCode := '';
  MsiVersion := '';
  if QueryMsiRegistration(HKCU, VC_UNINSTALL_KEY, ProductCode, MsiVersion) then
    Result := True
  else if QueryMsiRegistration(HKLM, VC_UNINSTALL_KEY, ProductCode, MsiVersion) then
    Result := True;
end;

function MsiUninstallProduct(const ProductCode: String; const Quiet: Boolean): Integer;
var
  Args: String;
  ResultCode: Integer;
begin
  if Quiet then
    Args := Format('/x %s /qn /norestart', [ProductCode])
  else
    Args := Format('/x %s', [ProductCode]);
  if Exec('msiexec.exe', Args, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Result := ResultCode
  else
    Result := -1;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ProductCode: String;
  MsiVersion: String;
  ExitCode: Integer;
  AskResult: Integer;
begin
  Result := '';

  if not IsLegacyMsiInstalled(ProductCode, MsiVersion) then
    Exit;

  if WizardSilent then
  begin
    ExitCode := MsiUninstallProduct(ProductCode, True);
    if ExitCode <> 0 then
      Result := 'The legacy MSI installation (version ' + MsiVersion + ') could not be removed. ' +
        'Windows Installer returned error code ' + IntToStr(ExitCode) + '. Aborting the new install ' +
        'to avoid two conflicting Video Compressor registrations.';
    { On success, run without an else branch: the fresh Inno install proceeds. }
    Exit;
  end;

  AskResult := MsgBox(
    'A previous Windows Installer (MSI) installation of Video Compressor was detected.' + #13#10 +
    'Setup will first remove the old MSI installation, then install the new version.' + #13#10 +
    'Your settings and user data will not be deleted.' + #13#10 + #13#10 +
    'Legacy installation version: ' + MsiVersion,
    mbConfirmation, MB_YESNO);
  if AskResult <> IDYES then
    RaiseException('Setup was cancelled because the legacy MSI installation was not removed.');

  ExitCode := MsiUninstallProduct(ProductCode, False);
  if ExitCode <> 0 then
    RaiseException(
      'Setup could not remove the legacy MSI installation (version ' + MsiVersion + '). ' +
      'Windows Installer returned error code ' + IntToStr(ExitCode) + '. Aborting the new install ' +
      'to avoid two conflicting Video Compressor registrations.');
end;
