#!/usr/bin/env python3
"""Gold report from trader/mt5_rates.csv (gold_data.py or mt5_data.py). Standard
library only. It reads prices and prints: it never places an order.

    python gold_report.py                    # XAUUSDm: trend, levels, moves by horizon, past look-alikes
    python gold_report.py --json

1. Trend on H4 and H1: EMA 20/50/200, RSI 14, ATR 14, the last swing highs and lows
   (a bar whose high or low is the extreme of the k bars on each side).
2. Levels: swing highs above and swing lows below the last close on H4 and H1,
   merged when closer than a quarter of the H4 ATR.
3. How far gold moves by horizon: ATR 14 of daily, weekly and two-week bars
   built from D1 (the metals day ends 17:00 New York; weeks run Monday to Friday).
4. Past look-alikes on D1, each counted once (the next one at least 10 sessions
   later), and what the price did 1, 5, 10 and 20 sessions after:
   A. a similar strong fall, any year: 5-day change -3 % to -6.5 %, 20-day -4 % or
      less, 60-day -8 % or less (percent terms, so any price level counts)
   B. the same price zone (close within 2 % of now) after a 5-day fall of 1.5 %+, last 2 years
   C. a test of the same support (a low within 0.6 % of this week's low) coming
      down from 1.6 %+ above it within 10 sessions, last 2 years
   "Kept falling" = lower close that many sessions later, "rebounded" = higher.
History is not a forecast: a handful of cases can point anywhere.
"""

import argparse
import csv
import datetime
import json
import statistics
import sys


class ReportError(Exception):
    pass


def load(path, symbol):
    frames = {}
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["symbol"].upper() != symbol.upper():
                    continue
                frames.setdefault(r["timeframe"], []).append(
                    {"t": int(r["time"]), "o": float(r["open"]), "h": float(r["high"]), "l": float(r["low"]),
                     "c": float(r["close"])})
    except (OSError, KeyError, ValueError) as e:
        raise ReportError("could not read %s: %s" % (path, e))
    for tf in ("H4", "H1", "D1"):
        if len(frames.get(tf) or []) < 250:
            raise ReportError("%s has too few %s bars of %s: run python gold_data.py" % (path, tf, symbol))
    return frames


def day_of(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date()


def stamp(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%a %Y-%m-%d %H:%M")


def ema(xs, n):
    k, e = 2.0 / (n + 1), xs[0]
    out = []
    for x in xs:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def rsi(xs, n=14):
    g = l = 0.0
    for i in range(1, n + 1):
        d = xs[i] - xs[i - 1]
        g, l = g + max(d, 0), l + max(-d, 0)
    g, l = g / n, l / n
    for i in range(n + 1, len(xs)):
        d = xs[i] - xs[i - 1]
        g, l = (g * (n - 1) + max(d, 0)) / n, (l * (n - 1) + max(-d, 0)) / n
    return 100.0 if not l else 100 - 100 / (1 + g / l)


def atr(bars, n=14):
    trs = [bars[0]["h"] - bars[0]["l"]] + [max(b["h"] - b["l"], abs(b["h"] - p["c"]), abs(b["l"] - p["c"]))
                                           for p, b in zip(bars, bars[1:])]
    a = sum(trs[:n]) / n
    for t in trs[n:]:
        a = (a * (n - 1) + t) / n
    return a


def swings(bars, k):
    hi, lo = [], []
    for i in range(k, len(bars) - k):
        w = bars[i - k:i + k + 1]
        if bars[i]["h"] == max(x["h"] for x in w):
            hi.append((bars[i]["t"], bars[i]["h"]))
        if bars[i]["l"] == min(x["l"] for x in w):
            lo.append((bars[i]["t"], bars[i]["l"]))
    return hi, lo


def trend(bars, k):
    c = [b["c"] for b in bars]
    e20 = ema(c, 20)
    hi, lo = swings(bars, k)
    out = {"close": c[-1], "at": stamp(bars[-1]["t"]), "ema20": e20[-1], "ema20_5_bars_ago": e20[-6],
           "ema50": ema(c, 50)[-1], "ema200": ema(c, 200)[-1], "rsi14": rsi(c), "atr14": atr(bars),
           "swing_highs": [p for _, p in hi[-4:]], "swing_lows": [p for _, p in lo[-4:]]}
    below = c[-1] < out["ema20"] < out["ema50"] < out["ema200"]
    above = c[-1] > out["ema20"] > out["ema50"] > out["ema200"]
    lower = len(hi) >= 3 and hi[-1][1] < hi[-2][1] < hi[-3][1]
    higher = len(lo) >= 3 and lo[-1][1] > lo[-2][1] > lo[-3][1]
    if below and lower:
        out["verdict"] = "down: price under EMA 20 < 50 < 200, each high lower than the one before"
    elif above and higher:
        out["verdict"] = "up: price over EMA 20 > 50 > 200, each low higher than the one before"
    elif c[-1] < out["ema50"]:
        out["verdict"] = "leaning down: price under the EMA 50, no clean staircase"
    elif c[-1] > out["ema50"]:
        out["verdict"] = "leaning up: price over the EMA 50, no clean staircase"
    else:
        out["verdict"] = "flat"
    return out


def levels(frames, close, merge):
    pts = []
    for tf, k in (("H4", 6), ("H1", 4)):
        hi, lo = swings(frames[tf][-600:], k)
        pts += [(p, t, tf) for t, p in hi + lo]
    up = sorted((p, t) for p, t, _ in pts if p > close)
    dn = sorted(((p, t) for p, t, _ in pts if p < close), reverse=True)

    def cluster(xs):
        out = []
        for p, t in xs:
            if out and abs(out[-1]["price"] - p) < merge:
                out[-1]["touches"] += 1
                out[-1]["last"] = max(out[-1]["last"], t)
            else:
                out.append({"price": round(p, 1), "touches": 1, "last": t})
        for x in out:
            x["last"] = str(day_of(x["last"]))
        return out[:5]
    return {"resistance": cluster(up), "support": cluster(dn)}


def periods(d1, key):
    out = {}
    for b in d1:
        out.setdefault(key(day_of(b["t"])), []).append(b)
    return [{"t": v[0]["t"], "o": v[0]["o"], "h": max(x["h"] for x in v), "l": min(x["l"] for x in v), "c": v[-1]["c"]}
            for _, v in sorted(out.items())]


def horizons(d1):
    week = lambda d: d.isocalendar()[:2]
    fortnight = lambda d: (d.isocalendar()[0], (d.isocalendar()[1] - 1) // 2)
    return {"1 day": atr(d1), "1 week": atr(periods(d1, week)[:-1]), "2 weeks": atr(periods(d1, fortnight)[:-1])}


H = (1, 5, 10, 20)


def lookalikes(d1):
    D, n = d1, len(d1) - 1
    C = [b["c"] for b in D]

    def ret(i, k):
        return C[i] / C[i - k] - 1

    def episodes(match, lo, ahead, gap=10):
        out, last = [], -999
        for i in range(max(lo, 61), n - ahead + 1):
            if i - last > gap and match(i):
                out.append(i)
                last = i
        return out

    def outcome(i):
        f = {h: C[i + h] / C[i] - 1 for h in H if i + h <= n}
        w = D[i + 1:i + 11]
        return dict(f, up10=max(x["h"] for x in w) / C[i] - 1, dn10=min(x["l"] for x in w) / C[i] - 1)

    two = next(i for i in range(len(D)) if day_of(D[i]["t"]) >= day_of(D[n]["t"]) - datetime.timedelta(days=730))
    week_low = min(x["l"] for x in D[n - 4:n + 1])
    sets = {
        "A": ("a similar strong fall, any year since %s" % day_of(D[0]["t"]).year,
              episodes(lambda i: -0.065 <= ret(i, 5) <= -0.03 and ret(i, 20) <= -0.04 and ret(i, 60) <= -0.08, 0, 20)),
        "B": ("the same price zone (close %.0f to %.0f) after a 5-day fall of 1.5 %%+, last 2 years"
              % (C[n] * 0.98, C[n] * 1.02),
              episodes(lambda i: abs(C[i] / C[n] - 1) <= 0.02 and ret(i, 5) <= -0.015, two, 10)),
        "C": ("a test of this week's low %.1f (a low within 0.6 %%) from 1.6 %%+ above it, last 2 years" % week_low,
              episodes(lambda i: abs(D[i]["l"] / week_low - 1) <= 0.006 and
                       max(x["h"] for x in D[i - 10:i]) >= week_low * 1.016, two, 10, gap=5)),
    }
    out = {"now": {"date": str(day_of(D[n]["t"])), "close": C[n], "change_5d": ret(n, 5), "change_20d": ret(n, 20),
                   "change_60d": ret(n, 60)}}
    for key, (title, eps) in sets.items():
        rows = [{"date": str(day_of(D[i]["t"])), "close": C[i], **{"after_%d" % h: v for h, v in outcome(i).items()
                                                                   if isinstance(h, int)},
                 "best_up_10": outcome(i)["up10"], "worst_down_10": outcome(i)["dn10"]} for i in eps]
        tally = {}
        for h in H:
            xs = [r["after_%d" % h] for r in rows if "after_%d" % h in r]
            if xs:
                tally[h] = {"cases": len(xs), "kept_falling": sum(1 for x in xs if x < 0),
                            "rebounded": sum(1 for x in xs if x > 0), "median": statistics.median(xs),
                            "avg_fall": statistics.mean([x for x in xs if x < 0] or [0.0]),
                            "avg_rise": statistics.mean([x for x in xs if x > 0] or [0.0])}
        out[key] = {"title": title, "cases": rows, "tally": tally}
    return out


def pct(x):
    return "%+.1f %%" % (100 * x)


def usd(x, price):
    return "%+.0f $" % (x * price)


def report(doc):
    lines = []
    for tf in ("H4", "H1"):
        t = doc["trend"][tf]
        lines += ["%s  close %.1f (%s UTC): %s" % (tf, t["close"], t["at"], t["verdict"]),
                  "    EMA20 %.1f (5 bars ago %.1f)  EMA50 %.1f  EMA200 %.1f  RSI14 %.1f  ATR14 %.1f"
                  % (t["ema20"], t["ema20_5_bars_ago"], t["ema50"], t["ema200"], t["rsi14"], t["atr14"]),
                  "    swing highs %s | swing lows %s" % (", ".join("%.1f" % x for x in t["swing_highs"]),
                                                          ", ".join("%.1f" % x for x in t["swing_lows"]))]
    lines.append("")
    for side in ("resistance", "support"):
        lines.append("%-10s " % side + " | ".join("%.1f (x%d, %s)" % (x["price"], x["touches"], x["last"])
                                                   for x in doc["levels"][side]))
    lines += ["", "How far gold moves (ATR 14): " +
              ", ".join("%s %.0f $" % (k, v) for k, v in doc["horizons"].items()), ""]
    la = doc["lookalikes"]
    price = la["now"]["close"]
    lines.append("Now %s close %.1f: 5 days %s, 20 days %s, 60 days %s" % (
        la["now"]["date"], price, pct(la["now"]["change_5d"]), pct(la["now"]["change_20d"]), pct(la["now"]["change_60d"])))
    for key in ("A", "B", "C"):
        s = la[key]
        lines.append("%s. %s: %d case(s)" % (key, s["title"], len(s["cases"])))
        for r in s["cases"]:
            lines.append("    %s close %8.1f | 5d %s | 10d %s | 20d %s | best in 10d %s, worst %s" % (
                r["date"], r["close"], pct(r["after_5"]), pct(r["after_10"]),
                pct(r["after_20"]) if "after_20" in r else "  n/a ", pct(r["best_up_10"]), pct(r["worst_down_10"])))
        for h, t in s["tally"].items():
            lines.append("    after %2d sessions: kept falling %d, rebounded %d of %d | median %s (%s at today's price)"
                         % (h, t["kept_falling"], t["rebounded"], t["cases"], pct(t["median"]), usd(t["median"], price)))
    lines.append("History is not a forecast: a handful of cases can point anywhere.")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="Gold trend, levels, moves by horizon and past look-alikes.")
    ap.add_argument("--csv", default="trader/mt5_rates.csv")
    ap.add_argument("--symbol", default="XAUUSDm")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        f = load(a.csv, a.symbol)
        tr = {"H4": trend(f["H4"], 3), "H1": trend(f["H1"], 4)}
        doc = {"symbol": a.symbol, "trend": tr, "levels": levels(f, tr["H1"]["close"], tr["H4"]["atr14"] / 4),
               "horizons": horizons(f["D1"]), "lookalikes": lookalikes(f["D1"])}
    except ReportError as e:
        print("gold_report.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(doc, indent=1) if a.json else "\n".join(report(doc)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
