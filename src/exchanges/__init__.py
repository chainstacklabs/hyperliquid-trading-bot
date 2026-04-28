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

        # SDK 0.12+ expires_after; absolute epoch-ms is what the SDK expects,
        # but it's more ergonomic to specify a TTL. Accept either: TTL_MS sets
        # a moving deadline at every connect, ABSOLUTE_MS pins one.
        expires_ttl = os.getenv("HYPERLIQUID_EXPIRES_AFTER_TTL_MS")
        expires_abs = os.getenv("HYPERLIQUID_EXPIRES_AFTER_MS")
        expires_after_ms: Optional[int] = None
        if expires_ttl:
            import time
            expires_after_ms = int(time.time() * 1000) + int(expires_ttl)
        elif expires_abs:
            expires_after_ms = int(expires_abs)

        priority_env = os.getenv("HYPERLIQUID_PRIORITY_FEE_BPS")
        default_priority_fee_bps = int(priority_env) if priority_env else None

        if not private_key:
            raise ValueError("private_key is required for Hyperliquid")

        return exchange_class(
            private_key,
            testnet,
            account_address=account_address,
            dex=dex,
            expires_after_ms=expires_after_ms,
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
