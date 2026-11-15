# FREELLMAPI Web3 Suite for Windows

This is the **Web3-focused companion suite maintained in the `web3-suite`
branch of `mikeminer/freellmapi`**. It combines an independent Python coding
agent, a Qt desktop client and a native Android remote client with the
unmodified official FreeLLMAPI router. The router aggregates provider APIs;
the companion agent supplies tools, persistent conversations and Web3
integrations. This fork is not an official upstream product endorsement.

## What is included

The Windows x64 installer includes a private Python runtime and its libraries,
CMD and desktop clients, the official router distribution, Tailscale's Windows
MSI, the Android APK, Node.js, PowerShell, MinGit, Android platform tools and
the Web3 integration source. The build manifest records versions and SHA-256
checksums. Python does not need to be installed on the destination PC.
Additional downloaded browser binaries and SDK dependencies are listed in
the release manifest. Voice transcription dependencies are included; the
Whisper base model is downloaded when voice transcription is first used.

Hyperliquid integration covers market/account reads, typed trade plans,
leverage verification, entry and TP/SL handling and persisted receipts.
Solana integration covers local wallet signing, RPC reads and simulations;
Helius and Pump.fun/PumpSwap workers use their SDKs. Availability of each
operation depends on its connector configuration and installed SDK/runtime.
Installing the suite does not connect a wallet or authorize a real trade.

## Install on a new Windows PC

1. Download `FREELLMAPI-Web3-Suite-Setup-0.1.0.exe` and `SHA256SUMS.txt`
   from this fork's release. Compare the checksum with `Get-FileHash`.
2. Run the installer. Full installation includes the router and Tailscale;
   choose Custom to reuse an existing router or VPN installation. Tailscale
   requires administrator approval. The suite itself is installed per user.
3. Desktop and Start menu shortcuts use the official FreeLLMAPI icon. Select
   the desktop shortcuts you want. An existing selection is kept on update.
4. Open **First-run setup and Android pairing** from the Start menu. Start the
   official router, open its dashboard and add your own provider credentials.
   Copy the unified API key into the setup dialog's masked field.
5. Keep the detected/default API URL (`http://127.0.0.1:31415`) or enter the
   actual URL of an existing router. The setup verifies the ready model
   catalog before saving. A ready catalog entry does not guarantee a live
   response or unlimited provider quota.
6. Open Tailscale and complete Google sign-in personally. Choose your vault
   folder. The memory files work without the Obsidian application; Obsidian
   is an optional editor for those Markdown files.
7. Open **FREELLMAPI Desktop** or **FREELLMAPI CMD**. Both use the same local
   coordinator and conversation storage. A model's quota or provider failure
   can still prevent a response even when installation is successful.

Provider accounts, Tailscale login and wallet credentials are personal setup
steps. They are not supplied by this installer. Paid services are not enabled
automatically. Free tiers retain their own limits and terms.

## Android / Seeker

Install Tailscale on Android and sign in with the same owner account as the
PC. The APK is installed under the suite's `mobile` directory. You can copy
it to the phone, or use the setup dialog's explicit USB installation button
after unlocking the phone and approving Android USB debugging.

The bundled APK is the existing personally tested debug-signed build; it is
not a Play Store release. Android updates require the same signing certificate.
The setup uses `adb install -r` and never clears app data or uninstalls a
different-signature app. On signature mismatch it reports failure instead.

Start Remote from FREELLMAPI using `/remote start`; then use `/remote pair`
to generate a short-lived pairing code/QR. In the Android app connect to the
PC and complete **Sign in with Tailscale / Google**. Only the PC owner's
Tailscale identity is accepted by the current personal-use implementation.
The PC, coordinator and VPN must remain available while using the phone.
An invitation to another Tailscale user is not application authorization.

## Conversations and memory

Choose the same session in CMD, Desktop and Android to continue the same
conversation. Different sessions remain different conversations. A single
coordinator owns each session; reconnects reconcile the existing request ID
instead of replaying an uncertain message. Model changes retain the same
history and previous tool results.

Conversation history, project facts and personal profile facts are distinct.
Facts limited to a chat are retained in that session rather than promoted to
the global profile. Memory scope boundaries survive restart and compaction.
Persistent memory retrieval is bounded: this is not infinite model context or
a guarantee that every old fact will be retrieved on every turn.

The selected vault contains readable Markdown history and project notes.
Relevant excerpts can be sent to the selected provider. Local storage does
not mean the cloud model sees no conversation data.

## Hyperliquid

Open Settings → Hyperliquid connector on the PC and configure the correct
network, account or vault. Enter signing credentials only in the local panel.
The model receives structured requests/results, not the signing key. DPAPI
protects saved credentials for the current Windows user; it is not a sandbox
against programs running as that user.

Market analysis and a plan are not proof of execution. Read the persisted
receipt, exchange order IDs and verification of TP/SL. When an entry result
is uncertain, reconcile those IDs instead of issuing a new entry. A take
profit level cannot guarantee profit or execution. Mainnet operations use
real funds; begin with read-only checks and simulations when configuring a
new installation. The installer and its acceptance tests send no orders.

## Solana, Helius and Pump.fun

Open the Solana connector to import the dedicated wallet locally. Configure
mainnet/testnet/devnet and an optional private RPC endpoint. An empty RPC
setting uses the connector's public default; private Helius URLs stay local.
Configure the Helius API features separately where their API credential is
required: an RPC URL does not necessarily grant every Helius API feature.

Use balance reads, mint/protocol checks, quotes and transaction simulation
before signing. Bonding-curve and PumpSwap AMM operations use different SDK
paths. Slippage, fees, liquidity and transaction confirmation matter. There
is no guaranteed return, rug-pull protection or background trading enabled
by installing the software.

## Storage, updates and removal

Program files: `%LOCALAPPDATA%\Programs\FREELLMAPI Web3 Suite`.
Agent settings, protected keys and sessions: `%APPDATA%\freellmapi-cli`.
Official router data: `%APPDATA%\FreeLLMAPI`.
The vault stays in the folder you select. Existing protected credentials are
not overwritten when updating or when the setup key field is left blank.
DPAPI files are bound to the Windows user; copying those files to another PC
does not provision credentials there. Configure/import credentials locally.

Close clients before updating. The installer replaces program files, keeps
user data and does not replay unfinished orders. The official portable router
is version-pinned with the suite; do not assume the NSIS auto-updater applies
to this bundled portable distribution. Tailscale is reused when already
installed. Removing the suite preserves the vault, user data and Tailscale.

No real trading monitor or Windows boot task is created by the installer.
Starting persistent trading is a separate intentional operation. To keep the
phone available, leave the PC and its existing coordinator running.

## Troubleshooting and validation limits

- **API unreachable:** start the official router, check the actual port and
  unified key, then run the setup check again. Do not paste keys into chat.
- **No usable model:** add/repair provider credentials and inspect cooldowns;
  catalog size does not establish usability.
- **Tailscale login rejected:** PC and phone must use the same owner account.
- **Pair code expired:** generate a new code on the PC, not a new API key.
- **Uncertain send:** inspect the receipt; do not resend a financial action.
- **Missing game-build tools:** Java/Android build SDKs, Blender and Docker are
  optional development prerequisites, not all bundled into this release.
- **Voice first use:** the Whisper model needs a download and local CPU time.

Read `BUILD_REPORT.md` and `results.json` supplied with the release for the
actual checks performed. A private-runtime smoke test on the build machine
is not a certified clean-VM, physical-phone or live-trading acceptance test.

## Build and provenance

Build tooling is under `companions/web3-suite/packaging/windows` in the fork.
The download lock records official sources and checksums. Dependencies and
application source are staged separately from accounts and runtime state.
See the packaging README for exact build commands. Third-party software keeps
its original licenses; the official logo's MIT notice is included. The suite
is a complementary fork maintained separately from upstream FreeLLMAPI.
