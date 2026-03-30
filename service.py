#!/usr/bin/env python3
"""
Solana market hot/low notification service.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any

from market_data import DexScreenerMarketData, LaunchCandidate
from notifications import send_telegram_message, validate_telegram_credentials
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


def build_message(result, state: dict[str, Any] | None = None, run_type: str = "recommendation") -> str:
    lines = [result.recommendation]

    # M1: score with delta and prior classification
    score_line = f"Score: {result.score} ({result.classification})"
    if state:
        prev_score_key = "last_recommendation_score" if run_type == "recommendation" else "last_breakout_score"
        prev_class_key = "last_recommendation_classification" if run_type == "recommendation" else "last_breakout_classification"
        prev_score = state.get(prev_score_key)
        prev_class = state.get(prev_class_key)
        if prev_score is not None and prev_class is not None:
            delta = result.score - int(prev_score)
            delta_str = f"+{delta}" if delta >= 0 else str(delta)
            score_line += f"  |  Was: {prev_score} ({prev_class}), {delta_str} pts"
    lines.append(score_line)

    # M3: per-metric signal breakdown (only non-zero buckets)
    metrics = result.metrics
    signal_parts = []
    if metrics.get("strong_launch_count", 0):
        signal_parts.append(f"strong\u00d7{metrics['strong_launch_count']}")
    if metrics.get("buy_pressure_count", 0):
        signal_parts.append(f"buy_pressure\u00d7{metrics['buy_pressure_count']}")
    if metrics.get("interesting_count", 0):
        signal_parts.append(f"interesting\u00d7{metrics['interesting_count']}")
    if metrics.get("meteora_candidate_count", 0):
        signal_parts.append(f"meteora\u00d7{metrics['meteora_candidate_count']}")
    if metrics.get("recent_launch_count", 0):
        signal_parts.append(f"recent\u00d7{metrics['recent_launch_count']}")
    if signal_parts:
        lines.append("Signals: " + ", ".join(signal_parts))

    lines.append(f"Reason: {result.summary}")

    # M2: top token names for HOT market
    if result.classification == "HOT" and result.leaders:
        token_parts = []
        for leader in result.leaders[:3]:
            sym = leader.get("symbol") or "?"
            vol = leader.get("volume_m5") or 0
            age = leader.get("age_minutes")
            age_str = f"{int(age)}m" if age is not None else "?"
            token_parts.append(f"{sym} (${vol / 1000:.0f}k, {age_str})")
        lines.append("Top tokens: " + ", ".join(token_parts))
        # L2: DexScreener deep-link for HOT alerts
        lines.append("https://dexscreener.com/solana?rankBy=trendingScoreH6&order=desc")

    # L1: fetch timestamp
    lines.append(f"Fetched: {utc_now().strftime('%H:%M')} UTC")

    return "\n".join(lines)


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
        self._shutdown = False
        self._last_successful_fetch_at: datetime | None = None
        self._health_alert_sent = False

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
                message_sent = send_telegram_message(
                    build_message(result, self.state, run_type="recommendation"),
                    base_dir=BASE_DIR,
                )
                self.state["last_recommendation_sent_at"] = fetched_at
                self.state["last_recommendation_classification"] = result.classification
                self.state["last_recommendation_score"] = result.score
            elif run_type == "breakout" and should_send_breakout(self.state, result, self.config):
                # BUG-05 fix: disable_notification=False so HOT breakouts are audible alerts
                message_sent = send_telegram_message(
                    build_message(result, self.state, run_type="breakout")
                    + "\nBreakout: momentum accelerated since last check.",
                    base_dir=BASE_DIR,
                    disable_notification=False,
                )
                self.state["last_breakout_sent_at"] = fetched_at
                self.state["last_breakout_classification"] = result.classification
                self.state["last_breakout_score"] = result.score

        self.state["last_seen_at"] = fetched_at
        self.state["last_seen_score"] = result.score
        self.state["last_seen_classification"] = result.classification
        self.storage.save_state(self.state)

        self._last_successful_fetch_at = utc_now()
        self._health_alert_sent = False  # reset suppression after a successful fetch

        snapshot["message_sent"] = message_sent
        return snapshot

    def _check_health(self, now: datetime) -> None:
        if self._last_successful_fetch_at is None:
            return
        threshold = timedelta(minutes=self.config.recommendation_interval_minutes * 2)
        gap = now - self._last_successful_fetch_at
        if gap >= threshold and not self._health_alert_sent:
            minutes_since = int(gap.total_seconds() // 60)
            msg = (
                f"Health alert: no successful market fetch in {minutes_since}m. "
                f"DexScreener may be unreachable or the bot has crashed."
            )
            logger.warning(msg)
            send_telegram_message(msg, base_dir=BASE_DIR, disable_notification=False)
            self._health_alert_sent = True

    def _register_signal_handlers(self) -> None:
        def _handle_signal(signum, frame):
            logger.info("Received signal %s — shutting down gracefully...", signum)
            self._shutdown = True

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

    def run_forever(self) -> None:
        self._register_signal_handlers()
        logger.info(
            "Starting market hot/low notifier | recommendation=%sm breakout=%sm",
            self.config.recommendation_interval_minutes,
            self.config.breakout_interval_minutes,
        )
        # BUG-04 fix: stagger initial timers so breakout and recommendation
        # don't both fire simultaneously on the very first loop tick.
        # Run recommendation immediately on start, then stagger breakout.
        now = utc_now()
        last_recommendation = now - timedelta(minutes=self.config.recommendation_interval_minutes)
        last_breakout = now  # breakout waits one full interval before first fire

        while not self._shutdown:
            now = utc_now()
            try:
                if now - last_breakout >= timedelta(minutes=self.config.breakout_interval_minutes):
                    self.run_cycle(run_type="breakout", send_notifications=True)
                    last_breakout = now

                if now - last_recommendation >= timedelta(minutes=self.config.recommendation_interval_minutes):
                    self.run_cycle(run_type="recommendation", send_notifications=True)
                    last_recommendation = now
            except Exception as exc:
                logger.exception("Notifier cycle failed: %s", exc)

            self._check_health(now)
            time.sleep(30)

        logger.info("Shutdown complete — state saved.")
        self.storage.save_state(self.state)


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

    if not args.dry_run and not validate_telegram_credentials(BASE_DIR):
        logger.error("Startup credential check failed. Set TELEGRAM_BOT_TOKEN and TELEGRAM_HOME_CHANNEL.")
        return 1

    service = MarketHotLowService(config)
    if args.once:
        snapshot = service.run_cycle(run_type="recommendation", send_notifications=not args.dry_run)
        result = snapshot["result"]
        # BUG-02 fix: don't show "/100" since max possible score is >100 due to additive buckets
        print(f"{result['classification']} score={result['score']} | {result['recommendation']} | {result['summary']}")
        return 0

    service.run_forever()
    return 0


# BUG-03 fix: entry point was present but argparse/main() were never wired in
if __name__ == "__main__":
    raise SystemExit(main())
