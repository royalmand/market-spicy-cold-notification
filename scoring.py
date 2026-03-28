#!/usr/bin/env python3
"""
Hardcoded scoring rules for the market hot/low notifier.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from typing import Any

from market_data import LaunchCandidate


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class MarketWindowConfig:
    candidate_limit: int = 60
    launch_lookback_minutes: int = 30
    min_liquidity_usd: float = 10000.0
    strong_volume_threshold_usd: float = 30000.0
    strong_txns_threshold: int = 80
    buy_pressure_ratio: float = 1.5
    buy_pressure_min_volume_usd: float = 20000.0
    interesting_volume_threshold_usd: float = 50000.0
    interesting_net_buys_threshold: int = 20
    meteora_volume_threshold_usd: float = 25000.0
    hot_score_threshold: int = 70
    normal_score_threshold: int = 35
    recommendation_interval_minutes: int = 15
    breakout_interval_minutes: int = 5
    breakout_min_delta: int = 12
    quiet_hours_between_same_classification_alerts: int = 2
    snapshot_history_limit: int = 5000

    @classmethod
    def from_sources(cls, config_dict: dict[str, Any] | None = None) -> "MarketWindowConfig":
        config_dict = config_dict or {}
        values = asdict(cls())
        values.update({key: value for key, value in config_dict.items() if key in values})
        values.update(
            {
                "candidate_limit": _env_int("MARKET_WINDOW_CANDIDATE_LIMIT", values["candidate_limit"]),
                "launch_lookback_minutes": _env_int("MARKET_WINDOW_LOOKBACK_MINUTES", values["launch_lookback_minutes"]),
                "min_liquidity_usd": _env_float("MARKET_WINDOW_MIN_LIQUIDITY_USD", values["min_liquidity_usd"]),
                "strong_volume_threshold_usd": _env_float("MARKET_WINDOW_STRONG_VOLUME_USD", values["strong_volume_threshold_usd"]),
                "strong_txns_threshold": _env_int("MARKET_WINDOW_STRONG_TXNS", values["strong_txns_threshold"]),
                "buy_pressure_ratio": _env_float("MARKET_WINDOW_BUY_PRESSURE_RATIO", values["buy_pressure_ratio"]),
                "buy_pressure_min_volume_usd": _env_float("MARKET_WINDOW_BUY_PRESSURE_MIN_VOLUME_USD", values["buy_pressure_min_volume_usd"]),
                "interesting_volume_threshold_usd": _env_float("MARKET_WINDOW_INTERESTING_VOLUME_USD", values["interesting_volume_threshold_usd"]),
                "interesting_net_buys_threshold": _env_int("MARKET_WINDOW_INTERESTING_NET_BUYS", values["interesting_net_buys_threshold"]),
                "meteora_volume_threshold_usd": _env_float("MARKET_WINDOW_METEORA_VOLUME_USD", values["meteora_volume_threshold_usd"]),
                "hot_score_threshold": _env_int("MARKET_WINDOW_HOT_SCORE", values["hot_score_threshold"]),
                "normal_score_threshold": _env_int("MARKET_WINDOW_NORMAL_SCORE", values["normal_score_threshold"]),
                "recommendation_interval_minutes": _env_int("MARKET_WINDOW_RECOMMENDATION_INTERVAL_MINUTES", values["recommendation_interval_minutes"]),
                "breakout_interval_minutes": _env_int("MARKET_WINDOW_BREAKOUT_INTERVAL_MINUTES", values["breakout_interval_minutes"]),
                "breakout_min_delta": _env_int("MARKET_WINDOW_BREAKOUT_MIN_DELTA", values["breakout_min_delta"]),
                "quiet_hours_between_same_classification_alerts": _env_int(
                    "MARKET_WINDOW_REPEAT_ALERT_HOURS",
                    values["quiet_hours_between_same_classification_alerts"],
                ),
                "snapshot_history_limit": _env_int("MARKET_WINDOW_HISTORY_LIMIT", values["snapshot_history_limit"]),
            }
        )
        return cls(**values)


@dataclass
class MarketWindowResult:
    classification: str
    score: int
    recommendation: str
    summary: str
    metrics: dict[str, Any]
    score_breakdown: dict[str, int]
    leaders: list[dict[str, Any]]
    assumptions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _candidate_to_leader(candidate: LaunchCandidate) -> dict[str, Any]:
    return {
        "symbol": candidate.symbol,
        "token_address": candidate.token_address,
        "dex_id": candidate.dex_id,
        "volume_m5": round(candidate.volume_m5, 2),
        "txns_m5_total": candidate.txns_m5_total,
        "net_buys": candidate.net_buys,
        "liquidity_usd": round(candidate.liquidity_usd, 2),
        "age_minutes": round(candidate.age_minutes, 1) if candidate.age_minutes is not None else None,
        "is_meteora": candidate.is_meteora,
        "is_dlmm_guess": candidate.is_dlmm_guess,
    }


def score_market_window(candidates: list[LaunchCandidate], config: MarketWindowConfig) -> MarketWindowResult:
    liquid_candidates = [item for item in candidates if item.liquidity_usd >= config.min_liquidity_usd]
    recent_launches = [
        item for item in candidates
        if item.age_minutes is not None and item.age_minutes <= config.launch_lookback_minutes
    ]
    strong_launches = [
        item for item in recent_launches
        if item.volume_m5 >= config.strong_volume_threshold_usd and item.txns_m5_total >= config.strong_txns_threshold
    ]
    buy_pressure_launches = [
        item for item in recent_launches
        if item.volume_m5 >= config.buy_pressure_min_volume_usd
        and item.txns_m5_sells >= 1
        and item.txns_m5_buys >= item.txns_m5_sells * config.buy_pressure_ratio
    ]
    interesting_tokens = [
        item for item in recent_launches
        if item.volume_m5 >= config.interesting_volume_threshold_usd
        and item.net_buys >= config.interesting_net_buys_threshold
    ]
    meteora_candidates = [
        item for item in recent_launches
        if item.is_meteora and item.volume_m5 >= config.meteora_volume_threshold_usd
    ]
    total_txns_m5 = sum(item.txns_m5_total for item in recent_launches)

    score_breakdown = {
        "recent_launches": min(len(recent_launches) * 3, 12),
        "strong_launches": min(len(strong_launches) * 12, 36),
        "tx_velocity": min((total_txns_m5 // 80) * 6, 18),
        "buy_pressure": min(len(buy_pressure_launches) * 8, 24),
        "meteora_relevance": min(len(meteora_candidates) * 10, 20),
        "interesting_tokens": min(len(interesting_tokens) * 12, 36),
    }
    score = min(sum(score_breakdown.values()), 100)

    if score >= config.hot_score_threshold:
        classification = "HOT"
        recommendation = "HOT market. Stay near screen. Do only 10-15 min walk."
    elif score >= config.normal_score_threshold:
        classification = "NORMAL"
        recommendation = "NORMAL market. 20-30 min exercise is okay."
    else:
        classification = "DEAD"
        recommendation = "DEAD market. Safe to leave for 45-60 min."

    reason_parts: list[str] = []
    if strong_launches:
        reason_parts.append(f"{len(strong_launches)} strong launches in last {config.launch_lookback_minutes}m")
    if interesting_tokens:
        reason_parts.append(f"{len(interesting_tokens)} tokens exceeded interesting threshold")
    if buy_pressure_launches:
        reason_parts.append(f"{len(buy_pressure_launches)} with clear buy pressure")
    if meteora_candidates:
        reason_parts.append(f"{len(meteora_candidates)} Meteora-relevant")
    if total_txns_m5:
        reason_parts.append(f"{total_txns_m5} total 5m txns")
    if not reason_parts:
        reason_parts.append("no meaningful launch momentum detected")

    leaders = sorted(
        recent_launches,
        key=lambda item: (item.volume_m5, item.net_buys, item.txns_m5_total),
        reverse=True,
    )[:5]

    assumptions = [
        "DexScreener latest Solana token profiles are the MVP launch universe.",
        "Meteora/DLMM relevance is inferred from DexScreener dexId and labels.",
        "Holder growth and wallet participation are intentionally omitted until a cheaper data source is added.",
    ]

    metrics = {
        "candidate_count": len(candidates),
        "liquid_candidate_count": len(liquid_candidates),
        "recent_launch_count": len(recent_launches),
        "strong_launch_count": len(strong_launches),
        "buy_pressure_count": len(buy_pressure_launches),
        "meteora_candidate_count": len(meteora_candidates),
        "interesting_count": len(interesting_tokens),
        "total_txns_m5": total_txns_m5,
    }

    return MarketWindowResult(
        classification=classification,
        score=score,
        recommendation=recommendation,
        summary=", ".join(reason_parts),
        metrics=metrics,
        score_breakdown=score_breakdown,
        leaders=[_candidate_to_leader(item) for item in leaders],
        assumptions=assumptions,
    )
