import csv
import importlib
from datetime import datetime, timedelta, timezone

import pytest
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.wranglers import TradeTickDataWrangler


def build_candles(count: int, start: datetime) -> list[dict]:
    candles = []
    for idx in range(count):
        start_ms = int((start + timedelta(minutes=idx)).timestamp() * 1000)
        end_ms = start_ms + 60_000
        open_px = 25_000 + idx * 25
        close_px = open_px + 15
        high_px = close_px + 5
        low_px = open_px - 5
        volume = 5 + idx
        candles.append(
            {
                "t": start_ms,
                "T": end_ms,
                "o": open_px,
                "c": close_px,
                "h": high_px,
                "l": low_px,
                "v": volume,
                "n": 10,
            }
        )
    return candles


def install_fake_info(monkeypatch: pytest.MonkeyPatch, fetcher_module, candles: list[dict]) -> None:
    class FakeInfo:
        def __init__(self, base_url: str, skip_ws: bool) -> None:
            self.base_url = base_url
            self.skip_ws = skip_ws
            self.name_to_coin = {"BTC": "BTC"}

        def candles_snapshot(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
            return candles

    monkeypatch.setattr(fetcher_module, "Info", FakeInfo)


class TickCollectorStrategy(Strategy):
    def __init__(self, instrument_id, target_ticks: int) -> None:
        super().__init__(config=StrategyConfig())
        self.instrument_id = instrument_id
        self.target_ticks = target_ticks
        self.received = 0

    def on_start(self) -> None:
        self.subscribe_trade_ticks(self.instrument_id)

    def on_trade_tick(self, event) -> None:
        self.received += 1
        if self.received >= self.target_ticks:
            self.stop()


def test_fetch_trades_to_csv_generates_wrangleable_ticks(tmp_path, monkeypatch):
    fetcher = importlib.import_module("backtesting.hyperliquid_data_fetcher")
    backtest_module = importlib.import_module("backtesting.hyperliquid_btc_ema_twap_backtest")

    candles = build_candles(5, datetime(2024, 1, 1, tzinfo=timezone.utc))
    install_fake_info(monkeypatch, fetcher, candles)

    output_path = tmp_path / "btc_trades.csv"
    fetcher.fetch_trades_to_csv(
        symbol="BTC",
        interval="1m",
        minutes=5,
        testnet=True,
        output_path=output_path,
        max_trades_per_candle=5,
    )

    with output_path.open() as csv_file:
        rows = list(csv.DictReader(csv_file))

    assert len(rows) == 25
    assert rows[0]["price"] != ""
    assert rows[0]["quantity"] != ""

    trade_frame = backtest_module.load_trade_data(output_path)
    instrument = backtest_module.hyperliquid_btcusdc_instrument()
    wrangler = TradeTickDataWrangler(instrument=instrument)
    ticks = wrangler.process(trade_frame)

    assert len(ticks) == 25
    assert all(tick.instrument_id == instrument.id for tick in ticks)


def test_backtest_engine_processes_generated_ticks(tmp_path, monkeypatch):
    fetcher = importlib.import_module("backtesting.hyperliquid_data_fetcher")
    backtest_module = importlib.import_module("backtesting.hyperliquid_btc_ema_twap_backtest")

    candles = build_candles(30, datetime(2024, 1, 1, tzinfo=timezone.utc))
    install_fake_info(monkeypatch, fetcher, candles)

    output_path = tmp_path / "btc_trades_full.csv"
    fetcher.fetch_trades_to_csv(
        symbol="BTC",
        interval="1m",
        minutes=30,
        testnet=True,
        output_path=output_path,
        max_trades_per_candle=10,
    )

    trade_frame = backtest_module.load_trade_data(output_path)
    instrument = backtest_module.hyperliquid_btcusdc_instrument()
    wrangler = TradeTickDataWrangler(instrument=instrument)
    ticks = wrangler.process(trade_frame)

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("TESTER-NAUTILUS"),
            logging=LoggingConfig(log_level="WARN", log_colors=False, use_pyo3=False),
        )
    )

    engine.add_venue(
        venue=instrument.id.venue,
        oms_type=OmsType.NETTING,
        book_type=BookType.L1_MBP,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(1_000_000.0, instrument.quote_currency), Money(10.0, instrument.base_currency)],
        trade_execution=True,
    )

    engine.add_instrument(instrument)
    engine.add_data(ticks)

    strategy = TickCollectorStrategy(instrument.id, target_ticks=30)
    engine.add_strategy(strategy=strategy)

    engine.run()

    assert strategy.received == 30
    engine.stop()
    engine.reset()
    engine.dispose()
    engine.reset()
    engine.dispose()
