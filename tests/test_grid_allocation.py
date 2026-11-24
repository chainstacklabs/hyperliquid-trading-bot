import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from interfaces.strategy import MarketData, SignalType
from strategies.grid.basic_grid import BasicGridStrategy


class GridAllocationTest(unittest.TestCase):
    def test_insufficient_budget_is_rejected_before_trading(self):
        for budget in (10.0, 20.0, 20.99):
            with self.subTest(budget=budget):
                strategy = BasicGridStrategy({"levels": 10, "total_allocation": budget})
                strategy.start()
                tick = MarketData("BTC", 100, 0, 1)
                with self.assertLogs("strategies.grid.basic_grid", level="WARNING"):
                    self.assertEqual(strategy.generate_signals(tick, [], 1000), [])
                self.assertFalse(strategy.is_active)
                self.assertEqual(strategy.generate_signals(tick, [], 1000), [])

    def test_minimum_budget_funds_both_sides(self):
        strategy = BasicGridStrategy({"levels": 10, "total_allocation": 21.0})
        strategy.start()
        signals = strategy.generate_signals(MarketData("BTC", 100, 0, 1), [], 1000)
        self.assertEqual([s.signal_type for s in signals], [SignalType.BUY, SignalType.SELL])
        for signal in signals:
            self.assertAlmostEqual(signal.size * signal.price, 10.5)
