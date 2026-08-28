# FlightDip

Building my own dataset to answer a question I couldn't find a real answer to:
**given that you have to fly at a particular time of year, when should you
actually buy the ticket?**

Everyone repeats the same advice — book six weeks out, book on a Tuesday,
prices spike at the weekend. Very little of it is backed by anything you can
check. So I'm collecting the data.

---

## The design

Five routes, tracking departures on the **15th of every month for the next
twelve months** — 60 flights in total, sampled four times a month.

| Route | Why it's in |
|---|---|
| MAN → BKK | long haul |
| MAN → AMS | short haul |
| MAN → BCN | medium haul |
| SIN → JFK | longest scheduled flight in the world |
| SIN → KUL | shortest real international route |

Fixed monthly dates give two variables from one dataset. **Across** the twelve
dates: seasonality. **Within** each date, as departure approaches: lead time.
Because the dates are a month apart, a single collection day samples every
lead-time bucket at once — 34 days out on one flight, 65 on the next, 95 on the
next, all the way to a year.

The answer I'm after lives in the interaction. A £700 December fare might be a
better *buy* than a £400 April fare, if £700 is December's floor and £400 isn't
April's. You can't see that by measuring either axis alone.

**Every design choice, with its reasoning and its trade-off, is written up in
[DECISIONS.md](DECISIONS.md).**

---

## Status

Collecting. Sweeps run automatically on the 5th, 12th, 19th and 27th of each
month via GitHub Actions. Analysis begins once there's enough coverage across
lead-time buckets.

---

## How it works

```
collect.py                       the whole collector
requirements.txt                 dependencies
data/flightdip.db                SQLite database, committed after each sweep
.github/workflows/collect.yml    the schedule
DECISIONS.md                     why any of this is the way it is
```

Each sweep is 60 API calls. SerpApi's free tier allows 250 searches/month and
50/hour, so a sweep is split across runs (40, then 20, then a catch-up) and
four sweeps come to 240/month.

A single query returns Google's ~61-day price history for that flight, so one
sweep yields roughly 3,600 price points rather than 60. That's what makes this
viable on a free tier — see decision #2.

### Tables

- **`prices`** — the ~61-day daily price curve per flight, with
  `days_before` departure precomputed. This is the table that answers the
  question.
- **`offers`** — live offers at collection time, with airline, stops, duration.
- **`targets`** — the work queue that makes sweeps splittable and resumable.
- **`runs`** — audit log, so gaps in the data are explainable.

---

## Running it yourself

```bash
git clone https://github.com/YOURNAME/flightdip.git
cd flightdip
pip install -r requirements.txt

cp .env.example .env     # then paste a SerpApi key into it
CALL_BUDGET=2 python collect.py
```

`CALL_BUDGET` caps how many API calls a run may make. Keep it low while
testing — searches are the scarce resource, not time.

---

## Roadmap

- [x] Panel design: fixed monthly dates × 5 routes
- [x] Quota-safe resumable sweeps
- [x] Automated collection
- [ ] Enough coverage to analyse
- [ ] Does price actually fall as departure approaches, or is that a myth?
- [ ] Optimal buy window per season, per route length
- [ ] Charts and writeup
- [ ] Public dashboard

---

## Licence

MIT.
