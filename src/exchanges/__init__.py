"""
Exchange Integrations

Technical implementations for different exchanges/DEXes.
Add new exchanges by implementing the ExchangeAdapter interface.

To add a new exchange:
1. Implement ExchangeAdapter interface
2. Add to EXCHANGE_REGISTRY
3. Update configuration to use exchange type
"""

from typing import Optional

from .hyperliquid import HyperliquidAdapter, HyperliquidMarketData


def _parse_int_env(name: str, raw: Optional[str]) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError as e:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from e

# Exchange registry - makes it easy to add new DEXes
EXCHANGE_REGISTRY = {
    "hyperliquid": HyperliquidAdapter,
}

# Aliases for convenience
EXCHANGE_REGISTRY["hl"] = HyperliquidAdapter


def create_exchange_adapter(exchange_type: str, config: dict):
    """
    Factory function to create exchange adapters.

    Makes it easy to add new exchanges:
    1. Implement ExchangeAdapter interface
    2. Add to EXCHANGE_REGISTRY
    3. Done!

    Args:
        exchange_type: Type of exchange (e.g., "hyperliquid", "binance")
        config: Exchange configuration dictionary

    Returns:
        ExchangeAdapter instance
    """
    if exchange_type not in EXCHANGE_REGISTRY:
        available = ", ".join(EXCHANGE_REGISTRY.keys())
        raise ValueError(
            f"Unknown exchange type: {exchange_type}. Available: {available}"
        )

    exchange_class = EXCHANGE_REGISTRY[exchange_type]

    # Extract common parameters for exchange initialization
    if exchange_type in ["hyperliquid", "hl"]:
        import os

        private_key = config.get("private_key")
        testnet = config.get("testnet", True)
        account_address = config.get("account_address") or os.getenv(
            "TESTNET_WALLET_ADDRESS" if testnet else "MAINNET_WALLET_ADDRESS"
        )
        dex = config.get("dex")

        # SDK 0.12+ expires_after for signed L1 actions. Two modes:
        #   TTL_MS: moving deadline computed inside HyperliquidAdapter.connect()
        #   ABSOLUTE_MS: pinned epoch-ms, set once
        # We pass the raw values through and let the adapter compute the
        # absolute deadline at connect time so long-running deployments don't
        # silently drift past a stale construction-time deadline.
        expires_ttl_raw = os.getenv("HYPERLIQUID_EXPIRES_AFTER_TTL_MS")
        expires_abs_raw = os.getenv("HYPERLIQUID_EXPIRES_AFTER_MS")
        if expires_ttl_raw and expires_abs_raw:
            import logging
            logging.getLogger(__name__).warning(
                "Both HYPERLIQUID_EXPIRES_AFTER_TTL_MS and "
                "HYPERLIQUID_EXPIRES_AFTER_MS are set; using TTL."
            )
        expires_after_ttl_ms = _parse_int_env(
            "HYPERLIQUID_EXPIRES_AFTER_TTL_MS", expires_ttl_raw
        )
        if expires_after_ttl_ms is not None and expires_after_ttl_ms <= 0:
            raise ValueError(
                f"HYPERLIQUID_EXPIRES_AFTER_TTL_MS must be > 0, got {expires_after_ttl_ms}"
            )
        expires_after_ms = (
            None
            if expires_after_ttl_ms is not None
            else _parse_int_env("HYPERLIQUID_EXPIRES_AFTER_MS", expires_abs_raw)
        )
        if expires_after_ms is not None:
            import time
            now_ms = int(time.time() * 1000)
            if expires_after_ms <= now_ms:
                raise ValueError(
                    f"HYPERLIQUID_EXPIRES_AFTER_MS={expires_after_ms} is "
                    f"already in the past (now={now_ms})"
                )
        default_priority_fee_bps = _parse_int_env(
            "HYPERLIQUID_PRIORITY_FEE_BPS",
            os.getenv("HYPERLIQUID_PRIORITY_FEE_BPS"),
        )

        if not private_key:
            raise ValueError("private_key is required for Hyperliquid")

        return exchange_class(
            private_key,
            testnet,
            account_address=account_address,
            dex=dex,
            expires_after_ms=expires_after_ms,
            expires_after_ttl_ms=expires_after_ttl_ms,
            default_priority_fee_bps=default_priority_fee_bps,
        )

    # Future exchanges will have their own initialization logic here
    # elif exchange_type == "binance":
    #     api_key = config.get("api_key")
    #     secret_key = config.get("secret_key")
    #     return exchange_class(api_key, secret_key)

    else:
        # Default: try to pass config directly
        return exchange_class(config)


__all__ = [
    "HyperliquidAdapter",
    "HyperliquidMarketData",
    "EXCHANGE_REGISTRY",
    "create_exchange_adapter",
]
