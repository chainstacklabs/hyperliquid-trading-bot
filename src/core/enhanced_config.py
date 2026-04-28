#!/usr/bin/env python3
"""
Enhanced Transparent Configuration System

All assumptions are explicit and user-configurable.
No magic numbers hidden from users.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Literal, Union
from enum import Enum
import yaml
from pathlib import Path


class RiskLevel(Enum):
    """Risk levels with explicit parameters"""

    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


@dataclass
class AccountConfig:
    """Account and risk settings - USER CONTROLS EVERYTHING"""

    max_allocation_pct: float = 20.0  # Max % of account to use for this bot
    risk_level: RiskLevel = RiskLevel.MODERATE  # Risk profile

    def validate(self) -> None:
        """Validate account configuration"""
        if not 1.0 <= self.max_allocation_pct <= 100.0:
            raise ValueError("max_allocation_pct must be between 1.0 and 100.0")


@dataclass
class AutoPriceRangeConfig:
    """Auto price range settings - TRANSPARENT ASSUMPTIONS"""

    range_pct: float = 10.0  # ±% from current price
    volatility_adjustment: bool = True  # Widen range if high volatility
    min_range_pct: float = 5.0  # Minimum range even in low volatility
    max_range_pct: float = 25.0  # Maximum range even in high volatility
    volatility_multiplier: float = 2.0  # How much to multiply volatility impact

    def validate(self) -> None:
        """Validate auto price range configuration"""
        if not 1.0 <= self.range_pct <= 50.0:
            raise ValueError("range_pct must be between 1.0 and 50.0")
        if not 1.0 <= self.min_range_pct <= self.max_range_pct:
            raise ValueError("min_range_pct must be <= max_range_pct")
        if self.range_pct < self.min_range_pct or self.range_pct > self.max_range_pct:
            raise ValueError(
                "range_pct must be between min_range_pct and max_range_pct"
            )


@dataclass
class ManualPriceRangeConfig:
    """Manual price range settings"""

    min: float = 90000.0
    max: float = 120000.0

    def validate(self) -> None:
        """Validate manual price range"""
        if self.min >= self.max:
            raise ValueError("min price must be less than max price")
        if self.min <= 0 or self.max <= 0:
            raise ValueError("prices must be positive")


@dataclass
class PriceRangeConfig:
    """Price range configuration with auto/manual modes"""

    mode: Literal["auto", "manual"] = "auto"
    auto: AutoPriceRangeConfig = field(default_factory=AutoPriceRangeConfig)
    manual: ManualPriceRangeConfig = field(default_factory=ManualPriceRangeConfig)

    def validate(self) -> None:
        """Validate price range configuration"""
        if self.mode not in ["auto", "manual"]:
            raise ValueError("mode must be 'auto' or 'manual'")
        self.auto.validate()
        self.manual.validate()


@dataclass
class AutoPositionSizingConfig:
    """Auto position sizing settings - TRANSPARENT ASSUMPTIONS"""

    balance_reserve_pct: float = 50.0  # Keep % of balance as reserve
    max_single_position_pct: float = 10.0  # Max % in one position
    grid_spacing_strategy: Literal["percentage", "fixed"] = "percentage"
    volatility_position_adjustment: bool = True  # Reduce size for volatile assets
    min_position_size_usd: float = 10.0  # Minimum position size in USD

    def validate(self) -> None:
        """Validate auto position sizing"""
        if not 10.0 <= self.balance_reserve_pct <= 90.0:
            raise ValueError("balance_reserve_pct must be between 10.0 and 90.0")
        if not 1.0 <= self.max_single_position_pct <= 50.0:
            raise ValueError("max_single_position_pct must be between 1.0 and 50.0")
        if self.min_position_size_usd <= 0:
            raise ValueError("min_position_size_usd must be positive")


@dataclass
class ManualPositionSizingConfig:
    """Manual position sizing settings"""

    size_per_level: float = 0.0001  # Asset amount per grid level

    def validate(self) -> None:
        """Validate manual position sizing"""
        if self.size_per_level <= 0:
            raise ValueError("size_per_level must be positive")


@dataclass
class PositionSizingConfig:
    """Position sizing configuration with auto/manual modes"""

    mode: Literal["auto", "manual"] = "auto"
    auto: AutoPositionSizingConfig = field(default_factory=AutoPositionSizingConfig)
    manual: ManualPositionSizingConfig = field(
        default_factory=ManualPositionSizingConfig
    )

    def validate(self) -> None:
        """Validate position sizing configuration"""
        if self.mode not in ["auto", "manual"]:
            raise ValueError("mode must be 'auto' or 'manual'")
        self.auto.validate()
        self.manual.validate()


@dataclass
class GridConfig:
    """Grid configuration.

    `dex` selects a HIP-3 builder-deployed perp dex (None = main perp dex).
    HIP-3 perps use namespaced symbols like "felix:CRCL"; the dex prefix is
    auto-inferred from the symbol when `dex` is unset.
    """

    symbol: str = "BTC"
    levels: int = 15  # Number of grid levels
    price_range: PriceRangeConfig = field(default_factory=PriceRangeConfig)
    position_sizing: PositionSizingConfig = field(default_factory=PositionSizingConfig)
    dex: Optional[str] = None

    def validate(self) -> None:
        """Validate grid configuration"""
        if not self.symbol:
            raise ValueError("symbol cannot be empty")
        if not 3 <= self.levels <= 50:
            raise ValueError("levels must be between 3 and 50")
        if self.dex is not None:
            if not isinstance(self.dex, str):
                raise ValueError("dex must be a string or None")
            self.dex = self.dex.strip()
            if not self.dex:
                raise ValueError("dex must be None or a non-empty string")
        self.price_range.validate()
        self.position_sizing.validate()


@dataclass
class RebalanceConfig:
    """Rebalancing trigger settings"""

    price_move_threshold_pct: float = 15.0  # Rebalance if price moves % outside range
    time_based: bool = False  # Don't rebalance on time
    cooldown_minutes: int = 30  # Wait minutes between rebalances
    max_rebalances_per_day: int = 10  # Limit rebalancing frequency

    def validate(self) -> None:
        """Validate rebalance configuration"""
        if not 5.0 <= self.price_move_threshold_pct <= 50.0:
            raise ValueError("price_move_threshold_pct must be between 5.0 and 50.0")
        if self.cooldown_minutes < 1:
            raise ValueError("cooldown_minutes must be at least 1")
        if self.max_rebalances_per_day < 1:
            raise ValueError("max_rebalances_per_day must be at least 1")


@dataclass
class RiskManagementConfig:
    """Risk management settings - ALL ASSUMPTIONS VISIBLE.

    `tpsl_mode` controls how stop-loss / take-profit are enforced:
      "polling" - legacy: engine polls unrealized_pnl on every price tick
                  and emits a CLOSE signal when thresholds are crossed.
                  Races the matching engine; can fire after a sharp move.
      "grouped" - SDK 0.21+ positionTpsl: at fill time the adapter places
                  a paired TP/SL trigger pair sized to fully close the
                  position. The matching engine fires whichever leg hits
                  first; no polling, no race.
    """

    max_drawdown_pct: float = 15.0  # Stop if % drawdown
    max_position_size_pct: float = 30.0  # Never hold more than % in asset
    stop_loss_enabled: bool = False  # Enable stop loss
    stop_loss_pct: float = 5.0  # Stop loss percentage
    take_profit_enabled: bool = False  # Enable take profit
    take_profit_pct: float = 20.0  # Take profit percentage
    tpsl_mode: str = "polling"  # "polling" or "grouped"
    rebalance: RebalanceConfig = field(default_factory=RebalanceConfig)

    def validate(self) -> None:
        """Validate risk management configuration"""
        if not 5.0 <= self.max_drawdown_pct <= 50.0:
            raise ValueError("max_drawdown_pct must be between 5.0 and 50.0")
        if not 10.0 <= self.max_position_size_pct <= 100.0:
            raise ValueError("max_position_size_pct must be between 10.0 and 100.0")
        if self.stop_loss_enabled and not 1.0 <= self.stop_loss_pct <= 20.0:
            raise ValueError("stop_loss_pct must be between 1.0 and 20.0")
        if self.take_profit_enabled and not 5.0 <= self.take_profit_pct <= 100.0:
            raise ValueError("take_profit_pct must be between 5.0 and 100.0")
        if self.tpsl_mode not in ("polling", "grouped"):
            raise ValueError(
                f"tpsl_mode must be 'polling' or 'grouped', got {self.tpsl_mode!r}"
            )
        self.rebalance.validate()


@dataclass
class MarketDataConfig:
    """Market data settings"""

    volatility_window_hours: int = 24  # Calculate volatility over hours
    connection_retry_attempts: int = 3  # Retry connection attempts
    connection_timeout_sec: int = 10  # Connection timeout
    websocket_reconnect_delay_sec: float = 5.0  # Delay before WebSocket reconnection

    def validate(self) -> None:
        """Validate market data configuration"""
        if not 1 <= self.volatility_window_hours <= 168:  # 1 week max
            raise ValueError("volatility_window_hours must be between 1 and 168")


@dataclass
class ExchangeConfig:
    """Exchange configuration settings.

    `dex` selects a HIP-3 builder-deployed perp dex at adapter level
    (None = main perp dex). Bot strategies may further narrow per-symbol
    via GridConfig.dex.
    """

    type: str = "hyperliquid"  # Exchange type (hyperliquid, hl, etc.)
    testnet: bool = True  # Use testnet for development
    dex: Optional[str] = None
    account_address: Optional[str] = None

    def validate(self) -> None:
        """Validate exchange configuration"""
        if not self.type:
            raise ValueError("exchange type cannot be empty")
        if self.dex is not None:
            if not isinstance(self.dex, str):
                raise ValueError("dex must be a string or None")
            self.dex = self.dex.strip()
            if not self.dex:
                raise ValueError("dex must be None or a non-empty string")


@dataclass
class MonitoringConfig:
    """Logging and monitoring settings"""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    report_interval_minutes: int = 60  # Status report interval
    save_trade_history: bool = True  # Save trade history to file
    metrics_export: bool = False  # Export metrics for monitoring

    def validate(self) -> None:
        """Validate monitoring configuration"""
        if self.log_level not in ["DEBUG", "INFO", "WARNING", "ERROR"]:
            raise ValueError("log_level must be DEBUG, INFO, WARNING, or ERROR")
        if self.report_interval_minutes < 1:
            raise ValueError("report_interval_minutes must be at least 1")


@dataclass
class EnhancedBotConfig:
    """Complete enhanced bot configuration with all assumptions explicit"""

    name: str
    active: bool = True
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    account: AccountConfig = field(default_factory=AccountConfig)
    grid: GridConfig = field(default_factory=GridConfig)
    risk_management: RiskManagementConfig = field(default_factory=RiskManagementConfig)
    market_data: MarketDataConfig = field(default_factory=MarketDataConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)

    # Optional bot-specific private key configuration (overrides environment keys)
    private_key: Optional[str] = None  # Single key for both environments
    testnet_private_key: Optional[str] = None  # Testnet-specific key
    mainnet_private_key: Optional[str] = None  # Mainnet-specific key
    private_key_file: Optional[str] = None  # Path to single key file
    testnet_key_file: Optional[str] = None  # Path to testnet key file
    mainnet_key_file: Optional[str] = None  # Path to mainnet key file

    def validate(self) -> None:
        """Validate entire configuration"""
        if not self.name:
            raise ValueError("Bot name cannot be empty")

        # Validate all sections
        self.exchange.validate()
        self.account.validate()
        self.grid.validate()
        self.risk_management.validate()
        self.market_data.validate()
        self.monitoring.validate()

        # Cross-validation checks
        if isinstance(self.account.risk_level, str):
            self.account.risk_level = RiskLevel(self.account.risk_level)

        # Ensure allocation makes sense with risk management
        if self.account.max_allocation_pct > (
            100.0 - self.grid.position_sizing.auto.balance_reserve_pct
        ):
            raise ValueError(
                f"max_allocation_pct ({self.account.max_allocation_pct}%) conflicts with "
                f"balance_reserve_pct ({self.grid.position_sizing.auto.balance_reserve_pct}%)"
            )

        # Validate private key configuration (security checks)
        self._validate_private_keys()

    def _validate_private_keys(self) -> None:
        """Validate private key configuration and issue security warnings"""
        import logging

        logger = logging.getLogger(__name__)

        # Check for keys directly in config (not recommended)
        direct_keys = [
            self.private_key,
            self.testnet_private_key,
            self.mainnet_private_key,
        ]
        if any(key is not None for key in direct_keys):
            logger.warning(
                "⚠️  SECURITY WARNING: Private keys found directly in config file!"
            )
            logger.warning(
                "⚠️  Consider using key files or environment variables instead"
            )

        # Validate key file paths if specified
        key_files = [
            self.private_key_file,
            self.testnet_key_file,
            self.mainnet_key_file,
        ]
        for key_file in key_files:
            if key_file is not None:
                key_path = Path(key_file)
                if not key_path.is_absolute():
                    # Convert relative paths relative to config directory
                    continue
                if not key_path.exists():
                    logger.warning(f"⚠️  Key file not found: {key_file}")

        # Validate key format if provided directly
        for key_name, key_value in [
            ("private_key", self.private_key),
            ("testnet_private_key", self.testnet_private_key),
            ("mainnet_private_key", self.mainnet_private_key),
        ]:
            if key_value is not None:
                if not isinstance(key_value, str):
                    raise ValueError(f"{key_name} must be a string")
                if not (key_value.startswith("0x") and len(key_value) == 66):
                    if not (len(key_value) == 64):  # Allow without 0x prefix
                        logger.warning(
                            f"⚠️  {key_name} may have invalid format (should be 64 hex chars or 0x + 64 hex chars)"
                        )

    @classmethod
    def from_yaml(cls, file_path: Union[str, Path]) -> "EnhancedBotConfig":
        """Load configuration from YAML file"""
        with open(file_path, "r") as f:
            data = yaml.safe_load(f)

        # Convert nested dicts to dataclasses
        config = cls._dict_to_dataclass(data)
        config.validate()
        return config

    @classmethod
    def _dict_to_dataclass(cls, data: Dict[str, Any]) -> "EnhancedBotConfig":
        """Convert dictionary to dataclass recursively"""
        # Handle nested structures
        if "exchange" in data:
            data["exchange"] = ExchangeConfig(**data["exchange"])

        if "account" in data:
            data["account"] = AccountConfig(**data["account"])

        if "grid" in data:
            grid_data = data["grid"]
            if "price_range" in grid_data:
                pr_data = grid_data["price_range"]
                if "auto" in pr_data:
                    pr_data["auto"] = AutoPriceRangeConfig(**pr_data["auto"])
                if "manual" in pr_data:
                    pr_data["manual"] = ManualPriceRangeConfig(**pr_data["manual"])
                grid_data["price_range"] = PriceRangeConfig(**pr_data)

            if "position_sizing" in grid_data:
                ps_data = grid_data["position_sizing"]
                if "auto" in ps_data:
                    ps_data["auto"] = AutoPositionSizingConfig(**ps_data["auto"])
                if "manual" in ps_data:
                    ps_data["manual"] = ManualPositionSizingConfig(**ps_data["manual"])
                grid_data["position_sizing"] = PositionSizingConfig(**ps_data)

            data["grid"] = GridConfig(**grid_data)

        if "risk_management" in data:
            rm_data = data["risk_management"]
            if "rebalance" in rm_data:
                rm_data["rebalance"] = RebalanceConfig(**rm_data["rebalance"])
            data["risk_management"] = RiskManagementConfig(**rm_data)

        if "market_data" in data:
            data["market_data"] = MarketDataConfig(**data["market_data"])

        if "monitoring" in data:
            data["monitoring"] = MonitoringConfig(**data["monitoring"])

        return cls(**data)

    def to_yaml(self, file_path: Union[str, Path]) -> None:
        """Save configuration to YAML file"""
        data = self._dataclass_to_dict()

        with open(file_path, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, indent=2, sort_keys=False)

    def _dataclass_to_dict(self) -> Dict[str, Any]:
        """Convert dataclass to dictionary recursively"""
        seen = set()

        def convert_value(value, path=""):
            # Handle primitive types
            if value is None or isinstance(value, (str, int, float, bool)):
                return value

            # Handle enums
            if isinstance(value, Enum):
                return value.value

            # Handle lists and tuples
            if isinstance(value, (list, tuple)):
                return [
                    convert_value(item, f"{path}[{i}]") for i, item in enumerate(value)
                ]

            # Handle dictionaries
            if isinstance(value, dict):
                return {k: convert_value(v, f"{path}.{k}") for k, v in value.items()}

            # Handle objects with __dict__
            if hasattr(value, "__dict__"):
                # Check for circular references
                obj_id = id(value)
                if obj_id in seen:
                    return f"<circular reference: {type(value).__name__}>"

                seen.add(obj_id)
                try:
                    result = {
                        k: convert_value(v, f"{path}.{k}")
                        for k, v in value.__dict__.items()
                    }
                finally:
                    seen.remove(obj_id)

                return result

            # For other types, try to convert to string
            return str(value)

        return {k: convert_value(v, k) for k, v in self.__dict__.items()}


def create_default_config(
    name: str, symbol: str, risk_level: RiskLevel = RiskLevel.MODERATE
) -> EnhancedBotConfig:
    """Create a default configuration with sensible settings"""

    # Adjust settings based on risk level
    if risk_level == RiskLevel.CONSERVATIVE:
        account_config = AccountConfig(max_allocation_pct=10.0, risk_level=risk_level)
        auto_price_range = AutoPriceRangeConfig(
            range_pct=5.0, min_range_pct=3.0, max_range_pct=10.0
        )
        auto_position_sizing = AutoPositionSizingConfig(
            balance_reserve_pct=70.0, max_single_position_pct=5.0
        )
        risk_management = RiskManagementConfig(
            max_drawdown_pct=10.0, max_position_size_pct=20.0
        )

    elif risk_level == RiskLevel.MODERATE:
        account_config = AccountConfig(max_allocation_pct=20.0, risk_level=risk_level)
        auto_price_range = AutoPriceRangeConfig(
            range_pct=10.0, min_range_pct=5.0, max_range_pct=20.0
        )
        auto_position_sizing = AutoPositionSizingConfig(
            balance_reserve_pct=50.0, max_single_position_pct=10.0
        )
        risk_management = RiskManagementConfig(
            max_drawdown_pct=15.0, max_position_size_pct=30.0
        )

    else:  # AGGRESSIVE
        account_config = AccountConfig(max_allocation_pct=40.0, risk_level=risk_level)
        auto_price_range = AutoPriceRangeConfig(
            range_pct=15.0, min_range_pct=10.0, max_range_pct=30.0
        )
        auto_position_sizing = AutoPositionSizingConfig(
            balance_reserve_pct=30.0, max_single_position_pct=20.0
        )
        risk_management = RiskManagementConfig(
            max_drawdown_pct=25.0, max_position_size_pct=50.0
        )

    return EnhancedBotConfig(
        name=name,
        account=account_config,
        grid=GridConfig(
            symbol=symbol,
            price_range=PriceRangeConfig(auto=auto_price_range),
            position_sizing=PositionSizingConfig(auto=auto_position_sizing),
        ),
        risk_management=risk_management,
    )
