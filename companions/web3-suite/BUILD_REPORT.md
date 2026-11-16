# FREELLMAPI Web3 Suite 0.1.2 — build and acceptance report

Result: **PASS WITH LIMITATIONS**, Windows x64 preview, 2026-11-16 (Europe/Rome).
Source: https://github.com/mikeminer/freellmapi/tree/6b6c8a91bd63bad8e22f53f1f608008412e10b7c/companions/web3-suite

## Tested artifact

- File: `FREELLMAPI-Web3-Suite-Setup-0.1.2.exe`
- Size: 887,536,814 bytes.
- SHA-256: `bb4dca74f4e4cf48eed11297d91d1a917c81911210bf9f33fda05a02b41562f2`.
- Inno Setup 7.1.1; official icon verified at 16, 32, 48, 64, 128 and 256 pixels.
- The exact compiled EXE was tested as an update from 0.1.1 and as a fresh
  program installation with an empty application profile, in directories with spaces.
- The installer is unsigned. This is a preview, not a certified clean-Windows-VM run.

## Included fixes and components

The suite includes the latest current-instrument/model-handoff context fixes,
native trade proposal buttons, one bounded malformed-plan correction and native
percentage allocation with a persisted margin/notional choice. The required
allocation module is present and checked after extraction.

Private CPython 3.13.17 with pip/venv and verified private
Tcl/Tk 8.6.15 for wallet panels, official unmodified
FreeLLMAPI router v0.16.30, Tailscale 1.104.7
MSI, Node v22.23.5, PowerShell v7.7.0,
MinGit v2.56.1.windows.1, Android platform tools, Chromium
145.0.7632.6 (revision 1208), Qt/PySide and
Web3 SDK dependencies are included. The Android APK is unchanged from 0.1.1.

The complete DevFridge Game Builder 1.12.0 package contains all **894 original
Git blobs**, references, scripts, assets, hidden support files and vendored
workflows. Every installed package file hash and managed registration was checked.
Original third-party notices remain included; the entire bundle is not relicensed as MIT.

## Verification

| Check | Observed result |
| --- | --- |
| Installed sources | 467 shipped file hashes matched the clean Git export/manifest |
| Private runtime regressions | 386 distinct Python tests passed using the installed Python 3.13 runtime; external socket connections blocked, none attempted |
| SDK regression tests | 14 Node tests passed using fake transports, no wallet or live RPC submission |
| Actual upgrade | Exact 0.1.1 EXE installed, followed by exact 0.1.2 EXE |
| Update preservation | Synthetic configuration, encrypted-key placeholder, session/context and custom skill remained byte-for-byte unchanged |
| Fresh installation | Empty application profile, no configuration or credential generated; bundled skill registered automatically |
| Host Python independence | System Python absent from the test PATH, private sys.prefix confirmed |
| Python environments | Private Python created and executed a venv |
| Desktop | Qt app rendered an explicit synthetic fixture |
| Browser | Bundled Chromium verified a local data page |
| Removal | Both scratch program installations removed; user fixtures preserved; only managed skill registration removed |
| Configured user accounts | Real encrypted credential, settings and pairing hashes unchanged |
| Financial activity | No real signing key read, model call, wallet operation or order requested by installer acceptance |

Tests run on host and private Python are not counted as twice as many unique cases.
Mock SDK tests do not certify live exchange execution or provider availability.

## Privacy

Build inputs came from an exported tracked Git tree, not the personal development
workspace. No runtime-home binding, environment secrets, wallet files, account
database, chat history, vault or Remote pairing data was staged. The full audit
scanned **45,574 distributable files** and
3,260,796,940 bytes: no configured-state files,
private identifier matches or private-key material were found.
After that scan, one public requirements file was added and the two source
manifests were refreshed. Those three files passed a separate delta audit; all
other staged source hashes and dependencies were unchanged. See privacy-audit.json.

The first candidate payload was rejected because an optional public PyCryptodome
self-test contained an example private key. The complete optional Crypto/SelfTest
directory was omitted; runtime cryptography and license notices remain. No rejected
candidate was published. Raw QA logs, private scan values, real profile hashes and
personal paths remain local. Public release reports contain aggregate/synthetic evidence.

The first compiled candidate was also rejected: the private Python lacked
Tcl/Tk, and three resource tests ran from the wrong working directory. The final
candidate includes matching official Tcl/Tk files and runs tests from the
installed agent directory. The exact final EXE passed both installation modes.

## Limits

- Both installation modes ran on the build machine, not a genuinely clean VM.
- Tailscale installation/login, provider onboarding and physical Android pairing
  were not exercised during installer acceptance. These require the owner's own
  account and interaction. The bundled MSI was verified, not reinstalled over the
  owner's connected VPN.
- The desktop fixture is synthetic. No funds or real model accounts were used.
- The EXE is not code signed. Existing Android signing compatibility is unchanged.
- No Windows startup task, trading service or financial authorization is created
  automatically by Setup. Installation does not provide accounts or free-tier quotas.

See USER_GUIDE.md, CHANGELOG.md, results.json, suite-manifest.json,
source-files.json, bundled-skills.json and SHA256SUMS.txt.
