#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2025 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------

"""EMA + TWAP backtest wired for Hyperliquid data."""

from __future__ import annotations

import argparse
import time
from decimal import Decimal
from pathlib import Path

import pandas as pd

from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import LoggingConfig
from nautilus_trader.examples.algorithms.twap import TWAPExecAlgorithm
from nautilus_trader.examples.strategies.ema_cross_twap import EMACrossTWAP
from nautilus_trader.examples.strategies.ema_cross_twap import EMACrossTWAPConfig
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDC
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.wranglers import TradeTickDataWrangler

try:
    from .hyperliquid_data_fetcher import DEFAULT_INTERVAL
    from .hyperliquid_data_fetcher import fetch_trades_to_csv
except ImportError:
    from hyperliquid_data_fetcher import DEFAULT_INTERVAL
    from hyperliquid_data_fetcher import fetch_trades_to_csv


DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent.parent / "hyperliquid" / "btcusdc-trades.csv"

HYPERLIQUID_VENUE = Venue("HYPERLIQUID")


def hyperliquid_btcusdc_instrument() -> CurrencyPair:
    return CurrencyPair(
        instrument_id=InstrumentId(symbol=Symbol("BTCUSDC"), venue=HYPERLIQUID_VENUE),
        raw_symbol=Symbol("BTCUSDC"),
        base_currency=BTC,
        quote_currency=USDC,
        price_precision=2,
        size_precision=5,
        price_increment=Price(1e-02, precision=2),
        size_increment=Quantity(1e-05, precision=5),
        lot_size=None,
        max_quantity=Quantity(1000, precision=5),
        min_quantity=Quantity(1e-05, precision=5),
        max_notional=None,
        min_notional=Money(10.0, USDC),
        max_price=Price(200000.0, precision=2),
        min_price=Price(1e-02, precision=2),
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=Decimal("0.0005"),
        taker_fee=Decimal("0.0005"),
        ts_event=0,
        ts_init=0,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Hyperliquid EMA + TWAP backtest")
    parser.add_argument(
        "--data-path",
        default=str(DEFAULT_DATA_PATH),
        help="Path to the trade CSV (default: hyperliquid/btcusdc-trades.csv)",
    )
    parser.add_argument(
        "--minutes",
        type=int,
        default=360,
        help="Minutes of history to download when generating data (default: 360)",
    )
    parser.add_argument(
        "--interval",
        default=DEFAULT_INTERVAL,
        help="Candle interval to use when fetching data (default: 1m)",
    )
    parser.add_argument(
        "--refresh-data",
        action="store_true",
        help="Force regeneration of the trade CSV before running",
    )
    parser.add_argument(
        "--mainnet",
        action="store_true",
        help="Fetch data from mainnet instead of testnet",
    )
    parser.add_argument(
        "--max-trades-per-candle",
        type=int,
        default=10,
        help="Synthetic trades to generate per candle (default: 10)",
    )
    return parser.parse_args()


def load_trade_data(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, format="mixed")
    frame["buyer_maker"] = frame["buyer_maker"].astype(str).str.lower() == "true"
    frame["price"] = frame["price"].astype(float)
    frame["quantity"] = frame["quantity"].astype(float)
    frame.set_index("timestamp", inplace=True)
    return frame


def main() -> None:
    args = parse_args()

    data_path = Path(args.data_path)
    if args.refresh_data or not data_path.exists():
        fetch_trades_to_csv(
            symbol="BTC",
            interval=args.interval,
            minutes=args.minutes,
            testnet=not args.mainnet,
            output_path=data_path,
            max_trades_per_candle=args.max_trades_per_candle,
        )

    trade_df = load_trade_data(data_path)

    config = BacktestEngineConfig(
        trader_id=TraderId("BACKTESTER-001"),
        logging=LoggingConfig(log_level="INFO", log_colors=True, use_pyo3=False),
    )
    engine = BacktestEngine(config=config)

    engine.add_venue(
        venue=HYPERLIQUID_VENUE,
        oms_type=OmsType.NETTING,
        book_type=BookType.L1_MBP,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(1_000_000.0, USDC), Money(10.0, BTC)],
        trade_execution=True,
    )

    btcusdc = hyperliquid_btcusdc_instrument()
    engine.add_instrument(btcusdc)

    wrangler = TradeTickDataWrangler(instrument=btcusdc)
    ticks = wrangler.process(trade_df)
    engine.add_data(ticks)

    strategy_config = EMACrossTWAPConfig(
        instrument_id=btcusdc.id,
        bar_type=BarType.from_str("BTCUSDC.HYPERLIQUID-250-TICK-LAST-INTERNAL"),
        trade_size=Decimal("0.10"),
        fast_ema_period=10,
        slow_ema_period=20,
        twap_horizon_secs=10.0,
        twap_interval_secs=2.5,
    )

    strategy = EMACrossTWAP(config=strategy_config)
    engine.add_strategy(strategy=strategy)

    exec_algorithm = TWAPExecAlgorithm()
    engine.add_exec_algorithm(exec_algorithm)

    time.sleep(0.1)

    engine.run()

    with pd.option_context("display.max_rows", 100, "display.max_columns", None, "display.width", 300):
        print(engine.trader.generate_account_report(HYPERLIQUID_VENUE))
        print(engine.trader.generate_order_fills_report())
        print(engine.trader.generate_positions_report())

    engine.reset()
    engine.dispose()


if __name__ == "__main__":
    main()
