# Market Hot Low Notification

Practical MVP service for your Solana memecoin workflow.

What it does:
- polls DexScreener on a schedule
- measures recent launch momentum with hardcoded rules
- classifies the market window as `HOT`, `NORMAL`, or `DEAD`
- sends a simple Telegram recommendation
- stores JSONL history for later threshold tuning

## Logic

This service is a simple rule-based market thermometer.

Every cycle it:
- fetches a recent Solana token candidate set from DexScreener
- enriches each token with pair-level metrics
- computes a hardcoded market opportunity score
- classifies the current window as `HOT`, `NORMAL`, or `DEAD`
- sends a short Telegram recommendation with a reason summary

The candidate universe:
- DexScreener latest Solana token profiles are used as the MVP proxy for recent launches

For each candidate, the service looks at:
- pair age in minutes
- 5-minute volume
- 1-hour volume
- 5-minute buy count
- 5-minute sell count
- 5-minute price change
- liquidity
- market cap
- whether the pair appears Meteora-related

The score is built from six explainable buckets:
- `recent_launches`: number of recent launches inside the lookback window
- `strong_launches`: launches with strong 5-minute volume and strong 5-minute txn count
- `tx_velocity`: total 5-minute transactions across recent launches
- `buy_pressure`: launches where buys clearly outweigh sells
- `meteora_relevance`: recent launches whose pair metadata suggests Meteora / DLMM relevance
- `interesting_tokens`: launches that exceed the custom interesting threshold

Default scoring thresholds from `config.json`:
- lookback window: `30m`
- strong volume threshold: `$30,000`
- strong txns threshold: `80`
- buy pressure ratio: `1.5x`
- interesting volume threshold: `$50,000`
- interesting net buys threshold: `20`
- Meteora relevance threshold: `$25,000` 5m volume

Classification thresholds:
- `HOT` if score `>= 70`
- `NORMAL` if score `>= 35`
- `DEAD` if score `< 35`

Recommendation outputs:
- `HOT market. Stay near screen. Do only 10-15 min walk.`
- `NORMAL market. 20-30 min exercise is okay.`
- `DEAD market. Safe to leave for 45-60 min.`

Scheduling defaults:
- recommendation cycle every `15` minutes
- optional breakout check every `5` minutes
- repeated same-class alerts are rate-limited by a quiet period

Persistence:
- every run stores raw candidates and scoring output in `data/history.jsonl`
- latest service state is stored in `data/state.json`
- replay mode rescans stored history using your current thresholds

Important MVP assumptions:
- no AI is used in the core logic
- no webhook setup is required
- holder growth and wallet participation are not included yet
- Meteora / DLMM relevance is inferred from DexScreener metadata, not direct pool crawling
- the model is intentionally cheap, simple, and easy to tune

Main commands:

```bash
python market-hot-low-notification/service.py --once
python market-hot-low-notification/service.py
python market-hot-low-notification/service.py --replay 20
```

## VPS setup

Upload the folder from your local machine to the VPS:

```bash
scp -r "C:\Users\User\Desktop\mi-boti-agent\market-hot-low-notification" root@YOUR_VPS_IP:/root/
```

SSH into the VPS:

```bash
ssh root@YOUR_VPS_IP
```

Install Python deps if needed:

```bash
python3 -m pip install requests
```

Add Telegram credentials.
This service reads `TELEGRAM_BOT_TOKEN` and `TELEGRAM_HOME_CHANNEL` from:
- `market-hot-low-notification/.env`
- repo root `.env`
- `~/.hermes/.env`

Simple option: create a local `.env` inside the folder:

```bash
cd /root/market-hot-low-notification
nano .env
```

Example `.env`:

```bash
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_HOME_CHANNEL=your_chat_id
```

Run one dry check first:

```bash
cd /root/market-hot-low-notification
python3 service.py --once --dry-run
```

Run one live recommendation:

```bash
cd /root/market-hot-low-notification
python3 service.py --once
```

## Run in screen

Start the notifier in a detached `screen` session:

```bash
cd /root/market-hot-low-notification
screen -dmS market-window python3 service.py
```

See active screen sessions:

```bash
screen -ls
```

Attach to the running session:

```bash
screen -r market-window
```

Detach without stopping it:

```bash
Ctrl+A then D
```

Stop the service:

```bash
screen -r market-window
Ctrl+C
exit
```

If you want to restart cleanly:

```bash
screen -S market-window -X quit
cd /root/market-hot-low-notification
screen -dmS market-window python3 service.py
```

Replay stored history on VPS:

```bash
cd /root/market-hot-low-notification
python3 service.py --replay 20
```

Telegram credentials:
- reads `TELEGRAM_BOT_TOKEN` and `TELEGRAM_HOME_CHANNEL`
- looks in local `.env`, repo-root `.env`, or `~/.hermes/.env`

Example output:
- `HOT market. Stay near screen. Do only 10-15 min walk.`
- `NORMAL market. 20-30 min exercise is okay.`
- `DEAD market. Safe to leave for 45-60 min.`

Files:
- `service.py` scheduler and orchestration
- `market_data.py` DexScreener fetch logic
- `scoring.py` hardcoded opportunity formula
- `storage.py` JSONL persistence and replay support
- `notifications.py` Telegram send helper
- `data/history.jsonl` local score history
- `data/state.json` last sent state

Assumptions:
- DexScreener latest Solana token profiles are used as the candidate universe
- Meteora/DLMM relevance is inferred from DexScreener pair metadata
- holder growth and wallet participation are intentionally left out for MVP simplicity
