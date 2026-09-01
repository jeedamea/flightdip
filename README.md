# FlightDip

## What it does

Five routes, tracked on the 15th of every month for the next year, swept automatically four times a month via GitHub Actions. Every sweep pulls both Google's backfilled price history and live flight offers (airline, duration, stops) for each tracked date.

That data feeds a self-contained dashboard:

- **Seasonality view.** Average, lowest and highest price per month, with the observed range, for whichever route you pick.
- **Click a month** and you get the buy-timing curve: price against days before departure, with a smoothed trend line so a single weird day doesn't get mistaken for a real pattern.
- **Recommendations.** Not just cheapest. A value-of-time score balances price against duration, calculated from that month's own offers (see `DECISIONS.md` #12 for why it has to be scoped that tightly).
- **Day-of-week breakdown** and a full offers table, filterable down to a specific day before departure by clicking the curve.

Every non-obvious choice behind how this works, and a couple of things I got wrong and fixed, is written up in **[DECISIONS.md](DECISIONS.md)**. That's honestly the part worth reading, not just the code.

---

## Try it yourself

You don't need my data for this. Anyone can run it against their own routes.

**1. Get a SerpApi key.**
[serpapi.com](https://serpapi.com), sign up (free tier is roughly 100 to 250 searches a month, check your own dashboard), then copy your API key.

**2. Clone and configure.**

```bash
git clone https://github.com/YOURNAME/flightdip.git
cd flightdip
pip install -r requirements.txt

cp .env.example .env
```

Open `.env` and swap the placeholder for your real key.

**3. Pick your own routes.**

Open `collect.py` and find the `ROUTES` list near the top:

```python
ROUTES = [
    ("MAN", "BKK"),
    ("MAN", "AMS"),
    ("MAN", "BCN"),
    ("SIN", "JFK"),
    ("SIN", "KUL"),
]
```

Swap in whatever origin and destination pairs you actually care about (IATA airport codes). More routes means each one gets swept less often on a free tier quota, `DECISIONS.md` #3 has the actual arithmetic if you want to resize the schedule.

**4. Test cheap, then collect for real.**

```bash
set CALL_BUDGET=2
python collect.py
```

Two calls, just to prove your key and schema work before you spend real quota on it. Once that's clean, run it manually with a bigger budget, or push to your own GitHub and let `.github/workflows/collect.yml` handle it on a schedule.

**5. Look at it.**

```bash
python export.py
```

Then open `dashboard/index.html` in a browser and load the `data/export.json` it just wrote.

---

## Repo structure

```
collect.py                       the collector, quota-safe and resumable
export.py                        SQLite to JSON, with all the derived stats
dashboard/index.html             the dashboard, open it directly, no server needed
data/flightdip.db                the database, committed after every sweep
.github/workflows/collect.yml    the automated schedule
DECISIONS.md                     why every non-obvious choice was made
```

---

## Roadmap

- [x] Panel design: fixed monthly dates across 5 routes, for seasonality and lead time in one dataset
- [x] Quota-safe, resumable, automated collection
- [x] Buy-timing curves with noise-resistant trend smoothing
- [x] Value-of-time recommendations, calculated from real offer data
- [x] Day-of-week breakdown
- [ ] A full year of coverage across every route
- [ ] A push notification, limit-order style, when a tracked flight drops below its own historical floor
- [ ] Public write-up of what the first year of data actually shows

---

## Known limitations

- **Single source.** Everything comes from Google Flights via SerpApi. Indicative, not bookable, and coverage varies by carrier.
- **Sparse live offers.** Quota rotation means offers (airline, duration, stops) are sampled far less often than the price history curve.
- **Value-of-time rates need enough same-month offers to calculate.** Early on, most months will just show an assumed fallback rate rather than one derived from real data. That's deliberate, see `DECISIONS.md` #12.
- **No booking-class detail.** Cheapest available fare, no cabin or baggage normalisation.
- **The database gets committed as a binary.** Fine at this size, would need rethinking well past where this currently sits.

---

## Licence

MIT.
