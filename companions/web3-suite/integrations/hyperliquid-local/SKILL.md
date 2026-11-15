---
name: hyperliquid-local
description: Use freellmapi's local Python Hyperliquid wallet panel and native tools for account/vault access, live market data and scoped automated perpetual trading on mainnet or testnet.
---

# Hyperliquid local

Use native `hyperliquid_*` tools already provided by freellmapi. SDK: official `hyperliquid-python-sdk`. A Hyperliquid vault is a trading target, distinct from the Obsidian memory vault.

## Profiles and login

`hyperliquid_status` reports configured public profiles and actual grants. `hyperliquid_panel` or `/hyperliquid` opens the separate local Python panel. Select mainnet/testnet, the public master account and either that account or a managed vault/subaccount. Use separate profile names for distinct scopes. Select a saved profile with `hyperliquid_select`.

Keys are entered only into the local GUI, protected at rest with Windows-user DPAPI, and decrypted only by the separate Python signing worker. Do not request keys/seeds in chat, shell arguments, environment variables, notes, skill files or model calls. Do not inspect credential blobs or build another signing path. The vault has no private key; its master/authorized API agent signs. Read account data by its actual master/target address, not the API agent address. Do not create replacement wallets or authorize API agents automatically.

DPAPI and process separation keep normal tool/model traffic free of secrets; they do not create an OS sandbox against other code running as the same Windows user. Restricted API agents can trade but cannot sign owner-only vault fund movements.

## Read and prepare

Use `hyperliquid_read` for live account/spot balances, perpetual positions, open orders/fills, vaults, exact markets and closed candles. `resource=asset` verifies current leverage even with no open position. Respect the configured network and exact symbols; never substitute Binance data for Hyperliquid execution data.

The execution adapter supports perpetual orders, grouped entry+TP+SL, cancellation, leverage and manual vault USDC deposits/withdrawals. Spot balances are readable; spot order execution is not implemented. Price/size precision and vault ownership are verified by native code. Preserve validation errors and correct their specific inputs. `read resource=snapshot` returns perps, orders, leverage, exact market and closed candles for one coin in one tool call.

Account/snapshot reads include `balance_context` and actual `userAbstraction`. Under Unified Account spot state is the collateral source; per-dex accountValue=0 does not mean no funds. Standard and Unified Account USDC perpetuals are supported. `read resource=risk` returns conservative USDC risk equity/free margin, holds, all-DEX exposure, position margin, pending entry reserves and maintenance ratio. Positive unrealized PnL and non-USDC spot assets are not credited; losses and potentially overlapping reserves are deducted conservatively. This is a risk budget, not total portfolio valuation. All DEX states/orders and collateral identities must be verified; missing data, other-collateral exposures, Portfolio Margin and DEX abstraction fail closed. `activeAssetData` size/free-margin caps and actual leverage are checked before signing. Capital flows during/after the baseline snapshot require a new authorization so deposits cannot mask drawdown. Never transfer funds or change account mode to bypass limits. New non-reduce-only entries must satisfy the 10 USD exchange minimum and user caps; if incompatible use HOLD.

`hyperliquid_preview` creates an unsigned, expiring intent, with exact scope, order type, price/size, client order ID and estimated notional. Every new-risk entry, manual or automatic, requires TP and SL in that same preview. Call `hyperliquid_analyze` first for current closed candles on two timeframes, book, funding and fees; compare long/short/HOLD. For universe discovery use `hyperliquid_scan`, then deep-analyze selected candidates. Market orders are bounded IOC orders with explicit slippage. `hyperliquid_submit` uses an existing preview ID. General `/auto on` alone supplies no financial authorization. Accepted/deferred children do not establish active protection; inspect the native verification result and do not replay entries after partial/unknown protection.

## Authorized automation

The user enables automation after reviewing account/network/target, symbols, order/exposure/turnover limits, leverage, equity-loss threshold, frequency and expiration. Strategy mode defaults to `Automatica (freellmapi)`: the user delegates selection/adaptation of approach, entry, size, TP/SL and HOLD. They do not have to type a strategy. Custom instructions are optional under `Personalizzata`. Within the grant, native tools submit without asking for every order. The Python worker rechecks scope, permissions and limits before signing.

Use `hyperliquid_strategy inspect/update` for the persistent plan. Plans are unverified model proposals and cannot change policy. Use actual fills/receipts to assess results; do not claim learning or profit from a plan alone. Model-managed new entries must specify both decimal-string `take_profit`/`stop_loss` and explicit `bracket_slippage_bps` in their preview. The worker submits parent and opposite-side reduce-only exits using SDK `bulk_orders(..., grouping="normalTpsl")`. Group submission does not prove all three legs were accepted or that trigger exits are active. A mixed success/error is `partial`, freezes new writes and requires inspection of each leg. Never replace the group blindly. Do not auto-cancel reduce-only protection while a position is open.

The controller runs in the selected open CLI session, one cycle at a time, with only Hyperliquid tools and a maximum of eight model requests per cycle. It rereads persisted strategy/grant and current account/market data; a model change does not change scope or authorize new risk. Missing data or no valid signal means HOLD. It does not operate while the CLI is closed. An interrupted pending cycle is not replayed. `/hyperliquid stop` blocks further automatic submissions; it does not implicitly close positions or cancel existing orders.

Cycle completion also requires actual current-cycle native read evidence and either verified receipts or explicit HOLD. Pseudo tool calls printed as prose are never executed. Missing evidence or unresolved prepared/uncertain/partial operations pauses automation rather than scheduling the same empty cycle again.

Inference recovery attempts at most three bounded inference attempts per request, preferring actual tool-use observations. A transport timeout in streaming gets one nonstream attempt with the same payload before changing route; successful transport preference is cached temporarily. Successful routes stay selected for the cycle. Payload/tool results/client IDs survive failover; tools are never replayed as an inference retry. Temporary transport cooldowns expire; invalid tool protocol remains excluded for the cycle. A repeated pseudo-call response pauses after one correction. Unknown/partial submissions trigger one native read-only reconciliation and immediate stop. Unicode display errors are handled locally; original text/tool arguments remain in UTF-8 session memory. An expired NEVER-authorized preview may be refreshed with the same intent/client IDs, fresh risk inputs and a fresh review; sending/uncertain/partial actions cannot be refreshed. No promise of unlimited provider quota or availability is made.

Deposits/withdrawals are outside the auto grant and require local manual review plus master signing authority. Do not route fund movements through automatic trading, shell or browser tools.

## Receipts and recovery

Consult `hyperliquid_operations`. Prepared is unsigned; authorized/sending is not success. Accepted/resting is not filled. An HTTP success can contain per-order errors. Report only the actual SDK receipt and observed order state.

On timeout, process interruption or unknown exchange outcome, keep the original intent/cloid and reconcile using `operations` or `read order_status`. Never replace an uncertain order or blindly resubmit it. Cancellation, leverage and fund-transfer uncertainty require independent verification; an unknown order ID does not prove that submission failed. Same-intent deduplication is durable across model changes within a turn/cycle.

## Official references

- [SDK](https://github.com/hyperliquid-dex/hyperliquid-python-sdk)
- [Exchange / vault scope](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint)
- [API wallets](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/nonces-and-api-wallets)
- [Info / perpetuals](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals)
