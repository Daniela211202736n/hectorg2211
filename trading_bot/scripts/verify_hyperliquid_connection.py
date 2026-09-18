#!/usr/bin/env python3
"""Standalone connectivity + sanity check for Hyperliquid access.

Run this BEFORE ever setting DRY_RUN=false. It touches only read-only,
unauthenticated public endpoints (unless you pass --with-account, which
additionally reads -- never writes -- your account state) and prints what
it gets back so you can eyeball that prices/fields look sane.

Usage:
    python scripts/verify_hyperliquid_connection.py
    python scripts/verify_hyperliquid_connection.py --network mainnet --symbol ETH
    python scripts/verify_hyperliquid_connection.py --with-account   # also reads account balance

This performs NO order placement, ever.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--network", default="testnet", choices=["testnet", "mainnet"])
    parser.add_argument("--symbol", default="BTC")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--with-account", action="store_true", help="Also read account state (requires .env)")
    args = parser.parse_args()

    print(f"1) Checking public market data on {args.network} for {args.symbol}...")
    try:
        from bot.exchange.hyperliquid_client import PublicMarketData
    except ImportError as exc:
        print(f"   FAILED to import the Hyperliquid SDK: {exc}")
        print("   Run: pip install -r requirements.txt")
        return 1

    try:
        market = PublicMarketData(network=args.network)
        df = market.get_candles(args.symbol, args.timeframe, lookback=10)
    except Exception as exc:  # noqa: BLE001
        print(f"   FAILED to fetch candles: {exc}")
        print("   Check your internet connection and that the symbol/network are correct.")
        return 1

    print(f"   OK -- got {len(df)} candles. Last 3 rows:")
    print(df.tail(3).to_string(index=False))
    last_close = float(df.iloc[-1]["close"])
    if last_close <= 0:
        print(f"   WARNING: last close price is {last_close} -- that looks wrong, do not trade live yet.")
        return 1
    print(f"   Last close price: {last_close}")

    if args.with_account:
        print("\n2) Checking authenticated account access...")
        from bot.config import AppConfig, ConfigError
        try:
            config = AppConfig.load()
        except ConfigError as exc:
            print(f"   FAILED to load config/.env: {exc}")
            return 1
        if not config.env.private_key:
            print("   HYPERLIQUID_PRIVATE_KEY is not set in .env -- skipping account check.")
        else:
            from bot.exchange.hyperliquid_client import HyperliquidClient
            try:
                client = HyperliquidClient(
                    private_key=config.env.private_key,
                    network=args.network,
                    account_address=config.env.account_address,
                )
                account = client.get_account_state()
            except Exception as exc:  # noqa: BLE001
                print(f"   FAILED to read account state: {exc}")
                return 1
            print(f"   OK -- account equity: ${account.equity_usd:.2f}, withdrawable: ${account.withdrawable_usd:.2f}")
            if account.equity_usd <= 0:
                print(
                    "   Your account currently shows $0 equity. If this is testnet, fund it from "
                    "the Hyperliquid testnet faucet before running the bot live."
                )

    print("\nAll checks passed. See PROMPT.md for how to proceed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
