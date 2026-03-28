#!/usr/bin/env python3
"""
Telegram notification helpers for the market hot/low notifier.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import requests


logger = logging.getLogger(__name__)


def load_env_file(*paths: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    env[key.strip()] = value.strip()
        except Exception as exc:
            logger.warning("Failed to read env file %s: %s", path, exc)
    return env


def resolve_telegram_credentials(base_dir: Optional[Path] = None) -> tuple[Optional[str], Optional[str]]:
    base_dir = base_dir or Path(__file__).resolve().parent
    env_values = load_env_file(
        base_dir / ".env",
        base_dir.parent / ".env",
        Path.home() / ".hermes" / ".env",
    )
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or env_values.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_HOME_CHANNEL") or env_values.get("TELEGRAM_HOME_CHANNEL")
    return token, chat_id


def send_telegram_message(
    message: str,
    *,
    base_dir: Optional[Path] = None,
    disable_notification: bool = False,
) -> bool:
    token, chat_id = resolve_telegram_credentials(base_dir)
    if not token or not chat_id:
        logger.info("Telegram credentials missing; skipping notification")
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_notification": disable_notification,
            },
            timeout=10,
        )
        response.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("Failed to send Telegram message: %s", exc)
        return False
