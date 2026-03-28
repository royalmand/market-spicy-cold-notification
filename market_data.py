#!/usr/bin/env python3
"""
Lightweight DexScreener fetcher for launch-window signals.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import logging
from typing import Iterable

import requests


logger = logging.getLogger(__name__)

DEX_TOKEN_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
DEX_TOKEN_BATCH_URL = "https://api.dexscreener.com/tokens/v1/solana/{addresses}"


@dataclass
class LaunchCandidate:
    token_address: str
    symbol: str
    name: str
    dex_id: str
    pair_address: str
    pair_created_at: int | None
    age_minutes: float | None
    volume_m5: float
    volume_h1: float
    txns_m5_buys: int
    txns_m5_sells: int
    price_change_m5: float
    liquidity_usd: float
    market_cap: float
    boosts_active: int
    labels: list[str]
    is_meteora: bool
    is_dlmm_guess: bool

    @property
    def txns_m5_total(self) -> int:
        return self.txns_m5_buys + self.txns_m5_sells

    @property
    def net_buys(self) -> int:
        return self.txns_m5_buys - self.txns_m5_sells

    def to_dict(self) -> dict:
        return asdict(self)


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _safe_float(value) -> float:
    try:
        if value in (None, "", "N/A"):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value) -> int:
    try:
        if value in (None, "", "N/A"):
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _select_best_pair(pairs: list[dict]) -> dict | None:
    if not pairs:
        return None
    return sorted(
        pairs,
        key=lambda pair: (
            _safe_float((pair.get("liquidity") or {}).get("usd")),
            _safe_float((pair.get("volume") or {}).get("m5")),
        ),
        reverse=True,
    )[0]


class DexScreenerMarketData:
    def __init__(self, timeout_seconds: int = 10):
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()

    def fetch_latest_solana_token_addresses(self, limit: int) -> list[str]:
        response = self.session.get(DEX_TOKEN_PROFILES_URL, timeout=self.timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            logger.warning("Unexpected token profile payload: %s", type(payload))
            return []

        addresses: list[str] = []
        seen: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            if item.get("chainId") != "solana":
                continue
            address = item.get("tokenAddress")
            if not address or address in seen:
                continue
            seen.add(address)
            addresses.append(address)
            if len(addresses) >= limit:
                break
        return addresses

    def fetch_candidates(self, limit: int = 60) -> list[LaunchCandidate]:
        addresses = self.fetch_latest_solana_token_addresses(limit)
        if not addresses:
            return []

        candidates: list[LaunchCandidate] = []
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)

        for chunk in _chunks(addresses, 30):
            response = self.session.get(
                DEX_TOKEN_BATCH_URL.format(addresses=",".join(chunk)),
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                logger.warning("Unexpected token batch payload: %s", type(payload))
                continue

            grouped: dict[str, list[dict]] = {}
            for pair in payload:
                if not isinstance(pair, dict):
                    continue
                base_token = pair.get("baseToken") or {}
                address = base_token.get("address")
                if address:
                    grouped.setdefault(address, []).append(pair)

            for address, pairs in grouped.items():
                pair = _select_best_pair(pairs)
                if not pair:
                    continue

                created_at = pair.get("pairCreatedAt")
                age_minutes = None
                if isinstance(created_at, (int, float)) and created_at > 0:
                    age_minutes = max(0.0, (now_ms - float(created_at)) / 60000.0)

                txns_m5 = (pair.get("txns") or {}).get("m5") or {}
                labels = [str(label) for label in (pair.get("labels") or [])]
                dex_id = str(pair.get("dexId") or "")
                label_values = [label.lower() for label in labels]
                dex_lower = dex_id.lower()

                base_token = pair.get("baseToken") or {}
                candidates.append(
                    LaunchCandidate(
                        token_address=address,
                        symbol=str(base_token.get("symbol") or address[:6]),
                        name=str(base_token.get("name") or address[:6]),
                        dex_id=dex_id,
                        pair_address=str(pair.get("pairAddress") or ""),
                        pair_created_at=created_at if isinstance(created_at, int) else None,
                        age_minutes=age_minutes,
                        volume_m5=_safe_float((pair.get("volume") or {}).get("m5")),
                        volume_h1=_safe_float((pair.get("volume") or {}).get("h1")),
                        txns_m5_buys=_safe_int(txns_m5.get("buys")),
                        txns_m5_sells=_safe_int(txns_m5.get("sells")),
                        price_change_m5=_safe_float((pair.get("priceChange") or {}).get("m5")),
                        liquidity_usd=_safe_float((pair.get("liquidity") or {}).get("usd")),
                        market_cap=_safe_float(pair.get("marketCap") or pair.get("fdv")),
                        boosts_active=_safe_int((pair.get("boosts") or {}).get("active")),
                        labels=labels,
                        is_meteora="meteora" in dex_lower,
                        is_dlmm_guess=("meteora" in dex_lower) or any("dlmm" in label for label in label_values),
                    )
                )

        candidates.sort(
            key=lambda item: (
                item.age_minutes if item.age_minutes is not None else 10**9,
                -item.volume_m5,
            )
        )
        return candidates[:limit]
