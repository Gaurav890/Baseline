from __future__ import annotations

import asyncio
from collections.abc import Iterable

from app.config import Settings, settings
from app.connectors.base import BaseConnector, ConnectorHealth
from app.connectors.fixture import FixtureConnector
from app.connectors.github import GitHubConnector
from app.connectors.http import SecureHttpClient
from app.connectors.sec_edgar import SecEdgarConnector
from app.connectors.snapshot import SourcePacketConnector
from app.connectors.tavily import TavilyConnector
from app.connectors.url_fetch import AllowedUrlConnector


class ConnectorRegistry:
    _ALIASES = {
        "sec": "sec_edgar",
        "edgar": "sec_edgar",
        "github_issues": "github",
        "web": "tavily",
        "snapshot": "source_packet",
    }

    def __init__(self, connectors: Iterable[BaseConnector] = ()) -> None:
        self._connectors: dict[str, BaseConnector] = {}
        for connector in connectors:
            self.register(connector)

    def register(self, connector: BaseConnector) -> None:
        if connector.name in self._connectors:
            raise ValueError(f"duplicate connector: {connector.name}")
        self._connectors[connector.name] = connector

    def get(self, name: str) -> BaseConnector:
        normalized = self._ALIASES.get(name.strip().lower(), name.strip().lower())
        try:
            return self._connectors[normalized]
        except KeyError as exc:
            raise KeyError(f"unknown connector: {name}") from exc

    def list(self, *, include_internal: bool = False) -> tuple[BaseConnector, ...]:
        return tuple(
            connector
            for connector in self._connectors.values()
            if include_internal or not connector.internal
        )

    def health(self, *, include_internal: bool = False) -> list[ConnectorHealth]:
        """Return cached state only; this method never makes network requests."""
        return [
            connector.health_snapshot()
            for connector in self.list(include_internal=include_internal)
        ]

    async def health_check(self, *, include_internal: bool = False) -> list[ConnectorHealth]:
        return list(
            await asyncio.gather(
                *(
                    connector.health_check()
                    for connector in self.list(include_internal=include_internal)
                )
            )
        )


def _http(
    domains: Iterable[str],
    config: Settings,
) -> SecureHttpClient:
    return SecureHttpClient(
        allowed_domains=domains,
        timeout_seconds=config.connector_timeout_seconds,
        max_response_bytes=config.connector_max_response_bytes,
        retries=config.connector_max_retries,
    )


def build_connector_registry(config: Settings = settings) -> ConnectorRegistry:
    connectors: list[BaseConnector] = [
        SecEdgarConnector(
            user_agent=config.sec_user_agent,
            requests_per_second=config.sec_requests_per_second,
            http=_http(("data.sec.gov",), config),
        ),
        TavilyConnector(
            api_key=config.tavily_api_key,
            http=_http(("api.tavily.com",), config),
        ),
        GitHubConnector(
            token=config.github_token,
            http=_http(("api.github.com",), config),
        ),
        AllowedUrlConnector(
            http=(
                _http(config.connector_allowed_domains, config)
                if config.connector_allowed_domains
                else None
            )
        ),
        SourcePacketConnector(),
    ]
    if config.fixture_connectors and config.environment.lower() == "test":
        connectors.append(FixtureConnector())
    return ConnectorRegistry(connectors)


connector_registry = build_connector_registry()
