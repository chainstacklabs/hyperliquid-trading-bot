"""
Exchange Integrations

Technical implementations for different exchanges/DEXes.
Add new exchanges by implementing the ExchangeAdapter interface.

To add a new exchange:
1. Implement ExchangeAdapter interface
2. Add to EXCHANGE_REGISTRY
3. Update configuration to use exchange type
"""

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

        if not private_key:
            raise ValueError("private_key is required for Hyperliquid")

        return exchange_class(
            private_key, testnet, account_address=account_address, dex=dex
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
