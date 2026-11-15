# Official SDK workers

Install pinned dependencies with `npm ci --ignore-scripts`. Node 20+ is required. On this Windows PC Node 24 and `NODE_USE_SYSTEM_CA=1` use the system certificate store.

- `worker.mjs`: Pump inspect, quote, sampled discovery; unsigned transactions only.
- `events.mjs`, `scout.mjs`: confirmed public events, emitter/discriminator checks, bounded queue, explicit gaps; not a complete event archive.
- `helius-worker.mjs`: official `helius-sdk` read-only facade, strict method/input allowlist, finite transport, no retries or sends. RPC provider credentials arrive only through stdin from the local DPAPI configuration; never in argv or logs. Wallet keys never enter Node.
- `idl/`: slim independent instruction schemas copied from the installed official Pump SDK for the Python validator.

Run `npm test` for SDK wire/event/offline-builder tests, and the Python native suite using `integrations/solana/.venv/Scripts/python.exe -m unittest test_pump_local test_helius test_solana_tools` from the project root.

`npm audit` has unresolved upstream findings; detailed report under `qa_runs/pumpfun_20261114/dependency-audit.json`. Do not apply incompatible forced SDK downgrades as an automatic audit fix. Native bigint bindings were not built; this environment uses the pure JavaScript implementation.

See the project’s `PUMPFUN.md` for local configuration, real execution scope, consent, recovery and limitations.
