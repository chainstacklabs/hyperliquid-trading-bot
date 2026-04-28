"""
Hyperliquid Exchange Adapter

Clean implementation of Hyperliquid integration using the exchange interface.
Technical implementation separated from business logic.
"""

from typing import Dict, List, Optional, Any
import time

from interfaces.exchange import (
    ExchangeAdapter,
    Order,
    OrderSide,
    OrderType,
    OrderStatus,
    Balance,
    MarketInfo,
)
from core.endpoint_router import get_endpoint_router


class HyperliquidAdapter(ExchangeAdapter):
    """
    Hyperliquid DEX adapter implementation

    Handles all Hyperliquid-specific technical details while implementing
    the clean exchange interface that strategies can use.
    """

    PERP_PX_MAX_DECIMALS = 6
    SPOT_PX_MAX_DECIMALS = 8

    def __init__(
        self,
        private_key: str,
        testnet: bool = True,
        account_address: Optional[str] = None,
    ):
        super().__init__("Hyperliquid")
        self.private_key = private_key
        self.testnet = testnet
        self.paper_trading = False

        # When the private key is an agent/API wallet, account_address is the
        # master address that holds funds. If not provided, falls back to the
        # signer's own address (i.e. signer == master).
        self.account_address = account_address

        # Hyperliquid SDK components (will be initialized on connect)
        self.info = None
        self.exchange = None

        # Endpoint router for smart routing
        self.endpoint_router = get_endpoint_router(testnet)

        # Asset metadata caches populated on connect
        self._perp_sz_decimals: Dict[str, int] = {}
        self._spot_sz_decimals: Dict[str, int] = {}

    async def connect(self) -> bool:
        """Connect to Hyperliquid with smart endpoint routing"""
        try:
            # Import here to avoid dependency issues
            from hyperliquid.info import Info
            from hyperliquid.exchange import Exchange
            from eth_account import Account

            # Get the info endpoint from router
            info_url = self.endpoint_router.get_endpoint_for_method("user_state")
            if not info_url:
                raise RuntimeError("No healthy info endpoint available")

            # Get the exchange endpoint from router
            exchange_url = self.endpoint_router.get_endpoint_for_method("cancel_order")
            if not exchange_url:
                raise RuntimeError("No healthy exchange endpoint available")

            # Remove /info and /exchange suffixes (SDK adds them automatically)
            info_base_url = (
                info_url.replace("/info", "")
                if info_url.endswith("/info")
                else info_url
            )
            exchange_base_url = (
                exchange_url.replace("/exchange", "")
                if exchange_url.endswith("/exchange")
                else exchange_url
            )

            wallet = Account.from_key(self.private_key)
            self.account_address = self.account_address or wallet.address

            self.info = Info(info_base_url, skip_ws=True)
            self.exchange = Exchange(
                wallet, exchange_base_url, account_address=self.account_address
            )

            self._load_asset_metadata()

            # Test connection against the master account
            self.info.user_state(self.account_address)

            self.is_connected = True
            print(
                f"✅ Connected to Hyperliquid ({'testnet' if self.testnet else 'mainnet'})"
            )
            print(f"📡 Info endpoint: {info_url}")
            print(f"💱 Exchange endpoint: {exchange_url}")
            print(f"🔑 Signer (agent): {wallet.address}")
            print(f"🏦 Account (master): {self.account_address}")
            return True

        except Exception as e:
            print(f"❌ Failed to connect to Hyperliquid: {e}")
            self.is_connected = False
            return False

    def _load_asset_metadata(self) -> None:
        meta = self.info.meta()
        for asset_info in meta.get("universe", []):
            name = asset_info.get("name")
            if name is not None:
                self._perp_sz_decimals[name] = int(asset_info.get("szDecimals", 0))

        try:
            spot_meta = self.info.spot_meta()
            for pair in spot_meta.get("universe", []):
                name = pair.get("name")
                base_idx = pair.get("tokens", [None])[0]
                tokens = spot_meta.get("tokens", [])
                if name is not None and base_idx is not None and base_idx < len(tokens):
                    self._spot_sz_decimals[name] = int(
                        tokens[base_idx].get("szDecimals", 0)
                    )
        except Exception:
            pass

    def _is_spot(self, asset: str) -> bool:
        return "/" in asset or asset.startswith("@")

    def _sz_decimals(self, asset: str) -> int:
        if self._is_spot(asset):
            return self._spot_sz_decimals.get(asset, 0)
        return self._perp_sz_decimals.get(asset, 0)

    def _round_price(self, asset: str, price: float) -> float:
        max_dec = (
            self.SPOT_PX_MAX_DECIMALS
            if self._is_spot(asset)
            else self.PERP_PX_MAX_DECIMALS
        )
        px_decimals = max(0, max_dec - self._sz_decimals(asset))
        sig5 = float(f"{float(price):.5g}")
        if price == int(price):
            return float(int(price))
        return round(sig5, px_decimals)

    def _round_size(self, asset: str, size: float) -> float:
        return round(float(size), self._sz_decimals(asset))

    async def disconnect(self) -> None:
        """Disconnect from Hyperliquid"""
        self.is_connected = False
        self.info = None
        self.exchange = None
        print("🔌 Disconnected from Hyperliquid")

    async def get_balance(self, asset: str) -> Balance:
        """Get account balance for an asset.

        For perp accounts ("USD"/"USDC") returns the cross-margin account value
        as available + total. For spot tokens, reads from spot_user_state.
        """
        if not self.is_connected:
            raise RuntimeError("Not connected to exchange")

        try:
            address = self.account_address

            if asset.upper() in ("USD", "USDC_PERP"):
                user_state = self.info.user_state(address)
                summary = user_state.get("crossMarginSummary", {})
                account_value = float(summary.get("accountValue", 0))
                margin_used = float(summary.get("totalMarginUsed", 0))
                available = max(0.0, account_value - margin_used)
                return Balance(
                    asset=asset,
                    available=available,
                    locked=margin_used,
                    total=account_value,
                )

            spot_state = self.info.spot_user_state(address)
            for balance_info in spot_state.get("balances", []):
                coin = balance_info.get("coin", "")
                if coin == asset:
                    total = float(balance_info.get("total", 0))
                    hold = float(balance_info.get("hold", 0))
                    available = total - hold
                    return Balance(
                        asset=asset, available=available, locked=hold, total=total
                    )

            return Balance(asset=asset, available=0.0, locked=0.0, total=0.0)

        except Exception as e:
            raise RuntimeError(f"Failed to get {asset} balance: {e}")

    async def get_market_price(self, asset: str) -> float:
        """Get current market price"""
        if not self.is_connected:
            raise RuntimeError("Not connected to exchange")

        try:
            # Get all mids (market prices)
            all_mids = self.info.all_mids()

            # Find asset price
            if asset in all_mids:
                return float(all_mids[asset])
            else:
                raise ValueError(f"Asset {asset} not found in market data")

        except Exception as e:
            raise RuntimeError(f"Failed to get {asset} price: {e}")

    async def place_order(self, order: Order) -> str:
        """Place an order on Hyperliquid"""
        if not self.is_connected:
            raise RuntimeError("Not connected to exchange")

        try:
            # Convert to Hyperliquid format
            is_buy = order.side == OrderSide.BUY

            from hyperliquid.utils.signing import OrderType as HLOrderType

            rounded_size = self._round_size(order.asset, order.size)

            if order.order_type == OrderType.MARKET:
                market_price = await self.get_market_price(order.asset)
                slippage = 1.01 if is_buy else 0.99
                adjusted_price = self._round_price(
                    order.asset, market_price * slippage
                )
                result = self.exchange.order(
                    name=order.asset,
                    is_buy=is_buy,
                    sz=rounded_size,
                    limit_px=adjusted_price,
                    order_type=HLOrderType({"limit": {"tif": "Ioc"}}),
                    reduce_only=False,
                )
            else:
                rounded_price = self._round_price(order.asset, order.price)
                result = self.exchange.order(
                    name=order.asset,
                    is_buy=is_buy,
                    sz=rounded_size,
                    limit_px=rounded_price,
                    order_type=HLOrderType({"limit": {"tif": "Gtc"}}),
                    reduce_only=False,
                )

            if result and result.get("status") == "ok":
                statuses = (
                    result.get("response", {}).get("data", {}).get("statuses", [])
                )
                if statuses:
                    status_info = statuses[0]
                    if "resting" in status_info:
                        return str(status_info["resting"]["oid"])
                    if "filled" in status_info:
                        return str(status_info["filled"]["oid"])
                    if "error" in status_info:
                        raise RuntimeError(status_info["error"])

            raise RuntimeError(f"Failed to place order: {result}")

        except Exception as e:
            raise RuntimeError(f"Failed to place {order.side.value} order: {e}")

    async def cancel_order(self, exchange_order_id: str) -> bool:
        """Cancel an order"""
        if not self.is_connected:
            raise RuntimeError("Not connected to exchange")

        try:
            # Convert to int (Hyperliquid uses integer order IDs)
            oid = int(exchange_order_id)

            # Find the asset name for this order by querying open orders
            open_orders = self.info.open_orders(self.account_address)
            target_order = None

            for order in open_orders:
                if order.get("oid") == oid:
                    target_order = order
                    break

            if not target_order:
                print(f"❌ Order {exchange_order_id} not found in open orders")
                return False

            asset_name = target_order.get("coin")
            if not asset_name:
                print(f"❌ Could not determine asset for order {exchange_order_id}")
                return False

            # Use the correct SDK method: cancel(name, oid)
            result = self.exchange.cancel(name=asset_name, oid=oid)

            # Check if cancellation was successful
            if result and isinstance(result, dict) and result.get("status") == "ok":
                response_data = result.get("response", {}).get("data", {})
                statuses = response_data.get("statuses", [])

                if statuses and statuses[0] == "success":
                    print(f"✅ Order {exchange_order_id} cancelled successfully")
                    return True
                else:
                    print(f"❌ Cancel failed with status: {statuses}")
                    return False
            else:
                print(f"❌ Cancel request failed: {result}")
                return False

        except Exception as e:
            print(f"❌ Error cancelling order {exchange_order_id}: {e}")
            return False

    async def get_order_status(self, exchange_order_id: str) -> Order:
        """Get order status (simplified implementation)"""
        if not self.is_connected:
            raise RuntimeError("Not connected to exchange")

        # This would require maintaining order state or querying open orders
        # For now, return a basic order object
        return Order(
            id=exchange_order_id,
            asset="BTC",  # Would need to track this
            side=OrderSide.BUY,  # Would need to track this
            size=0.0,  # Would need to track this
            order_type=OrderType.LIMIT,  # Would need to track this
            status=OrderStatus.SUBMITTED,  # Would need to query actual status
            exchange_order_id=exchange_order_id,
        )

    async def get_market_info(self, asset: str) -> MarketInfo:
        """Get market information"""
        if not self.is_connected:
            raise RuntimeError("Not connected to exchange")

        try:
            # Get market metadata
            meta = self.info.meta()
            universe = meta.get("universe", [])

            for asset_info in universe:
                if asset_info.get("name") == asset:
                    sz_dec = int(asset_info.get("szDecimals", 0))
                    px_dec = max(0, self.PERP_PX_MAX_DECIMALS - sz_dec)
                    return MarketInfo(
                        symbol=asset,
                        base_asset=asset,
                        quote_asset="USD",
                        min_order_size=10 ** (-sz_dec) if sz_dec > 0 else 1.0,
                        price_precision=px_dec,
                        size_precision=sz_dec,
                        is_active=True,
                    )

            raise ValueError(f"Asset {asset} not found")

        except Exception as e:
            raise RuntimeError(f"Failed to get market info for {asset}: {e}")

    async def get_open_orders(self) -> List[Order]:
        """Get all open orders"""
        if not self.is_connected:
            return []

        try:
            open_orders = self.info.open_orders(self.account_address)
            orders = []

            for order_info in open_orders:
                order = Order(
                    id=str(order_info.get("oid", "")),
                    asset=order_info.get("coin", ""),
                    side=OrderSide.BUY
                    if order_info.get("side") == "B"
                    else OrderSide.SELL,
                    size=float(order_info.get("sz", 0)),
                    order_type=OrderType.LIMIT,  # Hyperliquid default
                    price=float(order_info.get("limitPx", 0)),
                    status=OrderStatus.SUBMITTED,
                    exchange_order_id=str(order_info.get("oid", "")),
                )
                orders.append(order)

            return orders

        except Exception as e:
            print(f"❌ Error getting open orders: {e}")
            return []

    async def health_check(self) -> bool:
        """Check connection health"""
        if not self.is_connected:
            return False

        try:
            # Simple health check - get account state
            self.info.user_state(self.account_address)
            return True
        except Exception:
            return False

    async def get_positions(self) -> List["Position"]:
        """Get all current positions from Hyperliquid"""
        if not self.is_connected:
            return []

        try:
            # Import Position here to avoid circular imports
            from interfaces.strategy import Position

            # Get user state which includes positions
            user_state = self.info.user_state(self.account_address)
            positions = []

            for pos_info in user_state.get("assetPositions", []):
                pos = pos_info.get("position", {})
                position_size = float(pos.get("szi", 0))
                if position_size == 0:
                    continue

                coin = pos.get("coin", "")
                entry_price = float(pos.get("entryPx") or 0)
                current_price = await self.get_market_price(coin)
                current_value = abs(position_size) * current_price
                unrealized_pnl = float(pos.get("unrealizedPnl", 0))

                positions.append(
                    Position(
                        asset=coin,
                        size=position_size,
                        entry_price=entry_price,
                        current_value=current_value,
                        unrealized_pnl=unrealized_pnl,
                        timestamp=time.time(),
                    )
                )

            return positions

        except Exception as e:
            print(f"❌ Error getting positions: {e}")
            return []

    async def close_position(self, asset: str, size: Optional[float] = None) -> bool:
        """Close a position by placing a market order"""
        if not self.is_connected:
            return False

        try:
            # Get current positions to determine position details
            positions = await self.get_positions()
            target_position = None

            for pos in positions:
                if pos.asset == asset:
                    target_position = pos
                    break

            if not target_position:
                print(f"❌ No position found for {asset}")
                return False

            if size is None:
                close_size = abs(target_position.size)
            else:
                close_size = min(size, abs(target_position.size))

            close_size = self._round_size(asset, close_size)

            result = self.exchange.market_close(coin=asset, sz=close_size)

            if result and result.get("status") == "ok":
                print(f"✅ Position close order placed: {close_size} {asset}")
                return True
            else:
                print(f"❌ Failed to close position: {result}")
                return False

        except Exception as e:
            print(f"❌ Error closing position {asset}: {e}")
            return False

    async def get_account_metrics(self) -> Dict[str, Any]:
        """Get account-level metrics for risk assessment"""
        if not self.is_connected:
            return {
                "total_value": 0.0,
                "total_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "realized_pnl": 0.0,
                "drawdown_pct": 0.0,
            }

        try:
            # Get user state
            user_state = self.info.user_state(self.account_address)

            total_value = 0.0
            margin_used = 0.0
            if "crossMarginSummary" in user_state:
                margin_summary = user_state["crossMarginSummary"]
                total_value = float(margin_summary.get("accountValue", 0))
                margin_used = float(margin_summary.get("totalMarginUsed", 0))

            unrealized_pnl = sum(
                float(p.get("position", {}).get("unrealizedPnl", 0))
                for p in user_state.get("assetPositions", [])
            )

            positions = await self.get_positions()
            total_pnl = unrealized_pnl

            # Estimate drawdown percentage (this would be more sophisticated in production)
            if total_value > 0:
                drawdown_pct = (
                    max(0, -total_pnl / total_value * 100) if total_pnl < 0 else 0.0
                )
            else:
                drawdown_pct = 0.0

            return {
                "total_value": total_value,
                "total_pnl": total_pnl,
                "unrealized_pnl": unrealized_pnl,
                "realized_pnl": 0.0,  # Would need to track this separately
                "drawdown_pct": drawdown_pct,
                "positions_count": len(positions),
                "largest_position_pct": max(
                    [abs(pos.current_value) / total_value * 100 for pos in positions],
                    default=0.0,
                )
                if total_value > 0
                else 0.0,
            }

        except Exception as e:
            print(f"❌ Error getting account metrics: {e}")
            return {
                "total_value": 0.0,
                "total_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "realized_pnl": 0.0,
                "drawdown_pct": 0.0,
            }
