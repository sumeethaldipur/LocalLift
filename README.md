# LocalLift

**LocalLift is an agentic local-news intelligence platform that helps cafés turn nearby developments into timely marketing and business decisions.**

The home page is a continuously updated **Local Briefing**: *What's happening around your café — and what it means for you*. Each briefing item follows one flow: local news or an official signal → investigation and verification → impact on the café → recommended action → monitoring and revision. Ranked event opportunities, the map, phased campaigns and the business-impact model all hang off that briefing.

The app opens on **Opening Act Café**, a *fictional* café at a scenario coordinate near Seventh Avenue and West 30th Street in Midtown Manhattan (40.7480, -73.9920).
- **Real data:** events, news, official announcements and social posts.
- **Fictional data:** the café, its costs and its 8-week order history. These are labelled as fictional everywhere they appear.

---

## Quick start

```bash
cd ~/Desktop/LocalLift
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # then fill in what you have (see "Source setup")
```

Run the **web app** and the **background worker** in two terminals. The worker must keep running so the briefing refreshes while the dashboard is closed; `worker.py --once` runs the due jobs and exits, if you prefer cron or launchd.

```bash
.venv/bin/uvicorn app:app --port 8765
```

```bash
.venv/bin/python worker.py
```

Then open http://localhost:8765.

```bash
.venv/bin/python -m pytest -q                       # 56 deterministic tests (labelled fixtures, separate DB)
.venv/bin/python scripts/live_check.py              # live connectivity report → docs/LIVE_INTEGRATION_RESULTS.md
.venv/bin/python worker.py --job news_discovery     # force one job now
```

> A security hook on this Mac blocks running `node` directly, so LocalLift is built in Python (FastAPI + SQLite + vanilla JS + Leaflet) instead of the spec's suggested Next.js. The spec allows a different stack.

---

## Source setup

| Source | Category | Cost | What to do |
|---|---|---|---|
| **Gothamist RSS** | news (reporting) | Free | Nothing; works now. |
| **GDELT DOC API** | news (reporting) | Free | Nothing. It throttles this network heavily, so the app paces calls at 20 s and retries on later runs. |
| **NYC Permitted Event Information** (Open Data `tvpp-9vvx`) | news (official announcement) | Free | Nothing; works now. Street fairs, parades, plaza events and full street closures in Community Boards 4–5 and Precincts 10/13/14. |
| **MTA subway alerts** (GTFS-RT JSON) | news (official announcement) | Free | Nothing; works now. Only alerts that name 34 St–Penn Station or 34 St–Herald Sq as affected. |
| **NYC Parks events** (`w3wp-dpdi`) | events | Free | Nothing; works now. |
| **Bluesky search** | social | Free | Works now without login. Set `BSKY_HANDLE` and `BSKY_APP_PASSWORD` only if it starts refusing requests. |
| **Ticketmaster Discovery API** | events | Free key | See "Which Ticketmaster API?" below. |
| **Twitter/X** | social | Paid | See "Twitter/X" below. |
| **Agent model** | — | Free options | See "Agent model" below. |

### Which Ticketmaster API?

Only the **Discovery API (v2)**. It is the public search API for events, venues and attractions, and is the one LocalLift calls (`/discovery/v2/events.json` and `/discovery/v2/events/{id}.json`). You don't need the Commerce, Partner, Publish or International Discovery APIs.

1. Register at https://developer-acct.ticketmaster.com/user/register.
2. Open **My Apps**. The default app's **Consumer Key** is your API key; it covers the Discovery API at the default 5,000 calls/day and 5 requests/s. You don't need the Consumer Secret.
3. Put the key in `.env` as `TICKETMASTER_API_KEY=...`, restart the app and worker, then press **Refresh now**.

### Twitter/X

- **Adapter:** built. It uses recent search and recent counts, which degrade independently, and has a monthly spend guard. Credential status: no token configured, so the app shows **Setup required**.
- **Finding:** X has no free read/search tier for new developers. Reads have been pay-per-use since February 2026 (about $0.005 per post read according to third-party summaries; check https://docs.x.com/x-api/getting-started/pricing). A free X account does not include API search.
- **Proposed cap:** $5/month (about 50 searches at 20 posts each).
- **Next step, only if you approve spending:**
  1. Create a developer app at https://developer.x.com.
  2. Add credit yourself.
  3. Set `X_BEARER_TOKEN`, `ENABLE_PAID_SOURCES=true` and `X_MONTHLY_BUDGET_USD=5`.
  4. Run `scripts/live_check.py`.
- **Groq vs. Grok:**
  - **Groq** (groq.com) is a model-hosting company and can only be the *agent model*. It has no connection to X and cannot fetch tweets.
  - **Grok** (xAI) has an `x_search` tool. It is also paid (about $5 per 1,000 posts fetched as of September 2026), it returns model-mediated summaries rather than raw search results and counts, and it would still be labelled "via xAI". That makes it no cheaper or more complete than the official X API, so it is not wired in.

### Agent model (free options)

The agent needs a model that supports tool calling. Options:

| Option | Cost | `.env` |
|---|---|---|
| **Groq, free tier** | Free with rate limits | `MODEL_PROVIDER=openai_compatible`<br>`OPENAI_COMPAT_BASE_URL=https://api.groq.com/openai/v1`<br>`OPENAI_COMPAT_MODEL=llama-3.3-70b-versatile`<br>`OPENAI_COMPAT_API_KEY=` (key from https://console.groq.com/keys) |
| **Google Gemini API, free tier** | Free with rate limits. Free-tier prompts may be used to improve Google products. | `OPENAI_COMPAT_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/`<br>`OPENAI_COMPAT_MODEL=gemini-3.5-flash`<br>key from https://aistudio.google.com/apikey |
| **Ollama** (runs locally) | Free | `ollama pull qwen2.5:7b`<br>`OPENAI_COMPAT_BASE_URL=http://localhost:11434/v1`<br>`OPENAI_COMPAT_MODEL=qwen2.5:7b`<br>`OPENAI_COMPAT_API_KEY=ollama` |
| **Claude API** | Paid per token. A Claude.ai subscription does not cover API usage. | `MODEL_PROVIDER=anthropic`<br>`ANTHROPIC_API_KEY=...`<br>`MODEL_INPUT_COST_PER_MTOK=4`<br>`MODEL_OUTPUT_COST_PER_MTOK=20` |

- **No model configured:** all live data still works. Each briefing item shows a **rule-based** interpretation labelled "Inference — rule-based, no agent", and the header shows **Agent setup required**.
- **Model configured:** agent interpretations are saved as **"Agent draft — pending your review"**. They never silently replace an approved campaign or owner assumptions.

---

## Full test flow (about 15 minutes)

Open **http://localhost:8765**. The app and `worker.py` must be running.

**A. Briefing: what's happening**
1. Read the four tiles, then click the **Top pick** tile. The event panel opens.
2. In the panel, look at **Overview** (fit score, crowd **EST** and how it was estimated), **News** (only articles about this event), **Buzz** (X and Bluesky totals with trend %) and **Sources**.
3. Press **Check news & buzz** and watch the button spinner. Re-checks within 6 hours skip the paid X post search.

**B. The agent (agentic features)**
4. Still in the panel, open **Ask AI** and press **Why this event?**. The answer has labelled facts and inferences with source IDs. Open **Steps the agent took** to see its tool calls.
5. Type `Let me know if this game gets called off` and press **Ask**, or press **Follow this**. A green "Now following" banner appears and the question shows under **Following for this event**.
6. Close the panel. In **Your research agent · Follow a question** at the top, open **Questions you're following**. Your question is there; expand it, then press **Check now**.
7. Type your own question in the top box (e.g. `Tell me if Penn Station subway service is disrupted this week`) and press **Follow this**.

**C. Rush Radar**
8. Scroll to **Rush Radar**. Click the day with the red **!** cells (over capacity).
9. Read the busiest hours, who's coming and the staffing suggestion. Press **Copy staff briefing** and paste it somewhere.

**D. Local news & alerts**
10. On a card with **Get free promo kit**, press it and copy the sign or story text.
11. On any card, press **Look into it**. It checks the official source, then the agent investigates. Open **History** to see the decision.

**E. Campaign**
12. On the #1 pick, press **Plan campaign**. Check the event special (Strategy tab), the 5-step timeline, and the tasks you can tick off.
13. Edit the Instagram caption in **Posts & copy** and press Tab away; it's saved and marked "your edit".
14. Press **Mark as ready**, then **Start campaign**.

**F. Results**
15. Open **Results**. If Expected = Busy, the banner explains that the café is full.
16. In **What if you could serve more?**, try 60 orders/hour, then **Compare scenarios**. Open **Adjust assumptions**, change a number and press **Recalculate**.
17. Record an outcome (redemptions, sales, costs) and press **Save outcome**.

**G. Controls**
18. **Pulse icon:** health of each source. **Bell:** alerts, including "Your question was answered" when a followed question is met. **Shop icon:** change the radius to 1 mile, press **Save** and watch the rankings update.

## How to use LocalLift (button by button)

Keep the app and the worker running (see Quick start), then open **http://localhost:8765**.

**1. Briefing tab (home) — "what's happening around my café?"**
- **Agent panel:** **Investigate top pick**, or type a question in **Follow this** to have the agent keep watch (see "Follow a question").
- **Rush Radar** (below the shortlist): click a day to see the busiest hours and staffing, then **Copy staff briefing**.
- **Four tiles at the top:** events nearby, news & alerts, your **Top pick** (click it to open that event), and **Campaigns** (click it to jump to your plans).
- **Live ticker:** click any headline to open it.
- **Refresh** (top right): pulls fresh events and news now. It takes about 30–60 s.

**2. Best opportunities (3 cards + map) — the main output, built from events, news and social buzz together**
- **Each card shows:** date, distance, **Crowd** (a ballpark number, e.g. ~16k, marked EST), **Buzz** (total posts, split as X · Bluesky), and the **Fit ring** (0–100).
- **Details** opens the side panel:
  - **Overview:** fit breakdown, plus the crowd estimate and how it was made.
  - **News:** coverage of the event.
  - **Buzz:** real X and Bluesky posts.
  - **Ask AI:** try "Why this event?", "What would change your mind?", type your own question, or press **Full investigation** (about 1 minute).
  - **Sources:** where every fact came from.
- **Buttons in the panel:**
  - **Check news & buzz:** fetches fresh news plus Bluesky and X. X is checked at most once every 6 h per event, at about $0.05 per check.
  - **Follow:** alerts you if the event is cancelled or moved.
  - **Not interested:** removes it from your rankings.
- **Map:** click any dot to open the same panel. The dashed line is straight-line distance. **Show all nearby events** opens the full list (7 or 14 days).

**3. Local news & alerts cards**
- **Click a card** to open the side panel. It has three tabs:
  - **Overview:** why it matters and what to do.
  - **Evidence:** the sources and how sure we are.
  - **History:** what changed and the decisions taken.
- **The white button on each card** is the suggested move. It depends on timing, opening hours and the kind of item:
  - **Get free promo kit:** for a nearby gathering in the next few days while you're open. Opens ready-to-copy sidewalk-sign and Instagram-story text that costs $0.
  - **Brief your staff:** for a transit or street disruption in the next two days. Opens a copyable staff heads-up.
  - **Keep an eye on it:** not urgent yet; follow it and you'll get an alert if it changes.
  - **See why:** it happens while you're closed, so the advice is to skip it.
  - **Look into it:** when the date or details aren't confirmed. It checks the official source and asks the AI agent.
  - **Plan campaign / Review plan:** when the news is about an event you could target, or one you already have a plan for.
- **Accept AI summary:** appears when the agent has written its own read of the story.

**4. Plan campaign → Campaigns tab**
- **Money tiles:** the offer, what you keep per order, the budget, and the expected extra profit.
- **5-step timeline:** click a step to see its tasks, then tick them off.
- **Tabs:**
  - **Strategy:** edit the goal, audience and pitch.
  - **Posts & copy:** edit any text, then press **Copy** to paste it into Instagram or email.
  - **Budget:** what the campaign spends.
  - **What if…:** backup plans.
- **Moving a plan forward:** **Mark as ready → Start campaign → Mark as done**. Use **Copy plan** or **Download** to export it.
- **Suggested update banner:** appears when the event changes. Choose **Apply update** or **Keep current**; your own edits are always kept.

**5. Results tab**
- **Pick a campaign** from the menu at the top right. You'll see **If it's slow / Expected / If it's busy** profit, plus "Can the café handle it?" capacity bars.
- **Adjust assumptions:** change the crowd share, conversion and so on, then press **Recalculate**.
- **Record what happened** (right side): enter redemptions, sales and costs, then press **Save outcome**. The campaign is marked done.

**6. Icons at the top right**
- **Pulse icon:** data source health.
- **Shop icon:** café profile. Change the radius, hours or budgets, then **Save**; rankings update straight away.
- **Bell:** alerts.

**5-minute demo script**
1. On the Briefing page, read the tiles.
2. Open **Details** on the #1 pick, then look at the **Buzz** tab and use **Ask AI → "Why this event?"**.
3. Press **Plan campaign**. Click through the 5 steps, tick a task, and edit the Instagram caption.
4. Open **Results**, see the expected profit, open **Adjust assumptions**, change a number, and press **Recalculate**.
5. Back in Campaigns, press **Mark as ready → Start campaign**, then record an outcome in Results.
6. In the **Profile** (shop icon), set the radius to 1 mile and watch the rankings change. Then check the **bell** and **sources**.

## Walkthrough: one real development, traced end to end

Retrieved live on Oct 27, 2026 (UTC) from NYC Open Data `tvpp-9vvx`, record `permit:981376`:

1. **What happened** *(official announcement)*: "Permitted plaza partner event: Music Performances And Fitness Classes And Circus Arts", Greeley Square Plaza (Broadway between W 32nd and W 33rd St). The briefing card links to the city dataset and shows when it was last checked.
2. **When and where it matters:** Wed Oct 28, 12 PM. That time comes from the permit record, not a publication date. The location is street-segment text only; the city publishes no coordinates, so the briefing shows the item **without a map pin** and lists "no precise location (not pinned)" under its limitations.
3. **Why it matters to Opening Act** *(rule-based inference, labelled as such)*: a nearby public gathering could bring extra foot traffic during café hours. It is about two blocks from the café, but the app does not show a distance because no coordinates were published.
4. **What to do:** **Investigate**, because no verified event record or size estimate exists yet; confirm the time, place and expected size before spending. Pressing **Investigate** runs the primary-source check: the official record *is* the primary source, so the decision is "no change — keep monitoring the official record". That decision is saved, and **Evidence & what changed** shows the source, versions and decision history. With a model configured, the same button also starts an agent investigation.
5. **How certain:** an official record from one source; no organizer size estimate. The card's "would change if" line names the evidence that would raise the recommendation: a confirmed time inside café hours and an organizer estimate of attendance.

The briefing shows what live data exists at that moment; when nothing relevant is found, it says so ("successful empty search") and separates that from source failures.

**Change handling** is demonstrated with **labelled test fixtures**, not live news (`tests/test_briefing.py`):
- **AC14:** a report that "Fixture Band show moved to earlier start" queues a primary-listing check.
  - If confirmed, the app proposes a re-timed campaign with before/after timing and contribution, and recalculates the projection only because its inputs changed. Owner edits and completed tasks are preserved.
  - If the listing is unchanged, the claim is marked **disputed**, the decision is an explicit **"no change"**, and the event is watched.
- **AC15:** a street-fair report with no Ticketmaster match appears in the briefing. A syndicated copy merges into the same item, and the unknown location stays unpinned. A verified cancellation leads the briefing while being excluded from marketing opportunities.

---

## Using the app

- **Briefing** (home):
  - Café context and last successful update.
  - **Needs your review:** plan revisions proposed after material changes, with before/after.
  - Three to five **development cards**. Each has: what happened (source-type labels: reporting, official announcement, event listing, social observation), when and where it matters, why it matters, what to do, how certain, and what is affected. Cards are marked **New** or **Updated**.
  - Selecting a card highlights related opportunities and map markers.
  - **Evidence & what changed** opens the sources, version history and decisions (source change → affected assumption → recommendation).
  - Below the cards: the **top three opportunities** with the map, and the full event list.
- **Opportunity detail:**
  - Event facts, linked briefing developments, "What is being reported", social signals (including the Twitter/X status), audience needs, why this opportunity, **Ask the agent**, and sources.
  - **Build marketing plan** creates a full phased plan.
- **Campaigns:**
  - Phases: Assess and prepare, Build awareness, Convert interest, Execute, Learn and retain. A same-day plan collapses to Prepare now, Activate, Review.
  - Includes tasks you can check off, editable copy, a budget capped at $75/event and $200/week, contingencies and alternatives.
  - Proposed revisions show before/after and keep your edits.
  - States: Draft → Ready for owner use → Active → Completed → Archived. Nothing is ever posted, sent or bought.
- **Results:** editable scenario assumptions → low/base/high incremental contribution, break-even, and hourly capacity/seat checks. You can record and delete outcomes.
- **Secondary controls:** Sources (connector status, jobs, errors), Profile (editable, validated), Alerts (deduplicated, with affected plans and before/after).

---

## How it works

```
locallift/
  sources/                 ticketmaster, nycparks, news (Gothamist, GDELT), nyc_official (permits, MTA alerts),
                           social (Bluesky, X), approved (allow-listed page fetch + robots.txt), http (allow-list, pacing)
  ingest.py                events upsert, duplicate merge, change detection, news↔event links, social storage
  developments.py          news-led developments, grouping/dedupe, locations, rule-based briefing items, priority
  verify.py                primary-listing verification of reported changes → decision records
  ranking.py, finance.py   opportunity score; demand/capacity/contribution scenarios
  plans.py                 phased plans, retiming, owner edits, revisions with before/after, versions
  notify.py, jobs.py       alerts (affected plans, before/after); scheduler with locks/heartbeat
  agent/                   providers (Anthropic SDK / OpenAI-compatible), typed tools, adaptive loop
app.py, worker.py, static/ API + UI, background worker
tests/                     56 tests on labelled fixtures in a separate DB
```

### Developments and briefing priority

- **What becomes a development:** a local report or official record that affects demand, timing or access near the café. Types: cancellation, schedule change, access change, gathering, or coverage of a linked event.
- **What does not:** multi-topic roundups, retrospective stories, and general city news. Syndicated copies merge into one item. A cancellation is a risk, so it can lead the briefing without becoming a marketing opportunity.
- **Briefing priority** is editorial and separate from the opportunity score:
  - Affects an active plan: +40
  - Linked eligible event: +15
  - Located within the radius: +10
  - Type: cancellation 30, schedule change 25, access 20, gathering 15
  - Time to act: +15 within 3 days, +8 within 7
  - Freshness: +10 within 24 h, +5 within 72 h
  - Evidence: +5 for 2+ sources, +5 for an official or listing source, −5 if disputed

### Rush Radar (new)

An hour-by-hour grid of the **expected extra walk-ins** from nearby events over the next 7 days, from 7 AM to 10 PM, compared against the café's orders-per-hour limit.
- **How each event is counted:** crowd estimate × share of attendees passing the café (falls off steeply with distance) × 25% conversion, spread over the two hours before the event.
- **Overlapping events** add up against the same limit.
- **Clicking a day** shows the busiest hours, who's coming, a staffing suggestion (the owner decides), a **Copy staff briefing** button and **Plan campaign**.
- It is a planning estimate, not a forecast.

### Follow a question (new, agentic)

In the agent panel on the Briefing page, type a question with a condition, e.g. "Tell me if the Harry Styles show time changes or Penn Station service is disrupted that day".
- The research agent checks it straight away with its tools.
- It re-checks every 6 hours (worker job `question_watch`).
- It must end each check with `VERDICT: MET / NOT MET`.
- An alert is sent **only when the verdict flips to MET**, never for repeats.
- Each check is a stored investigation with a tool log and sources.

### Social trends

- **Bluesky:** logged-out search returns only the newest ~100 posts. When either window is cut off, LocalLift compares **posting rates** (posts per hour over the span each sample covers) between the last 24 h and the previous 24 h. With complete windows it compares counts.
- **X:** with `X_INCLUDE_COUNTS=true`, X's counts endpoint gives **total** matching posts per hour, so the 24 h vs. previous 24 h trend uses real volume. In testing this did not use post-read quota; $0.01 per call is booked against the cap as a precaution.

### Event-themed specials

Evening and ticketed events get a **fan-inspired special built from the existing menu**, instead of a generic combo:
- **Proposal:** the AI agent suggests a name and pairing tied to the event, such as a song title, the tour or the team's colours. It picks one drink and an optional treat, plus at most one cheap add-on (≤ $0.40/serving, flagged "needs your OK").
- **Code checks** run before anything is accepted:
  - Every item must exist on the menu.
  - The artist, team or venue name may not appear in the product name, so nothing implies endorsement.
  - Lyrics are never quoted; song titles only.
  - Price (≈15% off buying the items separately), cost and margin are calculated by code, not by the AI.
- **Examples (live):** Harry Styles at MSG → "As It Was Brew" (orange vanilla cold brew + cookie + orange-sugar rim, $9.00, $5.78 kept per order). Knicks → an orange-zest cold brew in team colours.
- **Fallbacks:** without a model, a themed default is used (e.g. "Encore Cold Brew + Cookie"). Same-day plans drop any new ingredient.

### News relevance

An article is linked to an event only if its **headline names the performer or event and also shows this event's context**: the venue (e.g. Madison Square Garden / MSG), the city, or event words (tour, concert, show, residency, tickets, game…).

Search engines match full text, so a celebrity mentioned anywhere in a gossip story isn't enough. Re-checking existing links with this rule dropped 21 of 25 as irrelevant.

### Opportunity ranking

Score = Σ weight × factor/5 × 100, summed over **known** factors only. Unknown factors add nothing and reduce coverage, and anything below 100% coverage is labelled provisional. The score compares options; it is not a success probability. Full rubric is in `locallift/ranking.py`.

| Factor | Weight |
|---|---|
| Distance/access | 25% |
| Timing & hours | 20% |
| Menu/occasion | 20% |
| Demand evidence | 15% |
| Lead time | 10% |
| Budget/capacity | 10% |

Crowd bands: Low < 500, Medium 500–4,999, High ≥ 5,000.

**How the crowd estimate works** (always labelled **EST** in the UI, with its method):

1. **Published attendance estimate:** used as-is. A number counts only if it appears in the cited source text.
2. **Venue capacity from Wikidata** (free, CC0, cited per venue):
   - Expected crowd = capacity × **assumed sell-through of 60% / 80% / 95%**, for low / expected / high.
   - Capacity is matched to the use when Wikidata records one. Example: Madison Square Garden concert configuration 20,000 → about 16,000 expected (range 12k–19k).
   - Capacity is never shown as attendance.
3. **Event-type ballpark:** used when nothing is published. Examples: a parks fitness class ~20 (8–40), a family activity ~40 (15–100), a small theater ~150 (60–400), a club show ~250 (80–700), an outdoor festival ~1,000 (300–3,000). It is always shown as a number with a range and marked EST, and it can be adjusted in Results.

**Projection model when a crowd estimate exists** (all inputs are editable owner assumptions):
- attendees × share who pass the café with time to stop, by straight-line distance:
  - ≤0.25 mi: 2 / 4 / 6%
  - ≤0.5 mi: 1 / 2 / 3%
  - ≤1 mi: 0.4 / 0.8 / 1.5%
- × conversion of 15 / 25 / 35%
- The result is capped at 48 orders/hour minus normal-day orders. Demand above that cap is shown as unserved.

Before this change, Harry Styles at MSG fell back to "normal evening +10%", which projected about −$31. With the crowd-based estimate it is now about +$595 expected (+$126 slow / +$608 busy), with 80 extra orders and about 40 more that the café can't serve.

### Financial model

`incremental contribution = net sales − product/packaging − fees − extra labor − campaign spend − other − promotional loss`

- Each event uses one modeling path: attendance-based (only with a validated estimate) or baseline change.
- Capacity is 48 orders/h, with unmet demand shown separately; overlapping events share that capacity.
- Seat estimate: customers/h × 35% dine-in × 35/60, as an average only.
- The spec's arithmetic example is a unit test: $146.16 contribution, $7.34 per order, break-even 5.

### Agent: automation vs. agency

- **Automation:** scheduled polling, distance and score arithmetic, rule-based briefing items, and primary-listing checks.
- **Agency:** the model picks its next tool from what is missing or conflicting:
  - `getDevelopmentHistory` and `getEventHistory` expose open questions, versions and decisions.
  - `searchNews` accepts a geographic or topic query without an event.
  - `fetchApprovedSource` verifies against official pages.
  - `saveBriefingDraft` and `savePlanDraft` record interpretations and revisions for owner review.
- **Guardrails:**
  - At most 8 retrievals and 60 s per investigation, plus a daily cost cap.
  - Every tool input is validated against its schema.
  - Retrieved text is wrapped as `untrusted_source_text` and scanned for prompt injection.
  - Outbound fetches go only to allow-listed hosts.
  - Fake source IDs are rejected, and all numbers come from code.
- The action log (tool, arguments, result) is stored; no hidden reasoning is stored.

### Background jobs

| Job | Cadence |
|---|---|
| Ticketmaster | 6 h |
| NYC Parks | daily |
| **News-led discovery** (Gothamist, GDELT geographic/topic search, NYC permits, MTA alerts → developments) | 6 h |
| GDELT for watched items | hourly |
| Bluesky buzz for the shortlist (free) | 2 h |
| **Twitter/X buzz for the top 2 picks** (paid, capped) | **once a day**, plus the "Check news & buzz" button. The same event is never re-sampled on X within 6 h. |
| Status of followed events within 24 h | 30 min |
| Verification/reassessment queue | 5 min |
| Agent scan (only with a model) | 12 h |

Jobs use locks, record a heartbeat, and catch up at most once after downtime.

---

## Acceptance criteria status (verified Oct 26–27, 2026)

| ID | Status |
|---|---|
| AC01 Real events | **Done (live).** Ticketmaster returned 300 events within 2 mi (paged; includes MSG concerts and Knicks games) and NYC Parks returned 80. All are persisted with provider IDs, URLs, coordinates and retrieval times. |
| AC02 News-led briefing | **Done (live).** Gothamist, NYC permits and MTA alerts are retrieved independently of events; the home page shows real source-linked developments with what changed, café impact, action and uncertainty, and keeps effective dates separate from publication dates. GDELT is live but heavily throttled. |
| AC03 Social | **Bluesky done (live)**: real samples, windows and trend. It occasionally returns HTTP 403 without login; a free app password fixes that. Twitter/X: **live**; one owner-approved search returned 10 real posts. |
| AC04 Agentic investigation | **Done (live, Gemini).** Investigation `inv-57ff37ad`: with crowd size unknown, the agent tried the Ticketmaster page (refused), then adapted to MSG's official page, ran the calculator, recorded a sourced claim, saved plan copy and set a watch. Details in `docs/LIVE_INTEGRATION_RESULTS.md`. |
| AC05 Distance & map | Done. |
| AC06 Phased plan | Done. |
| AC07 Feasibility | Done. |
| AC08 Financial integrity | Done (12 tests). |
| AC09 Background refresh | Done. The worker ran jobs without a browser and persisted results. |
| AC10 Change handling | Done (test). |
| AC11 Provenance | Done. |
| AC12 Usable app | Done locally. Responsive, keyboard-operable, forms validated. |
| AC13 Twitter/X attempt | **Done (live).** Runs once a day for the top 2 picks and on manual refresh, under an owner-approved $5/month cap (10 posts per search, counts off). Consumer keys exchanged for an app-only token. One recent search returned 10 real posts (truncated sample, resale-heavy, attention ≠ attendance) at about $0.05. Counts endpoint not yet exercised. |
| AC14 News → decision traceability | **Done (controlled fixtures):** confirmed → retimed revision with before/after; unconfirmed → explicit no-change, claim disputed, watched. With a model, the agent also investigates. |
| AC15 Discovery beyond ticket listings | **Done:** controlled tests, plus live NYC permit developments with no Ticketmaster match, unpinned. |

### Live requirements not completed (needs you)

1. **Twitter/X counts:** the counts endpoint is untested. Search works under a $1/month cap; the worker stops X calls when its local spend estimate reaches the cap.
2. **Bluesky app password** (optional, free) to avoid occasional unauthenticated 403s.
3. **Gemini free tier:** newer models are sometimes overloaded (503); LocalLift falls back through `OPENAI_COMPAT_FALLBACK_MODELS`.
4. **Blocked sources:** `www.ticketmaster.com` event pages (HTTP 401) and `nycgovparks.org` pages refuse automated fetches (HTTP 403). GDELT throttles this network (frequent HTTP 429).

### Design

The UI follows current (2026) dashboard conventions:
- **Layout:** a bento grid of stat tiles and a dark "espresso night" theme with frosted-glass cards over a slowly drifting aurora gradient.
- **Motion:** count-up numbers, animated fit-score rings and bars, staggered card entrances, a live headline ticker, a sliding tab indicator, View Transitions between tabs, a spring-in detail panel, and hover glows.
- **Map:** standard OpenStreetMap tiles, darkened with a CSS filter (no API key needed).
- **Accessibility:** all motion is disabled under "reduce motion".

### Known limitations

- Single-business local prototype with no login. Add server-side authorization before any public or multi-user use.
- Without a model, briefing interpretations are rule-based and generic, and labelled as such.
- City permits have no coordinates, so they never get a pin or a distance.
- Product assumptions have not been validated with café owners. Every projection is a planning scenario.
