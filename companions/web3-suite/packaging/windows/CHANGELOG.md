# Windows Web3 Suite changelog

## 0.1.2 — 2026-11-16

- Preserve the current instrument and answered user constraints in the first API
  payload after a model change. Proposal follow-ups retain Hyperliquid context.
- Expose native confirm, refresh and cancel controls for trade proposals; correct
  malformed or flattened plan arguments once without turning a preview into an
  execution request. Reject stale unoffered tools before dispatch.
- Calculate percentage allocation from verified available capital and exchange
  size precision. Persist one margin-versus-notional choice across model changes.
- Keep checkpoint-only recovery instructions scoped to actual checkpoint recovery.
  Known explicit model choices reuse the catalog instead of blocking on a reload.
- Include the new allocation module and verify installed source hashes against a
  clean Git export. Add private-state/identifier scanning before publication.
- Omit optional public PyCryptodome self-tests containing example private keys;
  keep the runtime cryptography and license notices intact.
- Preserve the private Python runtime, official router, Tailscale prerequisite,
  complete 894-file DevFridge Game Builder package and existing Android APK.

Installation QA uses isolated synthetic settings and fake SDK transports. It does
not connect wallets, start the trading service, submit orders or provide accounts.
