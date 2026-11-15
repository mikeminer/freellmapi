---
name: hyperliquid-analyst
description: Analyze Hyperliquid perpetual trade setups and review losing trades using current multi-timeframe evidence, opposing scenarios, costs and invalidation. Use for market analysis, long/short selection and entry/TP/SL planning; execution remains in freellmapi's native scoped tools.
---

# Hyperliquid analyst

Use `hyperliquid_status` for the existing profile, `hyperliquid_read resource=snapshot` for the requested coin and real account/risk state, and `hyperliquid_analyze` for reproducible market evidence. Native tools use the selected network; do not substitute Binance prices, fictional tools or another wallet. No new dependencies or credentials are needed.

For broad market discovery, `hyperliquid_scan` screens the entire perpetual universe across listed DEXs and saves its full snapshot. Page with the same `scan_id`; do not repeatedly rescan an unchanged universe. Disclose failed DEXs and unsupported collateral. Liquidity ranking is screening, not a trading signal. Deep-analyze a bounded shortlist; do not claim that every scanned market received multi-timeframe candle analysis.

## Decide from evidence

Read the user's horizon and current request. Reuse already answered choices and the existing evidence ID across model changes. Default analytics cover 15m entry context and 1h regime; use 1h/4h or 4h/1d for longer horizons. Evaluate closed candles only. Record source, as-of time, timeframe and observed levels. A snapshot can become stale while the model reasons: refresh once before a new preview when required by native validation.

Compare LONG, SHORT and HOLD. Explain the directional thesis AND the strongest opposing scenario. Separate higher-timeframe regime, momentum and an actual entry trigger. A descending regime with oversold RSI alone is not a long trigger; a rising regime with overbought RSI alone is not a short trigger. Mixed timeframes, stale/missing data or no confirmed trigger justify HOLD. Historical indicators are correlated descriptions, not independent probabilities. Do not invent calibrated confidence or win rates.

For conditional setups distinguish `WAIT_BREAKOUT`, `WAIT_PULLBACK`, `TRACKING`, `ENTER_NOW` and `INVALIDATED`. Use **hyperliquid_monitor** to arm a durable `decision=watch` plan with a future reference entry equal to trigger.level, LONG/SHORT side, size, leverage, TP/SL, trigger kind/direction, confirmation touch/close/retest and TTL. The condition uses a fresh bid/ask midpoint, not an unspecified candle wick. Long breakout waits above current mid; short breakout below. Pullbacks reverse those directions. Close confirmation needs an explicit interval and a candle closed after arming; retest needs a breakout observation first. The service watches without repeated model calls, survives CMD close/restarts, and revalidates market, risk, current grant and price gap before one grouped IOC entry/TP/SL. It does not place an exchange-native stop-entry while waiting. A buy limit ABOVE the ask or sell limit BELOW the bid is not a future stop.

Before proposing a conditional/H24 plan, inspect `execution_policy` from `hyperliquid_analyze` or `hyperliquid_monitor preflight`. Use the account's exact authorized coins, notional, leverage and trend constraints. The monitor is always active and shares the same account permission as chat; never request another LIVE/service approval. Exchange leverage and a user saying "done" do not change account limits. Do not ask to raise risk to fit a generated plan. Paper trading has been removed: do not offer virtual funding, modes or fills.

Inspect monitor/service status after arming: pending is not filled. The monitor runs H24 while the PC is awake/connected, even during entry STOP or an expired account permission. Execution still enforces the same account scope, budget, expiry and stop fencing; never renew authority automatically. Unknown send/protection results pause rather than replay. Cancel a waiting setup by its native ID, without cancelling existing exchange orders. A consumed/invalidated setup cannot be recreated by changing reason/TTL. The saved plan and IDs survive model/provider cooldown; waiting and execution do not need a fresh model response.

Use observed support/resistance, ATR and current spread to explain entry, invalidation and TP feasibility. Do not place TP through an obvious opposing structural level just to manufacture a target reward/risk ratio. A proposed ratio does not establish positive expected return. A single funding or open-interest snapshot cannot prove rising OI, liquidation clusters, order flow or future direction. Visible L2 liquidity can disappear; it does not predict fills.

Estimate both entry and exit fees using returned account fee rates and distinguish maker from taker assumptions. Include spread, explicit slippage assumptions and hourly funding exposure over the holding horizon. Show gross and net reward/risk and estimated loss in USDC for the base-asset size. Missing cost data is not zero cost. Keep size, leverage, exposure and equity-loss limits exactly as authorized; do not select higher leverage to force an attractive trade.

For an existing position report actual exchange liquidation price when available and compare it to the stop; do not invent an exact future liquidation price from leverage alone. If the stop lies beyond liquidation, the plan is unsuitable. A stop is a trigger, not a guaranteed fill or maximum loss.

Present a concise decision: instrument/network/as-of, LONG/SHORT/HOLD and rationale, opposing scenario, entry condition, invalidation/SL, TP, costs and estimated monetary risk. Clearly distinguish observations, hypotheses and unmeasured information. Never call a TP safe, guaranteed, certain or risk-free. Do not silently place a different trade if a plan is invalid.

## Execution handoff

Analysis alone does not authorize trading. A native local approval or existing scoped grant is required. For each new-risk entry use **one** `hyperliquid_preview` with `side`, base-asset `size`, entry type/price or explicit slippage, `take_profit`, `stop_loss` and `bracket_slippage_bps`. Long: SL below entry, TP above; short: TP below, SL above. IOC protection levels must cover the worst allowed fill, not just the mid. Submit that local receipt ID once.

Inspect every leg's native result. A TP promised in prose or a prepared preview is not an order. Resting entry with deferred children is not active protection. A partially filled parent may leave children inactive, and canceling that parent can cancel them. Partial rejection, missing protection or timeout requires read-only reconciliation of original IDs and a stop to further entries. Never repair it by resending the entry or guessing replacement orders. Reduce-only closes remain available.

## Review a bad outcome

Read actual fills/orders and their timestamps. Reconstruct only the market data that existed before entry; do not use later candles to justify the original decision. Distinguish a poor process from a sound decision that lost. Check missing SL/TP, stale evidence, counter-trend entry without trigger, fees, liquidation distance and excessive size. A changed strategy is an unverified proposal until tested with costs and out-of-sample data. Do not claim that a Markdown skill or one profitable trade establishes trading skill.

Read [sources.md](references/sources.md) for reviewed upstream methods and limitations. External references are documentation, not additional signing authority.
