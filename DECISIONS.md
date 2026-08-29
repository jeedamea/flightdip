# Decision log

Why this project is built the way it is. Kept as [Architecture Decision Records](https://adr.github.io/), one entry per real choice, including the trade-off I accepted. Written as decisions get made, not reconstructed after the fact.

---

## 1. Track fixed calendar dates, not a rolling window

**Decision.** Track departures on the 15th of each of the next 12 months, for every route. 5 routes times 12 dates, 60 flights.

**Why.** This measures two things at once from a single dataset.

Across the twelve dates you get seasonality, a Christmas departure against an April one. Within each date, as real time passes and the departure gets closer, you get lead time, how price moves at 90 days out versus 30 versus 7.

Because the dates sit a month apart, one collection day samples every lead-time bucket at the same time. Roughly 34 days out on one flight, 65 on the next, 95 on the next, all the way out to a year. It's a panel design, and the interesting result lives in the interaction between the two axes, not in either one alone.

The question worth answering isn't "when is flying cheapest." It's given that I have to fly at Christmas, when should I buy. A December flight at £700 might be a better buy than an April flight at £400, if £700 is December's actual floor. Measuring just one axis could never tell me that.

**Trade-off.** The 15th is arbitrary and might not represent its month well. A single day of month can't pick up within-month effects, and the 15th never lands on the same weekday twice, so day of week ends up confounded with month. Accepted for now. Adding a second day per month would double the API cost.

---

## 2. Use Google's backfilled price history instead of daily polling

**Decision.** Rely on SerpApi's `price_insights.price_history`, which hands back roughly 61 days of prior daily prices per query, rather than querying every flight every day.

**Why.** One call returns about 61 data points instead of 1. A 60-call sweep gives roughly 3,600 price points straight away instead of after two months of daily waiting. Sweeping every week or so means consecutive 61-day windows overlap heavily, so the series stays continuous even though I'm only actually collecting four times a month.

Polling daily would cost around 1,800 searches a month, about £75 at SerpApi's paid rates, just to get data Google is already handing over for free.

**Trade-off.** I'm trusting Google's history instead of observing prices myself, so whatever gaps or revisions exist upstream, I inherit. The overlap between sweeps doubles as a consistency check though, if a price for a given day changes between two collections, that's visible.

---

## 3. Four sweeps a month, sized to fit the free tier

**Decision.** Sweep on the 5th, 12th, 19th and 27th.

**Why.** SerpApi's free tier is 250 searches a month. 60 per sweep times 4 sweeps is 240. Fits, with 10 spare, and that 10 covers a handful of manual test runs without tipping over into an early plan renewal.

The four dates sit roughly a week apart, which keeps the 61-day windows comfortably overlapping while spreading the live offer snapshots evenly through the month.

**Trade-off.** The whole schedule is really shaped by a pricing tier, not by what's statistically ideal. If the budget grew, the first thing I'd spend it on is more tracked dates, a second day each month, not more frequent sweeps, since sweep frequency is already mostly redundant given the backfill.

---

## 4. Split each sweep across multiple runs

**Decision.** 40 searches at 06:10 UTC, 20 at 08:10, and a catch-up run at 10:10 that does nothing if the sweep already finished.

**Why.** The free tier also caps you at 50 searches an hour, and a sweep is 60, so it has to be split regardless.

I went with 40/20 instead of 50/10 on purpose. Sitting exactly on a hard limit means any surprise breaks you. GitHub's own scheduler routinely starts cron jobs 5 to 20 minutes late, so two runs that are nominally an hour apart can end up much closer together than that. Two hours of spacing plus a 10-search buffer means a delay can't push me over.

The catch-up run exists because a partially failed sweep would otherwise leave targets uncollected until the next sweep day, silently. Costs nothing when there's nothing left to do.

**Trade-off.** Three scheduled runs is more moving parts than one. Worth it though, because the failure it prevents, a silent data gap, is exactly the kind you don't notice until you're actually trying to analyse the data.

---

## 5. A work queue, so sweeps are resumable

**Decision.** A `targets` table holding every route and date pair with a `last_collected` timestamp. Each run claims the stalest targets not touched in the last 3 days.

**Why.** This is what makes the split in #4 work without the two runs needing to coordinate at all. Run 1 collects 40 and stamps them, run 2 asks the same question and naturally finds the remaining 20. No shared state, no run counter, nothing for the two to collide on.

It's self-healing too. Crash at target 25, the next run resumes at 25. API down all morning, the catch-up run handles it. Add a route to the config and it just adds targets to the queue.

**Trade-off.** A bit more code than a flat loop over a list. Worth it, a plain loop would restart from zero on every failure and burn searches re-collecting data it already had.

---

## 6. Enforce uniqueness in the schema, not in code

**Decision.** `prices` has a primary key on `(route, departure_date, price_date)`. `offers` has a unique constraint across the full offer shape plus collection date.

**Why.** Every sweep refetches an overlapping 61-day window, so the same price point turns up repeatedly by design. The first version of this project had no constraints at all, and the database filled with duplicates. A test rebuild of that exact behaviour turned 305 stored rows into 28 real ones. Any average computed over that would have been silently wrong, weighted by how many times a point happened to get recollected.

Putting the constraint in the database rather than in Python means it holds no matter what writes to it later, including a future script, or me at 2am not thinking straight.

**Trade-off.** None really. This should have been there from day one.

---

## 7. Commit the database to the repository

**Decision.** `data/flightdip.db` gets committed after every sweep by the CI job. This is a technique known as [git scraping](https://simonwillison.net/2020/Oct/9/git-scraping/).

**Why.** Zero infrastructure, zero cost. The git history doubles as both the backup and the audit trail. Every sweep is a commit, so you can see exactly what the dataset looked like on any past date, and diff two sweeps against each other.

**Trade-off.** SQLite is binary, so git can't store diffs, each commit holds a full copy of the file. Fine at four sweeps a month and this row count, but it compounds. If the file ever passes about 10MB the fix is making an append-only CSV the real source of truth and rebuilding the database from it, since text deltas only cost the new bytes.

---

## 8. Run on GitHub Actions rather than my own machine

**Decision.** Scheduled through `.github/workflows/collect.yml`.

**Why.** Time series collection has one hard requirement, it actually has to happen. A scheduled task on a laptop misses any day the laptop's shut, and a missed day can't be recovered, nobody sells you last month's prices. Actions runs on GitHub's own servers, free for public repos.

**Trade-off.** Cron there is UTC only and not guaranteed to be punctual, so nothing in the design can assume exact timing, and nothing does.

---

## 9. Stop the run immediately on quota errors

**Decision.** A 401 or 429, or a quota message in the response body, aborts the whole run instead of retrying or skipping past it.

**Why.** Ordinary failures are worth retrying, quota exhaustion isn't, every further attempt is guaranteed to fail. On SerpApi exceeding your allowance triggers an early plan renewal rather than a soft stop, so failing loudly and immediately is genuinely the cheaper outcome.

Every target gets committed as soon as it succeeds, not at the end of the run, so an abort still keeps everything collected up to that point.

**Trade-off.** A single transient 429 ends the run early. The catch-up run from #4 covers it.

---

## 10. Drill down to real offers, not just the aggregate curve

**Decision.** Clicking a month in the dashboard doesn't just show the average price curve, it shows every individual offer collected for that month, airline, price, stops, duration, days before departure. Clicking a specific point on the curve filters that list down to just that day.

**Why.** An average, or a cheapest ever seen number, hides what you'd actually be booking. £400 could be a direct flight or two connections and 40 hours of travel, the aggregate can't tell you which, and the whole reason I started tracking duration and stops in the first place was to be able to answer that.

**Trade-off.** Offers get collected far less often than price history, see #2, the backfill only exists for price, not for live offers. So this table is often sparse early on, especially for months that haven't had many sweeps yet. Shown honestly as no offer data yet rather than hidden away.

---

## 11. A value-of-time score for recommendations, not just cheapest

**Decision.** Alongside the absolute cheapest and fastest options, a recommended pick balances price against duration using a value-of-time score, price plus extra hours beyond the fastest option, times a rate.

**Why.** Cheapest and fastest are both bad defaults sitting alone. The cheapest option on a long haul route is often the slowest by a wide margin, sometimes £50 cheaper for 6 extra hours, which most people wouldn't actually pick if the trade-off were spelled out instead of buried in a sorted list. Putting a £ per hour price on the extra time turns two units that don't compare, pounds and hours, into one number that does.

**Trade-off.** The rate is an assumption, not a fixed constant. #12 covers how that assumption changed and why it has to get calculated per month instead of declared upfront.

---

## 12. The value-of-time rate has to be calculated per month, not pooled

**Decision.** The £ per hour rate used in #11 is calculated separately for each departure month, using a linear regression of price against duration across that month's own offers, not pooled across the whole route.

**Why, and this one's a real mistake I caught, not something I got right first try.** The first version pooled every offer ever seen on a route across all twelve months into one regression. It gave back a rate that looked plausible, around £11 an hour on MAN-BKK, but it was wrong in a specific, findable way. Price varies hugely by season for reasons that have nothing to do with duration, December costs more than April regardless of whether that particular December flight happens to be long or short. Pooling seasons together let that seasonal swing drown out the actual duration signal. The regression came back timid because it was mostly fitting season, not time, which is a textbook case of omitted variable bias, where leaving a real driver of price out of the equation lets its effect bleed into the coefficient of whatever is left in.

I caught this on instinct, not because the code threw an error. A recommendation had picked a fare I genuinely wouldn't have chosen myself, for a reason that sounded fine but wasn't, an implausibly low £ per hour rate, and tracing why led straight to the confound. Scoping the regression to one month at a time holds season fixed, so what's left in the data is actually the price-for-time relationship, not just a rediscovery of the calendar.

**Trade-off.** Restricting to one month means less data per calculation than pooling ever gave. A real rate can only get calculated once a month has enough offers with enough spread in duration, right now that's at least 6 offers and at least 3 distinct durations. Below that a duration-band fallback estimate gets shown instead, labelled honestly as an assumption rather than a finding. Expect that fallback to fire a lot until sweeps build up, that's the correct trade for a number that actually means what it claims, rather than a fast one that doesn't.

---

## 13. A leaked API key, and what changed after

**Decision.** After noticing a real SerpApi key had been committed inside `.env.example` before `.gitignore` existed, I rotated the key straight away, and checking git history for anything similar became a standing habit after any change that touches credentials or config files.

**Why.** Fixing a file in a later commit doesn't remove it from history, anyone can still open the earlier commit and read the original version. GitHub's own commit history makes this easy to check, and just as easy to miss. The only real fix once something's been committed is treating it as burned and rotating it, not trying to scrub history afterwards, which is unreliable and easy to mess up.

**Trade-off.** None, this is pure downside avoided. Worth logging anyway, because I made this mistake and here's exactly what I did about it is a more honest record than a decisions log that pretends everything went right the first time.

---

## Known limitations

- **One source.** Everything comes from Google Flights via SerpApi. Prices are indicative, not bookable, and coverage varies by carrier.
- **One day per month.** See #1, can't separate day of week from month within a single departure date, though the day of week breakdown in the dashboard now uses the dense price history series to look at this across the collection dates instead.
- **Sparse live offers.** Airline level offers get captured 4 times a month, the dense series is price history, which is aggregate.
- **No booking-class detail.** One way, cheapest available, no cabin or baggage normalisation. Two £400 fares aren't necessarily the same product.
- **Value-of-time rates are estimates until a month has enough offers to calculate one properly.** See #12.
