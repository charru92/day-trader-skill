#!/usr/bin/env python3
"""MT5 data for the day-trader-skill pack. READ ONLY: it connects to the
MetaTrader 5 terminal that is already open and logged in, reads the current
tick and downloads closed bars. It never calls order_send, order_check or
anything that opens, modifies or closes a position.

    python mt5_data.py                          # XAUUSDm: tick + M5, M15, H1, H4 -> trader/mt5_rates.csv
    python mt5_data.py --symbol XAUUSDm --out trader/mt5_rates.csv
    python mt5_data.py --server-utc-offset 2    # the broker's server clock is UTC+2

The CSV holds one row per bar, every timeframe in one file:
    symbol,timeframe,time,time_utc,open,high,low,close,tick_volume,spread,real_volume
time is the bar's open in Unix seconds, UTC. MT5 stamps bars in the broker's
server time, so the script subtracts the server's offset from UTC: worked out
from a fresh tick while the market is open, else --server-utc-offset (default
0, Exness server time). Only closed bars are written: the bar still forming is
left out. trader-exit and trader-timemachine read the M5 rows.
"""

import argparse
import csv
import datetime
import os
import sys
import time

import MetaTrader5 as mt5

TIMEFRAMES = {  # name: (MT5 constant, bars to download)
    "M5": (mt5.TIMEFRAME_M5, 20000),     # ~70 days of 24h gold, the time machine wants 60
    "M15": (mt5.TIMEFRAME_M15, 10000),
    "H1": (mt5.TIMEFRAME_H1, 5000),
    "H4": (mt5.TIMEFRAME_H4, 2000),
}
COLUMNS = ("symbol", "timeframe", "time", "time_utc", "open", "high", "low", "close",
           "tick_volume", "spread", "real_volume")


class DataError(Exception):
    pass


def market_open(utc):
    """Rough FX/metals week: Sunday 22:00 to Friday 22:00 UTC."""
    wd, h = utc.weekday(), utc.hour
    return not (wd == 5 or (wd == 6 and h < 22) or (wd == 4 and h >= 22))


def server_offset(tick_time, fallback):
    """(hours, how). A tick stamped within 90 s of a whole-hour offset from now is fresh enough to tell."""
    now = time.time()
    diff = tick_time - now
    hours = round(diff / 3600)
    if market_open(utc_of(now)) and -12 <= hours <= 14 \
            and abs(diff - hours * 3600) <= 90:
        return hours, "from the live tick"
    return fallback, "--server-utc-offset (market closed or tick not fresh)"


def utc_of(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)


def iso(ts):
    return utc_of(ts).strftime("%Y-%m-%d %H:%M:%S")


def read_tick(sym):
    tick = mt5.symbol_info_tick(sym)
    if tick is None:
        raise DataError("no tick for %s: %s" % (sym, mt5.last_error()))
    return tick


def read_rates(sym, tf_const, count, offset_s):
    rates = mt5.copy_rates_from_pos(sym, tf_const, 1, count)   # start at 1: skip the bar still forming
    if rates is None or len(rates) == 0:
        raise DataError("no bars for %s: %s" % (sym, mt5.last_error()))
    return [{"time": int(r["time"]) - offset_s, "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"]), "tick_volume": int(r["tick_volume"]),
             "spread": int(r["spread"]), "real_volume": int(r["real_volume"])} for r in rates]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only MT5 tick and bars to CSV.")
    ap.add_argument("--symbol", default="XAUUSDm")
    ap.add_argument("--out", default="trader/mt5_rates.csv")
    ap.add_argument("--server-utc-offset", type=int, default=0,
                    help="hours the broker's server clock is ahead of UTC, used when the market is closed")
    a = ap.parse_args(argv)

    if not mt5.initialize():   # the open terminal's own session: no login, no password
        print("mt5_data.py: could not connect to the MT5 terminal: %s" % (mt5.last_error(),), file=sys.stderr)
        return 1
    try:
        sym = a.symbol
        info = mt5.symbol_info(sym)
        if info is None:
            raise DataError("the terminal has no symbol %s" % sym)
        if not info.visible and not mt5.symbol_select(sym, True):   # Market Watch only, not a trade
            raise DataError("could not add %s to Market Watch: %s" % (sym, mt5.last_error()))
        tick = read_tick(sym)
        hours, how = server_offset(tick.time, a.server_utc_offset)
        offset_s = hours * 3600
        print("%s tick at %s UTC: bid %s  ask %s  spread %s"
              % (sym, iso(tick.time - offset_s), tick.bid, tick.ask, round(tick.ask - tick.bid, info.digits)))
        print("Server clock UTC%+d (%s)." % (hours, how))

        rows = []
        for name, (const, count) in TIMEFRAMES.items():
            bars = read_rates(sym, const, count, offset_s)
            rows += [dict(b, symbol=sym, timeframe=name, time_utc=iso(b["time"])) for b in bars]
            print("  %-4s %6d bars  %s to %s UTC" % (name, len(bars), iso(bars[0]["time"]), iso(bars[-1]["time"])))

        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        print("Wrote %d rows to %s." % (len(rows), a.out))
        return 0
    except DataError as e:
        print("mt5_data.py: %s" % e, file=sys.stderr)
        return 1
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())
