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
    MIN_NOTIONAL_USD = 10.0
    MAX_PRIORITY_FEE_BPS = 8
    SUPPORTED_GROUPINGS = frozenset({"na", "normalTpsl", "positionTpsl"})

    def __init__(
        self,
        private_key: str,
        testnet: bool = True,
        account_address: Optional[str] = None,
        dex: Optional[str] = None,
        expires_after_ms: Optional[int] = None,
        expires_after_ttl_ms: Optional[int] = None,
        default_priority_fee_bps: Optional[int] = None,
    ):
        super().__init__("Hyperliquid")
        self.private_key = private_key
        self.testnet = testnet
        self.paper_trading = False

        # When the private key is an agent/API wallet, account_address is the
        # master address that holds funds. If not provided, falls back to the
        # signer's own address (i.e. signer == master).
        self.account_address = account_address

        # HIP-3 builder-deployed perp dex name; None = main perp dex (default).
        # Per-call dex overrides are also accepted on get_balance/positions etc.
        self.dex = dex

        # SDK 0.12.0+ expires_after for signed L1 actions. Two modes (TTL has
        # precedence when both are set):
        #   expires_after_ttl_ms: deadline = connect_time + ttl, recomputed
        #     every connect() so long-running adapters stay valid.
        #   expires_after_ms: pinned absolute epoch-ms.
        # None on both disables the feature.
        self.expires_after_ms = expires_after_ms
        self.expires_after_ttl_ms = expires_after_ttl_ms

        # SDK 0.23.0+ default priority fee in bps applied to orders that don't
        # set their own. 0 == None (disabled). Range 1..MAX_PRIORITY_FEE_BPS.
        self.default_priority_fee_bps = (
            default_priority_fee_bps
            if default_priority_fee_bps
            else None
        )

        # Hyperliquid SDK components (will be initialized on connect)
        self.info = None
        self.exchange = None

        # Endpoint router for smart routing
        self.endpoint_router = get_endpoint_router(testnet)

        # Asset metadata caches populated on connect.
        # Perp cache is keyed by (dex_name_or_empty, asset).
        self._perp_sz_decimals: Dict[tuple, int] = {}
        self._spot_sz_decimals: Dict[str, int] = {}
        self._known_dexes: List[str] = [""]  # "" = main

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
            self._check_agent_approval(wallet.address)

            deadline = self._resolve_expires_after_deadline()
            if deadline is not None:
                self.exchange.set_expires_after(deadline)

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
        """Discover dexes and eagerly load main + configured dex metadata.

        209+ HIP-3 dexes makes a fan-out load at connect prohibitively slow.
        We eagerly load only what's needed (main perp + the adapter's
        configured `dex`); other dexes are lazy-loaded by `_ensure_dex_meta`
        on first asset access.
        """
        # Discover available HIP-3 dexes; main perp is "" by SDK convention.
        try:
            dexes = self.info.perp_dexs() or []
            self._known_dexes = [""] + [
                d.get("name")
                for d in dexes
                if isinstance(d, dict) and d.get("name")
            ]
        except Exception as e:
            print(f"⚠️ Failed to discover perp dexes (HIP-3 disabled): {e}")
            self._known_dexes = [""]

        eager = {""} | ({self.dex} if self.dex else set())
        for dex_name in eager:
            self._ensure_dex_meta(dex_name)

        try:
            spot_meta = self.info.spot_meta()
            tokens = spot_meta.get("tokens", [])
            for pair in spot_meta.get("universe", []):
                name = pair.get("name")
                pair_tokens = pair.get("tokens") or []
                if not name or not pair_tokens:
                    continue
                base_idx = pair_tokens[0]
                if 0 <= base_idx < len(tokens):
                    self._spot_sz_decimals[name] = int(
                        tokens[base_idx].get("szDecimals", 0)
                    )
        except Exception as e:
            print(f"⚠️ Failed to load spot metadata: {e}")

    def _check_agent_approval(self, signer_address: str) -> None:
        """Warn if signer is an agent that isn't approved for the master account.

        When signer == account_address the wallet is acting as its own master and
        no approval is needed. Otherwise the signer must appear in the master's
        extraAgents list, else order placements will fail with opaque signing
        errors at runtime.
        """
        if signer_address.lower() == self.account_address.lower():
            return
        try:
            agents = self.info.post(
                "/info", {"type": "extraAgents", "user": self.account_address}
            )
            approved = {a.get("address", "").lower() for a in agents or []}
            if signer_address.lower() not in approved:
                print(
                    f"⚠️ Agent {signer_address} is not approved on master "
                    f"{self.account_address}. Orders will be rejected. "
                    f"Approve via https://app.hyperliquid"
                    f"{'-testnet' if self.testnet else ''}.xyz/API"
                )
        except Exception as e:
            print(f"⚠️ Could not verify agent approval: {e}")

    def _ensure_dex_meta(self, dex_name: str) -> None:
        """Lazily load perp meta for `dex_name` if not already cached."""
        if any(k[0] == dex_name for k in self._perp_sz_decimals):
            return
        try:
            meta = self.info.meta(dex=dex_name)
            for asset_info in meta.get("universe", []):
                name = asset_info.get("name")
                if name is not None:
                    self._perp_sz_decimals[(dex_name, name)] = int(
                        asset_info.get("szDecimals", 0)
                    )
        except Exception as e:
            print(f"⚠️ Failed to load perp metadata for dex={dex_name!r}: {e}")

    def _is_spot(self, asset: str) -> bool:
        return "/" in asset or asset.startswith("@")

    def _dex_arg(self, dex: Optional[str]) -> str:
        """Resolve to the SDK's dex string ('' = main perp).

        If dex is unspecified, fall back to the adapter's configured dex.
        """
        return (dex if dex is not None else self.dex) or ""

    def _infer_dex_from_asset(self, asset: str, dex: Optional[str] = None) -> str:
        """HIP-3 asset names are namespaced as '<dex>:<symbol>'. If `dex` is
        unspecified and the asset has a colon prefix, take the prefix; else
        fall back to the adapter's default dex.
        """
        if dex is not None:
            return dex
        if ":" in asset and not asset.startswith("@"):
            return asset.split(":", 1)[0]
        return self._dex_arg(None)

    def _sz_decimals(self, asset: str, dex: Optional[str] = None) -> int:
        if self._is_spot(asset):
            if asset not in self._spot_sz_decimals:
                raise RuntimeError(
                    f"Missing spot precision metadata for {asset}"
                )
            return self._spot_sz_decimals[asset]

        resolved = self._infer_dex_from_asset(asset, dex)
        key = (resolved, asset)
        if key not in self._perp_sz_decimals:
            self._ensure_dex_meta(resolved)
        if key not in self._perp_sz_decimals:
            raise RuntimeError(
                f"Missing perp precision metadata for {asset} on dex={resolved!r}"
            )
        return self._perp_sz_decimals[key]

    def _round_price(
        self, asset: str, price: float, dex: Optional[str] = None
    ) -> float:
        max_dec = (
            self.SPOT_PX_MAX_DECIMALS
            if self._is_spot(asset)
            else self.PERP_PX_MAX_DECIMALS
        )
        px_decimals = max(0, max_dec - self._sz_decimals(asset, dex))
        # Hyperliquid: integer prices are exempt from the 5-sig-fig rule
        # (https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/tick-and-lot-size)
        if price == int(price):
            return float(int(price))
        sig5 = float(f"{float(price):.5g}")
        return round(sig5, px_decimals)

    def _round_size(
        self, asset: str, size: float, dex: Optional[str] = None
    ) -> float:
        sz_dec = self._sz_decimals(asset, dex)
        rounded = round(float(size), sz_dec)
        if float(size) > 0 and rounded <= 0:
            raise RuntimeError(
                f"Size {size} for {asset} rounds to 0 at szDecimals={sz_dec}; below minimum increment"
            )
        return rounded

    def list_perp_dexes(self) -> List[str]:
        """Return known perp dex names ('' = main perp dex)."""
        return list(self._known_dexes)

    def _resolve_expires_after_deadline(self) -> Optional[int]:
        """Compute the epoch-ms deadline to set on Exchange.set_expires_after.

        TTL takes precedence and is computed fresh on each connect() so a
        process running for days doesn't drift past a stale construction-time
        deadline. Absolute mode is pinned and the caller's responsibility.
        """
        if self.expires_after_ttl_ms is not None:
            return int(time.time() * 1000) + int(self.expires_after_ttl_ms)
        return self.expires_after_ms

    def _resolve_grouping(self, order: Order):
        """Resolve order grouping for SDK bulk_orders.

        Returns one of "na", "normalTpsl", "positionTpsl", or a
        PriorityGrouping dict {"p": bps} when a priority fee is requested.
        Order-level priority_fee_bps takes precedence over the adapter's
        default. Order grouping and priority fee are mutually exclusive.

        Note on priority_fee_bps=0: treated as "no priority fee" for caller
        ergonomics (use None or omit to disable). Out-of-range values
        (negative or > MAX_PRIORITY_FEE_BPS) raise.
        """
        bps = order.priority_fee_bps
        if bps is None:
            bps = self.default_priority_fee_bps

        if order.grouping is not None and order.grouping not in self.SUPPORTED_GROUPINGS:
            raise RuntimeError(
                f"Unsupported grouping {order.grouping!r}; expected one of "
                f"{sorted(self.SUPPORTED_GROUPINGS)}"
            )

        if order.grouping and order.grouping != "na":
            if bps:
                raise RuntimeError(
                    "Order.grouping and priority_fee_bps cannot be combined"
                )
            return order.grouping

        if bps is not None and bps < 0:
            raise RuntimeError(f"priority_fee_bps={bps} cannot be negative")
        if bps is not None and bps > self.MAX_PRIORITY_FEE_BPS:
            raise RuntimeError(
                f"priority_fee_bps={bps} exceeds MAX_PRIORITY_FEE_BPS="
                f"{self.MAX_PRIORITY_FEE_BPS}"
            )

        if bps:
            return {"p": int(bps)}

        return "na"

    TPSL_LIMIT_SLIPPAGE = 0.05  # 5%, mirrors market_close default.

    async def register_position_tpsl(
        self,
        asset: str,
        position_size: float,
        tp_price: Optional[float] = None,
        sl_price: Optional[float] = None,
        dex: Optional[str] = None,
        slippage: float = TPSL_LIMIT_SLIPPAGE,
    ) -> bool:
        """Register paired TP/SL trigger orders against a live position.

        Uses SDK 0.21+ `bulk_orders(grouping="positionTpsl")` so the matching
        engine fires either order without a polling race. `position_size` is
        signed (long > 0, short < 0); the trigger orders are sized to fully
        close that position. Pass `tp_price` / `sl_price` in absolute terms;
        pass `None` to skip a leg.

        `slippage` biases the order's `limit_px` away from `triggerPx` by that
        fraction so a single-tick gap past the trigger doesn't leave the
        market order resting. The reducing side determines direction:
        sells (long-TP, long-SL) get a *lower* cap; buys (short-TP, short-SL)
        get a *higher* cap. Default 5% mirrors `market_close`.

        Returns True on placement success.
        """
        if not self.is_connected:
            raise RuntimeError("Not connected to exchange")
        if position_size == 0:
            raise RuntimeError("position_size must be non-zero")
        if tp_price is None and sl_price is None:
            raise RuntimeError("at least one of tp_price/sl_price must be set")

        is_long = position_size > 0
        size = abs(position_size)
        rounded_sz = self._round_size(asset, size, dex=dex)

        # Reducing side is the opposite of position direction.
        reducing_is_buy = not is_long

        requests = []
        if tp_price is not None:
            requests.append(
                self._build_trigger_request(
                    asset, reducing_is_buy, rounded_sz, tp_price, "tp", slippage, dex
                )
            )
        if sl_price is not None:
            requests.append(
                self._build_trigger_request(
                    asset, reducing_is_buy, rounded_sz, sl_price, "sl", slippage, dex
                )
            )

        result = self.exchange.bulk_orders(requests, grouping="positionTpsl")
        if not (result and result.get("status") == "ok"):
            raise RuntimeError(f"register_position_tpsl failed: {result}")
        return True

    def _build_trigger_request(
        self,
        asset: str,
        is_buy: bool,
        sz: float,
        trigger_price: float,
        tpsl: str,
        slippage: float,
        dex: Optional[str],
    ) -> Dict[str, Any]:
        """Build a single reduce-only trigger order request for bulk_orders."""
        from hyperliquid.utils.signing import OrderType as HLOrderType

        trigger_px = self._round_price(asset, trigger_price, dex=dex)
        # The limit cap is offset away from the trigger so the IOC market can
        # fill across a tick of slippage. A reducing buy needs a higher cap;
        # a reducing sell needs a lower cap.
        bias = (1 + slippage) if is_buy else (1 - slippage)
        limit_px = self._round_price(asset, trigger_price * bias, dex=dex)
        return {
            "coin": asset,
            "is_buy": is_buy,
            "sz": sz,
            "limit_px": limit_px,
            "order_type": HLOrderType(
                {"trigger": {"triggerPx": trigger_px, "isMarket": True, "tpsl": tpsl}}
            ),
            "reduce_only": True,
        }

    async def disconnect(self) -> None:
        """Disconnect from Hyperliquid"""
        self.is_connected = False
        self.info = None
        self.exchange = None
        print("🔌 Disconnected from Hyperliquid")

    PERP_QUOTE_ALIASES = {"USD", "USDC", "USDC_PERP"}

    async def get_balance(
        self, asset: str, dex: Optional[str] = None
    ) -> Balance:
        """Get account balance for an asset.

        Asset names in PERP_QUOTE_ALIASES (USD / USDC / USDC_PERP) return the
        cross-margin account value for the selected `dex` (defaults to the
        adapter's configured dex; "" / None == main perp dex). Any other
        name reads spot_user_state and ignores `dex`.
        """
        if not self.is_connected:
            raise RuntimeError("Not connected to exchange")

        try:
            address = self.account_address

            if asset.upper() in self.PERP_QUOTE_ALIASES:
                user_state = self.info.user_state(address, dex=self._dex_arg(dex))
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

    async def get_market_price(
        self, asset: str, dex: Optional[str] = None
    ) -> float:
        """Get current market price for the asset on the selected dex.

        If `dex` is None, infer from a HIP-3 namespaced asset name
        (e.g. "felix:CRCL" -> dex="felix").
        """
        if not self.is_connected:
            raise RuntimeError("Not connected to exchange")

        try:
            resolved_dex = self._infer_dex_from_asset(asset, dex)
            all_mids = self.info.all_mids(dex=resolved_dex)

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

            order_dex = order.dex
            rounded_size = self._round_size(order.asset, order.size, dex=order_dex)

            # Resolve grouping for SDK bulk_orders (SDK 0.21+).
            grouping_arg = self._resolve_grouping(order)

            if order.order_type == OrderType.MARKET:
                # SDK market_open handles slippage and the IOC limit derivation;
                # avoids hand-rolled ±1% IOC. SDK 0.20.1 fixed HIP-3 markets.
                if grouping_arg != "na":
                    raise RuntimeError(
                        "grouping/priority_fee_bps not supported with MARKET orders"
                    )
                if order.reduce_only:
                    # market_open doesn't expose reduce_only; route through
                    # market_close (reduce-only by construction). Guard against
                    # a side that wouldn't actually reduce the position —
                    # market_close ignores side, so a buggy caller could
                    # otherwise close the live position even when the request
                    # was meant to add to it.
                    resolved_dex = self._infer_dex_from_asset(
                        order.asset, order_dex
                    )
                    current = next(
                        (
                            p
                            for p in await self.get_positions(dex=resolved_dex)
                            if p.asset == order.asset and p.size != 0
                        ),
                        None,
                    )
                    if current is None:
                        raise RuntimeError(
                            f"reduce_only MARKET on {order.asset}: no open position"
                        )
                    expected = OrderSide.SELL if current.size > 0 else OrderSide.BUY
                    if order.side != expected:
                        raise RuntimeError(
                            f"reduce_only MARKET side {order.side.value} does "
                            f"not reduce a position of size {current.size}"
                        )
                    result = self.exchange.market_close(
                        coin=order.asset, sz=rounded_size, slippage=0.05
                    )
                else:
                    result = self.exchange.market_open(
                        name=order.asset,
                        is_buy=is_buy,
                        sz=rounded_size,
                        slippage=0.05,
                    )
            else:
                rounded_price = self._round_price(
                    order.asset, order.price, dex=order_dex
                )
                # Priority-fee orders must be IOC (server-enforced).
                tif = "Ioc" if isinstance(grouping_arg, dict) else "Gtc"
                limit_order_type: HLOrderType = {"limit": {"tif": tif}}
                if grouping_arg != "na":
                    # SDK 0.21+: order grouping (positionTpsl, normalTpsl) and
                    # 0.23+ priority fees both require bulk_orders.
                    request = {
                        "coin": order.asset,
                        "is_buy": is_buy,
                        "sz": rounded_size,
                        "limit_px": rounded_price,
                        "order_type": limit_order_type,
                        "reduce_only": order.reduce_only,
                    }
                    result = self.exchange.bulk_orders(
                        [request], grouping=grouping_arg
                    )
                else:
                    result = self.exchange.order(
                        name=order.asset,
                        is_buy=is_buy,
                        sz=rounded_size,
                        limit_px=rounded_price,
                        order_type=limit_order_type,
                        reduce_only=order.reduce_only,
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

    async def cancel_order(
        self, exchange_order_id: str, dex: Optional[str] = None
    ) -> bool:
        """Cancel an order. Searches `dex` (defaults to adapter dex) for the oid."""
        if not self.is_connected:
            raise RuntimeError("Not connected to exchange")

        try:
            # Convert to int (Hyperliquid uses integer order IDs)
            oid = int(exchange_order_id)

            # Find the asset name for this order by querying open orders
            open_orders = self.info.open_orders(
                self.account_address, dex=self._dex_arg(dex)
            )
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

    async def get_market_info(
        self, asset: str, dex: Optional[str] = None
    ) -> MarketInfo:
        """Get market information for the asset on the selected dex."""
        if not self.is_connected:
            raise RuntimeError("Not connected to exchange")

        try:
            resolved_dex = self._infer_dex_from_asset(asset, dex)
            meta = self.info.meta(dex=resolved_dex)
            universe = meta.get("universe", [])

            for asset_info in universe:
                if asset_info.get("name") == asset:
                    sz_dec = int(asset_info.get("szDecimals", 0))
                    px_dec = max(0, self.PERP_PX_MAX_DECIMALS - sz_dec)
                    size_step = 10 ** (-sz_dec) if sz_dec > 0 else 1.0
                    try:
                        mark = await self.get_market_price(asset, dex=resolved_dex)
                        notional_min_size = (
                            self.MIN_NOTIONAL_USD / mark if mark > 0 else size_step
                        )
                    except Exception:
                        notional_min_size = size_step
                    return MarketInfo(
                        symbol=asset,
                        base_asset=asset,
                        quote_asset="USD",
                        min_order_size=max(size_step, notional_min_size),
                        price_precision=px_dec,
                        size_precision=sz_dec,
                        is_active=True,
                        min_notional=self.MIN_NOTIONAL_USD,
                        dex=resolved_dex or None,
                    )

            raise ValueError(f"Asset {asset} not found on dex={resolved_dex!r}")

        except Exception as e:
            raise RuntimeError(f"Failed to get market info for {asset}: {e}")

    async def get_open_orders(
        self, dex: Optional[str] = None, all_dexes: bool = False
    ) -> List[Order]:
        """Get open orders.

        By default returns orders for the adapter's configured dex (or main).
        `all_dexes=True` aggregates across every known HIP-3 dex; with 200+
        dexes on mainnet this issues one Info request per dex serially. Use
        sparingly — prefer narrowing via `dex=` for hot paths.
        """
        if not self.is_connected:
            return []

        try:
            dex_list = self._known_dexes if all_dexes else [self._dex_arg(dex)]
            orders: List[Order] = []
            for d in dex_list:
                raw = self.info.open_orders(self.account_address, dex=d)
                for order_info in raw or []:
                    asset_name = order_info.get("coin", "")
                    orders.append(
                        Order(
                            id=str(order_info.get("oid", "")),
                            asset=asset_name,
                            side=OrderSide.BUY
                            if order_info.get("side") == "B"
                            else OrderSide.SELL,
                            size=float(order_info.get("sz", 0)),
                            order_type=OrderType.LIMIT,
                            price=float(order_info.get("limitPx", 0)),
                            status=OrderStatus.SUBMITTED,
                            exchange_order_id=str(order_info.get("oid", "")),
                            dex=d or None,
                        )
                    )

            return orders

        except Exception as e:
            print(f"❌ Error getting open orders: {e}")
            return []

    async def health_check(self) -> bool:
        """Check connection health"""
        if not self.is_connected:
            return False

        try:
            self.info.user_state(self.account_address, dex=self._dex_arg(None))
            return True
        except Exception:
            return False

    async def get_positions(
        self, dex: Optional[str] = None, all_dexes: bool = False
    ) -> List["Position"]:
        """Get current positions on the selected dex (or aggregated).

        `all_dexes=True` issues one user_state request per known dex serially;
        cost grows with the number of HIP-3 dexes. Prefer `dex=` for hot paths.
        """
        if not self.is_connected:
            return []

        try:
            from interfaces.strategy import Position

            dex_list = self._known_dexes if all_dexes else [self._dex_arg(dex)]
            positions: List[Position] = []
            for d in dex_list:
                user_state = self.info.user_state(self.account_address, dex=d)
                # Fetch mids once per dex and reuse for current_value calc.
                mids = self.info.all_mids(dex=d) if user_state.get("assetPositions") else {}
                for pos_info in user_state.get("assetPositions", []):
                    pos = pos_info.get("position", {})
                    position_size = float(pos.get("szi", 0))
                    if position_size == 0:
                        continue

                    coin = pos.get("coin", "")
                    entry_price = float(pos.get("entryPx") or 0)
                    current_price = float(mids.get(coin, 0))
                    current_value = abs(position_size) * current_price
                    unrealized_pnl = float(pos.get("unrealizedPnl", 0))
                    margin_used = float(pos.get("marginUsed", 0))
                    return_on_equity = float(pos.get("returnOnEquity", 0))

                    positions.append(
                        Position(
                            asset=coin,
                            size=position_size,
                            entry_price=entry_price,
                            current_value=current_value,
                            unrealized_pnl=unrealized_pnl,
                            timestamp=time.time(),
                            dex=d or None,
                            margin_used=margin_used,
                            return_on_equity=return_on_equity,
                        )
                    )

            return positions

        except Exception as e:
            print(f"❌ Error getting positions: {e}")
            return []

    async def close_position(
        self,
        asset: str,
        size: Optional[float] = None,
        dex: Optional[str] = None,
    ) -> bool:
        """Close a position via market_close.

        `dex` defaults to the dex inferred from the asset name (HIP-3 prefix)
        or the adapter's configured dex.
        """
        if not self.is_connected:
            return False

        try:
            resolved_dex = self._infer_dex_from_asset(asset, dex)
            positions = await self.get_positions(dex=resolved_dex)
            target_position = None

            for pos in positions:
                if pos.asset == asset:
                    target_position = pos
                    break

            if not target_position:
                print(f"❌ No position found for {asset} on dex={resolved_dex!r}")
                return False

            if size is None:
                close_size = abs(target_position.size)
            else:
                close_size = min(size, abs(target_position.size))

            close_size = self._round_size(asset, close_size, dex=resolved_dex)

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

    async def get_account_metrics(
        self, dex: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get account-level metrics for risk assessment.

        `dex` selects which perp dex's clearinghouse state to read; defaults
        to the adapter's configured dex.
        """
        if not self.is_connected:
            return {
                "total_value": 0.0,
                "total_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "realized_pnl": 0.0,
                "drawdown_pct": 0.0,
            }

        try:
            resolved_dex = self._dex_arg(dex)
            user_state = self.info.user_state(self.account_address, dex=resolved_dex)

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

            positions = await self.get_positions(dex=resolved_dex)
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
