"""
Hyperliquid Endpoint Router

Smart routing system for Hyperliquid API endpoints with automatic fallback.
Supports multiple providers (public, Chainstack) with method-specific routing.
"""

import os
import re
import asyncio
import time
import logging
from typing import Dict, List, Optional, Set, Tuple, Callable, Any
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit, urlunsplit

import httpx


_TOKEN_SEG = re.compile(r"^[A-Za-z0-9_-]{16,}$")


def redact_address(addr: Optional[str]) -> str:
    """Shorten a hex address to ``0xXXXX…YYYY`` for safe logging."""
    if not addr:
        return ""
    s = str(addr)
    if s.startswith("0x") and len(s) > 12:
        return f"{s[:6]}…{s[-4:]}"
    if len(s) > 10:
        return f"{s[:4]}…{s[-4:]}"
    return s


def redact_url(url: str) -> str:
    """Replace API-key-looking path segments with ``***`` for safe logging.

    Long alphanumeric segments (16+ chars) are treated as secrets;
    short route names like ``info`` / ``exchange`` / ``evm`` are kept.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.path:
        return url
    redacted = [
        "***" if _TOKEN_SEG.match(seg) else seg for seg in parts.path.split("/")
    ]
    return urlunsplit(
        (parts.scheme, parts.netloc, "/".join(redacted), parts.query, parts.fragment)
    )


class EndpointType(Enum):
    """Types of Hyperliquid endpoints"""

    INFO = "info"  # Read-only data (market data, user info)
    EXCHANGE = "exchange"  # Trading operations (place/cancel orders)
    WEBSOCKET = "websocket"  # Real-time data streams
    EVM = "evm"  # HyperEVM JSON-RPC calls


class Provider(Enum):
    """Endpoint providers"""

    PUBLIC = "public"
    CHAINSTACK = "chainstack"


@dataclass
class EndpointConfig:
    """Configuration for a specific endpoint"""

    url: str
    provider: Provider
    endpoint_type: EndpointType
    priority: int
    testnet: bool = False
    is_healthy: bool = True
    last_health_check: float = 0


class HyperliquidEndpointRouter:
    """
    Smart endpoint router for Hyperliquid API calls

    Features:
    - Method-specific routing based on compatibility matrix
    - Automatic fallback on failures
    - Periodic health monitoring
    - Environment-based configuration
    """

    # Static compatibility matrix - which methods work with which endpoint types
    METHOD_COMPATIBILITY = {
        # Info API methods - work with both public and Chainstack
        "all_mids": [EndpointType.INFO],
        "user_state": [EndpointType.INFO],
        "spot_user_state": [EndpointType.INFO],
        "clearinghouse_state": [EndpointType.INFO],
        "spot_clearinghouse_state": [EndpointType.INFO],
        "open_orders": [EndpointType.INFO],
        "frontend_open_orders": [EndpointType.INFO],
        "meta": [EndpointType.INFO],
        "meta_and_asset_ctxs": [EndpointType.INFO],
        "spot_meta": [EndpointType.INFO],
        "spot_meta_and_asset_ctxs": [EndpointType.INFO],
        "candles": [EndpointType.INFO],
        "candles_snapshot": [EndpointType.INFO],
        "user_fills": [EndpointType.INFO],
        "user_funding": [EndpointType.INFO],
        "user_non_funding_ledger_updates": [EndpointType.INFO],
        "user_rate_limits": [EndpointType.INFO],
        "extra_agents": [EndpointType.INFO],
        # HIP-3 (Oct 2025) and dex abstraction (SDK 0.20.0)
        "perp_dexs": [EndpointType.INFO],
        "all_dexs_clearinghouse_state": [EndpointType.INFO],
        "all_dexs_asset_ctxs": [EndpointType.INFO],
        "twap_states": [EndpointType.INFO],
        "predicted_fundings": [EndpointType.INFO],
        # Exchange API methods - ONLY work with public endpoints (auth required)
        "place_order": [EndpointType.EXCHANGE],
        "bulk_orders": [EndpointType.EXCHANGE],
        "cancel_order": [EndpointType.EXCHANGE],
        "bulk_cancel": [EndpointType.EXCHANGE],
        "modify_order": [EndpointType.EXCHANGE],
        "bulk_modify_orders_new": [EndpointType.EXCHANGE],
        "market_open": [EndpointType.EXCHANGE],
        "market_close": [EndpointType.EXCHANGE],
        "update_leverage": [EndpointType.EXCHANGE],
        "transfer": [EndpointType.EXCHANGE],
        "send_asset": [EndpointType.EXCHANGE],
        "withdraw": [EndpointType.EXCHANGE],
        "approve_agent": [EndpointType.EXCHANGE],
        # HyperEVM methods - work better with Chainstack (no rate limits)
        "eth_getBalance": [EndpointType.EVM],
        "eth_call": [EndpointType.EVM],
        "eth_blockNumber": [EndpointType.EVM],
        "eth_chainId": [EndpointType.EVM],
        "eth_getLogs": [EndpointType.EVM],
        "eth_getBlockByNumber": [EndpointType.EVM],
        "eth_getTransactionReceipt": [EndpointType.EVM],
        # WebSocket subscriptions (2025-2026)
        "subscribe_price": [EndpointType.WEBSOCKET],  # allMids
        "subscribe_bbo": [EndpointType.WEBSOCKET],  # SDK 0.15+
        "subscribe_active_asset_ctx": [EndpointType.WEBSOCKET],  # SDK 0.16+
        "subscribe_active_asset_data": [EndpointType.WEBSOCKET],  # SDK 0.16+
        "subscribe_user_twap_slice_fills": [EndpointType.WEBSOCKET],
        "subscribe_user_twap_history": [EndpointType.WEBSOCKET],
        "subscribe_web_data3": [EndpointType.WEBSOCKET],  # supersedes webData2
        "subscribe_all_dexs_clearinghouse_state": [EndpointType.WEBSOCKET],
        "subscribe_all_dexs_asset_ctxs": [EndpointType.WEBSOCKET],
        "subscribe_fills": [EndpointType.WEBSOCKET],  # userFills
        "subscribe_orders": [EndpointType.WEBSOCKET],  # orderUpdates
        "subscribe_user_events": [EndpointType.WEBSOCKET],
    }

    # Methods known to be unsupported on a given provider for a given network.
    # Probed against Chainstack testnet 2026-05-11: this subset of /info methods
    # returns HTTP 422 upstream. Public endpoint handles them.
    PROVIDER_INCOMPATIBLE_METHODS: Dict[Tuple["Provider", bool], Set[str]] = {
        (Provider.CHAINSTACK, True): {
            "all_mids",
            "user_state",
            "l2_book",
            "meta_and_asset_ctxs",
            "spot_meta_and_asset_ctxs",
            "predicted_fundings",
            "all_dexs_asset_ctxs",
            "candles",
            "candles_snapshot",
        },
        (Provider.CHAINSTACK, False): set(),
    }

    # Provider priority for each endpoint type (first = preferred)
    PROVIDER_PRIORITIES = {
        EndpointType.INFO: [
            Provider.CHAINSTACK,
            Provider.PUBLIC,
        ],  # Prefer Chainstack for data
        EndpointType.EXCHANGE: [Provider.PUBLIC],  # MUST use public for trading
        EndpointType.EVM: [
            Provider.CHAINSTACK,
            Provider.PUBLIC,
        ],  # Prefer Chainstack for EVM
        EndpointType.WEBSOCKET: [
            Provider.CHAINSTACK,
            Provider.PUBLIC,
        ],  # Prefer Chainstack for WS
    }

    def __init__(self, testnet: bool = True):
        self.testnet = testnet
        self.endpoints: List[EndpointConfig] = []
        self.health_check_interval = int(
            os.getenv("ENDPOINT_HEALTH_CHECK_INTERVAL", "300")
        )
        self.health_check_timeout = int(
            os.getenv("ENDPOINT_HEALTH_CHECK_TIMEOUT", "10")
        )
        self.logger = logging.getLogger(__name__)

        # Load endpoint configurations from environment
        self._load_endpoints_from_env()

        # Health monitoring will be started lazily when needed
        self._health_monitor_started = False

    def _load_endpoints_from_env(self) -> None:
        """Load endpoint configurations from environment variables"""

        env_prefix = "HYPERLIQUID_TESTNET_" if self.testnet else "HYPERLIQUID_"

        # Define endpoint mappings
        endpoint_mappings = [
            # (env_suffix, provider, endpoint_type)
            ("PUBLIC_INFO_URL", Provider.PUBLIC, EndpointType.INFO),
            ("PUBLIC_EXCHANGE_URL", Provider.PUBLIC, EndpointType.EXCHANGE),
            ("PUBLIC_WS_URL", Provider.PUBLIC, EndpointType.WEBSOCKET),
            ("PUBLIC_EVM_URL", Provider.PUBLIC, EndpointType.EVM),
            ("CHAINSTACK_INFO_URL", Provider.CHAINSTACK, EndpointType.INFO),
            ("CHAINSTACK_EVM_URL", Provider.CHAINSTACK, EndpointType.EVM),
            ("CHAINSTACK_WS_URL", Provider.CHAINSTACK, EndpointType.WEBSOCKET),
        ]

        for env_suffix, provider, endpoint_type in endpoint_mappings:
            url = os.getenv(f"{env_prefix}{env_suffix}")
            if url:
                priority_key = f"{env_prefix}{env_suffix.replace('_URL', '_PRIORITY')}"
                priority = int(os.getenv(priority_key, "5"))

                config = EndpointConfig(
                    url=url,
                    provider=provider,
                    endpoint_type=endpoint_type,
                    priority=priority,
                    testnet=self.testnet,
                )

                self.endpoints.append(config)
                self.logger.debug(
                    f"Loaded {provider.value} {endpoint_type.value} endpoint: {redact_url(url)}"
                )

        if not self.endpoints:
            self.logger.warning("No endpoints configured! Using defaults.")
            self._load_default_endpoints()

    def _load_default_endpoints(self) -> None:
        """Load default public endpoints as fallback"""

        if self.testnet:
            defaults = [
                (
                    "https://api.hyperliquid-testnet.xyz/info",
                    Provider.PUBLIC,
                    EndpointType.INFO,
                ),
                (
                    "https://api.hyperliquid-testnet.xyz/exchange",
                    Provider.PUBLIC,
                    EndpointType.EXCHANGE,
                ),
                (
                    "wss://api.hyperliquid-testnet.xyz/ws",
                    Provider.PUBLIC,
                    EndpointType.WEBSOCKET,
                ),
                (
                    "https://rpc.hyperliquid-testnet.xyz/evm",
                    Provider.PUBLIC,
                    EndpointType.EVM,
                ),
            ]
        else:
            defaults = [
                (
                    "https://api.hyperliquid.xyz/info",
                    Provider.PUBLIC,
                    EndpointType.INFO,
                ),
                (
                    "https://api.hyperliquid.xyz/exchange",
                    Provider.PUBLIC,
                    EndpointType.EXCHANGE,
                ),
                (
                    "wss://api.hyperliquid.xyz/ws",
                    Provider.PUBLIC,
                    EndpointType.WEBSOCKET,
                ),
                ("https://rpc.hyperliquid.xyz/evm", Provider.PUBLIC, EndpointType.EVM),
            ]

        for url, provider, endpoint_type in defaults:
            config = EndpointConfig(
                url=url,
                provider=provider,
                endpoint_type=endpoint_type,
                priority=5,  # Default priority
                testnet=self.testnet,
            )
            self.endpoints.append(config)

    def get_endpoint_for_method(self, method_name: str) -> Optional[str]:
        """
        Get the best available endpoint for a specific method

        Args:
            method_name: The API method name (e.g., 'user_state', 'place_order')

        Returns:
            The endpoint URL to use, or None if no compatible endpoint is available
        """

        # Start health monitoring if not already started
        self._ensure_health_monitoring()

        # Find which endpoint types support this method
        compatible_types = self.METHOD_COMPATIBILITY.get(method_name, [])
        if not compatible_types:
            self.logger.warning(f"Unknown method: {method_name}")
            return None

        # For each endpoint type, try to find the best provider
        for endpoint_type in compatible_types:
            endpoint = self._get_best_endpoint(endpoint_type, method_name)
            if endpoint:
                self.logger.debug(
                    f"Routing {method_name} to {endpoint.provider.value} {endpoint.endpoint_type.value}"
                )
                return endpoint.url

        self.logger.error(f"No healthy endpoints available for method: {method_name}")
        return None

    def get_info_routing(
        self,
    ) -> Optional[Tuple[EndpointConfig, Optional[EndpointConfig], Set[str]]]:
        """Resolve INFO routing as ``(primary, fallback, primary_unsupported)``.

        ``primary`` is the preferred INFO endpoint (typically Chainstack when
        configured); ``fallback`` is a different-provider endpoint to use for
        methods listed in ``primary_unsupported``. Returns ``None`` if no INFO
        endpoint is configured at all.
        """
        info_endpoints = [
            ep
            for ep in self.endpoints
            if ep.endpoint_type == EndpointType.INFO and ep.is_healthy
        ]
        if not info_endpoints:
            return None

        priorities = self.PROVIDER_PRIORITIES.get(EndpointType.INFO, [])

        def sort_key(ep: EndpointConfig) -> Tuple[int, int]:
            provider_idx = (
                priorities.index(ep.provider) if ep.provider in priorities else 999
            )
            return (provider_idx, ep.priority)

        info_endpoints.sort(key=sort_key)
        primary = info_endpoints[0]
        fallback = next(
            (ep for ep in info_endpoints[1:] if ep.provider != primary.provider),
            None,
        )
        unsupported = self.PROVIDER_INCOMPATIBLE_METHODS.get(
            (primary.provider, primary.testnet), set()
        )
        return primary, fallback, unsupported

    def _get_best_endpoint(
        self,
        endpoint_type: EndpointType,
        method_name: Optional[str] = None,
    ) -> Optional[EndpointConfig]:
        """Get the best available endpoint for a specific type"""

        def supports_method(ep: EndpointConfig) -> bool:
            if method_name is None:
                return True
            blocked = self.PROVIDER_INCOMPATIBLE_METHODS.get(
                (ep.provider, ep.testnet), set()
            )
            return method_name not in blocked

        # Filter endpoints by type, health, and per-method compatibility
        candidates = [
            ep
            for ep in self.endpoints
            if ep.endpoint_type == endpoint_type
            and ep.is_healthy
            and supports_method(ep)
        ]

        if not candidates:
            # If no healthy endpoints, try unhealthy ones as last resort
            candidates = [
                ep
                for ep in self.endpoints
                if ep.endpoint_type == endpoint_type and supports_method(ep)
            ]
            if candidates:
                self.logger.warning(
                    f"Using potentially unhealthy endpoint for {endpoint_type.value}"
                )

        if not candidates:
            return None

        # Sort by provider priority first, then endpoint priority
        provider_priorities = self.PROVIDER_PRIORITIES.get(endpoint_type, [])

        def sort_key(endpoint: EndpointConfig) -> Tuple[int, int]:
            # Lower numbers = higher priority
            provider_priority = (
                provider_priorities.index(endpoint.provider)
                if endpoint.provider in provider_priorities
                else 999
            )
            return (provider_priority, endpoint.priority)

        candidates.sort(key=sort_key)
        return candidates[0]

    def _ensure_health_monitoring(self) -> None:
        """Ensure health monitoring is started (lazy initialization)"""
        if not self._health_monitor_started:
            try:
                # Try to start health monitoring if we have an event loop
                loop = asyncio.get_running_loop()
                self._start_health_monitoring()
                self._health_monitor_started = True
            except RuntimeError:
                # No event loop running, monitoring will start later
                pass

    def _start_health_monitoring(self) -> None:
        """Start periodic health monitoring of endpoints"""

        async def health_monitor():
            while True:
                await self._check_all_endpoints_health()
                await asyncio.sleep(self.health_check_interval)

        # Start monitoring task
        asyncio.create_task(health_monitor())
        self.logger.info(
            f"Started endpoint health monitoring (interval: {self.health_check_interval}s)"
        )

    async def _check_all_endpoints_health(self) -> None:
        """Check health of all configured endpoints"""

        tasks = []
        for endpoint in self.endpoints:
            if time.time() - endpoint.last_health_check > self.health_check_interval:
                tasks.append(self._check_endpoint_health(endpoint))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_endpoint_health(self, endpoint: EndpointConfig) -> None:
        """Check health of a specific endpoint"""

        endpoint.last_health_check = time.time()

        try:
            # Skip WebSocket health checks for now (would need different logic)
            if endpoint.endpoint_type == EndpointType.WEBSOCKET:
                endpoint.is_healthy = True
                return

            async with httpx.AsyncClient(timeout=self.health_check_timeout) as client:
                # Different health check methods for different endpoint types
                if endpoint.endpoint_type == EndpointType.INFO:
                    response = await client.post(
                        endpoint.url,
                        json={"type": "meta"},
                        headers={"Content-Type": "application/json"},
                    )
                elif endpoint.endpoint_type == EndpointType.EVM:
                    # eth_chainId verifies HyperEVM mainnet (999) or testnet
                    # (998) — wrong chain id under EVM endpoint is fatal.
                    response = await client.post(
                        endpoint.url,
                        json={
                            "jsonrpc": "2.0",
                            "method": "eth_chainId",
                            "params": [],
                            "id": 1,
                        },
                        headers={"Content-Type": "application/json"},
                    )
                    if response.status_code == 200:
                        try:
                            chain_id_hex = response.json().get("result", "0x0")
                            chain_id = int(chain_id_hex, 16)
                            expected = 998 if endpoint.testnet else 999
                            if chain_id != expected:
                                self.logger.error(
                                    f"EVM endpoint {endpoint.url} returned "
                                    f"chainId={chain_id}, expected {expected}"
                                )
                                endpoint.is_healthy = False
                                return
                        except (ValueError, KeyError, TypeError):
                            endpoint.is_healthy = False
                            return
                elif endpoint.endpoint_type == EndpointType.EXCHANGE:
                    # Can't really health check exchange endpoint without auth
                    # Just assume it's healthy if configured
                    endpoint.is_healthy = True
                    return
                else:
                    endpoint.is_healthy = True
                    return

                endpoint.is_healthy = response.status_code == 200

        except Exception as e:
            self.logger.debug(
                f"Health check failed for {endpoint.provider.value} {endpoint.endpoint_type.value}: {e}"
            )
            endpoint.is_healthy = False

    def get_status(self) -> Dict[str, Any]:
        """Get status of all endpoints"""

        status = {"testnet": self.testnet, "endpoints": []}

        for endpoint in self.endpoints:
            status["endpoints"].append(
                {
                    "provider": endpoint.provider.value,
                    "type": endpoint.endpoint_type.value,
                    "url": endpoint.url[:50] + "..."
                    if len(endpoint.url) > 50
                    else endpoint.url,
                    "priority": endpoint.priority,
                    "healthy": endpoint.is_healthy,
                    "last_check": endpoint.last_health_check,
                }
            )

        return status


# Global router instances
_mainnet_router: Optional[HyperliquidEndpointRouter] = None
_testnet_router: Optional[HyperliquidEndpointRouter] = None


def get_endpoint_router(testnet: bool = True) -> HyperliquidEndpointRouter:
    """Get singleton endpoint router instance"""

    global _mainnet_router, _testnet_router

    if testnet:
        if _testnet_router is None:
            _testnet_router = HyperliquidEndpointRouter(testnet=True)
        return _testnet_router
    else:
        if _mainnet_router is None:
            _mainnet_router = HyperliquidEndpointRouter(testnet=False)
        return _mainnet_router
