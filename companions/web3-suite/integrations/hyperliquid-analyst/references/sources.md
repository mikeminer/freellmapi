# Sources reviewed 2026-11-14

- [NousResearch Hermes Hyperliquid](https://github.com/NousResearch/hermes-agent/blob/main/optional-skills/blockchain/hyperliquid/SKILL.md): public read-only account/market data and post-trade review. Useful separation of decision quality from outcome; no predictive validation supplied by the skill. Its Hermes shell paths are replaced by native freellmapi tools.
- [eplt Binance Signal Engine](https://github.com/eplt/binance-signal-engine/blob/main/SKILL.md): separate regime, momentum and entry context across timeframes; ATR/structure and cost-aware sizing. Methodological reference only. No upstream scripts or Binance account defaults imported; freellmapi uses actual Hyperliquid data. Scores are not calibrated win probabilities or proof of profitability.
- [yshi0730 Hyperliquid](https://github.com/yshi0730/hyperliquid-skill/blob/main/SKILL.md): reviewed, not installed. Requires a private-key environment variable and tools freellmapi does not provide; contains hardcoded leverage/fees and an eight-hour funding statement. Those defaults are not used.
- [Official funding](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding): hourly settlement; do not confuse the eight-hour rate formula with payment frequency.
- [Official TP/SL](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/take-profit-and-stop-loss-orders-tp-sl): children of a parent order are conditional on its fills; partial fills and cancellation have important consequences.
- [Official exchange API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint) and [Info API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals): native endpoints and grouped order schema.

This integration is original freellmapi code/instructions informed by these references, not a bundled upstream trading engine. Unit/integration tests prove data handling and execution constraints, not a profitable strategy.
