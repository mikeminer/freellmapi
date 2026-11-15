# Build FREELLMAPI Web3 Suite for Windows x64

The fork's `web3-suite` branch contains independent Python/Qt/Android clients
under `companions/web3-suite`. The official TypeScript router stays separate.
See [USER_GUIDE.md](USER_GUIDE.md) for accounts, Web3, memory and remote access.

## Build

Use a Windows x64 build machine with Python/pip, Git, Internet and about 10 GB free.
The destination PC needs neither Python nor Node. Inno Setup 7 is the compiler;
its official signed installer is included in the download lock. Install the
compiler into a development tools directory without changing Tailscale/router.

Run from the companion source root (the directory containing freellmapi.py):

```powershell
python packaging/windows/fetch_components.py
python packaging/windows/prepare_runtime.py
python packaging/windows/assemble.py
$suiteRoot = (Resolve-Path .).Path
$env:PATH = "$suiteRoot\dist\payload\runtime\node;" + $env:PATH
foreach ($sdk in @('pumpfun', 'solana')) {
    Push-Location "$suiteRoot\dist\payload\agent\integrations\$sdk"
    & "$suiteRoot\dist\payload\runtime\node\npm.cmd" ci --ignore-scripts --no-audit --no-fund
    if ($LASTEXITCODE) { throw "SDK installation failed: $sdk" }
    Pop-Location
}
$env:PLAYWRIGHT_BROWSERS_PATH = "$suiteRoot\dist\payload\runtime\browsers"
$env:NODE_USE_SYSTEM_CA = '1'
& "$suiteRoot\dist\payload\runtime\python\python.exe" -I -m playwright install chromium
python packaging/windows/build_installer.py --payload dist/payload --check
python packaging/windows/build_installer.py --payload dist/payload --version 0.1.1 --iscc C:\tools\inno\ISCC.exe
```

The component lock pins official URLs and SHA-256 digests. CPython's NuGet
package supplies a complete private runtime with pip and venv. Wheels are
installed from requirements-suite.lock, not copied from the host environment.
Browser downloads keep TLS validation enabled and use Windows trusted CAs.
SDK install scripts do not run. The Pump SDK's official CommonJS exports avoid
its ESM/Anchor BN interop failure.

The Android APK must exist at mobile/build/freellmapi-remote.apk. This release
includes the existing debug-signed personal-use build; replacing it requires
Android SDK/JDK and a compatible signing certificate. Private signing keys,
credentials, conversations, local policies and runtime-home.json are excluded.

`assemble.py` also exports every Git blob in the pinned DevFridge subtree,
including vendor and hidden files. `bundled-skills.lock.json` pins version
1.12.0, commit, tree and 894-file count; staging verifies Git blob hashes and
writes a per-file SHA-256 manifest. It copies the upstream license separately.
The build fails if any bundled file is missing, altered or unexpected. No
skill install hooks or scripts execute. Git is needed only on the build host;
the installed skill is fully available offline.

Output: dist/windows/FREELLMAPI-Web3-Suite-Setup-0.1.1.exe.
The official icon appears on Setup, Uninstall, Installed Apps and shortcuts.
Desktop AppUserModelID matches Qt. Both desktop shortcuts are selected by
default; selection is preserved on updates. The current-user PATH receives
only the suite's bin directory; uninstall removes that exact entry and retains
user data. The app starts through private Python with quoted installation paths.

Full setup includes the official portable router and Tailscale MSI; existing
Tailscale is reused. Visual C++ Redistributable installs only when missing.
Prerequisites may request Windows administrator approval. No account login,
wallet authorization, trading service or boot task is created automatically.
Setup registers the built-in DevFridge package in the shared user profile.
CMD, Desktop and Remote coordinators also ensure this managed registration
on first use. Registration is idempotent and leaves manually installed skills
alone. Uninstall removes only its own managed link and preserves user files.

## Validate

```powershell
python -m unittest test_bundled_skills test_windows_installer test_suite_setup test_skills test_skill_router test_desktop_connectors test_memory_write_policy test_memory test_brain_memory test_shared_desktop test_session_routing test_desktop_languages_brand
python packaging/windows/qa_release.py --version 0.1.1 --upgrade-from 0.1.0
```

Exercise the exact compiled EXE in a test directory containing spaces, using
`/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOICONS /TASKS=""`
`/COMPONENTS="core,router" /NOPATH=1 /DIR="C:\test\suite with spaces"`.
NOPATH suppresses PATH registration for isolated installation/portable tests.
It does not suppress prerequisite detection. Set a temporary APPDATA and
FREELLMAPI_HOME **before launching Setup**, since its postinstall child
registers the skill. Keep the user's live environment and configuration
separate. Run runtime/python/python.exe -I launcher/suite.py
doctor with a temporary APPDATA/FREELLMAPI_HOME and PATH lacking system Python.
Check venv, a synthetic desktop fixture, local browser navigation and fake-
transport SDK tests. Check that Setup itself registered all 894 files before
constructing a Skills instance, offline support-file reads, desktop selection,
idempotent reopen, upgrade preservation and removal of only managed metadata
at uninstall. Do not configure keys, replay requests or send orders.

Complete acceptance additionally needs a genuinely clean Windows VM, personal
Tailscale/provider login and physical Android pairing. Distinguish those from
build-host smoke tests. Publish an explicitly labelled prerelease while clean-
VM acceptance or code signing remains incomplete. Release assets: EXE, APK,
SHA256SUMS.txt, English guide, build report and machine-readable results.

Third-party components keep their licenses and notices. Qt/PySide, PyMuPDF,
Git, Python and Chromium are not collectively relicensed as MIT. The official
logo's MIT notice is included. Companion source is available in the fork;
upstream source and notices remain preserved. This is not upstream endorsement.
DevFridge's Business Source License 1.1 is included as a separate notice;
vendored skills keep their own licenses. Do not label the entire bundle MIT.
