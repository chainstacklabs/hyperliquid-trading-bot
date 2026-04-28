"""
Risk Management Module

Handles all risk-related decisions including stop loss, take profit, drawdown limits,
and position size management. Designed for extensibility and clarity.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from enum import Enum
import time

from interfaces.strategy import Position, MarketData


class RiskAction(Enum):
    """Actions that can be taken when risk rules are violated"""

    NONE = "none"
    CLOSE_POSITION = "close_position"
    REDUCE_POSITION = "reduce_position"
    CANCEL_ORDERS = "cancel_orders"
    PAUSE_TRADING = "pause_trading"
    EMERGENCY_EXIT = "emergency_exit"


@dataclass
class RiskEvent:
    """Risk event notification"""

    rule_name: str
    asset: str
    action: RiskAction
    reason: str
    severity: str  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    metadata: Dict[str, Any]
    timestamp: float = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()


@dataclass
class AccountMetrics:
    """Account-level metrics for risk assessment"""

    total_value: float
    total_pnl: float
    unrealized_pnl: float
    realized_pnl: float
    drawdown_pct: float
    positions_count: int
    largest_position_pct: float


class RiskRule(ABC):
    """
    Base interface for risk rules

    Each rule implements one specific risk check (e.g., stop loss, drawdown)
    and returns risk events when violations occur.
    """

    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config
        self.enabled = config.get("enabled", True)

    @abstractmethod
    def evaluate(
        self,
        positions: List[Position],
        market_data: Dict[str, MarketData],
        account_metrics: AccountMetrics,
    ) -> List[RiskEvent]:
        """
        Evaluate risk rule and return events if violations occur

        Args:
            positions: Current positions
            market_data: Latest market data by asset
            account_metrics: Account-level metrics

        Returns:
            List of risk events (empty if no violations)
        """
        pass

    def get_status(self) -> Dict[str, Any]:
        """Get rule status"""
        return {"name": self.name, "enabled": self.enabled, "config": self.config}


class StopLossRule(RiskRule):
    """Stop loss risk rule - closes positions when loss exceeds threshold"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__("stop_loss", config)
        self.loss_pct = config.get("loss_pct", 5.0)

    def evaluate(
        self,
        positions: List[Position],
        market_data: Dict[str, MarketData],
        account_metrics: AccountMetrics,
    ) -> List[RiskEvent]:
        """Check stop loss conditions"""

        if not self.enabled:
            return []

        events = []

        for position in positions:
            # Calculate current loss percentage
            if position.entry_price > 0:
                loss_pct = (
                    abs(
                        position.unrealized_pnl
                        / (position.entry_price * abs(position.size))
                    )
                    * 100
                )

                if loss_pct >= self.loss_pct:
                    events.append(
                        RiskEvent(
                            rule_name=self.name,
                            asset=position.asset,
                            action=RiskAction.CLOSE_POSITION,
                            reason=f"Stop loss triggered: {loss_pct:.2f}% loss exceeds {self.loss_pct}%",
                            severity="HIGH",
                            metadata={
                                "position_size": position.size,
                                "entry_price": position.entry_price,
                                "current_loss_pct": loss_pct,
                                "threshold_pct": self.loss_pct,
                                "unrealized_pnl": position.unrealized_pnl,
                            },
                        )
                    )

        return events


class TakeProfitRule(RiskRule):
    """Take profit risk rule - closes positions when profit exceeds threshold"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__("take_profit", config)
        self.profit_pct = config.get("profit_pct", 20.0)

    def evaluate(
        self,
        positions: List[Position],
        market_data: Dict[str, MarketData],
        account_metrics: AccountMetrics,
    ) -> List[RiskEvent]:
        """Check take profit conditions"""

        if not self.enabled:
            return []

        events = []

        for position in positions:
            # Calculate current profit percentage
            if position.entry_price > 0 and position.unrealized_pnl > 0:
                profit_pct = (
                    position.unrealized_pnl
                    / (position.entry_price * abs(position.size))
                ) * 100

                if profit_pct >= self.profit_pct:
                    events.append(
                        RiskEvent(
                            rule_name=self.name,
                            asset=position.asset,
                            action=RiskAction.CLOSE_POSITION,
                            reason=f"Take profit triggered: {profit_pct:.2f}% profit exceeds {self.profit_pct}%",
                            severity="MEDIUM",
                            metadata={
                                "position_size": position.size,
                                "entry_price": position.entry_price,
                                "current_profit_pct": profit_pct,
                                "threshold_pct": self.profit_pct,
                                "unrealized_pnl": position.unrealized_pnl,
                            },
                        )
                    )

        return events


class DrawdownRule(RiskRule):
    """Drawdown risk rule - stops trading when account drawdown exceeds threshold"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__("max_drawdown", config)
        self.max_drawdown_pct = config.get("max_drawdown_pct", 15.0)

    def evaluate(
        self,
        positions: List[Position],
        market_data: Dict[str, MarketData],
        account_metrics: AccountMetrics,
    ) -> List[RiskEvent]:
        """Check drawdown conditions"""

        if not self.enabled:
            return []

        events = []

        if account_metrics.drawdown_pct >= self.max_drawdown_pct:
            events.append(
                RiskEvent(
                    rule_name=self.name,
                    asset="ACCOUNT",
                    action=RiskAction.EMERGENCY_EXIT,
                    reason=f"Max drawdown exceeded: {account_metrics.drawdown_pct:.2f}% >= {self.max_drawdown_pct}%",
                    severity="CRITICAL",
                    metadata={
                        "current_drawdown_pct": account_metrics.drawdown_pct,
                        "max_drawdown_pct": self.max_drawdown_pct,
                        "total_pnl": account_metrics.total_pnl,
                        "account_value": account_metrics.total_value,
                    },
                )
            )

        return events


class PositionSizeRule(RiskRule):
    """Position size risk rule - prevents individual positions from being too large"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__("max_position_size", config)
        self.max_position_size_pct = config.get("max_position_size_pct", 30.0)

    def evaluate(
        self,
        positions: List[Position],
        market_data: Dict[str, MarketData],
        account_metrics: AccountMetrics,
    ) -> List[RiskEvent]:
        """Check position size conditions"""

        if not self.enabled:
            return []

        events = []

        for position in positions:
            if account_metrics.total_value > 0:
                position_pct = (
                    position.current_value / account_metrics.total_value
                ) * 100

                if position_pct >= self.max_position_size_pct:
                    events.append(
                        RiskEvent(
                            rule_name=self.name,
                            asset=position.asset,
                            action=RiskAction.REDUCE_POSITION,
                            reason=f"Position too large: {position_pct:.2f}% >= {self.max_position_size_pct}%",
                            severity="MEDIUM",
                            metadata={
                                "position_value": position.current_value,
                                "account_value": account_metrics.total_value,
                                "position_pct": position_pct,
                                "max_position_pct": self.max_position_size_pct,
                                "suggested_reduction": position_pct
                                - self.max_position_size_pct,
                            },
                        )
                    )

        return events


class RiskManager:
    """
    Main risk management orchestrator

    Coordinates multiple risk rules and provides unified risk assessment.
    Designed to be extensible - new risk rules can be easily added.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.rules: List[RiskRule] = []
        self.risk_events_history: List[RiskEvent] = []

        # Initialize risk rules based on configuration
        self._initialize_rules()

    def _initialize_rules(self):
        """Initialize risk rules from configuration"""

        risk_config = self.config.get("risk_management", {})
        # In "grouped" mode, paired TP/SL trigger orders are placed at fill
        # time (engine.py); the polling rules become redundant and would race.
        tpsl_mode = risk_config.get("tpsl_mode", "polling")
        polling_tpsl = tpsl_mode == "polling"

        # Stop loss rule (polling-only)
        if polling_tpsl and risk_config.get("stop_loss_enabled", False):
            self.rules.append(
                StopLossRule(
                    {"enabled": True, "loss_pct": risk_config.get("stop_loss_pct", 5.0)}
                )
            )

        # Take profit rule (polling-only)
        if polling_tpsl and risk_config.get("take_profit_enabled", False):
            self.rules.append(
                TakeProfitRule(
                    {
                        "enabled": True,
                        "profit_pct": risk_config.get("take_profit_pct", 20.0),
                    }
                )
            )

        # Drawdown rule
        self.rules.append(
            DrawdownRule(
                {
                    "enabled": True,
                    "max_drawdown_pct": risk_config.get("max_drawdown_pct", 15.0),
                }
            )
        )

        # Position size rule
        self.rules.append(
            PositionSizeRule(
                {
                    "enabled": True,
                    "max_position_size_pct": risk_config.get(
                        "max_position_size_pct", 30.0
                    ),
                }
            )
        )

    def evaluate_risks(
        self,
        positions: List[Position],
        market_data: Dict[str, MarketData],
        account_metrics: AccountMetrics,
    ) -> List[RiskEvent]:
        """
        Evaluate all risk rules and return consolidated risk events

        Args:
            positions: Current positions
            market_data: Latest market data by asset
            account_metrics: Account-level metrics

        Returns:
            List of risk events from all rules
        """

        all_events = []

        for rule in self.rules:
            try:
                events = rule.evaluate(positions, market_data, account_metrics)
                all_events.extend(events)

                # Store events in history
                self.risk_events_history.extend(events)

            except Exception as e:
                # Log error but continue with other rules
                error_event = RiskEvent(
                    rule_name=rule.name,
                    asset="SYSTEM",
                    action=RiskAction.NONE,
                    reason=f"Risk rule evaluation failed: {e}",
                    severity="LOW",
                    metadata={"error": str(e)},
                )
                all_events.append(error_event)

        return all_events

    def add_rule(self, rule: RiskRule):
        """Add a custom risk rule"""
        self.rules.append(rule)

    def remove_rule(self, rule_name: str):
        """Remove a risk rule by name"""
        self.rules = [rule for rule in self.rules if rule.name != rule_name]

    def get_status(self) -> Dict[str, Any]:
        """Get risk manager status"""

        return {
            "enabled_rules": [rule.name for rule in self.rules if rule.enabled],
            "disabled_rules": [rule.name for rule in self.rules if not rule.enabled],
            "total_rules": len(self.rules),
            "recent_events": len(
                [
                    e
                    for e in self.risk_events_history
                    if time.time() - e.timestamp < 3600
                ]
            ),  # Last hour
            "config": self.config.get("risk_management", {}),
        }

    def get_recent_events(self, hours: int = 1) -> List[RiskEvent]:
        """Get recent risk events"""
        cutoff_time = time.time() - (hours * 3600)
        return [
            event
            for event in self.risk_events_history
            if event.timestamp >= cutoff_time
        ]
