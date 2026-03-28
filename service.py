#!/usr/bin/env python3
"""
Solana market hot/low notification service.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from market_data import DexScreenerMarketData, LaunchCandidate
from notifications import send_telegram_message
from scoring import MarketWindowConfig, score_market_window
from storage import SnapshotStorage


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = BASE_DIR / "config.json"
LOG_PATH = DATA_DIR / "service.log"

DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def load_config() -> MarketWindowConfig:
    payload: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    return MarketWindowConfig.from_sources(payload)


def build_message(result) -> str:
    return "\n".join(
        [
            result.recommendation,
            f"Score: {result.score}/100 ({result.classification})",
            f"Reason: {result.summary}",
        ]
    )


def build_snapshot(fetched_at: str, run_type: str, result, candidates) -> dict[str, Any]:
    return {
        "fetched_at": fetched_at,
        "run_type": run_type,
        "result": result.to_dict(),
        "candidates": [candidate.to_dict() for candidate in candidates],
    }


def should_send_recommendation(state: dict[str, Any], result, config: MarketWindowConfig, now: datetime) -> bool:
    last_sent_at = parse_iso(state.get("last_recommendation_sent_at"))
    last_classification = state.get("last_recommendation_classification")
    if last_sent_at is None:
        return True
    if result.classification != last_classification:
        return True
    return now - last_sent_at >= timedelta(hours=config.quiet_hours_between_same_classification_alerts)


def should_send_breakout(state: dict[str, Any], result, config: MarketWindowConfig) -> bool:
    last_breakout_score = int(state.get("last_breakout_score", 0) or 0)
    last_breakout_classification = state.get("last_breakout_classification")
    if result.classification != "HOT":
        return False
    if last_breakout_classification != "HOT":
        return True
    return result.score - last_breakout_score >= config.breakout_min_delta


class MarketHotLowService:
    def __init__(self, config: MarketWindowConfig):
        self.config = config
        self.fetcher = DexScreenerMarketData()
        self.storage = SnapshotStorage(DATA_DIR, history_limit=config.snapshot_history_limit)
        self.state = self.storage.load_state()

    def run_cycle(self, *, run_type: str, send_notifications: bool = True) -> dict[str, Any]:
        fetched_at = iso_now()
        candidates = self.fetcher.fetch_candidates(limit=self.config.candidate_limit)
        result = score_market_window(candidates, self.config)
        snapshot = build_snapshot(fetched_at, run_type, result, candidates)
        self.storage.append_snapshot(snapshot)

        logger.info(
            "[%s] score=%s class=%s recent=%s strong=%s meteora=%s",
            run_type,
            result.score,
            result.classification,
            result.metrics["recent_launch_count"],
            result.metrics["strong_launch_count"],
            result.metrics["meteora_candidate_count"],
        )

        now = utc_now()
        message_sent = False
        if send_notifications:
            if run_type == "recommendation" and should_send_recommendation(self.state, result, self.config, now):
                message_sent = send_telegram_message(build_message(result), base_dir=BASE_DIR)
                self.state["last_recommendation_sent_at"] = fetched_at
                self.state["last_recommendation_classification"] = result.classification
                self.state["last_recommendation_score"] = result.score
            elif run_type == "breakout" and should_send_breakout(self.state, result, self.config):
                message_sent = send_telegram_message(
                    build_message(result) + "\nBreakout check: momentum accelerated since last check.",
                    base_dir=BASE_DIR,
                    disable_notification=True,
                )
                self.state["last_breakout_sent_at"] = fetched_at
                self.state["last_breakout_classification"] = result.classification
                self.state["last_breakout_score"] = result.score

        self.state["last_seen_at"] = fetched_at
        self.state["last_seen_score"] = result.score
        self.state["last_seen_classification"] = result.classification
        self.storage.save_state(self.state)

        snapshot["message_sent"] = message_sent
        return snapshot

    def run_forever(self) -> None:
        logger.info(
            "Starting market hot/low notifier | recommendation=%sm breakout=%sm",
            self.config.recommendation_interval_minutes,
            self.config.breakout_interval_minutes,
        )
        last_recommendation = datetime.min.replace(tzinfo=timezone.utc)
        last_breakout = datetime.min.replace(tzinfo=timezone.utc)

        while True:
            now = utc_now()
            try:
                if now - last_breakout >= timedelta(minutes=self.config.breakout_interval_minutes):
                    self.run_cycle(run_type="breakout", send_notifications=True)
                    last_breakout = now

                if now - last_recommendation >= timedelta(minutes=self.config.recommendation_interval_minutes):
                    self.run_cycle(run_type="recommendation", send_notifications=True)
                    last_recommendation = now
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                logger.exception("Notifier cycle failed: %s", exc)
            time.sleep(30)


def replay_snapshots(config: MarketWindowConfig, limit: int) -> int:
    storage = SnapshotStorage(DATA_DIR, history_limit=config.snapshot_history_limit)
    snapshots = storage.load_snapshots(limit=limit)
    if not snapshots:
        print("No snapshots found.")
        return 0

    print(f"Replaying {len(snapshots)} snapshots with current thresholds:\n")
    for snapshot in snapshots:
        restored = [LaunchCandidate(**candidate) for candidate in snapshot.get("candidates") or []]
        result = score_market_window(restored, config)
        print(
            f"{snapshot.get('fetched_at')} | "
            f"stored={snapshot.get('result', {}).get('classification')}:{snapshot.get('result', {}).get('score')} | "
            f"replayed={result.classification}:{result.score} | "
            f"{result.summary}"
        )
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Market hot/low Solana notifier")
    parser.add_argument("--once", action="store_true", help="Run one recommendation cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Run but do not send Telegram")
    parser.add_argument("--replay", type=int, metavar="N", help="Re-score the last N stored snapshots")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    config = load_config()

    if args.replay:
        return replay_snapshots(config, args.replay)

    service = MarketHotLowService(config)
    if args.once:
        snapshot = service.run_cycle(run_type="recommendation", send_notifications=not args.dry_run)
        result = snapshot["result"]
        print(f"{result['classification']} {result['score']}/100 | {result['recommendation']} | {result['summary']}")
        return 0

    service.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
