# FREELLMAPI Web3 Suite 0.1.1 — build and acceptance report

Result: **PASS WITH LIMITATIONS**, Windows x64 preview, 2026-11-15.
Source: https://github.com/mikeminer/freellmapi/tree/web3-suite/companions/web3-suite

## Tested artifact

- Filename: FREELLMAPI-Web3-Suite-Setup-0.1.1.exe
- Size: 888,899,440 bytes.
- SHA-256: `b4933f39a8cd355a56926924e89b22e0dfc3f3f4f0a29d0087db9c8b508dd77d`.
- Compiled with Inno Setup 7.1.1, LZMA2 normal compression, four block threads.
- The exact EXE was installed over 0.1.0 in an isolated directory with spaces.
  All installed-file checks used that extracted installation, not just staging.
- The installer is unsigned. It is a preview, not clean-VM certification.

## Complete DevFridge Game Builder

Version **1.12.0**, **894 original files**, **8,662,636 bytes**.
Source: https://github.com/mikeminer/devfridge/tree/bb8668f2bf838fde3f42288cc827432c9125ad78/skills/devfridge-game-builder
Git tree: `430fda812dd5ddbc73e63ad3173080b127b6eb98`.

Every original Git blob was copied, including references, assets, scripts,
hidden configuration, vendored workflows and license files. The source Git
blob hashes and per-file SHA-256 hashes are recorded in bundled-skills.json.
The installed package passed verification of all 894 hashes and the exact
file set. The parent repository's Business Source License 1.1 is also included
as licenses/DevFridge-BUSL-1.1.txt; vendored licenses remain in their folders.

Setup automatically created the managed registration before any Skills object
was constructed by QA. Offline loading and a support-file read passed. The
desktop's DevFridge action selected the registered package without a download
or model request. CMD and Remote use the same shared Skills registration.
Reopening was idempotent. Existing manually linked DevFridge packages remain
preferred by the desktop entry point. Installation executes no skill scripts
or hooks and does not start a game build or financial operation.

## Checks performed

The first draft EXE was rejected before publication: its generic Inno exclusion
`router\*` unintentionally removed four files from the skill's nested
vendor/gamedev-skills/router folder. The integrity guard refused registration
and the actual-EXE QA failed. The exclusion is now anchored at the payload root
(`\router\*`, likewise for prerequisites), and the final compiled EXE passed
the complete installed-file check. The rejected draft's checksum and missing
paths are retained in results.json; it is not the released download.

| Check | Observed result |
| --- | --- |
| Skill, packaging, setup, memory, session, branding and Android fixture regressions | 114 unique tests passed on host Python 3.12 and again on private Python 3.13 |
| Full source package | 894 original Git blobs exported and verified; all installed SHA-256 hashes matched |
| Actual update | 0.1.0 installed, then the exact 0.1.1 EXE installed over it |
| Skill availability | Setup registered it; offline SKILL.md/support-file reads and catalog lookup passed |
| Desktop DevFridge action | Selected the built-in registration; command dispatch mocked, no game/model call |
| Data preservation | Synthetic config, key placeholder, session/context and custom skill remained byte-for-byte unchanged through update and removal |
| Registration | Idempotent reopen; unit tests cover missing/altered files, path escape, foreign metadata and manual supersession |
| Runtime | Doctor passed with system Python excluded from test PATH; private sys.prefix confirmed |
| Python environments | Private Python created a venv and executed its interpreter |
| Desktop | Native Qt app started and saved a screenshot using an explicit synthetic fixture |
| Browser | Bundled Chromium navigated a local data page and verified its heading |
| Web3 SDKs | 14 Node tests passed from the extracted installation using fake transports |
| DevFridge helpers | 40 fixture tests passed on a temporary copy using bundled Node/Python, including icon generation, quality controls and subscription evidence |
| Uninstall | Scratch installation removed; only managed skill metadata removed, synthetic user data retained |
| External activity | No real signing key read, model call, wallet operation or real order requested by these tests |

Two runtime executions do not count as 228 unique cases. Unit tests and fake
transports do not certify exchange execution or provider availability.

## Other included components

Private CPython 3.13.17 with pip/venv, official unmodified FreeLLMAPI portable
router v0.16.30, Tailscale 1.104.7 MSI, Node 22.23.5, PowerShell 7.7.0, MinGit
2.56.1, Android platform-tools, Chromium 145/revision 1208, PySide6/Qt 6.10.1,
the Web3 SDK dependencies, Visual C++ Redistributable and the existing Android
APK. Component source URLs/checksums are in the locks and suite-manifest.json.
The Inno compiler is build tooling, not installed application functionality.
Third-party software retains its own notices; the complete suite is not MIT.

## Limits and privacy

- Earlier attempts in a Documents-based scratch program directory failed with
  Windows access denied (5) replacing requirements-audio.txt, including a retry
  with the old 0.1.0 installer. The file was not read-only and Restart Manager
  reported no holder; separate rename/delete probes succeeded. Final acceptance
  used isolated LocalAppData Temp storage. The underlying Windows denial cause
  was not established; the failed logs were retained locally. Custom protected
  installation directories are not certified by the successful final run.
- This was an isolated build-host installation with a private PATH, not a
  genuinely clean Windows VM. No code-signing certificate was applied.
- Tailscale install/login and provider onboarding were not repeated on the
  live PC. The test excluded Tailscale and never started the bundled router.
- The existing debug-signed Android 0.2.8 APK is unchanged, SHA-256
  `5e3bca0156c6cb7ad70e4f3f6d8c93d0222982fa171acf8328c89cb7317a7941`.
  No physical-phone installation or pairing was performed in this run.
- No new game, Blender assets, Android game APK, live model conversation,
  voice-model download or financial execution was tested for this release.
- Android SDK/JDK and Blender are separate optional game-development tools;
  bundling the skill does not supply them or guarantee game-build completion.
- Existing bridges, wallets, keys, vault and conversations were not packaged
  or replaced. All acceptance-test credentials/state were synthetic.

This is the user's complementary Web3 fork, not an upstream release or
endorsement. Changes were pushed to web3-suite; no PR was opened.
