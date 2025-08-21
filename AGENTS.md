# AGENTS Guidelines for This Repository

This repository contains a Hyperliquid trading bot written in Python. When working on the project interactively with an agent (e.g. the Codex CLI) please follow the guidelines below for safe development and testing.

## 1. Use Paper Trading Mode for Testing

* **Always use paper trading mode** (`PAPER_TRADING=true`) when testing any changes.
* **Never run live trading** during agent development sessions to avoid unintended financial consequences.
* **Test thoroughly** using `test_paper_trading.py` before any deployment.

## 2. Keep Dependencies in Sync

If you modify dependencies:

1. Update dependencies using `uv add <package>` or `uv remove <package>`.
2. The `uv.lock` file will be automatically updated.
3. Verify compatibility with Python 3.13+ as specified in the project.

## 3. Testing Guidelines

* **Always test in paper mode first:**
  ```bash
  PAPER_TRADING=true uv run python test_paper_trading.py
  ```
* **Run unit tests:**
  ```bash
  uv run python test_bot.py
  ```
* **Never commit or expose private keys** - use `.env` files (git-ignored).

## 4. Environment Configuration

Create a `.env` file for development (never commit this):
```env
HYPERLIQUID_PRIVATE_KEY=your_test_key_here
PAPER_TRADING=true
PAPER_BALANCE=10000
BUY_AMOUNT_USDC=100
SLIPPAGE=0.05
POLL_INTERVAL=0.5
```

## 5. Code Safety Practices

* Validate all configuration values before use.
* Include error handling for network operations.
* Log all trading decisions for audit purposes.
* Never disable paper trading safeguards programmatically.

## 6. Useful Commands Recap

| Command                                      | Purpose                               |
| -------------------------------------------- | ------------------------------------- |
| `uv sync`                                    | Install/update dependencies           |
| `uv run python test_bot.py`                 | Run unit tests                        |
| `PAPER_TRADING=true uv run python sniper_bot.py` | Run bot in paper trading mode   |
| `uv run python test_paper_trading.py`       | Test paper trading functionality     |

## 7. Security Reminders

* **Never expose private keys** in code, logs, or commits.
* **Always use paper trading** for development and testing.
* **Review all changes carefully** before deploying to production.
* **Keep audit logs** of all trading operations.

---

Following these practices ensures safe development and prevents accidental trades or exposure of sensitive credentials. Always prioritize security and use paper trading mode during development.