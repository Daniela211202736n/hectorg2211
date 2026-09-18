#!/usr/bin/env python3
"""Command-line entry point for the trading bot.

    python cli.py start                          # dry-run/live per .env, foreground
    python cli.py status                         # one-shot status snapshot
    python cli.py pause                           # hold new entries (running bot picks it up)
    python cli.py resume                          # resume after pause
    python cli.py stop                            # graceful shutdown (running bot picks it up)

`start` blocks in the foreground and is meant to be left running in its own
terminal/tmux/screen session/service. `pause`, `resume`, `stop` and `status`
are separate, quick invocations from another terminal -- see PROMPT.md for
how to background it properly on your OS.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from bot.config import AppConfig, ConfigError  # noqa: E402
from bot.core.control import ControlChannel, ControlCommand  # noqa: E402
from bot.core.engine import TradingEngine  # noqa: E402
from bot.core.state import StateStore  # noqa: E402
from bot.logging_setup import setup_logging  # noqa: E402


def _resolve_state_dir(config: AppConfig) -> Path:
    d = Path(config.env.state_dir)
    return d if d.is_absolute() else PROJECT_ROOT / d


def _load_config(args: argparse.Namespace):
    return AppConfig.load(env_file=args.env_file, settings_path=args.settings)


def _build_exchange(config: AppConfig, logger):
    """LIVE -> the real, authenticated Hyperliquid client.
    DRY_RUN -> a simulated client fed by real public market data when
    reachable, falling back to a clearly-labelled synthetic feed if not."""
    from bot.exchange.dry_run import DryRunExchangeClient

    if config.env.is_live:
        from bot.exchange.hyperliquid_client import HyperliquidClient
        return HyperliquidClient(
            private_key=config.env.private_key,
            network=config.env.network,
            account_address=config.env.account_address,
        )

    from bot.exchange.hyperliquid_client import PublicMarketData
    candle_provider = None
    try:
        probe = PublicMarketData(network=config.env.network)
        probe.get_candles(config.strategy.market.symbols[0], config.strategy.market.timeframe, 5)
        candle_provider = probe.get_candles
        logger.info("DRY_RUN: using REAL public market data from Hyperliquid %s.", config.env.network)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "DRY_RUN: could not reach Hyperliquid public market data (%s). Falling back to a "
            "SYNTHETIC random-walk feed -- signals will not reflect real market conditions.", exc,
        )
        from bot.testing.synthetic_feed import synthetic_candle_provider
        candle_provider = synthetic_candle_provider()

    return DryRunExchangeClient(
        candle_provider=candle_provider,
        starting_equity_usd=config.strategy.capital.initial_capital_usd,
        taker_fee_pct=config.strategy.fees.taker_fee_pct,
        maker_fee_pct=config.strategy.fees.maker_fee_pct,
    )


def cmd_start(args: argparse.Namespace) -> int:
    try:
        config = _load_config(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    state_dir = _resolve_state_dir(config)
    state_dir.mkdir(parents=True, exist_ok=True)
    log_dir = PROJECT_ROOT / "logs"
    logger = setup_logging(log_dir, level=config.env.log_level)

    mode = "LIVE (REAL MONEY)" if config.env.is_live else "DRY_RUN (simulated, no real orders)"
    logger.info("=" * 78)
    logger.info("Starting trading bot | mode=%s | network=%s", mode, config.env.network)
    logger.info(
        "Symbols=%s | Timeframe=%s | Capital assumption=$%.2f | Risk/trade=%.2f%% | Max leverage=%dx",
        config.strategy.market.symbols, config.strategy.market.timeframe,
        config.strategy.capital.initial_capital_usd, config.strategy.capital.risk_per_trade_pct,
        config.strategy.capital.max_leverage,
    )
    logger.info("=" * 78)

    if config.env.is_live:
        print("!" * 78)
        print("LIVE MODE: this will place REAL orders with REAL money on "
              f"Hyperliquid {config.env.network}.")
        print("!" * 78)
        if not args.yes_i_understand_live_trading:
            print(
                "Refusing to start in LIVE mode without explicit confirmation.\n"
                "Re-run with --yes-i-understand-live-trading to proceed.",
                file=sys.stderr,
            )
            return 1
        print("Live trading confirmed by operator flag. Starting in 5 seconds... (Ctrl+C to abort)")
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print("Aborted.")
            return 1

    exchange = _build_exchange(config, logger)
    engine = TradingEngine(config=config, exchange=exchange, state_dir=state_dir, log_dir=log_dir)

    pid_file = state_dir / "bot.pid"
    import os
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    try:
        engine.run_forever()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received -- shutting down.")
    finally:
        if pid_file.exists():
            pid_file.unlink()
    return 0


def cmd_signal(args: argparse.Namespace, command: ControlCommand, verb: str) -> int:
    try:
        config = _load_config(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    state_dir = _resolve_state_dir(config)
    control = ControlChannel(state_dir)
    control.send(command)
    print(f"{verb} signal sent. It will take effect on the bot's next loop tick "
          f"(up to {config.strategy.execution.loop_interval_seconds}s) if it is running.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    try:
        config = _load_config(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    state_dir = _resolve_state_dir(config)
    store = StateStore(state_dir)
    control = ControlChannel(state_dir)
    data = store.load()

    print(f"Control status file : {control.status_path}")
    print(f"Reported status      : {control.current_status}")

    if data is None:
        print("No state.json found yet -- the bot has not completed a tick since it last started.")
        return 0

    updated_at = data.get("updated_at")
    staleness = "unknown"
    if updated_at:
        try:
            age_s = (datetime.now(timezone.utc) - datetime.fromisoformat(updated_at)).total_seconds()
            threshold = max(180, 3 * config.strategy.execution.loop_interval_seconds)
            staleness = f"{age_s:.0f}s ago ({'OK' if age_s < threshold else 'STALE -- process may not be running'})"
        except ValueError:
            pass

    print(f"Last update           : {updated_at}  [{staleness}]")
    print("-" * 60)
    for key, value in (data.get("snapshot") or {}).items():
        print(f"{key:28s}: {value}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    # --env-file/--settings live on this shared parent so they work in the
    # position most people actually type them: AFTER the subcommand (e.g.
    # `cli.py start --settings x.yaml`), not just before it. Every
    # subparser below includes it via `parents=[common]`.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env-file", default=None, help="Path to a .env file (default: trading_bot/.env)")
    common.add_argument("--settings", default=None, help="Path to settings.yaml (default: config/settings.yaml)")

    # NOTE: --env-file/--settings are intentionally NOT added to this
    # top-level parser too. argparse subparsers get their own copy of any
    # `parents=` argument with its own default, and that copy silently
    # overwrites whatever the top-level parser already parsed for the same
    # flag -- so accepting the flag in both places actually makes
    # `cli.py --settings X start` silently lose X. Only the position that
    # unambiguously works (after the subcommand) is offered.
    parser = argparse.ArgumentParser(description="Autonomous Hyperliquid perpetuals trading bot.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="Start the monitoring/trading loop (foreground).", parents=[common])
    p_start.add_argument(
        "--yes-i-understand-live-trading", action="store_true",
        help="Required to actually start when DRY_RUN=false. Safety confirmation, not a shortcut.",
    )
    p_start.set_defaults(func=cmd_start)

    p_status = sub.add_parser("status", help="Print the last known state of a running/stopped bot.", parents=[common])
    p_status.set_defaults(func=cmd_status)

    p_pause = sub.add_parser("pause", help="Tell a running bot to stop opening new positions.", parents=[common])
    p_pause.set_defaults(func=lambda a: cmd_signal(a, ControlCommand.PAUSE, "PAUSE"))

    p_resume = sub.add_parser("resume", help="Tell a paused bot to resume opening new positions.", parents=[common])
    p_resume.set_defaults(func=lambda a: cmd_signal(a, ControlCommand.RESUME, "RESUME"))

    p_stop = sub.add_parser("stop", help="Tell a running bot to shut down gracefully.", parents=[common])
    p_stop.set_defaults(func=lambda a: cmd_signal(a, ControlCommand.STOP, "STOP"))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
