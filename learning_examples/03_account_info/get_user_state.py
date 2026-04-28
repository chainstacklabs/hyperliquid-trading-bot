"""
Displays account state including balances, positions, and margin health.
"""

import asyncio
import os
from typing import Optional

import httpx
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.info import Info

load_dotenv()

BASE_URL = os.getenv("HYPERLIQUID_CHAINSTACK_BASE_URL") or os.getenv(
    "HYPERLIQUID_TESTNET_PUBLIC_BASE_URL"
)
WALLET_ADDRESS = os.getenv("TESTNET_WALLET_ADDRESS")
# HIP-3: optional builder-deployed dex name. Empty = main perp.
DEX = os.getenv("DEX", "")


async def method_1_sdk() -> Optional[Account]:
    """Method 1: Using Hyperliquid Python SDK"""
    print("Method 1: Hyperliquid SDK")
    print("-" * 30)

    try:
        print("Connecting to Hyperliquid testnet...")
        info = Info(BASE_URL, skip_ws=True)

        user_state = info.user_state(WALLET_ADDRESS, dex=DEX)
        print(f"Connection successful! API responded with account data (dex={DEX or 'main'})")

        margin_summary = user_state.get("marginSummary", {})
        account_value = float(margin_summary.get("accountValue", 0))
        withdrawable = float(user_state.get("withdrawable", 0))
        total_margin_used = float(margin_summary.get("totalMarginUsed", 0))

        print(f"Account value: ${account_value:,.2f}")
        print(f"Withdrawable: ${withdrawable:,.2f}")
        print(f"Margin used: ${total_margin_used:,.2f}")

        return user_state

    except Exception as e:
        print(f"Connection failed: {e}")
        return None


async def method_2_raw_api() -> Optional[Account]:
    """Method 2: Raw HTTP API calls"""
    print("\nMethod 2: Raw HTTP API")
    print("-" * 30)

    try:
        print("Making direct HTTP request to Hyperliquid API...")
        async with httpx.AsyncClient() as client:
            payload = {"type": "clearinghouseState", "user": WALLET_ADDRESS}
            if DEX:
                payload["dex"] = DEX
            response = await client.post(
                f"{BASE_URL}/info",
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            if response.status_code == 200:
                print("Connection successful! HTTP API responded")
                user_state = response.json()

                margin_summary = user_state.get("marginSummary", {})
                account_value = float(margin_summary.get("accountValue", 0))
                withdrawable = float(user_state.get("withdrawable", 0))
                cross_margin_used = float(
                    user_state.get("crossMaintenanceMarginUsed", 0)
                )

                print(f"Account value: ${account_value:,.2f}")
                print(f"Withdrawable: ${withdrawable:,.2f}")
                print(f"Margin used: ${cross_margin_used:,.2f}")
                return user_state
            else:
                print(f"Connection failed: HTTP {response.status_code}")
                return None

    except Exception as e:
        print(f"Connection failed: {e}")
        return None


async def main() -> None:
    print("Hyperliquid Connection Methods")
    print("=" * 40)

    await method_1_sdk()
    await method_2_raw_api()


if __name__ == "__main__":
    asyncio.run(main())
