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

    out = {"routes": {}, "duration": {}}

    for route in routes:
        dur_row = conn.execute(
            "SELECT AVG(duration_mins), MIN(duration_mins), "
            "MAX(duration_mins), COUNT(*) FROM offers "
            "WHERE route = ? AND duration_mins IS NOT NULL",
            (route,),
        ).fetchone()

        stop_row = conn.execute(
            "SELECT stops, COUNT(*) as n FROM offers WHERE route = ? "
            "GROUP BY stops ORDER BY n DESC LIMIT 1",
            (route,),
        ).fetchone()

        if dur_row and dur_row[3]:
            out["duration"][route] = {
                "avg_mins": round(dur_row[0]),
                "min_mins": dur_row[1],
                "max_mins": dur_row[2],
                "typical_stops": stop_row[0] if stop_row else None,
                "n_offers": dur_row[3],
            }

        by_month = defaultdict(list)   # departure_date -> [prices]
        curves = defaultdict(list)     # departure_date -> [{days_before, price}]
        offers_by_month = defaultdict(list)  # departure_date -> [offer dicts]

        rows = conn.execute(
            "SELECT departure_date, days_before, price FROM prices "
            "WHERE route = ? ORDER BY departure_date, days_before DESC",
            (route,),
        ).fetchall()

        for r in rows:
            by_month[r["departure_date"]].append(r["price"])
            curves[r["departure_date"]].append(
                {"days_before": r["days_before"], "price": r["price"]})

        offer_rows = conn.execute(
            "SELECT departure_date, days_before, price, airline, stops, "
            "duration_mins, collected_date FROM offers WHERE route = ? "
            "ORDER BY departure_date, price ASC",
            (route,),
        ).fetchall()

        for r in offer_rows:
            offers_by_month[r["departure_date"]].append({
                "days_before": r["days_before"],
                "price": r["price"],
                "airline": r["airline"],
                "stops": r["stops"],
                "duration_mins": r["duration_mins"],
                "collected_date": r["collected_date"],
            })

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
                "offers": offers_by_month.get(dep_date, []),
            })

        out["routes"][route] = months

    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2)

    total = sum(len(v) for v in out["routes"].values())
    print(f"wrote {OUT_PATH}")
    print(f"{len(routes)} routes, {total} route-months")
    for route, months in out["routes"].items():
        pts = sum(m["n"] for m in months)
        dur = out["duration"].get(route)
        dur_str = ""
        if dur:
            h, m = divmod(dur["avg_mins"], 60)
            dur_str = f", ~{h}h{m:02d}m avg, {dur['typical_stops']} stop(s) typical"
        print(f"  {route:<10} {len(months)} months, {pts} price points{dur_str}")


if __name__ == "__main__":
    main()
