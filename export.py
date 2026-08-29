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
from datetime import datetime

DB_PATH = "data/flightdip.db"
OUT_PATH = "data/export.json"

# Fallback bands, in £/hour, used only when there isn't enough offer data
# on a route yet to calculate a real rate. See value_per_hour_for_route().
FALLBACK_BANDS = [
    (3,  10), (6,  15), (10, 20), (999, 25),
]

MIN_OFFERS_FOR_REGRESSION = 6   # below this, a slope is just noise

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def weekday_name(date_str):
    return WEEKDAYS[datetime.strptime(date_str, "%Y-%m-%d").weekday()]


def fallback_rate(fastest_mins):
    hours = fastest_mins / 60
    for ceiling, rate in FALLBACK_BANDS:
        if hours < ceiling:
            return rate
    return FALLBACK_BANDS[-1][1]


def value_per_hour_for_route(all_offers_this_route):
    """
    Try to derive £/hour of travel time from this route's OWN offers,
    instead of guessing.

    Method: linear regression of price against duration across every offer
    seen on this route. If longer options are systematically cheaper (the
    normal case - connections cost less than directs), the slope is
    negative, and its size in £-per-minute is exactly what the market is
    charging for time saved. Flip the sign, convert to £/hour.

    Falls back to a rough duration-based guess when there isn't enough
    data to trust a slope - a line through 3 points is noise, not a
    finding.
    """
    timed = [o for o in all_offers_this_route if o.get("duration_mins")]
    durations = [o["duration_mins"] for o in timed]

    enough_data = (len(timed) >= MIN_OFFERS_FOR_REGRESSION
                   and len(set(durations)) >= 3)

    if not enough_data:
        fastest = min(durations) if durations else 600
        return {"rate": fallback_rate(fastest), "method": "assumed",
                "n_offers": len(timed)}

    n = len(timed)
    x = durations
    y = [o["price"] for o in timed]
    xbar, ybar = sum(x) / n, sum(y) / n
    num = sum((xi - xbar) * (yi - ybar) for xi, yi in zip(x, y))
    den = sum((xi - xbar) ** 2 for xi in x)
    slope = num / den if den else 0   # £ per minute

    if slope >= 0:
        # Longer flights costing MORE, not less - unusual, and means the
        # data can't support this technique here. Fall back rather than
        # report a value-of-time that implies negative value.
        fastest = min(durations)
        return {"rate": fallback_rate(fastest), "method": "assumed",
                "n_offers": n}

    rate = round(-slope * 60)
    return {"rate": max(rate, 1), "method": "calculated", "n_offers": n}


def build_recommendations(offers, vph_info):
    """
    Cheapest, fastest, and a value-of-time-balanced shortlist.

    vph_info comes from value_per_hour_for_route() - either a real rate
    derived from this route's own price/duration relationship, or a
    fallback guess when there wasn't enough data to calculate one.
    """
    if not offers:
        return None

    timed = [o for o in offers if o.get("duration_mins")]
    if not timed:
        return {"cheapest": min(offers, key=lambda o: o["price"]),
                "fastest": None, "recommended": [], "value_per_hour": None}

    fastest_mins = min(o["duration_mins"] for o in timed)
    vph = vph_info["rate"]
    cheapest = min(offers, key=lambda o: o["price"])
    fastest = min(timed, key=lambda o: o["duration_mins"])

    scored = []
    for o in timed:
        extra_hours = (o["duration_mins"] - fastest_mins) / 60
        score = o["price"] + extra_hours * vph
        scored.append((score, extra_hours, o))
    scored.sort(key=lambda triple: triple[0])

    recommended = []
    seen = set()
    for score, extra_hours, o in scored:
        key = (o["airline"], o["price"], o["duration_mins"])
        if key in seen:
            continue
        seen.add(key)
        recommended.append({
            **o,
            "score": round(score),
            "extra_hours": round(extra_hours, 1),
            "calc": (f"£{o['price']} ticket + {extra_hours:.1f}h extra "
                     f"\u00d7 £{vph}/hr = £{round(score)}"),
        })
        if len(recommended) == 3:
            break

    return {"cheapest": cheapest, "fastest": fastest,
            "recommended": recommended, "value_per_hour": vph,
            "value_per_hour_method": vph_info["method"],
            "value_per_hour_n": vph_info["n_offers"]}



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
            "SELECT departure_date, days_before, price, price_date FROM prices "
            "WHERE route = ? ORDER BY departure_date, days_before DESC",
            (route,),
        ).fetchall()

        weekday_totals = defaultdict(lambda: defaultdict(list))  # dep_date -> weekday -> [prices]

        for r in rows:
            by_month[r["departure_date"]].append(r["price"])
            curves[r["departure_date"]].append(
                {"days_before": r["days_before"], "price": r["price"]})
            weekday_totals[r["departure_date"]][weekday_name(r["price_date"])].append(r["price"])

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

        # Calculate this route's value-of-time rate ONCE, from every offer
        # ever seen on it (pooled across months) - more data than any
        # single month has alone, so the regression has a fighting chance.
        all_offers_this_route = [o for lst in offers_by_month.values() for o in lst]
        vph_info = value_per_hour_for_route(all_offers_this_route)

        months = []
        for dep_date in sorted(by_month.keys()):
            prices = by_month[dep_date]
            n = len(prices)
            avg = sum(prices) / n
            variance = sum((p - avg) ** 2 for p in prices) / n

            weekday_avg = {}
            for day in WEEKDAYS:
                vals = weekday_totals[dep_date].get(day)
                if vals:
                    weekday_avg[day] = round(sum(vals) / len(vals), 1)

            offers = offers_by_month.get(dep_date, [])
            cheapest_airline = None
            if offers:
                cheapest_airline = min(offers, key=lambda o: o["price"])["airline"]

            months.append({
                "departure_date": dep_date,
                "min": min(prices),
                "max": max(prices),
                "avg": round(avg, 1),
                "stddev": round(variance ** 0.5, 1),
                "n": n,
                "curve": sorted(curves[dep_date],
                                key=lambda c: c["days_before"]),
                "offers": offers,
                "weekday_avg": weekday_avg,
                "cheapest_airline": cheapest_airline,
                "recommendations": build_recommendations(offers, vph_info),
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
