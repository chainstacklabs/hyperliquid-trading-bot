#!/usr/bin/env python3
"""
Grid Trading Bot Runner

Clean, simple entry point for running grid trading strategies.
No confusing naming - just "run_bot.py".
"""

import asyncio
import argparse
import sys
import os
import signal
from pathlib import Path
import yaml
from typing import Optional

# Load .env file if it exists
from dotenv import load_dotenv

load_dotenv()

# Add src to path for imports
sys.path.append(str(Path(__file__).parent))

from core.engine import TradingEngine
from core.enhanced_config import EnhancedBotConfig


class GridTradingBot:
    """
    Simple grid trading bot runner

    Clean interface - no "enhanced" or "advanced" confusion.
    Just a bot that runs grid trading strategies.
    """

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = None
        self.engine = None
        self.running = False

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        print(f"\n📡 Received signal {signum}, shutting down...")
        self.running = False
        if self.engine:
            asyncio.create_task(self.engine.stop())

    async def run(self) -> None:
        """Run the bot"""

        try:
            # Load configuration
            try:
                display_path = Path(self.config_path).resolve().relative_to(Path.cwd())
            except ValueError:
                display_path = Path(self.config_path).name
            print(f"📁 Loading configuration: {display_path}")
            self.config = EnhancedBotConfig.from_yaml(Path(self.config_path))
            print(f"✅ Configuration loaded: {self.config.name}")

            # Convert to engine config format
            engine_config = self._convert_config()

            # Initialize trading engine
            self.engine = TradingEngine(engine_config)

            if not await self.engine.initialize():
                print("❌ Failed to initialize trading engine")
                return

            # Start trading
            print(f"🚀 Starting {self.config.name}")
            self.running = True
            await self.engine.start()

        except KeyboardInterrupt:
            print("\n📡 Keyboard interrupt received")
        except Exception as e:
            print(f"❌ Error: {e}")
        finally:
            if self.engine:
                await self.engine.stop()

    def _convert_config(self) -> dict:
        """Convert EnhancedBotConfig to engine config format"""

        testnet = os.getenv("HYPERLIQUID_TESTNET", "true").lower() == "true"

        # Calculate total allocation in USD from account balance percentage
        # Note: This is a simplified approach - in production, you'd get actual account balance
        # For now, using a default base amount of $1000 USD
        base_allocation_usd = 1000.0
        total_allocation_usd = base_allocation_usd * (
            self.config.account.max_allocation_pct / 100.0
        )

        return {
            "exchange": {
                "type": self.config.exchange.type,
                "testnet": self.config.exchange.testnet,
            },
            "strategy": {
                "type": "basic_grid",  # Default to basic grid
                "symbol": self.config.grid.symbol,
                "levels": self.config.grid.levels,
                "range_pct": self.config.grid.price_range.auto.range_pct,
                "total_allocation": total_allocation_usd,
                "rebalance_threshold_pct": self.config.risk_management.rebalance.price_move_threshold_pct,
            },
            "bot_config": {
                # Pass through the entire config so KeyManager can look for bot-specific keys
                "name": self.config.name,
                "private_key_file": getattr(self.config, "private_key_file", None),
                "testnet_key_file": getattr(self.config, "testnet_key_file", None),
                "mainnet_key_file": getattr(self.config, "mainnet_key_file", None),
                "private_key": getattr(self.config, "private_key", None),
                "testnet_private_key": getattr(
                    self.config, "testnet_private_key", None
                ),
                "mainnet_private_key": getattr(
                    self.config, "mainnet_private_key", None
                ),
            },
            "log_level": self.config.monitoring.log_level,
        }


def find_first_active_config() -> Optional[Path]:
    """Find the first active config in the bots folder"""

    # Look for bots folder relative to the script location
    script_dir = Path(__file__).parent
    bots_dir = script_dir.parent / "bots"

    if not bots_dir.exists():
        return None

    # Scan for YAML files
    yaml_files = list(bots_dir.glob("*.yaml")) + list(bots_dir.glob("*.yml"))

    for yaml_file in sorted(yaml_files):
        try:
            with open(yaml_file, "r") as f:
                data = yaml.safe_load(f)

            # Check if config is active
            if data and data.get("active", False):
                print(f"📁 Found active config: {yaml_file.name}")
                return yaml_file

        except Exception as e:
            print(f"⚠️ Error reading {yaml_file.name}: {e}")
            continue

    return None


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Grid Trading Bot")
    parser.add_argument(
        "config",
        nargs="?",
        help="Configuration file path (optional - will auto-discover if not provided)",
    )
    parser.add_argument(
        "--validate", action="store_true", help="Validate configuration only"
    )

    args = parser.parse_args()

    # Determine config file
    config_path = None
    if args.config:
        config_path = Path(args.config)
        if not config_path.exists():
            print(f"❌ Config file not found: {args.config}")
            return 1
    else:
        # Auto-discover first active config
        print("🔍 No config specified, auto-discovering active config...")
        config_path = find_first_active_config()
        if not config_path:
            print("❌ No active config found in bots/ folder")
            print("💡 Create a config file in bots/ folder with 'active: true'")
            return 1

    if args.validate:
        # Just validate the config
        try:
            config = EnhancedBotConfig.from_yaml(config_path)
            config.validate()
            print("✅ Configuration is valid")
            return 0
        except Exception as e:
            print(f"❌ Configuration error: {e}")
            return 1

    # Run the bot
    bot = GridTradingBot(str(config_path))
    await bot.run()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
