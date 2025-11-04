# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Workflow

Before implementing anything, send me your plan of action for approval.
Start implementing things only after my explicit approval.

Follow test driven development.

## Package Management

Use UV package manager for all commands in this repo:
- `uv sync` - Install/sync dependencies
- `uv add <package>` - Add new dependencies  
- `uv run <command>` - Run commands in the virtual environment
- `uv run <script>` - Run Python scripts

## Project Overview

This is a professional-grade automated grid trading system for Hyperliquid DEX supporting:
- **Spot trading** - Direct asset ownership (cash trading)
- **Perpetuals (perps)** - Leveraged derivatives trading  
- **Grid trading strategies** - Automated buy/sell orders across price ranges
- **Risk management** - Stop loss, take profit, drawdown limits, and position sizing
- **Real-time market data** - WebSocket price feeds and order book data

The codebase follows SOLID principles without overcomplicating the implementation.

## Repository Structure

```
├── bots/                          # Bot configurations (YAML files)
│   └── btc_conservative.yaml      # Conservative BTC grid strategy
├── src/                           # Source code
│   ├── run_bot.py                # Main bot runner (auto-discovers config)
│   ├── core/                     # Core engine components
│   │   ├── engine.py             # Main trading engine
│   │   ├── enhanced_config.py    # Configuration management
│   │   ├── key_manager.py        # Private key management
│   │   ├── risk_manager.py       # Risk management and exit strategies
│   │   └── endpoint_router.py    # API endpoint routing
│   ├── strategies/               # Trading strategies
│   │   └── grid/
│   │       └── basic_grid.py     # Grid trading implementation
│   ├── exchanges/                # Exchange adapters
│   │   └── hyperliquid/
│   │       ├── adapter.py        # Hyperliquid exchange integration
│   │       └── market_data.py    # Real-time market data provider
│   ├── interfaces/               # Business logic interfaces
│   │   ├── strategy.py           # Trading strategy interface
│   │   └── exchange.py           # Exchange adapter interface
│   └── utils/                    # Shared utilities
│       ├── events.py             # Event definitions
│       └── exceptions.py         # Custom exception classes
├── learning_examples/            # Standalone educational scripts
│   ├── 01_websockets/            # Real-time data streams via WebSocket
│   ├── 02_market_data/           # Price data and market info
│   ├── 03_account_info/          # Account state and orders
│   ├── 04_trading/               # Order placement and cancellation
│   ├── 05_funding/               # Funding rates and spot/perp availability
│   └── 06_copy_trading/          # Order mirroring and copy trading strategies
└── .env                          # Environment variables (not in git)
```

## Configuration System

**Bot configurations are stored as YAML files in the `bots/` directory.**

Each configuration includes:
- `active: true/false` - Controls whether bot runs automatically
- `name` - Unique bot identifier
- `account` - Account allocation settings
- `grid` - Grid strategy parameters (symbol, levels, price range)
- `risk_management` - Stop loss, take profit, drawdown limits, position sizing, and rebalancing thresholds
- `monitoring` - Logging and monitoring settings

**Configuration comments provide guidance:**
- Available options for each parameter
- Conservative vs aggressive recommendations
- Risk/reward trade-offs explained

## Running the System

**Simple Bot Execution:**
```bash
# Auto-discover and run first active config
uv run src/run_bot.py

# Run specific config
uv run src/run_bot.py bots/btc_conservative.yaml

# Validate configuration only
uv run src/run_bot.py --validate
```

**Configuration Management:**
```bash
# Check which config will be auto-discovered
ls bots/*.yaml

# Edit configuration
# Set active: true to enable, active: false to disable
```

## Development Patterns & Style

### Code Style
- **NO COMMENTS** in code unless explicitly requested
- Follow existing patterns in the codebase
- Use type hints consistently
- Keep functions focused and single-purpose

### Architecture Patterns
- **Interface-based design** - Clear separation between business logic and implementation
- **Dependency injection** - Adapters injected into strategies and engines
- **Event-driven** - WebSocket events trigger strategy decisions
- **Async/await** - Non-blocking I/O for real-time operations

### Error Handling
- Use custom exceptions from `utils/exceptions.py`
- Graceful degradation for network issues
- Comprehensive logging at appropriate levels
- Clean shutdown on signals (SIGINT, SIGTERM)

### Testing Requirements
- **Validate against Hyperliquid testnet** using provided test private key
- **Test all learning examples** ensure they work with real API responses
- **Configuration validation** verify all parameters actually work
- **Integration testing** test end-to-end trading workflows

### Debugging Guidelines
- Use appropriate log levels (DEBUG for troubleshooting, INFO for normal operations)
- Test with small position sizes on testnet
- Validate API responses contain expected data
- Check precision and tick size handling for order placement

## Learning Examples

**Standalone educational scripts in `learning_examples/` directory:**

- **Purpose**: Teach Hyperliquid API usage independent of the main bot
- **Structure**: Each script is self-contained with minimal dependencies
- **Documentation**: Comprehensive docstrings explaining SPOT vs PERPS modes
- **Testing**: All examples tested against real Hyperliquid testnet
- **Categories**:
  - WebSockets: Real-time data streams and price monitoring
  - Market Data: Price feeds and market information
  - Account Info: Balance and position queries
  - Trading: Order placement and management
  - Funding: Funding rates and spot/perp availability checks
  - Copy Trading: Order mirroring and automated copy trading strategies
  
  **Development style for learning examples**:
  - Place imports always at the top
  - Use short docstrings

**Usage:**
```bash
# Run any learning example directly
uv run learning_examples/01_websockets/realtime_prices.py
uv run learning_examples/02_market_data/get_all_prices.py
uv run learning_examples/04_trading/place_limit_order.py
uv run learning_examples/05_funding/get_funding_rates.py
uv run learning_examples/06_copy_trading/mirror_spot_orders.py
```

## Key Dependencies

- `hyperliquid-python-sdk>=0.20.0` - Main SDK for Hyperliquid integration
- `eth-account>=0.10.0` - Ethereum account management and signing
- `websockets` - Real-time WebSocket connections
- `pyyaml` - YAML configuration parsing
- `python-dotenv` - Environment variable management

## API Configuration

**Hyperliquid API Usage:**
- **Testnet**: `https://api.hyperliquid-testnet.xyz` (for development)
- **WebSocket**: `wss://api.hyperliquid-testnet.xyz/ws` (for real-time data)
- **Authentication**: Uses Ethereum private keys for transaction signing
- **Rate Limits**: Handled automatically by SDK and adapter layer

**Critical SDK Method Names:**
- `exchange.order()` - Place orders (NOT `limit_order()`)
- `exchange.cancel_order()` - Cancel orders
- `info.all_mids()` - Get all asset prices
- `info.open_orders()` - Get open orders

## Environment Setup

**Required Environment Variables:**
```bash
# .env file
HYPERLIQUID_TESTNET_PRIVATE_KEY=0x...  # For testnet trading
HYPERLIQUID_TESTNET=true               # Enable testnet mode
```

**Development Workflow:**
1. Set up environment variables
2. Test with learning examples first
3. Configure bot with small allocation percentages
4. Validate configuration with `--validate` flag
5. Test on testnet before any mainnet deployment

## Important Development Notes

- **Private Key Security**: Never commit private keys to git
- **Precision Handling**: BTC requires 5 decimal places, handle tick sizes properly
- **Order Status**: Check `result.status == "ok"` for successful operations
- **WebSocket Reliability**: Implement reconnection logic for production use
- **Risk Management**: Always use conservative settings for initial testing