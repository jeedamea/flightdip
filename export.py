"""
Export flightdip.db into a single JSON file the dashboard can load.

Run it after every pull:
    python export.py

Writes data/export.json. That file is what you load into the dashboard's
"Load data" button - re-run this and reload it any time you've pulled fresh
data from the bot.
"""

import json
import sqlite3
from collections import defaultdict

DB_PATH = "data/flightdip.db"
OUT_PATH = "data/export.json"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    routes = [r[0] for r in conn.execute(
        "SELECT DISTINCT route FROM prices ORDER BY route")]

    out = {"routes": {}}

    for route in routes:
        by_month = defaultdict(list)   # departure_date -> [prices]
        curves = defaultdict(list)     # departure_date -> [{days_before, price}]

        rows = conn.execute(
            "SELECT departure_date, days_before, price FROM prices "
            "WHERE route = ? ORDER BY departure_date, days_before DESC",
            (route,),
        ).fetchall()

        for r in rows:
            by_month[r["departure_date"]].append(r["price"])
            curves[r["departure_date"]].append(
                {"days_before": r["days_before"], "price": r["price"]})

        months = []
        for dep_date in sorted(by_month.keys()):
            prices = by_month[dep_date]
            n = len(prices)
            avg = sum(prices) / n
            variance = sum((p - avg) ** 2 for p in prices) / n
            months.append({
                "departure_date": dep_date,
                "min": min(prices),
                "max": max(prices),
                "avg": round(avg, 1),
                "stddev": round(variance ** 0.5, 1),
                "n": n,
                "curve": sorted(curves[dep_date],
                                key=lambda c: c["days_before"]),
            })

        out["routes"][route] = months

    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2)

    total = sum(len(v) for v in out["routes"].values())
    print(f"wrote {OUT_PATH}")
    print(f"{len(routes)} routes, {total} route-months")
    for route, months in out["routes"].items():
        pts = sum(m["n"] for m in months)
        print(f"  {route:<10} {len(months)} months, {pts} price points")


if __name__ == "__main__":
    main()
