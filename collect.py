"""
FlightDip collector.

5 routes x the 15th of the next 12 months = 60 flights tracked.
Sweeps on the 5th, 12th, 19th and 27th of each month.

SerpApi limits: 250 searches/month, 50/hour.
  60 per sweep x 4 sweeps = 240/month. Fits, with 10 spare.
  50/hour cap means one sweep is split across runs (40, then 20).

Each run grabs up to CALL_BUDGET targets that have not been collected in the
last 3 days. So run 1 takes 40, run 2 (two hours later) takes the last 20.
If a run dies halfway, the next one picks up where it stopped. Nothing is
ever collected twice in the same sweep.
"""

import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

# ===========================================================================
# CONFIG - this is the only bit you'll normally edit
# ===========================================================================

ROUTES = [
    ("MAN", "BKK"),   # long haul
    ("MAN", "AMS"),   # short haul
    ("MAN", "BCN"),   # medium haul
    ("SIN", "JFK"),   # longest flight in the world
    ("SIN", "KUL"),   # shortest real international route
]

DAY_OF_MONTH = 15      # track the 15th of each month
MONTHS_AHEAD = 12      # rolling year

CURRENCY = "GBP"
TRIP_TYPE = "2"        # 1 = round trip, 2 = one way

CALL_BUDGET = int(os.getenv("CALL_BUDGET", "40"))   # per run; hourly cap is 50
RESWEEP_AFTER_DAYS = 3   # don't re-collect a target within this many days
SLEEP_BETWEEN_CALLS = 1  # seconds, be polite to the API

DB_PATH = "data/flightdip.db"
ENDPOINT = "https://serpapi.com/search"


# ===========================================================================
# DATABASE
# ===========================================================================

SCHEMA = """
-- Google's ~61-day price history for each tracked flight.
-- Primary key means re-collecting an overlapping window updates rather than
-- duplicating. This is the table that answers "when should I buy".
CREATE TABLE IF NOT EXISTS prices (
    route           TEXT    NOT NULL,
    departure_date  TEXT    NOT NULL,
    price_date      TEXT    NOT NULL,   -- the day this price was observed
    days_before     INTEGER NOT NULL,   -- departure_date - price_date
    price           INTEGER NOT NULL,
    first_seen      TEXT    NOT NULL,   -- when WE collected it
    PRIMARY KEY (route, departure_date, price_date)
);

-- Live offers at the moment of collection, with airline / stops / duration.
CREATE TABLE IF NOT EXISTS offers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    route           TEXT NOT NULL,
    departure_date  TEXT NOT NULL,
    collected_date  TEXT NOT NULL,
    collected_at    TEXT NOT NULL,
    days_before     INTEGER,
    airline         TEXT,
    price           INTEGER,
    duration_mins   INTEGER,
    stops           INTEGER,
    UNIQUE (route, departure_date, collected_date, airline, price,
            duration_mins, stops)
);

-- The work queue. last_collected is what splits a sweep across two runs.
CREATE TABLE IF NOT EXISTS targets (
    route           TEXT NOT NULL,
    departure_date  TEXT NOT NULL,
    last_collected  TEXT,
    fail_count      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (route, departure_date)
);

-- Audit log, so you can always see what the robot did while you slept.
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    calls_used      INTEGER DEFAULT 0,
    targets_ok      INTEGER DEFAULT 0,
    targets_failed  INTEGER DEFAULT 0,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_prices_lookup
    ON prices (route, days_before);
"""


def open_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ===========================================================================
# TARGETS
# ===========================================================================

def wanted_dates(today):
    """The 15th of each of the next MONTHS_AHEAD months."""
    dates = []
    year, month = today.year, today.month
    for _ in range(MONTHS_AHEAD):
        month += 1
        if month > 12:
            month = 1
            year += 1
        dates.append(f"{year}-{month:02d}-{DAY_OF_MONTH:02d}")
    return dates


def sync_targets(conn, today):
    """Register any route/date pair we don't already have. Safe to re-run."""
    added = 0
    for origin, dest in ROUTES:
        for date in wanted_dates(today):
            cur = conn.execute(
                "INSERT OR IGNORE INTO targets (route, departure_date) "
                "VALUES (?, ?)",
                (f"{origin}-{dest}", date),
            )
            added += cur.rowcount
    return added


def pick_targets(conn, today_str, budget):
    """
    Targets not collected in the last RESWEEP_AFTER_DAYS, oldest first.
    NULL (never collected) sorts first in SQLite, which is what we want.
    """
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=RESWEEP_AFTER_DAYS)).isoformat()
    rows = conn.execute(
        """
        SELECT route, departure_date, last_collected
        FROM targets
        WHERE departure_date >= ?
          AND (last_collected IS NULL OR last_collected < ?)
        ORDER BY last_collected ASC, fail_count ASC, departure_date ASC
        LIMIT ?
        """,
        (today_str, cutoff, budget),
    ).fetchall()
    return [dict(r) for r in rows]


# ===========================================================================
# API
# ===========================================================================

class QuotaExhausted(Exception):
    """Out of searches. Stop immediately, don't burn the rest."""


def fetch(origin, dest, outbound_date):
    key = os.getenv("SERPAPI_KEY")
    if not key:
        raise RuntimeError(
            "SERPAPI_KEY not set. Locally: create a .env file. "
            "On GitHub: Settings > Secrets and variables > Actions."
        )

    params = {
        "engine": "google_flights",
        "departure_id": origin,
        "arrival_id": dest,
        "outbound_date": outbound_date,
        "currency": CURRENCY,
        "type": TRIP_TYPE,
        "api_key": key,
    }

    last_error = None
    for attempt in range(1, 4):
        try:
            r = requests.get(ENDPOINT, params=params, timeout=30)

            if r.status_code in (401, 429):
                raise QuotaExhausted(f"HTTP {r.status_code} - out of "
                                     f"searches or rate limited")
            r.raise_for_status()
            data = r.json()

            if "error" in data:
                msg = str(data["error"]).lower()
                if any(w in msg for w in ("run out", "exceed", "quota",
                                          "limit")):
                    raise QuotaExhausted(data["error"])
                raise RuntimeError(data["error"])

            return data

        except QuotaExhausted:
            raise
        except Exception as exc:              # noqa: BLE001
            last_error = exc
            if attempt < 3:
                wait = 2 ** attempt
                print(f"    attempt {attempt} failed, retry in {wait}s")
                time.sleep(wait)

    raise RuntimeError(f"failed 3x: {last_error}")


def days_between(departure_date, price_date):
    d1 = datetime.strptime(departure_date, "%Y-%m-%d")
    d2 = datetime.strptime(price_date, "%Y-%m-%d")
    return (d1 - d2).days


# ===========================================================================
# SAVE
# ===========================================================================

def save_prices(conn, route, dep, data, now):
    history = (data.get("price_insights") or {}).get("price_history") or []
    n = 0
    for entry in history:
        try:
            ts, price = entry
            price_date = datetime.fromtimestamp(
                ts, tz=timezone.utc).strftime("%Y-%m-%d")
            conn.execute(
                """
                INSERT INTO prices (route, departure_date, price_date,
                                    days_before, price, first_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (route, departure_date, price_date)
                DO UPDATE SET price = excluded.price
                """,
                (route, dep, price_date, days_between(dep, price_date),
                 int(price), now),
            )
            n += 1
        except (TypeError, ValueError):
            continue   # one malformed point shouldn't kill the route
    return n


def save_offers(conn, route, dep, data, now):
    flights = (data.get("best_flights") or []) + \
              (data.get("other_flights") or [])
    today = now[:10]
    n = 0
    for offer in flights:
        legs = offer.get("flights") or []
        price = offer.get("price")
        if not legs or price is None:
            continue
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO offers
                (route, departure_date, collected_date, collected_at,
                 days_before, airline, price, duration_mins, stops)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (route, dep, today, now, days_between(dep, today),
             legs[0].get("airline"), int(price),
             offer.get("total_duration"), len(legs) - 1),
        )
        n += cur.rowcount
    return n


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    now_dt = datetime.now(timezone.utc)
    today = now_dt.strftime("%Y-%m-%d")
    started = now_dt.isoformat()

    conn = open_db()
    run_id = conn.execute(
        "INSERT INTO runs (started_at) VALUES (?)", (started,)).lastrowid

    added = sync_targets(conn, now_dt)
    conn.commit()

    targets = pick_targets(conn, today, CALL_BUDGET)

    print(f"FlightDip | {today} | budget {CALL_BUDGET}")
    if added:
        print(f"registered {added} new targets")
    print(f"{len(targets)} target(s) due this run")
    print("-" * 58)

    calls = ok = failed = 0
    note = None

    for i, t in enumerate(targets, 1):
        route, dep = t["route"], t["departure_date"]
        origin, dest = route.split("-")
        print(f"[{i}/{len(targets)}] {route} {dep}", end=" ")

        try:
            data = fetch(origin, dest, dep)
            calls += 1
            stamp = datetime.now(timezone.utc).isoformat()

            p = save_prices(conn, route, dep, data, stamp)
            o = save_offers(conn, route, dep, data, stamp)

            conn.execute(
                "UPDATE targets SET last_collected = ?, fail_count = 0 "
                "WHERE route = ? AND departure_date = ?",
                (stamp, route, dep))
            conn.commit()      # commit per target so a crash loses nothing
            ok += 1
            print(f"-> {p} prices, {o} offers")

        except QuotaExhausted as exc:
            note = f"quota: {exc}"
            print(f"\nSTOPPING - {exc}")
            break

        except Exception as exc:               # noqa: BLE001
            failed += 1
            conn.execute(
                "UPDATE targets SET fail_count = fail_count + 1 "
                "WHERE route = ? AND departure_date = ?", (route, dep))
            conn.commit()
            print(f"-> FAILED: {exc}")

        time.sleep(SLEEP_BETWEEN_CALLS)

    conn.execute(
        "UPDATE runs SET finished_at = ?, calls_used = ?, targets_ok = ?, "
        "targets_failed = ?, notes = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), calls, ok, failed,
         note, run_id))
    conn.commit()

    tp = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    to = conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
    remaining = conn.execute(
        "SELECT COUNT(*) FROM targets WHERE departure_date >= ? AND "
        "(last_collected IS NULL OR last_collected < ?)",
        (today, (now_dt - timedelta(days=RESWEEP_AFTER_DAYS)).isoformat()),
    ).fetchone()[0]
    conn.close()

    print("-" * 58)
    print(f"{calls} calls | {ok} ok | {failed} failed")
    print(f"database now holds {tp:,} price points, {to:,} offers")
    if remaining:
        print(f"{remaining} target(s) left for the next run")

    return 1 if (failed and not ok) else 0


if __name__ == "__main__":
    sys.exit(main())
