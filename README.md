# Outsampler API — OpenBB Integration Guide

Outsampler is an AI-powered market intelligence platform that monitors news, press releases, and SEC filings for tracked assets, scores them for materiality, and surfaces the most relevant signals to investors.

---

## How It Works

Outsampler monitors a watchlist of assets and continuously processes incoming information from multiple sources:

- **News and press releases** via Benzinga, refreshed every 15 minutes
- **SEC filings** (8-K, 10-K, 10-Q, Form 4) as they are filed
- **Private company intelligence** via PM Insights

Each piece of content is passed through a two-stage AI scoring pipeline that evaluates probability of price impact, magnitude, novelty, source credibility, and relationship to the primary asset via its driver network (competitors, supply chain, macro factors). The result is a severity label and score for each alert.

Intelligence briefs are AI-generated narrative summaries of the day's material activity for each tracked asset, produced once daily at 04:00 AM UTC. All data is scoped to your watchlist — you only see alerts for assets you track.

---

## Base URL

```
https://app.outsampler.com
```

---

## Authentication

All endpoints require an API key passed as a request header:

```
X-API-Key: your-api-key
```

To request an API key, contact **info@outsampler.com**

---

## Severity Scale

| Indicator | Label | Score | Meaning |
|-----------|-------|-------|---------|
| 🔴 | Critical | ≥ 80 | Significant material event |
| 🟡 | Watch | 60–79 | Notable development worth monitoring |
| 🔵 | Info | 40–59 | Informational, low immediate impact |
| — | None | < 40 | Routine, no severity shown |

Scores are normalised to a 0–100 scale. The daily summary severity indicator reflects the highest-scoring alert of that day.

---

## Endpoints

### GET /api/v1/watchlist

Returns all assets on the user's tracked watchlist.

**Auth:** Required | **Params:** None

```bash
curl -H "X-API-Key: your-key" "https://app.outsampler.com/api/v1/watchlist"
```

**Response:**
```json
[
  {
    "ticker": "ORCL",
    "name": "Oracle",
    "type": "public",
    "sector": "Technology",
    "industry": "Computer Software: Prepackaged Software"
  },
  {
    "ticker": "SpaceX",
    "name": "SpaceX",
    "type": "private",
    "sector": "Industrials",
    "industry": "Aerospace & Defense"
  }
]
```

| Field | Type | Description |
|-------|------|-------------|
| ticker | string | Ticker symbol or internal identifier |
| name | string | Company name |
| type | string | `public`, `private`, or `commodity` |
| sector | string | Sector classification |
| industry | string | Industry classification |

---

### GET /api/v1/alerts/feed

Returns scored, non-suppressed alerts for a ticker on a given date, formatted as a newsfeed and sorted by severity (CRITICAL → WATCH → INFO).

**Auth:** Required

| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| ticker | string | yes | — | Ticker symbol e.g. `ORCL` |
| date | string | no | today | Date in `YYYY-MM-DD` format |

```bash
curl -H "X-API-Key: your-key" \
  "https://app.outsampler.com/api/v1/alerts/feed?ticker=ORCL&date=2026-04-14"
```

**Response:**
```json
[
  {
    "title": "Nvidia's Biggest Risk Could Be A Mineral Nobody Has Heard Of",
    "date": "2026-04-14T13:18:11+00:00",
    "author": "Surbhi Jain",
    "excerpt": "Oracle could experience indirect ripple effects from supply chain disruptions.",
    "body": "**Severity:** WATCH\n\n**Summary:** Nvidia and the entire AI boom may hinge on gallium...\n\n**Why it matters:** Nvidia faces potential supply risks...\n\n**Via:** NVIDIA (Supply Chain)\n\n**Source:** [BENZINGA NEWS](https://...)"
  }
]
```

| Field | Type | Description |
|-------|------|-------------|
| title | string | Alert headline |
| date | string | ISO 8601 timestamp of the source document |
| author | string | Article author or provider name |
| excerpt | string | AI-generated impact statement for the primary asset |
| body | string | Full markdown-formatted alert detail including severity, summary, reasoning, driver relationship, and source link |

**Notes:** Suppressed alerts are excluded. Alerts via driver relationships include a `Via:` line in the body. Returns an empty array if no alerts exist for the given date.

---

### GET /api/v1/summaries/markdown

Returns an AI-generated daily intelligence brief as plain text markdown, with a severity indicator based on the day's highest-scoring alert.

**Auth:** Required

| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| ticker | string | yes | — | Ticker symbol e.g. `ORCL` |
| date | string | no | today | Date in `YYYY-MM-DD` format |

```bash
curl -H "X-API-Key: your-key" \
  "https://app.outsampler.com/api/v1/summaries/markdown?ticker=ORCL&date=2026-04-14"
```

**Response** (plain text markdown):
```
# ORCL — 2026-04-14

🔴 **Critical**

Goldman Sachs forecasts a 220% surge in data center demand by 2030, potentially
boosting Oracle's cloud and infrastructure services...

---
*Want to track additional assets? Contact us at info@outsampler.com*
```

**Notes:** Returns `No intelligence brief available for this date.` if no brief exists. Severity indicator is omitted if the day's top score is below 40. Briefs are generated once daily at 04:00 AM UTC.

---

### GET /api/v1/asset-brief

Returns a combined markdown snapshot of the intelligence brief, key driver relationships, and top WATCH/CRITICAL alerts for a ticker on a given date.

**Auth:** Required

| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| ticker | string | yes | — | Ticker symbol e.g. `ORCL` |
| date | string | no | today | Date in `YYYY-MM-DD` format |

```bash
curl -H "X-API-Key: your-key" \
  "https://app.outsampler.com/api/v1/asset-brief?ticker=ORCL&date=2026-04-14"
```

**Response** (plain text markdown):
```
# ORCL

## Intelligence Brief — 2026-04-14
Goldman Sachs forecasts a 220% surge in data center demand by 2030...

## Key Relationships
| Company        | Type         | Ticker |
|----------------|--------------|--------|
| AWS            | Competitor   | AMZN   |
| Microsoft Azure| Competitor   | MSFT   |
| NVIDIA         | Supply Chain | NVDA   |
| Salesforce     | Competitor   | CRM    |

## Alerts — 2026-04-14
🟡 **Nvidia's Biggest Risk Could Be A Mineral Nobody Has Heard Of**

Oracle could experience indirect ripple effects from supply chain disruptions...
*Via: NVIDIA (Supply Chain)*

---

🟡 **Lowey Dannenberg P.C. Investigates Oracle for Breaches of Fiduciary Duties**

This investigation could lead to financial penalties, reputational damage...

---
*Want to track additional assets? Contact us at info@outsampler.com*
```

**Notes:** Only WATCH and CRITICAL alerts are shown (INFO excluded). Maximum 5 alerts shown. Driver relationships are specific to the authenticated user's configuration.

---

### GET /api/v1/summaries

Returns the raw daily summary as JSON including the numeric alert score. Use this when you need the score programmatically rather than the formatted markdown version.

**Auth:** Required

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| ticker | string | yes | Ticker symbol e.g. `ORCL` |
| date | string | yes | Date in `YYYY-MM-DD` format |

```bash
curl -H "X-API-Key: your-key" \
  "https://app.outsampler.com/api/v1/summaries?ticker=ORCL&date=2026-04-14"
```

**Response:**
```json
{
  "status": "success",
  "summary": "Goldman Sachs forecasts a 220% surge in data center demand by 2030...",
  "score": 0.82
}
```

| Field | Type | Description |
|-------|------|-------------|
| status | string | `success` or error detail |
| summary | string | AI-generated brief text, or `null` if none exists |
| score | float | Highest alert score for the day, normalised 0.0–1.0 |

---

### GET /api/v1/info

Returns trial account information as formatted markdown.

**Auth:** Required | **Params:** None

```bash
curl -H "X-API-Key: your-key" "https://app.outsampler.com/api/v1/info"
```

**Response** (plain text markdown):
```
# Outsampler   — Trial Access

You are currently on a **trial account** with access to the following assets:
META, ORCL, NVDA, AAPL, SpaceX.

To track additional assets or upgrade to a paid plan, contact us at
**info@outsampler.com**
```

---

## OpenBB Workspace Setup

1. Go to [pro.openbb.co](https://pro.openbb.co)
2. Right-click on a dashboard → **Add data**
3. Enter `c` as the backend URL
4. Add header: `X-API-Key` → `your-api-key`
5. Click **Test** — OpenBB fetches `/widgets.json` automatically
6. Five widgets appear under the **Outsampler** category

| Widget | Type | Params |
|--------|------|--------|
| Watchlist | Table | None |
| Alerts Feed | Newsfeed | Ticker, Date |
| Daily Intelligence Brief | Markdown | Ticker, Date |
| Asset Intelligence | Markdown | Ticker, Date |
| About Outsampler | Markdown | None |

All widgets with a Ticker or Date parameter can be updated interactively using the parameter toolbar at the top of each widget in OpenBB Workspace.

---

## Adding Assets to Your Watchlist

Trial accounts include a fixed watchlist. To track additional assets or upgrade to a paid plan, contact **info@outsampler.com**
