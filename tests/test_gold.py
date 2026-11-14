"""Gold hours (--market gold) in trader-timemachine and trader-exit, and gold_data.py's
CSV rows. Synthetic 24-hour bars: 25 metals sessions, 18:00 to 17:00 New York."""

import datetime
import io
import json
import os
import random
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT

tm = load("trader-timemachine", "timemachine")
exitp = load("trader-exit", "exit")
gold = None
spec_path = os.path.join(ROOT, "gold_data.py")
if os.path.exists(spec_path):
    import importlib.util
    _s = importlib.util.spec_from_file_location("gold_data", spec_path)
    gold = importlib.util.module_from_spec(_s)
    _s.loader.exec_module(gold)


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def sessions_csv(path, days, skip=()):
    """One M5 row every 5 minutes from 18:00 ET the evening before each day to 17:00 ET (EST: +5h to UTC)."""
    rng = random.Random(7)
    p = 4000.0
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("symbol,timeframe,time,time_utc,open,high,low,close,tick_volume,spread,real_volume\n")
        for d in days:
            start = datetime.datetime.combine(d - datetime.timedelta(days=1), datetime.time(23, 0))   # 18:00 EST
            for i in range(276):
                t = int((start + datetime.timedelta(minutes=5 * i) - datetime.datetime(1970, 1, 1)).total_seconds())
                c = p * (1 + rng.gauss(0, 0.0008))
                if (d, i) not in skip:
                    fh.write("XAUUSDm,M5,%d,,%.2f,%.2f,%.2f,%.2f,1,0,0\n" % (t, p, max(p, c) + 0.5, min(p, c) - 0.5, c))
                p = c


def weekdays(first, n):
    out, d = [], first
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


DAYS = weekdays(datetime.date(2026, 11, 16), 25)     # all after the DST change: EST, UTC-5


class GoldSessions(unittest.TestCase):
    def tearDown(self):
        tm.use_market("nyse")

    def test_market_guess(self):
        self.assertEqual(tm.guess_market("XAUUSDm"), "gold")
        self.assertEqual(tm.guess_market("GC=F"), "gold")
        self.assertEqual(tm.guess_market("TSLA"), "nyse")
        self.assertEqual(exitp.guess_market("xauusd"), "gold")

    def test_clock_and_slots(self):
        tm.use_market("gold")
        self.assertEqual((tm.OPEN_MIN, tm.SESSION_BARS, tm.CLOSE_MIN), (1080, 276, 2460))
        self.assertEqual(tm.parse_clock("2:15", "t"), 135)              # no p.m. guess on a 24-hour market
        plan = tm.normalize_plan({"symbol": "XAUUSDm", "date": "2026-11-17", "entry": 10, "stop": 9, "target": 11,
                                  "entry_time": "18:05"})
        self.assertEqual((plan["entry_bar"], plan["entry_time"], plan["market"]), (0, "6:05 PM", "gold"))
        plan = tm.normalize_plan({"symbol": "XAUUSDm", "date": "2026-11-17", "entry": 10, "stop": 9, "target": 11,
                                  "entry_time": "02:15"})
        self.assertEqual(plan["entry_bar"], 98)                         # 8 h 15 m after 18:00
        with self.assertRaises(tm.TMError):
            tm.normalize_plan({"symbol": "XAUUSDm", "date": "2026-11-17", "entry": 10, "stop": 9, "target": 11,
                               "entry_time": "17:00"})                  # the close: nothing left to replay
        tm.use_market("nyse")
        self.assertEqual(tm.parse_clock("2:15", "t"), 14 * 60 + 15)

    def test_bars_land_in_the_session_they_end(self):
        tm.use_market("gold")
        sun_18 = int((datetime.datetime(2026, 11, 15, 23, 0) - datetime.datetime(1970, 1, 1)).total_seconds())
        res = {"meta": {}, "timestamp": [sun_18, sun_18 + 22 * 3600 + 3300, sun_18 + 23 * 3600],
               "indicators": {"quote": [{"open": [1, 1, 1], "high": [1, 1, 1], "low": [1, 1, 1], "close": [1, 1, 1],
                                         "volume": [0, 0, 0]}]}}
        _, bars, dropped = tm.parse_bars(res, datetime.datetime(2026, 11, 20))
        self.assertEqual([(b["date"], b["slot"], b["start"]) for b in bars],
                         [("2026-11-16", 0, "18:00"), ("2026-11-16", 275, "16:55")])
        self.assertEqual(dropped["outside_hours"], 1)                   # 17:00, the daily break

    def test_small_gaps_are_filled_big_ones_left_out(self):
        tm.use_market("gold")
        s = {i: {"date": "d", "slot": i, "o": 1.0 + i, "h": 2.0 + i, "l": 0.5 + i, "c": 1.5 + i} for i in range(276)}
        for i in (0, 100, 101):
            del s[i]
        full, short = tm.full_sessions({"2026-11-16": s}, "2026-12-01")
        self.assertEqual((len(full), short), (1, []))
        bars = full[0][1]
        self.assertEqual(len(bars), 276)
        self.assertEqual((bars[0]["c"], bars[100]["c"], bars[101]["o"]), (2.0, 100.5, 100.5))
        del s[200]
        full, short = tm.full_sessions({"2026-11-16": s}, "2026-12-01")
        self.assertEqual((full, short), ([], [{"date": "2026-11-16", "bars": 272}]))


class GoldCli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.csv = os.path.join(self.d, "mt5_rates.csv")
        sessions_csv(self.csv, DAYS, skip={(DAYS[3], 50)})
        self.next_day = (DAYS[-1] + datetime.timedelta(days=3)).isoformat()   # a Monday after the data

    def tearDown(self):
        shutil.rmtree(self.d)
        tm.use_market("nyse")

    def flags(self, *extra):
        return ["run", "--symbol", "XAUUSDm", "--date", self.next_day, "--entry", "4000", "--stop", "3980",
                "--target", "4020", "--entry-time", "18:05", "--setup", "level", "--csv", self.csv,
                "--replays", "400", "--json", "--now", "%s 18:00" % DAYS[-1]] + list(extra)

    def test_plan_ahead_needs_the_flag(self):
        code, _, err = run(tm, self.flags())
        self.assertEqual(code, 1)
        self.assertIn("--ahead plans a session after the data", err)
        code, text, err = run(tm, self.flags("--ahead"))
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual(doc["plan"]["market"], "gold")
        self.assertEqual(doc["mc"]["pool_sessions"], 25)                # the session with one gap is filled, kept
        self.assertEqual(doc["mc"]["bars_left"], 275)
        self.assertEqual(doc["mc"]["until"], "17:00")
        self.assertEqual(doc["mc"]["band_times"][-1], "17:00")
        self.assertIn("planned ahead", doc["warnings"][0])
        self.assertAlmostEqual(doc["mc"]["p_target"] + doc["mc"]["p_stop"] + doc["mc"]["p_neither"], 1.0, places=3)

    def test_until_across_midnight_and_a_sell(self):
        code, text, err = run(tm, self.flags("--ahead", "--until", "02:00", "--side", "sell", "--stop", "4020",
                                             "--target", "3980"))
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual((doc["mc"]["until"], doc["mc"]["bars_left"]), ("02:00", 95))
        code, _, err = run(tm, self.flags("--ahead", "--until", "17:30"))
        self.assertEqual(code, 1)

    def test_held_for_several_sessions(self):
        code, text, err = run(tm, self.flags("--ahead", "--sessions", "3", "--replays", "200"))
        self.assertEqual(code, 0, err)
        m = json.loads(text)["mc"]
        self.assertEqual((m["sessions"], m["bars_left"], m["gap_pool"]), (3, 275 + 2 * 276, 24))
        self.assertEqual(m["end_date"], tm.session_dates(self.next_day, 3)[-1])
        self.assertEqual((m["band_times"][275], m["band_times"][276], m["band_times"][-1]), ("17:00", "18:05", "17:00"))
        code, text, _ = run(tm, [x for x in self.flags("--ahead", "--sessions", "3", "--replays", "200") if x != "--json"])
        self.assertIn("3 sessions held (gaps at each open drawn from 24 real ones)", text)
        code, _, err = run(tm, self.flags("--ahead", "--sessions", "31"))
        self.assertEqual(code, 1)
        self.assertIn("--sessions must be between 1 and 30", err)

    def test_one_session_is_unchanged_by_the_gap_pool(self):
        one = json.loads(run(tm, self.flags("--ahead"))[1])["mc"]
        self.assertEqual((one["sessions"], one["bars_left"]), (1, 275))
        tm.use_market("gold")
        pool = [[(1.0, 1.0, 1.0, 1.0)] for _ in range(276)]
        a = tm.simulate(pool, 100.0, 99.0, 101.0, 0, 275, replays=50, seed=3)
        pool[0] = [(9.0, 9.0, 9.0, 9.0)]                                 # slot 0 is never drawn inside one session
        b = tm.simulate(pool, 100.0, 99.0, 101.0, 0, 275, replays=50, seed=3)
        self.assertEqual(a, b)

    def test_detrend_takes_the_drift_out(self):
        tm.use_market("gold")
        pool = [[(1.001, 1.002, 1.0, 1.001), (1.003, 1.004, 1.002, 1.003)] for _ in range(276)]
        flat, g = tm.detrend(pool)
        self.assertAlmostEqual(g, (1.001 * 1.003) ** 0.5, places=12)
        self.assertAlmostEqual(flat[5][0][3] * flat[5][1][3], 1.0, places=12)
        code, text, err = run(tm, self.flags("--ahead", "--detrend", "--sessions", "2", "--replays", "200"))
        self.assertEqual(code, 0, err)
        m = json.loads(text)["mc"]
        self.assertIs(m["detrended"], True)
        self.assertIsInstance(m["drift_removed_pct_per_session"], float)

    def test_exit_has_no_forced_close_and_reads_gold_hours(self):
        code, text, err = run(exitp, ["plan", "--symbol", "XAUUSDm", "--entry", "4000", "--setup", "level",
                                      "--stop", "price:3980", "--target", "1R", "--date", DAYS[-1].isoformat(),
                                      "--time", "18:05", "--json", "--now", "%s 12:00" % DAYS[-1]])
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["flat_by"], p["market"], p["target"]), (None, "gold", 4020.0))
        code, text, err = run(exitp, ["plan", "--symbol", "XAUUSDm", "--entry", "4000", "--setup", "level",
                                      "--stop", "atr:1", "--date", DAYS[-1].isoformat(), "--entry-bar", "200",
                                      "--csv", self.csv, "--json", "--now", "%s 16:30" % DAYS[-1]])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(text)["atr_bars"], 14)
        code, text, _ = run(exitp, ["plan", "--symbol", "XAUUSDm", "--entry", "4000", "--setup", "level",
                                    "--stop", "price:3980", "--date", DAYS[-1].isoformat(), "--now",
                                    "%s 12:00" % DAYS[-1]])
        self.assertIn("Flat by       none", text)


class GoldReport(unittest.TestCase):
    def test_report_on_synthetic_bars(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("gold_report", os.path.join(ROOT, "gold_report.py"))
        rep = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rep)
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "r.csv")
            rng, p, rows = random.Random(5), 3000.0, []
            for tf, step, n in (("H1", 3600, 900), ("H4", 14400, 900), ("D1", 86400, 900)):
                p = 3000.0
                for i in range(n):
                    c = p * (1 + rng.gauss(0, 0.01))
                    rows.append("XAUUSDm,%s,%d,,%.2f,%.2f,%.2f,%.2f,0,0,0" % (tf, 1500000000 + step * i, p,
                                                                             max(p, c) + 1, min(p, c) - 1, c))
                    p = c
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("symbol,timeframe,time,time_utc,open,high,low,close,tick_volume,spread,real_volume\n")
                fh.write("\n".join(rows) + "\n")
            code, text, err = run(rep, ["--csv", path, "--json"])
            self.assertEqual(code, 0, err)
            doc = json.loads(text)
            self.assertEqual(sorted(doc["trend"]), ["H1", "H4"])
            self.assertEqual(sorted(doc["horizons"]), ["1 day", "1 week", "2 weeks"])
            self.assertLess(doc["horizons"]["1 day"], doc["horizons"]["2 weeks"])
            for x in doc["levels"]["support"]:
                self.assertLess(x["price"], doc["trend"]["H1"]["close"])
            for k in ("A", "B", "C"):
                for t in doc["lookalikes"][k]["tally"].values():
                    self.assertLessEqual(t["kept_falling"] + t["rebounded"], t["cases"])
            code, _, err = run(rep, ["--csv", os.path.join(d, "missing.csv")])
            self.assertEqual(code, 1)
        finally:
            shutil.rmtree(d)


@unittest.skipIf(gold is None, "gold_data.py not at the repo root")
class GoldData(unittest.TestCase):
    def test_offset_grid_and_unfinished_bars(self):
        doc = {"chart": {"result": [{"meta": {"regularMarketTime": 1830},
                                     "timestamp": [1200, 1500, 1620, 1800, 2100],
                                     "indicators": {"quote": [{"open": [1, 2, 9, None, 3], "high": [2, 3, 9, 1, 4],
                                                               "low": [0.5, 1, 9, 1, 2], "close": [1.5, 2.5, 9, 1, 3.5],
                                                               "volume": [5, None, 1, 1, 1]}]}}]}}
        bars, _ = gold.parse_bars(doc, 300, 10 ** 9, -10.0)
        self.assertEqual([(b["time"], b["open"], b["close"], b["tick_volume"]) for b in bars],
                         [(1200, -9.0, -8.5, 5), (1500, -8.0, -7.5, 0)])   # 1620 off grid, 1800 no open, 2100 unfinished

    def test_daily_bars_close_at_17_new_york(self):
        day = 1783900800                                                  # 2026-07-13 00:00 UTC, daylight time
        doc = {"chart": {"result": [{"meta": {}, "timestamp": [day + 4 * 3600],
                                     "indicators": {"quote": [{"open": [1], "high": [2], "low": [0.5], "close": [1.5],
                                                               "volume": [3]}]}}]}}
        self.assertEqual(gold.close_utc(day), day + 21 * 3600)
        self.assertEqual(gold.close_utc(1794528000), 1794528000 + 22 * 3600)   # 2026-11-13, standard time
        self.assertEqual(gold.parse_bars(doc, 86400, day + 21 * 3600 - 1, 0)[0], [])
        bars, _ = gold.parse_bars(doc, 86400, day + 21 * 3600, 0)
        self.assertEqual([(b["time"], b["close"]) for b in bars], [(day, 1.5)])

    def test_h4_on_the_utc_grid(self):
        h1 = [{"time": 14400 * 2 + 3600 * i, "open": i, "high": 10 + i, "low": -i, "close": i + 0.5, "tick_volume": 1}
              for i in range(6)]
        h4 = gold.to_h4(h1, now=14400 * 4 - 1)
        self.assertEqual(len(h4), 1)                                       # 12:00-16:00 is still forming
        self.assertEqual((h4[0]["time"], h4[0]["open"], h4[0]["high"], h4[0]["low"], h4[0]["close"],
                          h4[0]["tick_volume"]), (28800, 0, 13, -3, 3.5, 4))


if __name__ == "__main__":
    unittest.main()
