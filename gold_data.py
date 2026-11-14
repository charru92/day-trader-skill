#!/usr/bin/env python3
"""Gold bars from Yahoo Finance (GC=F, COMEX gold futures) in mt5_data.py's CSV
format, for when the MT5 terminal is not reachable. Standard library only. It
reads prices and writes a file: it never places an order.

    python gold_data.py                      # GC=F -> trader/mt5_rates.csv as XAUUSDm, M5 M15 H1 H4 D1
    python gold_data.py --offset -12.5       # XAUUSDm = GC=F - 12.50 (read both prices in the app)
    python gold_data.py --symbol XAUUSDm --out trader/mt5_rates.csv

What it fetches (Yahoo v8 chart, free and unofficial, delayed about 10 min):
  M5   range=60d  interval=5m
  M15  range=60d  interval=15m
  H1   range=730d interval=1h
  H4   built from the H1 bars on a 00/04/08/12/16/20 UTC grid, like MT5's H4 on
       an Exness (UTC+0) server
  D1   period1=2000-01-01 interval=1d: one bar per metals day, stamped 00:00 UTC
       of the day it ends on (17:00 New York), kept once that close has passed
Rows: symbol,timeframe,time,time_utc,open,high,low,close,tick_volume,spread,real_volume
time is the bar's open in Unix seconds, UTC; tick_volume is the futures volume;
spread and real_volume are 0. Bars with a missing price, off the grid, or not
finished yet (ending after the last trade or after now) are left out.

--offset is added to every price: GC=F is the front futures contract and
usually trades a few dollars above spot gold, so XAUUSDm = GC=F + offset. Take
both prices at the same moment and pass the difference (XAUUSDm - GC=F).
"""

import argparse
import csv
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/Jakeschincariol/day-trader-skill)"
PAUSE = 0.5          # seconds between requests: be polite to free endpoints
BACKOFF = 5.0        # seconds to wait once after a 429
HOSTS = ("query2", "query1")
FETCH = (("M5", "range=60d", "5m", 300), ("M15", "range=60d", "15m", 900), ("H1", "range=730d", "1h", 3600),
         ("D1", "period1=946684800&period2=%(now)d", "1d", 86400))
DAY = 86400
H4 = 4 * 3600
COLUMNS = ("symbol", "timeframe", "time", "time_utc", "open", "high", "low", "close",
           "tick_volume", "spread", "real_volume")


class DataError(Exception):
    pass


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _nth_sunday(year, month, n):
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(6 - d.weekday()) % 7)
    return d + datetime.timedelta(weeks=n - 1)


def close_utc(day_start):
    """Unix time of 17:00 New York on the UTC day starting at day_start (US daylight time rule since 2007)."""
    d = datetime.datetime.fromtimestamp(day_start, datetime.timezone.utc).date()
    edt = _nth_sunday(d.year, 3, 2) <= d < _nth_sunday(d.year, 11, 1)
    return day_start + (21 if edt else 22) * 3600


def fetch_chart(ticker, query, interval):
    """query2 first, one retry after a 429, then query1 on 429 or 5xx."""
    last = None
    for n, host in enumerate(HOSTS):
        if n:
            time.sleep(PAUSE)
        for attempt in (0, 1):
            url = "https://%s.finance.yahoo.com/v8/finance/chart/%s?%s&interval=%s" % (
                host, urllib.parse.quote(ticker), query, interval)
            try:
                return json.loads(_get(url))
            except urllib.error.HTTPError as e:
                last = e.code
                e.close()
                if e.code == 429 and attempt == 0:
                    time.sleep(BACKOFF)
                    continue
                if e.code == 429 or e.code >= 500:
                    break
                raise DataError("Yahoo answered HTTP %d for %s" % (e.code, ticker))
            except urllib.error.URLError as e:
                raise DataError("could not reach Yahoo: %s" % e.reason)
            except ValueError:
                raise DataError("Yahoo returned something that is not JSON for %s" % ticker)
    raise DataError("Yahoo answered HTTP %s for %s on both hosts. Try again later." % (last, ticker))


def parse_bars(doc, step, cutoff, offset):
    """Closed, on-grid bars, oldest first, with the offset added to every price. Daily bars
    (step 86400) are stamped 00:00 UTC of their day and closed at 17:00 New York."""
    res = (doc.get("chart") or {}).get("result")
    if not res:
        err = (doc.get("chart") or {}).get("error") or {}
        raise DataError("Yahoo has no data: %s" % (err.get("description") or "unknown ticker"))
    r = res[0]
    rmt = (r.get("meta") or {}).get("regularMarketTime")
    if isinstance(rmt, (int, float)):
        cutoff = min(cutoff, int(rmt) + 60)      # the bar holding the last trade is finished only after it
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0] or {}
    cols = [q.get(k) or [] for k in ("open", "high", "low", "close", "volume")]
    bars = {}
    for i, ts in enumerate(r.get("timestamp") or []):
        o, h, l, c, v = [col[i] if i < len(col) else None for col in cols]
        if ts is None or None in (o, h, l, c):
            continue
        ts = int(ts)
        if step == DAY:
            ts -= ts % DAY
            if close_utc(ts) > cutoff:
                continue
        elif ts % step or ts + step > cutoff:
            continue
        bars[ts] = {"time": ts, "open": round(o + offset, 3), "high": round(h + offset, 3),
                         "low": round(l + offset, 3), "close": round(c + offset, 3), "tick_volume": int(v or 0)}
    return [bars[t] for t in sorted(bars)], r.get("meta") or {}


def to_h4(h1, now):
    """H1 bars -> H4 bars on the UTC 4-hour grid. A bucket whose 4 hours have not passed is still forming: left out."""
    out = {}
    for b in h1:
        t = b["time"] - b["time"] % H4
        if t + H4 > now:
            continue
        x = out.get(t)
        if x is None:
            out[t] = dict(b, time=t)
        else:
            x.update(high=max(x["high"], b["high"]), low=min(x["low"], b["low"]), close=b["close"],
                     tick_volume=x["tick_volume"] + b["tick_volume"])
    return [out[t] for t in sorted(out)]


def main(argv=None):
    ap = argparse.ArgumentParser(description="GC=F gold bars from Yahoo into mt5_data.py's CSV.")
    ap.add_argument("--ticker", default="GC=F")
    ap.add_argument("--symbol", default="XAUUSDm", help="the symbol written in the CSV (default XAUUSDm)")
    ap.add_argument("--offset", type=float, default=0.0,
                    help="dollars added to every price: XAUUSDm minus GC=F at the same moment (default 0)")
    ap.add_argument("--out", default="trader/mt5_rates.csv")
    a = ap.parse_args(argv)
    try:
        now = int(time.time())
        rows, frames = [], {}
        for n, (name, query, interval, step) in enumerate(FETCH):
            if n:
                time.sleep(PAUSE)
            frames[name], meta = parse_bars(fetch_chart(a.ticker, query % {"now": now}, interval), step, now, a.offset)
            if not frames[name]:
                raise DataError("no closed %s bars for %s" % (interval, a.ticker))
        frames["H4"] = to_h4(frames["H1"], now)
        last = frames["M5"][-1]
        print("%s last close %.2f (%s UTC bar)%s" % (a.ticker, last["close"] - a.offset, iso(last["time"]),
                                                     "  ->  %s %.2f with offset %+.2f" % (a.symbol, last["close"], a.offset)
                                                     if a.offset else ""))
        for name in ("M5", "M15", "H1", "H4", "D1"):
            bars = frames[name]
            rows += [dict(b, symbol=a.symbol, timeframe=name, time_utc=iso(b["time"]), spread=0, real_volume=0)
                     for b in bars]
            print("  %-4s %6d bars  %s to %s UTC" % (name, len(bars), iso(bars[0]["time"]), iso(bars[-1]["time"])))
        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        print("Wrote %d rows to %s as %s (offset %+.2f)." % (len(rows), a.out, a.symbol, a.offset))
        return 0
    except DataError as e:
        print("gold_data.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
