"""
Basic Grid Trading Strategy

Simple grid strategy that places buy and sell orders at regular intervals.
This is the main business logic for grid trading.
"""

import logging
import time
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from enum import Enum

MIN_NOTIONAL_USD = 10.5  # Hyperliquid $10 floor + 5% buffer for size-rounding

logger = logging.getLogger(__name__)

from interfaces.strategy import (
    TradingStrategy,
    TradingSignal,
    SignalType,
    MarketData,
    Position,
)


class GridState(Enum):
    """Grid states"""

    INITIALIZING = "initializing"
    ACTIVE = "active"
    REBALANCING = "rebalancing"
    STOPPED = "stopped"


@dataclass
class GridLevel:
    """Individual grid level"""

    price: float
    size: float
    level_index: int
    is_buy_level: bool  # True for buy levels, False for sell levels
    is_filled: bool = False


@dataclass
class GridConfig:
    """Grid configuration"""

    symbol: str
    levels: int = 10
    range_pct: float = 10.0  # ±10% from center price
    total_allocation: float = 1000.0  # USD

    # Price range (auto-calculated if not set)
    min_price: Optional[float] = None
    max_price: Optional[float] = None

    # Rebalancing
    rebalance_threshold_pct: float = 15.0  # Rebalance if price moves 15% outside range

    # HIP-3: optional builder-deployed perp dex (None = main perp).
    # Auto-inferred from "<dex>:<symbol>" prefix when unset.
    dex: Optional[str] = None


class BasicGridStrategy(TradingStrategy):
    """
    Basic Grid Trading Strategy

    Places buy and sell orders at regular price intervals:
    - Buy orders below current price
    - Sell orders above current price
    - Rebalances when price moves outside range

    Perfect for sideways/ranging markets.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__("basic_grid", config)

        # Extract grid config
        symbol = config.get("symbol", "BTC")
        # Auto-infer dex from HIP-3 namespaced symbol if not given explicitly.
        inferred_dex = (
            symbol.split(":", 1)[0]
            if ":" in symbol and not symbol.startswith("@")
            else None
        )
        self.grid_config = GridConfig(
            symbol=symbol,
            levels=config.get("levels", 10),
            range_pct=config.get("range_pct", 10.0),
            total_allocation=config.get("total_allocation", 1000.0),
            min_price=config.get("min_price"),
            max_price=config.get("max_price"),
            rebalance_threshold_pct=config.get("rebalance_threshold_pct", 15.0),
            dex=config.get("dex", inferred_dex),
        )

        # Grid state
        self.state = GridState.INITIALIZING
        self.center_price: Optional[float] = None
        self.grid_levels: List[GridLevel] = []
        self.last_rebalance_time = 0.0

        # Performance tracking
        self.total_trades = 0
        self.total_profit = 0.0

    def generate_signals(
        self, market_data: MarketData, positions: List[Position], balance: float
    ) -> List[TradingSignal]:
        """Generate grid trading signals"""

        if not self.is_active:
            return []

        signals = []
        current_price = market_data.price

        # Initialize grid on first run
        if self.state == GridState.INITIALIZING:
            signals.extend(self._initialize_grid(current_price, balance))

        # Check if rebalancing is needed
        elif self.state == GridState.ACTIVE and self._should_rebalance(current_price):
            signals.extend(self._rebalance_grid(current_price, balance))

        return signals

    def _initialize_grid(
        self, current_price: float, balance: float
    ) -> List[TradingSignal]:
        """Initialize the grid around current price"""

        self.center_price = current_price

        # Calculate price range
        if self.grid_config.min_price is None or self.grid_config.max_price is None:
            range_size = current_price * (self.grid_config.range_pct / 100)
            min_price = current_price - range_size
            max_price = current_price + range_size
        else:
            min_price = self.grid_config.min_price
            max_price = self.grid_config.max_price

        # Create grid levels
        self.grid_levels = self._create_grid_levels(min_price, max_price, current_price)

        # Generate initial signals
        signals = []
        for level in self.grid_levels:
            if level.is_buy_level and level.price < current_price:
                # Buy order below current price
                signals.append(
                    TradingSignal(
                        signal_type=SignalType.BUY,
                        asset=self.grid_config.symbol,
                        size=level.size,
                        price=level.price,
                        reason=f"Grid buy level at ${level.price:.2f}",
                        metadata={
                            "level_index": level.level_index,
                            "grid_type": "initial",
                        },
                        dex=self.grid_config.dex,
                    )
                )
            elif not level.is_buy_level and level.price > current_price:
                # Sell order above current price
                signals.append(
                    TradingSignal(
                        signal_type=SignalType.SELL,
                        asset=self.grid_config.symbol,
                        size=level.size,
                        price=level.price,
                        reason=f"Grid sell level at ${level.price:.2f}",
                        metadata={
                            "level_index": level.level_index,
                            "grid_type": "initial",
                        },
                        dex=self.grid_config.dex,
                    )
                )

        self.state = GridState.ACTIVE
        return signals

    def _create_grid_levels(
        self, min_price: float, max_price: float, current_price: float
    ) -> List[GridLevel]:
        """Create grid levels with geometric spacing"""

        levels = []
        num_levels = self.grid_config.levels
        total = self.grid_config.total_allocation

        max_levels = max(1, int(total // MIN_NOTIONAL_USD))
        if num_levels > max_levels:
            logger.warning(
                "Grid allocation $%.2f / %d levels = $%.2f per level is below "
                "$%.2f min notional; reducing to %d levels.",
                total, num_levels, total / num_levels, MIN_NOTIONAL_USD, max_levels,
            )
            num_levels = max_levels

        size_per_level_usd = total / num_levels

        # Create levels using geometric spacing (equal percentage intervals)
        price_ratio = (max_price / min_price) ** (1 / (num_levels - 1))

        for i in range(num_levels):
            price = min_price * (price_ratio**i)
            size_btc = size_per_level_usd / price  # Convert USD to BTC size

            # Determine if this is a buy or sell level based on current price
            is_buy_level = price < current_price

            level = GridLevel(
                price=price, size=size_btc, level_index=i, is_buy_level=is_buy_level
            )
            levels.append(level)

        return levels

    def _should_rebalance(self, current_price: float) -> bool:
        """Check if grid should be rebalanced"""

        if not self.center_price:
            return False

        # Check price movement threshold
        price_move_pct = (
            abs(current_price - self.center_price) / self.center_price * 100
        )

        return price_move_pct > self.grid_config.rebalance_threshold_pct

    def _rebalance_grid(
        self, current_price: float, balance: float
    ) -> List[TradingSignal]:
        """Rebalance grid around new center price"""

        self.state = GridState.REBALANCING

        # Cancel all existing orders (implementation will handle this)
        cancel_signals = [
            TradingSignal(
                signal_type=SignalType.CLOSE,
                asset=self.grid_config.symbol,
                size=0,  # Close all
                reason="Rebalancing grid",
                metadata={"action": "cancel_all"},
                dex=self.grid_config.dex,
            )
        ]

        # Re-initialize grid at new price
        self.state = GridState.INITIALIZING
        init_signals = self._initialize_grid(current_price, balance)

        self.last_rebalance_time = time.time()

        return cancel_signals + init_signals

    def on_trade_executed(
        self, signal: TradingSignal, executed_price: float, executed_size: float
    ) -> None:
        """Handle trade execution"""

        self.total_trades += 1

        # Mark grid level as filled
        level_index = signal.metadata.get("level_index")
        if level_index is not None and level_index < len(self.grid_levels):
            level = self.grid_levels[level_index]
            level.is_filled = True

            # Calculate profit (simplified)
            if signal.signal_type == SignalType.SELL:
                # Estimate profit from buy-sell spread
                buy_price = executed_price * 0.99  # Approximate
                profit = (executed_price - buy_price) * executed_size
                self.total_profit += profit

    def get_status(self) -> Dict[str, Any]:
        """Get grid strategy status"""

        active_levels = sum(1 for level in self.grid_levels if not level.is_filled)
        filled_levels = len(self.grid_levels) - active_levels

        return {
            **super().get_status(),
            "state": self.state.value,  # Generic state key for compatibility
            "grid_state": self.state.value,  # Specific grid state
            "center_price": self.center_price,
            "total_levels": len(self.grid_levels),
            "active_levels": active_levels,
            "filled_levels": filled_levels,
            "total_trades": self.total_trades,
            "total_profit": self.total_profit,
            "last_rebalance": self.last_rebalance_time,
            "config": {
                "symbol": self.grid_config.symbol,
                "levels": self.grid_config.levels,
                "range_pct": self.grid_config.range_pct,
                "total_allocation": self.grid_config.total_allocation,
            },
        }
