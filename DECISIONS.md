# Decision log

Why this project is built the way it is. Kept as
[Architecture Decision Records](https://adr.github.io/) — one entry per real
choice, including the trade-off I accepted. Written as decisions are made, not
reconstructed afterwards.

---

## 1. Track fixed calendar dates, not a rolling window

**Decision.** Track departures on the 15th of each of the next 12 months, for
every route. 5 routes × 12 dates = 60 flights.

**Why.** This measures two things at once from a single dataset.

*Across* the twelve dates you get **seasonality** — a Christmas departure
against an April one. *Within* each date, as real time passes and the
departure gets closer, you get **lead time** — how price moves at 90 days out
versus 30 versus 7.

Because the dates sit a month apart, one collection day samples every lead-time
bucket simultaneously: ~34 days out on one flight, ~65 on the next, ~95 on the
next, all the way to a year. This is a panel design, and the interesting result
lives in the interaction between the two axes, not in either alone.

The question worth answering isn't "when is flying cheapest" — it's *"given
that I must fly at Christmas, when should I buy?"* A December flight at £700
may be a better buy than an April flight at £400, if £700 is December's floor.
Anything that measured only one axis couldn't tell me that.

**Trade-off.** The 15th is arbitrary and may not represent its month. A single
day-of-month can't detect within-month effects, and the 15th never lands on the
same weekday twice, so day-of-week is confounded with month. Accepted for now;
adding a second day per month would double the API cost.

---

## 2. Use Google's backfilled price history, not daily polling

**Decision.** Rely on SerpApi's `price_insights.price_history`, which returns
roughly 61 days of prior daily prices per query, rather than querying every
flight every day.

**Why.** One call returns ~61 data points instead of 1. A 60-call sweep yields
about 3,600 price points immediately rather than after two months of waiting.
Sweeping every ~7 days means consecutive 61-day windows overlap heavily, so the
series stays continuous even though I'm only collecting four times a month.

Polling daily would cost roughly 1,800 searches a month — around £75 at
SerpApi's paid rates — to obtain data Google is already handing over for free.

**Trade-off.** I'm trusting Google's history rather than observing prices
myself, so I inherit whatever revisions or gaps exist upstream. The overlap
between sweeps is partly a consistency check: if a price for a given day
changes between two collections, that's visible.

---

## 3. Four sweeps a month, sized to fit the free tier

**Decision.** Sweep on the 5th, 12th, 19th and 27th.

**Why.** SerpApi's free tier allows 250 searches/month. 60 per sweep × 4 sweeps
= 240. That fits with 10 spare, and 10 is enough headroom for a handful of
manual test runs without triggering an early plan renewal.

The four dates are roughly a week apart, which keeps the 61-day windows
comfortably overlapping while spreading the live-offer snapshots evenly through
the month.

**Trade-off.** The whole schedule is shaped by a pricing tier rather than by
what's statistically ideal. If the budget grew, the first thing I'd buy is more
tracked dates — a second day each month — not more frequent sweeps, because
sweep frequency is already largely redundant given the backfill.

---

## 4. Split each sweep across multiple runs

**Decision.** 40 searches at 06:10 UTC, 20 at 08:10, and a catch-up run at
10:10 that does nothing if the sweep already finished.

**Why.** The free tier also caps throughput at 50 searches/hour, and a sweep is
60. So it has to be split regardless.

I chose 40/20 rather than 50/10 deliberately: sitting exactly on a hard limit
means any surprise breaks you. GitHub's scheduler routinely starts cron jobs
5–20 minutes late, so two runs nominally an hour apart can end up much closer
together. Two hours of spacing plus a 10-search buffer means a delay can't
push me over.

The catch-up run exists because a partially failed sweep would otherwise leave
targets uncollected until the next sweep day, silently. It costs nothing when
there's nothing to do.

**Trade-off.** Three scheduled runs is more moving parts than one. Justified by
the fact that the failure it prevents — silent data gaps — is exactly the kind
you don't notice until you're trying to analyse the data.

---

## 5. A work queue, so sweeps are resumable

**Decision.** A `targets` table holding every route/date pair with a
`last_collected` timestamp. Each run claims the stalest targets not touched in
the last 3 days.

**Why.** This is what makes the split in #4 work without any coordination
between runs. Run 1 collects 40 and stamps them; run 2 queries the same way and
naturally finds only the remaining 20. No shared state, no run counter, no way
for the two to collide.

It's also self-healing. If a run crashes at target 25, the next one resumes at
25. If the API is down all morning, the catch-up run handles it. Adding a route
to the config just adds targets to the queue.

**Trade-off.** Slightly more code than a flat loop over a list. Worth it — a
loop would restart from zero on every failure and re-spend searches on data
already collected.

---

## 6. Enforce uniqueness in the schema, not in code

**Decision.** `prices` has a primary key on `(route, departure_date,
price_date)`. `offers` has a unique constraint on the full offer shape plus
collection date.

**Why.** Every sweep re-fetches an overlapping 61-day window, so the same price
point arrives repeatedly by design. The first version of this project had no
constraints and the database filled with duplicates — a test reconstruction of
that behaviour turned 305 stored rows into 28 real ones. Aggregates computed
over it would have been silently wrong, weighted by how many times each point
happened to be re-collected.

Putting the constraint in the database rather than in Python means it holds no
matter what writes to it — including a future script, or me at 2am.

**Trade-off.** None meaningful. This should have been there from the start.

---

## 7. Commit the database to the repository

**Decision.** `data/flightdip.db` is committed after every sweep by the CI job.
This is the technique known as
[git scraping](https://simonwillison.net/2020/Oct/9/git-scraping/).

**Why.** Zero infrastructure and zero cost. The git history becomes both the
backup and the audit trail — every sweep is a commit, so it's possible to see
exactly what the dataset looked like on any past date, and to diff two sweeps
against each other.

**Trade-off.** SQLite is a binary, so git can't store diffs — each commit holds
a full copy of the file. At four sweeps a month and this row count that's
fine, but it compounds. If the file passes ~10 MB the fix is to make an
append-only CSV the source of truth and rebuild the database from it, since
text deltas cost only the new bytes.

---

## 8. Run on GitHub Actions rather than my own machine

**Decision.** Scheduled via `.github/workflows/collect.yml`.

**Why.** Time-series collection has one hard requirement: it has to actually
happen. A scheduled task on a laptop misses any day the laptop is shut, and a
missed day is unrecoverable — nobody sells you last month's prices.
Actions runs on GitHub's servers, free for public repositories.

**Trade-off.** Cron there is UTC-only and not guaranteed punctual, so the
schedule can't assume exact timing. Nothing in the design depends on it.

---

## 9. Stop the run immediately on quota errors

**Decision.** A 401 or 429, or a quota message in the response body, aborts the
entire run rather than being retried or skipped.

**Why.** Ordinary failures are worth retrying; quota exhaustion isn't — every
further attempt is guaranteed to fail. On SerpApi, exceeding the allowance
triggers an early plan renewal rather than a soft stop, so failing loudly and
immediately is the cheap outcome.

Every target is committed as soon as it succeeds rather than at the end of the
run, so an abort keeps everything collected up to that point.

**Trade-off.** A single transient 429 ends the run early. The catch-up run in
#4 covers it.

---

## Known limitations

- **One source.** Everything is Google Flights via SerpApi. Prices are
  indicative rather than bookable, and coverage varies by carrier.
- **One day per month.** See #1 — can't separate day-of-week from month.
- **Sparse live offers.** Airline-level offers are captured 4× a month; the
  dense series is the price history, which is aggregate.
- **No booking-class detail.** One-way, cheapest available, no cabin or
  baggage normalisation. Two £400 fares aren't necessarily the same product.
