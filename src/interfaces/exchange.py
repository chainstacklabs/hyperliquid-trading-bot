"""
Exchange Interface

Simple interface for integrating new exchanges/DEXes.
Newbies can add new exchanges by implementing this interface.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum


class OrderSide(Enum):
    """Order side"""

    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    """Order type"""

    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(Enum):
    """Order status"""

    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Order:
    """Order representation.

    `dex` selects a HIP-3 builder-deployed perp dex when not None; default
    None means the main perp dex (or spot, depending on `is_spot`).
    """

    id: str
    asset: str
    side: OrderSide
    size: float
    order_type: OrderType
    price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_size: float = 0.0
    average_fill_price: float = 0.0
    exchange_order_id: Optional[str] = None
    created_at: float = 0.0  # Timestamp when order was created
    dex: Optional[str] = None
    # SDK 0.21.0: order grouping for OCO / TPSL pairs. None == "na".
    # Valid: "normalTpsl" (linked TP/SL), "positionTpsl" (position-bound TP/SL).
    grouping: Optional[str] = None
    # SDK 0.23.0: priority fee in bps (0-8 currently, capped server-side).
    # Routes through bulk_orders with PriorityGrouping when set.
    priority_fee_bps: Optional[int] = None
    # SDK 0.21.0 reduce-only flag, for closing a position with a fresh order.
    reduce_only: bool = False


@dataclass
class Balance:
    """Account balance"""

    asset: str
    available: float
    locked: float
    total: float


@dataclass
class MarketInfo:
    """Market/trading pair information"""

    symbol: str
    base_asset: str
    quote_asset: str
    min_order_size: float
    price_precision: int
    size_precision: int
    is_active: bool
    min_notional: float = 10.0
    dex: Optional[str] = None


class ExchangeAdapter(ABC):
    """
    Base interface for all exchange integrations.

    This is the ONLY class newbies need to understand to add new exchanges.

    Example implementation:

    class MyDEXAdapter(ExchangeAdapter):
        def __init__(self, api_key, secret):
            super().__init__("MyDEX")
            self.api_key = api_key
            self.secret = secret

        async def get_balance(self, asset):
            # Call your DEX API
            return Balance(asset, available=1000, locked=0, total=1000)
    """

    def __init__(self, exchange_name: str):
        self.exchange_name = exchange_name
        self.is_connected = False

    @abstractmethod
    async def connect(self) -> bool:
        """
        Connect to the exchange.

        Returns:
            True if connection successful, False otherwise
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the exchange."""
        pass

    @abstractmethod
    async def get_balance(self, asset: str) -> Balance:
        """
        Get account balance for an asset.

        Args:
            asset: Asset symbol (e.g., "BTC", "ETH")

        Returns:
            Balance information
        """
        pass

    @abstractmethod
    async def get_market_price(self, asset: str) -> float:
        """
        Get current market price for an asset.

        Args:
            asset: Asset symbol

        Returns:
            Current market price
        """
        pass

    @abstractmethod
    async def place_order(self, order: Order) -> str:
        """
        Place an order on the exchange.

        Args:
            order: Order to place

        Returns:
            Exchange order ID
        """
        pass

    @abstractmethod
    async def cancel_order(self, exchange_order_id: str) -> bool:
        """
        Cancel an order.

        Args:
            exchange_order_id: Exchange's order ID

        Returns:
            True if cancelled successfully
        """
        pass

    @abstractmethod
    async def get_order_status(self, exchange_order_id: str) -> Order:
        """
        Get order status.

        Args:
            exchange_order_id: Exchange's order ID

        Returns:
            Updated order information
        """
        pass

    @abstractmethod
    async def get_market_info(self, asset: str) -> MarketInfo:
        """
        Get market information for an asset.

        Args:
            asset: Asset symbol

        Returns:
            Market information
        """
        pass

    # Position management methods (optional - implement if exchange supports positions)

    async def get_positions(self) -> List["Position"]:
        """
        Get all current positions.

        Returns:
            List of current positions (empty if not supported)
        """
        return []

    async def close_position(self, asset: str, size: Optional[float] = None) -> bool:
        """
        Close a position (market sell/buy to close).

        Args:
            asset: Asset symbol
            size: Size to close (None = close entire position)

        Returns:
            True if close order placed successfully
        """
        return False

    async def get_account_metrics(self) -> Dict[str, Any]:
        """
        Get account-level metrics for risk assessment.

        Returns:
            Dictionary with account metrics (total_value, pnl, drawdown, etc.)
        """
        return {
            "total_value": 0.0,
            "total_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "realized_pnl": 0.0,
            "drawdown_pct": 0.0,
        }

    # Optional methods with default implementations

    async def get_open_orders(self) -> List[Order]:
        """Get all open orders. Override if exchange supports this."""
        return []

    async def cancel_all_orders(self) -> int:
        """Cancel all open orders. Override if exchange supports this."""
        orders = await self.get_open_orders()
        cancelled = 0
        for order in orders:
            if order.exchange_order_id:
                if await self.cancel_order(order.exchange_order_id):
                    cancelled += 1
        return cancelled

    def get_status(self) -> Dict[str, Any]:
        """Get exchange adapter status."""
        return {"exchange": self.exchange_name, "connected": self.is_connected}

    async def health_check(self) -> bool:
        """Perform health check. Override for exchange-specific checks."""
        return self.is_connected
