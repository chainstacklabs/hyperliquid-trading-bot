"""
Real-time WebSocket monitor for Hyperliquid.

Supports:
- allMids (mid prices for all assets)
- trades  (trade prints for a specific coin)

Designed so you can add more subscriptions by:
1) adding a new subscription dict
2) registering a handler for its channel
"""

import asyncio
import json
import os
import signal
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from dotenv import load_dotenv
import websockets
from hyperliquid.info import Info

load_dotenv()

# WS_URL = os.getenv("HYPERLIQUID_CHAINSTACK_WS_URL")
WS_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_WS_URL")

# BASE_URL = os.getenv("HYPERLIQUID_CHAINSTACK_BASE_URL")
BASE_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_BASE_URL")

ASSETS_TO_TRACK = ["ETH"]  # for allMids prints
TRADES_COIN = "ETH"        # for trades subscription

# ---- Types ----

JsonDict = Dict[str, Any]
Handler = Callable[[JsonDict], Awaitable[None]]


@dataclass(frozen=True)
class Subscription:
    """Represents one WS subscription object (the inner 'subscription': {...})."""
    type: str
    coin: Optional[str] = None
    dex: Optional[str] = None

    def to_ws(self) -> JsonDict:
        sub: JsonDict = {"type": self.type}
        if self.coin is not None:
            sub["coin"] = self.coin
        if self.dex is not None:
            sub["dex"] = self.dex
        return sub


class HyperliquidWsClient:
    def __init__(self, ws_url: str, base_url: str) -> None:
        self.ws_url = ws_url
        self.base_url = base_url

        # state
        self.prices: Dict[str, float] = {}
        self.id_to_symbol: Dict[str, str] = {}

        # dispatcher
        self.handlers: Dict[str, Handler] = {}

        # stop flag
        self._running = True

    # ---- lifecycle ----

    def stop(self) -> None:
        self._running = False

    def install_signal_handlers(self) -> None:
        def _sigint_handler(signum, frame):
            print("\nShutting down...")
            self.stop()

        signal.signal(signal.SIGINT, _sigint_handler)

    async def load_symbol_mapping(self) -> None:
        """
        Loads assetId -> symbol mapping using Info.meta().

        Note: the official allMids doc describes mids as Record<string, string>.
        In practice you may see keys that look like "@<asset_id>" (what your code handles).
        This mapping lets you translate those to symbols.
        """
        info = Info(self.base_url, skip_ws=True)
        meta = info.meta()

        self.id_to_symbol.clear()
        for i, asset_info in enumerate(meta["universe"]):
            symbol = asset_info["name"]
            self.id_to_symbol[str(i)] = symbol

        print(f"Loaded {len(self.id_to_symbol)} asset mappings")

    # ---- subscription helpers ----

    async def send_subscribe(self, websocket, sub: Subscription) -> None:
        msg = {"method": "subscribe", "subscription": sub.to_ws()}
        await websocket.send(json.dumps(msg))

    async def send_unsubscribe(self, websocket, sub: Subscription) -> None:
        msg = {"method": "unsubscribe", "subscription": sub.to_ws()}
        await websocket.send(json.dumps(msg))

    # ---- handler registration ----

    def on(self, channel: str, handler: Handler) -> None:
        """Register a handler for a given incoming message channel."""
        self.handlers[channel] = handler

    async def dispatch(self, data: JsonDict) -> None:
        channel = data.get("channel")
        if not channel:
            return
        handler = self.handlers.get(channel)
        if handler:
            await handler(data)
        else:
            # uncomment if you want to see other channels
            # print(f"ℹ️ Unhandled channel: {channel}")
            pass

    # ---- handlers ----

    async def handle_subscription_response(self, data: JsonDict) -> None:
        print(f"✅ Subscription confirmed: {data.get('data')}")

    async def handle_all_mids(self, data: JsonDict) -> None:
        mids = (data.get("data") or {}).get("mids") or {}
        if not isinstance(mids, dict):
            return

        for k, price_str in mids.items():
            # Keys may be "@<asset_id>" (what your original code assumed),
            # or they may already be coin symbols depending on backend/version.
            symbol: Optional[str] = None

            if isinstance(k, str) and k.startswith("@"):
                # asset_id = k.lstrip("@")
                # symbol = self.id_to_symbol.get(asset_id)
                # if symbol is None:
                #     # This asset ID is not in the perps universe, ignore
                #     continue  # do NOT treat it as ETH
                continue
            elif isinstance(k, str):
                # treat as symbol directly
                symbol = k

            if not symbol or symbol not in ASSETS_TO_TRACK:
                continue

            try:
                new_price = float(price_str)
            except (TypeError, ValueError):
                continue

            old_price = self.prices.get(symbol)
            self.prices[symbol] = new_price

            if old_price is None:
                print(f"🔄 {symbol}: ${new_price:,.2f}")
                continue

            change = new_price - old_price
            change_pct = (change / old_price) * 100 if old_price != 0 else 0.0
            direction = "📈" if change > 0 else "📉" if change < 0 else "➡️"
            print(f"{direction} {symbol}: ${new_price:,.2f} ({change_pct:+.2f}%)")

    async def handle_trades(self, data: JsonDict) -> None:
        trades = data.get("data")
        if not isinstance(trades, list):
            return

        for t in trades:
            if not isinstance(t, dict):
                continue

            coin = t.get("coin")
            if coin != TRADES_COIN:
                continue

            side = t.get("side")
            px = t.get("px")
            sz = t.get("sz")
            ts = t.get("time")
            tid = t.get("tid")

            # minimal, readable trade print
            print(f"🧾 TRADE {coin} {side} px={px} sz={sz} time={ts} tid={tid}")

    # ---- main loop ----

    async def run(self, subs: List[Subscription]) -> None:
        print("🔗 Loading asset mappings...")
        await self.load_symbol_mapping()

        print(f"🔗 Connecting to {self.ws_url}")
        self.install_signal_handlers()

        # register default handlers
        self.on("subscriptionResponse", self.handle_subscription_response)
        self.on("allMids", self.handle_all_mids)
        self.on("trades", self.handle_trades)

        try:
            async with websockets.connect(self.ws_url) as websocket:
                print("✅ WebSocket connected!")

                # subscribe to everything requested
                for sub in subs:
                    await self.send_subscribe(websocket, sub)

                print("📡 Subscribed to:")
                for sub in subs:
                    print(f"  - {sub.to_ws()}")
                print("=" * 60)

                async for message in websocket:
                    if not self._running:
                        break

                    try:
                        payload = json.loads(message)
                    except json.JSONDecodeError:
                        print("⚠️ Received invalid JSON")
                        continue

                    try:
                        await self.dispatch(payload)
                    except Exception as e:
                        print(f"❌ Handler error: {e}")

        except websockets.exceptions.ConnectionClosed:
            print("🔌 WebSocket connection closed")
        except Exception as e:
            print(f"❌ WebSocket error: {e}")
        finally:
            print("👋 Disconnected")


async def main():
    print("Hyperliquid WebSocket Monitor")
    print("=" * 60)

    if not WS_URL or not BASE_URL:
        print("❌ Missing environment variables")
        print("Set HYPERLIQUID_TESTNET_PUBLIC_WS_URL and HYPERLIQUID_TESTNET_PUBLIC_BASE_URL in your .env file")
        return

    client = HyperliquidWsClient(ws_url=WS_URL, base_url=BASE_URL)
    # Subscriptions:
    # mids
    # allMids
    # trades
    # book
    # user
    # funding
    # liquidations
    # openOrders
    # fills
    # ohlc
    subs = [
        Subscription(type="allMids"),                                
        # Subscription(type="allMids", dex="xyz"),                 
        # Subscription(type="trades", coin=TRADES_COIN),
    ]
    # Dexes:
    #     curl -s https://api.hyperliquid.xyz/info \
    #   -H 'Content-Type: application/json' \
    #   -d '{"type":"perpDexs"}'

    # xyz (fullName: “XYZ”)
    # flx (fullName: “Felix Exchange”)
    # vntl (fullName: “Ventuals”)
    # hyna (fullName: “HyENA”)
    await client.run(subs)


if __name__ == "__main__":
    asyncio.run(main())
