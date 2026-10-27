# LocalLift vs. the Newsren assignment

The Newsren plan (`Newsren_Plan.pdf`, October 2026) is a proposed example. Its non-negotiable point (p. 1) is **agentic capability**: "The agent must have a defined goal, access to appropriate tools, and a controlled process for gathering evidence. Merely calling an LLM to generate a summary does not make a feature agentic."

LocalLift applies that blueprint to one domain: local news and events for a café owner. This document maps the plan to what is built and shows where the agentic behavior is evidenced.

## Agentic core (p. 1, p. 6)

| Plan requirement | LocalLift | Evidence |
|---|---|---|
| Defined goal | Each run has an explicit goal: investigate an event, answer an owner question, assess a development, scan the week, or **monitor a followed question** | `locallift/agent/run.py` prompts |
| Access to appropriate tools | 13 typed tools: event discovery, topic/event news search, social search, approved-page fetch, event and development history, distance, ranking, demand/contribution calculator, claim recording, briefing and plan drafts, watches | `locallift/agent/tools.py` |
| Controlled process | Schema-validated tool calls; at most 8 retrievals, 60 s and 12 turns per run; daily cost cap; allow-listed hosts and robots.txt; untrusted-text wrapping and prompt-injection flags; numbers only from code | `agent/run.py`, `agent/tools.py`, `sources/http.py` |
| Orchestrator + specialist roles | The agent orchestrates deterministic specialist services: source discovery (adapters), evidence verification (`verify.py`, claim checks), story intelligence (`developments.py`), explanation (briefing items, plans) and a quality gate (owner review states) | module layout |
| Plans, adapts and reports what it could not verify | Live run `inv-57ff37ad`: the Ticketmaster page refused access, so the agent switched to the official MSG page, recorded a claim and saved a plan. Live run `inv-285ab1da` (follow-a-question): checked the listing, news (rate-limited, reported as such) and history, then returned **VERDICT: NOT MET** with cited facts and stated unknowns | `docs/LIVE_INTEGRATION_RESULTS.md`, investigations table |

## The six proposed features, adapted

| Newsren feature | LocalLift equivalent | Status |
|---|---|---|
| 2.1 My Newsren: personal research agent | The **Local Briefing** for the café's location, hours, menu and budget, plus the research agent ("Investigate", "Ask AI") | Built |
| 2.2 Story Intelligence | **Developments** group reporting, official records and listing changes into one evolving story, with a version history ("What changed?") and decisions recorded as before/after | Built |
| 2.3 News-to-Learning (audience adaptation) | The same evidence re-explained for different audiences: the owner (briefing, plan), **staff** (one-tap Rush Radar staff briefing), customers (promo copy) and organizers (outreach draft) | Built for this domain; no quizzes or classroom material, which belong to the education vertical |
| 2.4 Industry Impact | "Why it matters to Opening Act": impact on hours, capacity, demand and plans, labelled as inference; financial scenarios computed by code | Built |
| 2.5 Evidence and Bias Inspection | Source types (reporting, official, listing, social); claim labels (fact, inference, assumption, unknown, disputed); primary-listing verification; disputed claims preserved; news relevance rule; Wikidata plausibility checks | Built |
| **2.6 Follow This Story** | **Follow a question:** the owner writes a question and condition; the agent re-checks it every 6 hours with its tools and alerts only when the verdict flips to MET, so repeats and opinion pieces don't trigger alerts | **Built (new)** |

## Critical product requirements (p. 6)

- **Source records kept for every item:** source URL, publication time and retrieval time for events, news, social posts and claims.
- **Disagreement shown, not hidden:** conflicting claims become "disputed"; unconfirmed reports lead to an explicit "no change" decision.
- **Forecasts labelled:** crowd figures are marked EST with their method; projections are labelled planning scenarios; trends state their method.
- **Restricted access:** approved APIs only, allow-listed page fetches, robots.txt respected, no scraping around blocks (Ticketmaster and NYC Parks pages that refuse access are reported, not bypassed).
- **Human approval:** nothing is posted, sent or bought. Plans and AI drafts await owner review; paid X reads run only under the owner-approved cap.

## Build order (p. 7)

| Stage | Status |
|---|---|
| 1. Foundation: approved feeds, deduplication, citations, summaries | Done: 9 live sources, duplicate and syndication merging, cited sources |
| 2. Ask and explain: Q&A grounded in sources, explanation modes | Done: Ask AI with cited, labelled answers; owner, staff and customer outputs |
| 3. Connect and follow: timelines, topic tracking, change detection | Done: developments, change detection, deduplicated alerts, **Follow a question** |
| 4. Briefings and depth: briefings, impact reports, trackers | Partly done: Rush Radar and staff briefing, campaign reports. Opt-in email delivery is not built; the plan says to require approval before delivery. |

## Not in scope

The four news verticals (Technology, Education, Industry, Finance) and their editorial products. LocalLift is a single-domain adaptation; the same architecture would carry them.

## Success metrics (p. 8) we can measure today

- **Trust:** every number cites a source or is labelled an assumption; numeric claims must appear in the source text.
- **Cost:** request log with per-provider counts; model usage and cost; X spend against its cap.
- **Engagement:** followed events and questions, and alerts sent vs. re-checks (alerts fire only when a verdict flips).
- **Outcomes:** recorded redemptions, sales and costs per campaign.

Not yet measured: reader usefulness ratings and claim-support sampling. These need real users.
