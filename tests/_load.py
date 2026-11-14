"""Load a script from a skill folder (the folder names have hyphens)."""

import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")


def mt5_csv(chart_text, path, symbol="TSLA"):
    """A Yahoo chart fixture rewritten as mt5_data.py's CSV (M5 rows, UTC seconds)."""
    import csv
    import json
    r = json.loads(chart_text)["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "timeframe", "time", "time_utc", "open", "high", "low", "close",
                    "tick_volume", "spread", "real_volume"])
        for i, t in enumerate(r["timestamp"]):
            row = [q[k][i] for k in ("open", "high", "low", "close")]
            if t is None or None in row or t % 300:
                continue
            w.writerow([symbol, "M5", t, "", *row, q["volume"][i] or 0, 0, 0])
            w.writerow([symbol, "H1", t, "", *row, 0, 0, 0])   # other timeframes must be ignored


def load(folder, name):
    path = os.path.join(ROOT, folder, name + ".py")
    spec = importlib.util.spec_from_file_location("trader_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
