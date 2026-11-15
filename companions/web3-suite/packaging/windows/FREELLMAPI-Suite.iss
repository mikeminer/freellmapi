; Compile through build_installer.py: it verifies the private Python payload.
#ifndef PayloadRoot
  #error PayloadRoot must point to a validated, self-contained suite payload.
#endif
#ifndef SuiteVersion
  #define SuiteVersion "0.1.1"
#endif

[Setup]
AppId=freellmapi.agent.suite
AppName=FREELLMAPI Web3 Suite
AppVersion={#SuiteVersion}
AppPublisher=FREELLMAPI companion clients
AppComments=Client CMD, Desktop e Remote complementari al router ufficiale FreeLLMAPI.
DefaultDirName={localappdata}\Programs\FREELLMAPI Web3 Suite
DefaultGroupName=FREELLMAPI
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.17763
SetupIconFile=..\..\desktop\freellmapi-official.ico
UninstallDisplayIcon={app}\assets\freellmapi.ico
OutputDir=..\..\dist\windows
OutputBaseFilename=FREELLMAPI-Web3-Suite-Setup-{#SuiteVersion}
Compression=lzma2/normal
LZMANumBlockThreads=4
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UsePreviousTasks=yes
ChangesEnvironment=yes

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Crea il collegamento FREELLMAPI Desktop sul desktop"; GroupDescription: "Collegamenti:"
Name: "cmdicon"; Description: "Crea il collegamento FREELLMAPI CMD sul desktop"; GroupDescription: "Collegamenti:"

[Types]
Name: "full"; Description: "Full Web3 Suite"
Name: "custom"; Description: "Custom installation"; Flags: iscustom

[Components]
Name: "core"; Description: "Private Python, CMD, Desktop, Web3 SDKs, full DevFridge skill and Android APK"; Types: full custom; Flags: fixed
Name: "router"; Description: "Official FreeLLMAPI router"; Types: full
Name: "tailscale"; Description: "Tailscale private networking (requires Windows administrator approval)"; Types: full

[Files]
Source: "{#PayloadRoot}\*"; Excludes: "\router\*,\prerequisites\*,__pycache__,.links,__dirlock"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: core
Source: "{#PayloadRoot}\router\*"; DestDir: "{app}\router"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: router
Source: "{#PayloadRoot}\prerequisites\tailscale.msi"; DestDir: "{app}\prerequisites"; Flags: ignoreversion; Components: tailscale
Source: "{#PayloadRoot}\prerequisites\vc_redist.x64.exe"; DestDir: "{app}\prerequisites"; Flags: ignoreversion; Components: core
Source: "..\..\desktop\freellmapi-official.ico"; DestDir: "{app}\assets"; DestName: "freellmapi.ico"; Flags: ignoreversion
Source: "..\..\desktop\third_party\FREELLMAPI-LICENSE.txt"; DestDir: "{app}\licenses"; DestName: "FreeLLMAPI-MIT.txt"; Flags: ignoreversion

[Icons]
Name: "{autodesktop}\FREELLMAPI Desktop"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-I -X utf8 ""{app}\launcher\suite.py"" desktop"; WorkingDir: "{userdocs}"; IconFilename: "{app}\assets\freellmapi.ico"; AppUserModelID: "freellmapi.agent.desktop"; Tasks: desktopicon
Name: "{autodesktop}\FREELLMAPI CMD"; Filename: "{app}\runtime\python\python.exe"; Parameters: "-I -X utf8 ""{app}\launcher\suite.py"" cmd"; WorkingDir: "{userdocs}"; IconFilename: "{app}\assets\freellmapi.ico"; AppUserModelID: "freellmapi.agent.cmd"; Tasks: cmdicon
Name: "{group}\FREELLMAPI Desktop"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-I -X utf8 ""{app}\launcher\suite.py"" desktop"; WorkingDir: "{userdocs}"; IconFilename: "{app}\assets\freellmapi.ico"; AppUserModelID: "freellmapi.agent.desktop"
Name: "{group}\FREELLMAPI CMD"; Filename: "{app}\runtime\python\python.exe"; Parameters: "-I -X utf8 ""{app}\launcher\suite.py"" cmd"; WorkingDir: "{userdocs}"; IconFilename: "{app}\assets\freellmapi.ico"; AppUserModelID: "freellmapi.agent.cmd"
Name: "{group}\First-run setup and Android pairing"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-I -X utf8 ""{app}\launcher\suite.py"" setup"; IconFilename: "{app}\assets\freellmapi.ico"
Name: "{group}\Official FreeLLMAPI router"; Filename: "{app}\router\FreeLLMAPI.exe"; IconFilename: "{app}\assets\freellmapi.ico"; Components: router
Name: "{group}\English user guide"; Filename: "{app}\docs\USER_GUIDE.html"; IconFilename: "{app}\assets\freellmapi.ico"

[Run]
Filename: "{app}\prerequisites\vc_redist.x64.exe"; Parameters: "/install /passive /norestart"; Verb: "runas"; Flags: shellexec waituntilterminated; Check: NeedsVisualCpp
Filename: "{app}\runtime\python\python.exe"; Parameters: "-I -X utf8 ""{app}\launcher\suite.py"" register-skills"; Flags: runhidden waituntilterminated; Components: core
Filename: "{sys}\msiexec.exe"; Parameters: "/i ""{app}\prerequisites\tailscale.msi"" /passive /norestart"; Verb: "runas"; Flags: shellexec waituntilterminated; Components: tailscale; Check: NeedsTailscale
Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-I -X utf8 ""{app}\launcher\suite.py"" setup"; Description: "Configure provider accounts, Tailscale and memory"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{app}\runtime\python\python.exe"; Parameters: "-I -X utf8 ""{app}\launcher\suite.py"" unregister-skills"; Flags: runhidden waituntilterminated; RunOnceId: "freellmapi-bundled-skill-registry"

[Code]
function NeedsVisualCpp: Boolean;
var
  Installed: Cardinal;
begin
  Result := not RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed);
  if not Result then Result := Installed <> 1;
end;

function NeedsTailscale: Boolean;
begin
  Result := not FileExists(ExpandConstant('{commonpf64}\Tailscale\tailscale.exe'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Existing, Entry: String;
begin
  if ExpandConstant('{param:NOPATH|0}') = '1' then Exit;
  if CurStep = ssPostInstall then begin
    RegQueryStringValue(HKCU, 'Environment', 'Path', Existing);
    Entry := ExpandConstant('{app}\bin');
    if Pos(';' + Lowercase(Entry) + ';', ';' + Lowercase(Existing) + ';') = 0 then begin
      if (Existing <> '') and (Existing[Length(Existing)] <> ';') then Existing := Existing + ';';
      RegWriteExpandStringValue(HKCU, 'Environment', 'Path', Existing + Entry);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Existing, Entry: String;
  Start: Integer;
begin
  if CurUninstallStep = usPostUninstall then begin
    RegQueryStringValue(HKCU, 'Environment', 'Path', Existing);
    Existing := ';' + Existing + ';';
    Entry := ';' + ExpandConstant('{app}\bin') + ';';
    Start := Pos(Lowercase(Entry), Lowercase(Existing));
    if Start > 0 then begin
      Delete(Existing, Start, Length(Entry) - 1);
      if (Length(Existing) > 0) and (Existing[1] = ';') then Delete(Existing, 1, 1);
      if (Length(Existing) > 0) and (Existing[Length(Existing)] = ';') then Delete(Existing, Length(Existing), 1);
      RegWriteExpandStringValue(HKCU, 'Environment', 'Path', Existing);
    end;
  end;
end;

// No data-directory removal, background agent launch, or trading action here.
