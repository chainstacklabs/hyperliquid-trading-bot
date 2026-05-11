"""
Hyperliquid Market Data Provider

WebSocket-based real-time market data implementation.
Technical implementation separated from business logic.
"""

import asyncio
import json
from typing import Dict, List, Optional, Callable, Any
import time

from interfaces.strategy import MarketData
from core.endpoint_router import get_endpoint_router, redact_address, redact_url


class HyperliquidMarketData:
    """
    Hyperliquid WebSocket market data provider

    Provides real-time price feeds and market data via WebSocket.
    Handles reconnection and error recovery automatically.
    """

    def __init__(self, testnet: bool = True):
        self.testnet = testnet
        self.ws = None
        self.running = False
        self.subscribed_assets: set = set()

        # Callbacks
        self.price_callbacks: Dict[str, List[Callable[[MarketData], None]]] = {}

        # Latest data cache
        self.latest_data: Dict[str, MarketData] = {}

        # Connection parameters
        self.reconnect_delay = 5.0
        self.max_reconnect_attempts = 10

        # Task management
        self.message_handler_task = None

        # Endpoint router for smart routing
        self.endpoint_router = get_endpoint_router(testnet)

        # HIP-3 dex tracking: which dex an asset is subscribed via, and which
        # dex-scoped allMids subscriptions have already been sent.
        self._asset_dex: Dict[str, str] = {}
        self._dex_subscriptions: set = set()

        # Generic channel subscription state. Callbacks are keyed by
        # (type, coin) so multiple per-coin subscriptions on the same channel
        # type don't cross-fire. Subscriptions list is replayed on reconnect.
        self._channel_callbacks: Dict[
            tuple, List[Callable[[Dict[str, Any]], Any]]
        ] = {}
        self._channel_subscriptions: List[Dict[str, Any]] = []

    def _resolve_ws_url(self) -> str:
        url = self.endpoint_router.get_endpoint_for_method("subscribe_price")
        if url:
            return url
        return (
            "wss://api.hyperliquid-testnet.xyz/ws"
            if self.testnet
            else "wss://api.hyperliquid.xyz/ws"
        )

    async def connect(self) -> bool:
        """Connect to Hyperliquid WebSocket using public endpoint"""
        try:
            import websockets

            ws_url = self._resolve_ws_url()

            self.ws = await websockets.connect(ws_url)
            self.running = True

            # Only start message handler if not already running
            if self.message_handler_task is None or self.message_handler_task.done():
                self.message_handler_task = asyncio.create_task(self._message_handler())

            print(
                f"✅ Connected to Hyperliquid WebSocket ({'testnet' if self.testnet else 'mainnet'})"
            )
            print(f"📡 Using WebSocket: {redact_url(ws_url)}")
            return True

        except Exception as e:
            print(f"❌ Failed to connect to WebSocket: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from WebSocket"""
        self.running = False

        # Cancel message handler task
        if self.message_handler_task and not self.message_handler_task.done():
            self.message_handler_task.cancel()
            try:
                await self.message_handler_task
            except asyncio.CancelledError:
                pass

        if self.ws:
            await self.ws.close()
            self.ws = None
        print("🔌 Disconnected from Hyperliquid WebSocket")

    async def subscribe_price_updates(
        self,
        asset: str,
        callback: Callable[[MarketData], None],
        dex: Optional[str] = None,
    ) -> None:
        """Subscribe to allMids price updates for an asset.

        `dex` selects a HIP-3 dex; if not given and the asset name is
        namespaced (e.g. "felix:CRCL"), the prefix is used. allMids on the
        main dex returns spot mids too, so spot subscriptions stay there.
        """
        resolved_dex = (
            dex
            if dex is not None
            else (
                asset.split(":", 1)[0]
                if ":" in asset and not asset.startswith("@")
                else ""
            )
        )
        self._asset_dex[asset] = resolved_dex

        if asset not in self.price_callbacks:
            self.price_callbacks[asset] = []
        self.price_callbacks[asset].append(callback)
        self.subscribed_assets.add(asset)

        if (
            self.ws
            and self.running
            and resolved_dex not in self._dex_subscriptions
        ):
            subscription: Dict[str, Any] = {"type": "allMids"}
            if resolved_dex:
                subscription["dex"] = resolved_dex
            await self.ws.send(
                json.dumps({"method": "subscribe", "subscription": subscription})
            )
            self._dex_subscriptions.add(resolved_dex)

        print(
            f"📊 Subscribed to {asset} price updates (dex={resolved_dex or 'main'})"
        )

    async def unsubscribe_price_updates(
        self, asset: str, callback: Callable[[MarketData], None]
    ) -> None:
        """Unsubscribe from price updates"""

        if asset in self.price_callbacks:
            try:
                self.price_callbacks[asset].remove(callback)
                if not self.price_callbacks[asset]:
                    del self.price_callbacks[asset]
                    self.subscribed_assets.discard(asset)
            except ValueError:
                pass

    async def subscribe_channel(
        self,
        subscription: Dict[str, Any],
        callback: Callable[[Dict[str, Any]], Any],
    ) -> None:
        """Generic subscription for HIP-3-era channels (bbo, activeAssetCtx,
        activeAssetData, userTwapSliceFills, userTwapHistory, webData3,
        allDexsAssetCtxs, allDexsClearinghouseState, etc.).

        Pass the full `subscription` dict (e.g. {"type": "bbo", "coin": "BTC"}).
        Callbacks are keyed by (type, coin) so per-coin subscriptions don't
        cross-fire. activeSpotAssetCtx is aliased to activeAssetCtx for
        callers using the generic name.
        """
        if not (self.ws and self.running):
            raise RuntimeError("WebSocket not connected")

        sub_type = subscription.get("type")
        if not sub_type:
            raise ValueError("subscription must have a 'type'")

        coin = subscription.get("coin")
        self._channel_callbacks.setdefault((sub_type, coin), []).append(callback)
        self._channel_subscriptions.append(dict(subscription))
        await self.ws.send(
            json.dumps({"method": "subscribe", "subscription": subscription})
        )
        display_sub = {
            k: (redact_address(v) if k == "user" and isinstance(v, str) else v)
            for k, v in subscription.items()
        }
        print(f"📡 Subscribed to channel: {display_sub}")

    def get_latest_price(self, asset: str) -> Optional[float]:
        """Get latest cached price for an asset"""
        if asset in self.latest_data:
            return self.latest_data[asset].price
        return None

    def get_latest_data(self, asset: str) -> Optional[MarketData]:
        """Get latest cached market data for an asset"""
        return self.latest_data.get(asset)

    async def _message_handler(self) -> None:
        """Handle incoming WebSocket messages"""

        reconnect_attempts = 0

        while self.running:
            try:
                if not self.ws:
                    # Attempt reconnection (without calling self.connect() to avoid task recursion)
                    if reconnect_attempts < self.max_reconnect_attempts:
                        print(
                            f"🔄 Reconnecting to WebSocket (attempt {reconnect_attempts + 1})"
                        )
                        if await self._reconnect():
                            reconnect_attempts = 0
                            # Re-subscribe to assets
                            await self._resubscribe_all()
                        else:
                            reconnect_attempts += 1
                            await asyncio.sleep(self.reconnect_delay)
                            continue
                    else:
                        print("❌ Max reconnection attempts exceeded")
                        break

                # Listen for messages
                async for message in self.ws:
                    try:
                        data = json.loads(message)
                        await self._process_message(data)
                    except json.JSONDecodeError:
                        continue
                    except Exception as e:
                        print(f"❌ Error processing message: {e}")
                        continue

            except Exception as e:
                print(f"❌ WebSocket error: {e}")
                self.ws = None
                reconnect_attempts += 1

                if reconnect_attempts < self.max_reconnect_attempts:
                    await asyncio.sleep(self.reconnect_delay)
                else:
                    break

    async def _process_message(self, data: Dict[str, Any]) -> None:
        """Process incoming WebSocket message"""

        channel = data.get("channel")
        if channel == "allMids":
            await self._handle_price_update(data.get("data", {}))
            return

        # activeSpotAssetCtx is delivered under that channel name but callers
        # subscribe via the generic "activeAssetCtx" type; alias them.
        lookup_type = (
            "activeAssetCtx" if channel == "activeSpotAssetCtx" else channel
        )

        payload = data.get("data", {})
        # Per-coin filtering: a callback registered with coin=X only fires for
        # messages that match that coin (or for channels with no coin scope).
        msg_coin = payload.get("coin") if isinstance(payload, dict) else None
        recipients = []
        for (sub_type, sub_coin), cbs in self._channel_callbacks.items():
            if sub_type != lookup_type:
                continue
            if sub_coin is None or msg_coin is None or sub_coin == msg_coin:
                recipients.extend(cbs)

        for cb in recipients:
            try:
                if asyncio.iscoroutinefunction(cb):
                    asyncio.create_task(cb(payload))
                else:
                    cb(payload)
            except Exception as e:
                print(f"❌ Error in channel callback for {channel}: {e}")

    async def _handle_price_update(self, price_data: Dict[str, Any]) -> None:
        """Handle price update message"""

        # Extract mids data (price_data structure: {"mids": {"BTC": "12345.67", "ETH": "3456.78", ...}})
        mids = price_data.get("mids", {})

        for asset, price_str in mids.items():
            if asset in self.subscribed_assets:
                try:
                    price = float(price_str)
                    timestamp = time.time()

                    # Create MarketData object
                    market_data = MarketData(
                        asset=asset,
                        price=price,
                        volume_24h=0.0,  # Not provided in allMids
                        timestamp=timestamp,
                    )

                    # Cache latest data
                    self.latest_data[asset] = market_data

                    # Notify callbacks
                    if asset in self.price_callbacks:
                        for callback in self.price_callbacks[asset]:
                            try:
                                # Check if callback is async
                                if asyncio.iscoroutinefunction(callback):
                                    asyncio.create_task(callback(market_data))
                                else:
                                    callback(market_data)
                            except Exception as e:
                                print(f"❌ Error in price callback: {e}")

                except (ValueError, TypeError) as e:
                    print(f"❌ Invalid price data for {asset}: {e}")

    async def _reconnect(self) -> bool:
        """Reconnect to WebSocket without creating new tasks"""
        try:
            import websockets

            ws_url = self._resolve_ws_url()

            self.ws = await websockets.connect(ws_url)

            print(
                f"✅ Connected to Hyperliquid WebSocket ({'testnet' if self.testnet else 'mainnet'})"
            )
            print(f"📡 Using WebSocket: {redact_url(ws_url)}")
            return True

        except Exception as e:
            print(f"❌ Failed to reconnect to WebSocket: {e}")
            return False

    async def _resubscribe_all(self) -> None:
        """Replay every subscription (allMids per dex + generic channels)."""

        if not (self.ws and self.running):
            return

        # Replay per-dex allMids subs.
        prior_dexes = set(self._dex_subscriptions) or (
            {""} if self.subscribed_assets else set()
        )
        self._dex_subscriptions.clear()
        for d in prior_dexes:
            subscription: Dict[str, Any] = {"type": "allMids"}
            if d:
                subscription["dex"] = d
            await self.ws.send(
                json.dumps({"method": "subscribe", "subscription": subscription})
            )
            self._dex_subscriptions.add(d)

        # Replay generic channels (bbo, activeAssetCtx, ...).
        for sub in self._channel_subscriptions:
            await self.ws.send(
                json.dumps({"method": "subscribe", "subscription": sub})
            )

        print(
            f"🔄 Re-subscribed: {len(prior_dexes)} dex(es), "
            f"{len(self._channel_subscriptions)} generic channel(s)"
        )

    def get_status(self) -> Dict[str, Any]:
        """Get market data provider status"""
        return {
            "connected": self.running and self.ws is not None,
            "subscribed_assets": list(self.subscribed_assets),
            "latest_data_count": len(self.latest_data),
        }
