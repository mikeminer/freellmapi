# FREELLMAPI Web3 Suite

Independent CMD, Qt desktop and Android companion clients with Hyperliquid, Solana, Helius and Pump.fun integrations.

Also supports [DevFridge Game Builder](https://github.com/mikeminer/devfridge/tree/master/skills/devfridge-game-builder)
for Three.js browser games, Seeker APKs and optional Solana wallet/subscription
integration. Setup 0.1.1 includes the complete skill version 1.12.0 with all 894
files, including references, assets, scripts and vendored workflows. It registers
automatically for CMD, Desktop and the shared Android coordinator. No separate
clone or skill download is needed. Original licenses are retained.

[Download Setup 0.1.1](https://github.com/mikeminer/freellmapi/releases/tag/web3-suite-v0.1.1).


Current source additionally includes the 2026-11-16 execution reliability fixes
and interactive trade proposal choices. Model handoff now retains the current
instrument and answered constraints; malformed proposal arguments receive one
bounded format correction. Percentage sizing is calculated natively from verified
available capital and asset precision, with one persisted margin/notional choice.
Physical Seeker acceptance used real Gemini and Agnes responses: ETH, 25x, 50%,
visible confirmation/refresh/cancel buttons, and cancellation without orders.
304 regression tests passed. This covers those two reported models and the
proposal workflow, not all models or live execution. The 0.1.1 installer predates
these fixes.

[English user guide](packaging/windows/USER_GUIDE.md) · [Build and verification](packaging/windows/README.md)

Uses the unmodified official FreeLLMAPI router. This fork is a complementary Web3 distribution, not an upstream endorsement.

![Desktop installation smoke test](docs/desktop-preview.png)

The screenshot uses a synthetic UI fixture from the installed private runtime;
it does not depict a live model response or trading operation.

[Installer acceptance report](BUILD_REPORT.md) · [Structured results](results.json)
