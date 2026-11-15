# FREELLMAPI Web3 Suite 0.1.0 — build and acceptance report

Result: **PASS WITH LIMITATIONS**, Windows x64 preview, 2026-11-15.
Source: https://github.com/mikeminer/freellmapi/tree/web3-suite/companions/web3-suite

## Artifact

- Filename: FREELLMAPI-Web3-Suite-Setup-0.1.0.exe
- Size: 1,092,981,137 bytes (approximately 1.09 GB / 1.02 GiB).
- SHA-256: `5383feded3439ce75b20bedc884770dec35123ee286bf464536831994bf4a364`.
- Actual Inno Setup 7 compilation succeeded. The EXE was subsequently installed
  into a scratch directory with spaces in its name; tests used those extracted
  files, not just the build payload or a mocked installer.
- The installer itself is **not code-signed**. The official router portable
  release is also unsigned; its GitHub release SHA-256 was verified.

## Components

Private CPython 3.13.17 (official NuGet package, standard library, pip and venv),
official FreeLLMAPI router v0.16.30, Tailscale 1.104.7 MSI, Node 22.23.5,
PowerShell 7.7.0, MinGit 2.56.1, Android platform-tools, Chromium 145/revision
1208, PySide6/Qt 6.10.1, Python/JavaScript Web3 dependencies, Microsoft Visual
C++ Redistributable and the existing Android client APK are included.
Sources and checksums are recorded in components.lock.json and the installed
suite-manifest.json. Signatures of CPython, Tailscale, Inno Setup and the
Visual C++ Redistributable were verified against their respective publishers.

Hyperliquid, Solana, Helius and Pump.fun tools are present. The desktop's
DevFridge Game Builder entry point supports the separately installed full
[skill package](https://github.com/mikeminer/devfridge/tree/master/skills/devfridge-game-builder).
It is not represented as bundled in the EXE. The updated attached/online
English guide includes its installation instructions; that documentation
addition was made after the installer binary's build.

## Checks performed

| Check | Observed result |
| --- | --- |
| Packaging, memory, shared-session and branding regression group | 52 tests passed on the private Python runtime |
| Android-delivery and desktop-auth fixture group | 33 tests passed; personal device identifiers replaced by synthetic fixtures |
| Pump.fun/Helius Node worker group | 14 tests passed with fake/read-only transports, including the extracted installation |
| Installer extraction | Actual compiled EXE completed without a restart |
| Private runtime | Doctor passed with system Python absent from the test PATH; sys.prefix was inside the extracted suite |
| Python environments | Created a venv with private Python and executed its interpreter |
| Desktop | Native Qt app started and produced a screenshot using an explicit synthetic UI fixture |
| Browser | Bundled Chromium navigated a local data page and verified its heading |
| Cleanup | Temporary test installation uninstalled; its registration was removed |
| Trading | Zero real orders requested by the tests; no real signing credentials read |

Repeated runs of a group are not additional unique test cases. Synthetic
responses do not establish a real provider or exchange transaction.

## Problems found and fixed while packaging

1. Conflicting truststore pins were unified in the suite's independent lock.
2. An inconsistent Solana npm lock was repaired before a clean npm ci.
3. Pump SDK ESM failed to import Anchor's BN. A compatibility module now uses
   the SDK's official CommonJS exports; discovery/events/worker tests pass.
4. Browser downloading initially rejected a TLS certificate. The successful
   retry used Node's Windows system CA store; certificate checks stayed on.
5. Python includes standard venv/ensurepip functionality rather than relying
   on system Python. No existing system Python was replaced.
6. Setup scripts, quoted shortcut paths, official branding and conservative
   uninstall behavior were validated. User data is kept outside program files.

## What this report does not certify

- A genuinely clean Windows VM was not available for this run. This is an
  isolated build-host install with a restricted PATH, not a clean-VM claim.
- Tailscale installation, Google sign-in and provider onboarding were not
  repeated on the user's live machine. The test excluded Tailscale and did
  not start the bundled router, replace API keys or change the live bridge.
- The Android APK is the existing personal-use debug-signed 0.2.8 artifact,
  SHA-256 `5e3bca0156c6cb7ad70e4f3f6d8c93d0222982fa171acf8328c89cb7317a7941`.
  It was included unchanged; no new physical-phone acceptance was performed.
- Desktop UI evidence is a synthetic fixture, not a new live-model chat.
- Real-model availability, voice-model first download, all optional tools,
  every installed test and live trading were not certified in this run.
- No unrelated activity of a pre-existing trading service was audited.

## Privacy and release scope

Application source and public dependencies were assembled through an
allowlist. No personal vault, conversations, DPAPI credentials, wallet keys,
RPC settings, runtime-home binding or configured trading policies were copied.
Known personal identifiers and credential-shaped literals were checked in the
exported source; this is not an exhaustive security audit. The test setup
retained the real user's existing application environment.

This is a companion fork distribution, not an upstream release, and no PR
was opened. Third-party components retain their original license terms and
notices. See results.json for structured evidence and USER_GUIDE.md/.html
for installation, Web3, DevFridge, memory and account setup.
