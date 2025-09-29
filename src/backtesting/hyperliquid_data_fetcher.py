#!/usr/bin/env python3
"""Hyperliquid data fetcher for backtesting utilities.

This module uses the official ``hyperliquid-python-sdk`` to download
historical candlestick data and converts it into a trade-like CSV that can be
consumed by Nautilus Trader's ``TradeTickDataWrangler``.

The generated CSV mirrors the structure of the sample datasets shipped with
Nautilus Trader (``timestamp``, ``trade_id``, ``price``, ``quantity``,
``buyer_maker``).
"""

from __future__ import annotations

import argparse
import csv
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

from hyperliquid.info import Info


DEFAULT_INTERVAL = "1m"
DEFAULT_LOOKBACK_MINUTES = 360
DEFAULT_MAX_TRADES_PER_CANDLE = 10


@dataclass
class Candle:
    """Simple container for candle fields returned by the SDK."""

    start_ms: int
    end_ms: int
    open_px: float
    close_px: float
    high_px: float
    low_px: float
    volume: float
    trades: int

    @classmethod
    def from_raw(cls, raw: dict) -> "Candle":
        return cls(
            start_ms=int(raw["t"]),
            end_ms=int(raw["T"]),
            open_px=float(raw["o"]),
            close_px=float(raw["c"]),
            high_px=float(raw["h"]),
            low_px=float(raw["l"]),
            volume=float(raw["v"]),
            trades=int(raw.get("n", 0)),
        )


def _select_base_url(testnet: bool) -> str:
    return (
        "https://api.hyperliquid-testnet.xyz" if testnet else "https://api.hyperliquid.xyz"
    )


def _fetch_candles(symbol: str, interval: str, minutes: int, testnet: bool) -> List[Candle]:
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - minutes * 60 * 1000

    info = Info(base_url=_select_base_url(testnet), skip_ws=True)

    if symbol not in info.name_to_coin:
        available = ", ".join(sorted(info.name_to_coin.keys()))
        raise ValueError(f"Unknown Hyperliquid symbol '{symbol}'. Available: {available}")

    candles_raw = info.candles_snapshot(symbol, interval, start_ms, end_ms)
    if not candles_raw:
        raise RuntimeError(
            f"No candles returned for {symbol} over the last {minutes} minutes."
        )

    return [Candle.from_raw(item) for item in candles_raw]


def _interpolate_prices(candle: Candle, slices: int) -> Iterable[float]:
    if slices <= 1 or math.isclose(candle.open_px, candle.close_px):
        yield candle.close_px
        return

    step = (candle.close_px - candle.open_px) / (slices - 1)
    for idx in range(slices):
        yield candle.open_px + idx * step


def _generate_trade_rows(
    candles: Iterable[Candle],
    symbol: str,
    max_trades_per_candle: int,
) -> List[List[str]]:
    rows: List[List[str]] = []

    for candle in candles:
        trade_count = max(1, min(candle.trades or 1, max_trades_per_candle))
        if candle.volume <= 0:
            # Skip empty candles - no trades occurred so nothing to record.
            continue

        quantity = candle.volume / trade_count
        if quantity <= 0:
            continue

        duration_ms = max(1, candle.end_ms - candle.start_ms)
        buyer_maker = candle.close_px < candle.open_px

        for idx, price in enumerate(_interpolate_prices(candle, trade_count)):
            ts_ms = candle.start_ms + int(duration_ms * (idx / trade_count))
            timestamp = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
            trade_id = f"{symbol}-{ts_ms}-{idx}"
            rows.append(
                [
                    timestamp,
                    trade_id,
                    f"{price:.6f}",
                    f"{quantity:.6f}",
                    "True" if buyer_maker else "False",
                ]
            )

    return rows


def fetch_trades_to_csv(
    *,
    symbol: str,
    interval: str = DEFAULT_INTERVAL,
    minutes: int = DEFAULT_LOOKBACK_MINUTES,
    testnet: bool = True,
    output_path: Path,
    max_trades_per_candle: int = DEFAULT_MAX_TRADES_PER_CANDLE,
) -> Path:
    """Fetch Hyperliquid candles and convert them to a trade CSV."""

    candles = _fetch_candles(symbol, interval, minutes, testnet)
    rows = _generate_trade_rows(candles, symbol, max_trades_per_candle)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["timestamp", "trade_id", "price", "quantity", "buyer_maker"])
        writer.writerows(rows)

    return output_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Hyperliquid data and write a CSV.")
    parser.add_argument("--symbol", default="BTC", help="Hyperliquid symbol to fetch (default: BTC)")
    parser.add_argument(
        "--interval",
        default=DEFAULT_INTERVAL,
        help="Candle interval (default: 1m)",
    )
    parser.add_argument(
        "--minutes",
        type=int,
        default=DEFAULT_LOOKBACK_MINUTES,
        help="Number of minutes to fetch (default: 360)",
    )
    parser.add_argument(
        "--output",
        default="hyperliquid/btcusdc-trades.csv",
        help="Destination CSV path (default: hyperliquid/btcusdc-trades.csv)",
    )
    parser.add_argument(
        "--max-trades-per-candle",
        type=int,
        default=DEFAULT_MAX_TRADES_PER_CANDLE,
        help="Cap synthetic trades generated per candle (default: 10)",
    )
    parser.add_argument(
        "--mainnet",
        action="store_true",
        help="Fetch from mainnet instead of testnet",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    output = fetch_trades_to_csv(
        symbol=args.symbol,
        interval=args.interval,
        minutes=args.minutes,
        testnet=not args.mainnet,
        output_path=Path(args.output),
        max_trades_per_candle=max(1, args.max_trades_per_candle),
    )
    print(f"✅ Wrote Hyperliquid data to {output}")


if __name__ == "__main__":
    main()
