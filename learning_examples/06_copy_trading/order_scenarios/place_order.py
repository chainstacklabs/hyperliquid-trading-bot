"""
Test script for placing spot orders to see how they appear in WebSocket.
Creates a single spot order that can be monitored by the mirroring script.
"""

import asyncio
import json
import os
from math import floor, log10
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.signing import OrderType as HLOrderType

load_dotenv()

BASE_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_BASE_URL")
SYMBOL = "PURR/USDC"  # Spot pair
ORDER_SIZE = 3.0  # Size to meet minimum $10 USDC requirement
PRICE_OFFSET_PCT = -50  # 50% below market for buy order (won't fill)


def round_hl_spot_price(px: float, sz_decimals: int = 0) -> float:
    """Round to Hyperliquid's spot price rules: max 5 significant figures and
    at most (8 - szDecimals) decimal places."""
    if px <= 0:
        return px
    sig = round(px, -int(floor(log10(abs(px)))) + 4)
    return round(sig, max(0, 8 - sz_decimals))


async def place_spot_order():
    """Place a spot order"""
    print("Placing Spot Order Test")
    print("=" * 40)

    private_key = os.getenv("HYPERLIQUID_TESTNET_PRIVATE_KEY")
    if not private_key:
        print("❌ Missing HYPERLIQUID_TESTNET_PRIVATE_KEY in .env file")
        return

    try:
        wallet = Account.from_key(private_key)
        exchange = Exchange(wallet, BASE_URL)
        info = Info(BASE_URL, skip_ws=True)

        print(f"📱 Wallet: {wallet.address}")
        print(f"🏛️  Vault address: {exchange.vault_address}")
        print(f"🌐 Is mainnet: {exchange.base_url == 'https://api.hyperliquid.xyz'}")

        # Get spot metadata to find the asset
        spot_data = info.spot_meta_and_asset_ctxs()
        if len(spot_data) >= 2:
            spot_meta = spot_data[0]
            asset_ctxs = spot_data[1]

            # Find PURR/USDC
            target_pair = None
            for pair in spot_meta.get("universe", []):
                if pair.get("name") == SYMBOL:
                    target_pair = pair
                    break

            if not target_pair:
                print(f"❌ Could not find {SYMBOL} in spot universe")
                return

            pair_index = target_pair.get("index")

            # Get current price
            if pair_index < len(asset_ctxs):
                ctx = asset_ctxs[pair_index]
                market_price = float(ctx.get("midPx", ctx.get("markPx", 0)))

                if market_price <= 0:
                    print(f"❌ Could not get valid price for {SYMBOL}")
                    return

                base_token_idx = target_pair.get("tokens", [0])[0]
                tokens = spot_meta.get("tokens", [])
                sz_decimals = tokens[base_token_idx].get("szDecimals", 0) if base_token_idx < len(tokens) else 0

                order_price = market_price * (1 + PRICE_OFFSET_PCT / 100)
                order_price = round_hl_spot_price(order_price, sz_decimals)

                print(f"💰 Current {SYMBOL} price: ${market_price}")
                print(f"📝 Placing BUY order: {ORDER_SIZE} {SYMBOL} @ ${order_price}")

                # Place the order using asset name for spot trading
                result = exchange.order(
                    name=SYMBOL,
                    is_buy=True,
                    sz=ORDER_SIZE,
                    limit_px=order_price,
                    order_type=HLOrderType({"limit": {"tif": "Gtc"}}),
                    reduce_only=False,
                )

                print(f"📋 Order result:")
                print(json.dumps(result, indent=2))

                if result and result.get("status") == "ok":
                    response_data = result.get("response", {}).get("data", {})
                    statuses = response_data.get("statuses", [])

                    if statuses:
                        status_info = statuses[0]
                        if "resting" in status_info:
                            order_id = status_info["resting"]["oid"]
                            print(f"✅ Order placed successfully! ID: {order_id}")
                            print(f"🔍 Monitor this order in your WebSocket stream")
                        elif "filled" in status_info:
                            print(f"✅ Order filled immediately!")
                        else:
                            print(f"⚠️ Unexpected status: {status_info}")
                else:
                    print(f"❌ Order failed: {result}")
            else:
                print(f"❌ Asset index {pair_index} out of range")
        else:
            print("❌ Could not get spot metadata")

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(place_spot_order())
