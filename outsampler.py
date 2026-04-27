import asyncio
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

from typing import Annotated

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent

base_url = "https://app.outsampler.com/api/v1"

cors_origins = [
    "https://app.outsampler.com",
    "https://pro.openbb.co",
    "https://pro.openbb.dev",
    "http://localhost:1420",
    "http://localhost:6775",
]

# Past-date results from /summaries and /alerts/feed are immutable (briefs are
# generated once at 04:00 UTC; alerts for past days don't change). A simple
# in-memory cache avoids hammering upstream when the dashboard re-fires the
# same fan-out for prior dates. Today's date is always re-fetched.
_UPSTREAM_CACHE: dict[tuple[str, str, str], object] = {}


def _is_immutable_date(date_str: str) -> bool:
    """True if the date is strictly before today (UTC) — safe to cache."""
    today = datetime.now(timezone.utc).date().isoformat()
    return bool(date_str) and date_str < today


tickerParam = Annotated[
    str,
    Query(
        description="The ticker symbol of the asset, e.g. 'AAPL' for Apple Inc.",
    ),
]
dateParam = Annotated[
    str,
    Query(
        description="The date for which to retrieve data.",
        json_schema_extra={"x-widget_config": {"value": "$currentDate-1d"}},
    ),
]


class NewsfeedItem(BaseModel):
    title: str = Field(description="The headline of the news item.")
    date: str = Field(description="The publication date of the news item.")
    author: str = Field(description="The author of the news item.")
    excerpt: str = Field(description="A short excerpt from the news item.")
    body: str = Field(description="The full body of the news item.")


class WatchlistItem(BaseModel):
    ticker: str = Field(
        description="The ticker symbol of the asset, e.g. 'AAPL' for Apple Inc.",
        alias="ticker",
    )
    name: str = Field(description="The full name of the asset, e.g. 'Apple Inc.'")
    asset_type: str = Field(
        description="The type of the asset, one of: 'public', 'private', 'commoidity'",
        alias="type",
    )
    sector: str = Field(description="The sector of the asset, e.g. 'Technology'")
    industry: str = Field(
        description="The industry classification of the asset, e.g. 'Consumer Electronics'"
    )


class PortfolioSnapshotItem(BaseModel):
    ticker: str = Field(description="Ticker symbol.")
    name: str = Field(description="Asset name.")
    sector: str = Field(description="Sector classification.")
    severity: str = Field(
        description="Severity bucket derived from score: Critical / Watch / Info / Quiet."
    )
    score: float = Field(description="Highest alert score for the day.")
    summary_preview: str = Field(
        description="First line of the AI-generated daily intelligence brief."
    )


class MetricItem(BaseModel):
    label: str = Field(description="KPI label.")
    value: str = Field(description="KPI value, formatted as a string.")
    subvalue: str | None = Field(
        default=None, description="Optional sub-value or context line."
    )


class SeverityCount(BaseModel):
    severity: str = Field(description="Severity bucket label.")
    count: int = Field(description="Number of assets in this bucket on the date.")


class SourceCount(BaseModel):
    source: str = Field(description="Source provider label, e.g. BENZINGA NEWS.")
    count: int = Field(description="Number of alerts from this source on the date.")


SEVERITY_LABELS = {
    "Critical": "🔴 Critical",
    "Watch": "🟡 Watch",
    "Info": "🔵 Info",
    "Quiet": "⚫ Quiet",
}
SEVERITY_ORDER = ["Critical", "Watch", "Info", "Quiet"]


def _bare_severity(label: str) -> str:
    """Strip the emoji prefix from a severity label."""
    for key, value in SEVERITY_LABELS.items():
        if label == value:
            return key
    return label


def _summary_preview(text: str | None, limit: int = 220) -> str:
    if not text:
        return ""
    cleaned = " ".join(text.split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=True,
)


@app.exception_handler(httpx.HTTPStatusError)
async def upstream_status_error(request: Request, exc: httpx.HTTPStatusError):
    """Surface upstream Outsampler errors with their original status and body
    instead of collapsing everything into a generic 500."""
    detail = exc.response.text or str(exc)
    return JSONResponse(
        status_code=exc.response.status_code,
        content={"detail": detail, "upstream": str(exc.request.url)},
    )


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/apps.json")
async def apps_json():
    """Serve the OpenBB apps configuration."""
    return json.loads((ROOT / "apps.json").read_text(encoding="utf-8"))


@app.get("/widgets.json")
async def widgets_json():
    """Serve the OpenBB widgets configuration."""
    return json.loads((ROOT / "widgets.json").read_text(encoding="utf-8"))


PUBLIC_PATHS = {"/health", "/apps.json", "/widgets.json"}


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    api_key = request.headers.get("X-API-Key")
    workspace = request.headers.get("X-OpenBB-User")
    if not workspace:
        return JSONResponse(
            status_code=401,
            content={"detail": "Unauthorized."},
        )
    if not api_key:
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing required header: X-API-Key"},
        )
    request.state.api_key = api_key
    return await call_next(request)


@app.get(
    "/outsampler_info",
    openapi_extra={
        "widget_config": {
            "name": "Outsampler Intelligence",
            "type": "markdown",
            "source": ["Outsampler"],
            "category": "Outsampler",
            "mcp_tool": {},
        }
    },
)
async def outsampler_info(request: Request) -> str:
    """
    Outsampler is a market intelligence platform that monitors news, press releases, and SEC filings across a watchlist of assets.
    It scores each item for materiality using a multi-stage AI pipeline, and surfaces the signals that matter — filtered, ranked, and ready to act on.
    This integration brings Outsampler's intelligence directly into OpenBB Workspace as a set of interactive widgets.
    """
    url = f"{base_url}/info"
    headers = {"X-API-Key": request.state.api_key}
    async with httpx.AsyncClient(timeout=60, headers=headers) as http:
        resp = await http.get(url)
        resp.raise_for_status()
        data = resp.read().decode("utf-8")

    return data


@app.get(
    "/outsampler_watchlist",
    response_model=list[WatchlistItem],
    openapi_extra={
        "widget_config": {
            "name": "Outsampler Watchlist",
            "type": "table",
            "gridData": {
                "w": 40,
                "h": 10,
            },
            "data": {
                "table": {
                    "columnsDefs": [
                        {
                            "field": "ticker",
                            "headerName": "Ticker",
                            "renderFn": "cellOnClick",
                            "renderFnParams": {
                                "actionType": "groupBy",
                                "groupBy": {"paramName": "ticker"},
                            },
                        }
                    ]
                }
            },
            "params": [
                {
                    "paramName": "ticker",
                    "show": False,
                }
            ],
            "source": ["Outsampler"],
            "category": "Outsampler",
            "mcp_tool": {},
        }
    },
)
async def outsampler_watchlist(request: Request, ticker: tickerParam = ""):
    """All assets currently on your Outsampler, tracked watchlist."""
    url = f"{base_url}/watchlist"
    headers = {"X-API-Key": request.state.api_key}
    async with httpx.AsyncClient(timeout=60, headers=headers) as http:
        resp = await http.get(url)
        resp.raise_for_status()
        data = resp.json()

    return data


@app.get(
    "/outsampler_alerts",
    openapi_extra={
        "widget_config": {
            "type": "newsfeed",
            "source": ["Outsampler"],
            "category": "Outsampler",
            "mcp_tool": {},
        }
    },
    response_model=list[NewsfeedItem],
)
async def get_alerts(
    request: Request, ticker: tickerParam, date: dateParam
) -> list[NewsfeedItem]:
    """
    Scored, non-suppressed alerts for a ticker on a given date, formatted as a newsfeed and sorted by severity (CRITICAL → WATCH → INFO)
    """
    url = f"{base_url}/alerts/feed?ticker={ticker}&date={date}"
    headers = {"X-API-Key": request.state.api_key}
    async with httpx.AsyncClient(timeout=60, headers=headers) as http:
        resp = await http.get(url)
        resp.raise_for_status()
        data = resp.json()

    return data


@app.get(
    "/outsampler_summary",
    openapi_extra={
        "widget_config": {
            "name": "Asset Summary",
            "type": "markdown",
            "source": ["Outsampler"],
            "category": "Outsampler",
            "mcp_tool": {},
        }
    },
)
async def outsampler_summary(
    request: Request, ticker: tickerParam, date: dateParam
) -> str:
    """AI-generated daily intelligence brief as plain text markdown, with a severity indicator based on the day's highest-scoring alert."""
    url = f"{base_url}/summaries/markdown?ticker={ticker}&date={date}"
    headers = {"X-API-Key": request.state.api_key}
    async with httpx.AsyncClient(timeout=60, headers=headers) as http:
        resp = await http.get(url)
        resp.raise_for_status()
        data = resp.read().decode("utf-8")

    return data


@app.get(
    "/outsampler_brief",
    openapi_extra={
        "widget_config": {
            "name": "Asset Intelligence",
            "type": "markdown",
            "source": ["Outsampler"],
            "category": "Outsampler",
            "mcp_tool": {},
        }
    },
)
async def outsampler_brief(
    request: Request, ticker: tickerParam, date: dateParam
) -> str:
    """A combined markdown snapshot of the intelligence brief, key driver relationships, and top WATCH/CRITICAL alerts for a ticker on a given date."""
    url = f"{base_url}/asset-brief?ticker={ticker}&date={date}"
    headers = {"X-API-Key": request.state.api_key}
    async with httpx.AsyncClient(timeout=60, headers=headers) as http:
        resp = await http.get(url)
        resp.raise_for_status()
        data = resp.read().decode("utf-8")

    return data


async def _fetch_watchlist(client: httpx.AsyncClient) -> list[dict]:
    resp = await client.get(f"{base_url}/watchlist")
    resp.raise_for_status()
    return resp.json()


async def _fetch_summary_for(
    client: httpx.AsyncClient, ticker: str, date: str
) -> dict:
    cache_key = ("summary", ticker, date)
    if _is_immutable_date(date) and cache_key in _UPSTREAM_CACHE:
        return _UPSTREAM_CACHE[cache_key]
    try:
        resp = await client.get(
            f"{base_url}/summaries", params={"ticker": ticker, "date": date}
        )
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception as exc:  # noqa: BLE001 - per-ticker isolation; surface via empty payload
        print(f"[portfolio_snapshot] {ticker} {date} failed: {exc}")
        return {"status": "error", "summary": None, "score": 0.0}
    if _is_immutable_date(date):
        _UPSTREAM_CACHE[cache_key] = data
    return data


async def _fetch_alerts_for(
    client: httpx.AsyncClient, ticker: str, date: str
) -> list[dict]:
    cache_key = ("alerts", ticker, date)
    if _is_immutable_date(date) and cache_key in _UPSTREAM_CACHE:
        return _UPSTREAM_CACHE[cache_key]
    try:
        resp = await client.get(
            f"{base_url}/alerts/feed", params={"ticker": ticker, "date": date}
        )
        resp.raise_for_status()
        data = resp.json() or []
    except Exception as exc:  # noqa: BLE001
        print(f"[portfolio_alerts] {ticker} {date} failed: {exc}")
        return []
    if _is_immutable_date(date):
        _UPSTREAM_CACHE[cache_key] = data
    return data


_SOURCE_RE = re.compile(r"\*\*Source:\*\*\s*\[([^\]]+)\]")


def _extract_source(alert: dict) -> str:
    """Pull a normalized source label like 'BENZINGA NEWS' out of an alert body."""
    body = alert.get("body") or ""
    m = _SOURCE_RE.search(body)
    return m.group(1).strip().upper() if m else "UNKNOWN"


# The upstream daily severity emoji ("🔴 Critical" etc.) is rendered by
# /summaries/markdown using internal logic that doesn't line up with the
# numeric `score` field on a 0-100 scale (observed: score 48.2 → markdown
# Critical). To stay consistent with what the user sees in the markdown
# widget, we parse severity directly from the markdown response rather than
# bucketing the numeric score.
_SEVERITY_EMOJI = (
    ("🔴", "Critical"),
    ("🟡", "Watch"),
    ("🔵", "Info"),
    ("⚫", "Quiet"),
)


async def _fetch_severity_for(
    client: httpx.AsyncClient, ticker: str, date: str
) -> str:
    """Severity bucket as the upstream daily-summary markdown labels it."""
    cache_key = ("severity_md", ticker, date)
    if _is_immutable_date(date) and cache_key in _UPSTREAM_CACHE:
        return _UPSTREAM_CACHE[cache_key]
    try:
        resp = await client.get(
            f"{base_url}/summaries/markdown",
            params={"ticker": ticker, "date": date},
        )
        resp.raise_for_status()
        md = resp.text or ""
    except Exception as exc:  # noqa: BLE001
        print(f"[severity] {ticker} {date} failed: {exc}")
        return "Quiet"
    bucket = "Quiet"
    for emoji, label in _SEVERITY_EMOJI:
        if emoji in md:
            bucket = label
            break
    if _is_immutable_date(date):
        _UPSTREAM_CACHE[cache_key] = bucket
    return bucket


@app.get(
    "/outsampler_portfolio_snapshot",
    response_model=list[PortfolioSnapshotItem],
    openapi_extra={
        "widget_config": {
            "name": "Portfolio Severity Snapshot",
            "type": "table",
            "gridData": {"w": 40, "h": 12},
            "source": ["Outsampler"],
            "category": "Outsampler",
            "mcp_tool": {},
        }
    },
)
async def outsampler_portfolio_snapshot(
    request: Request, date: dateParam
) -> list[PortfolioSnapshotItem]:
    """
    Cross-section snapshot of severity across the entire watchlist for a given date.
    Mirrors the Outsampler Daily Summaries page: one row per tracked asset with
    severity bucket, raw score, and a preview of the day's intelligence brief,
    sorted by score descending.
    """
    headers = {"X-API-Key": request.state.api_key}
    async with httpx.AsyncClient(timeout=60, headers=headers) as http:
        watchlist = await _fetch_watchlist(http)
        summaries, severities = await asyncio.gather(
            asyncio.gather(
                *(_fetch_summary_for(http, item["ticker"], date) for item in watchlist)
            ),
            asyncio.gather(
                *(_fetch_severity_for(http, item["ticker"], date) for item in watchlist)
            ),
        )

    rows: list[PortfolioSnapshotItem] = []
    for asset, summary, severity in zip(watchlist, summaries, severities):
        score = float(summary.get("score") or 0.0)
        rows.append(
            PortfolioSnapshotItem(
                ticker=asset.get("ticker", ""),
                name=asset.get("name", ""),
                sector=asset.get("sector", ""),
                severity=SEVERITY_LABELS.get(severity, SEVERITY_LABELS["Quiet"]),
                score=score,
                summary_preview=_summary_preview(summary.get("summary")),
            )
        )

    rows.sort(key=lambda r: (SEVERITY_ORDER.index(_bare_severity(r.severity)), -r.score))
    return rows


@app.get(
    "/outsampler_portfolio_alerts",
    response_model=list[NewsfeedItem],
    openapi_extra={
        "widget_config": {
            "name": "Portfolio Alerts Feed",
            "type": "newsfeed",
            "gridData": {"w": 40, "h": 18},
            "source": ["Outsampler"],
            "category": "Outsampler",
            "mcp_tool": {},
        }
    },
)
async def outsampler_portfolio_alerts(
    request: Request, date: dateParam
) -> list[NewsfeedItem]:
    """
    Unified, cross-asset alerts feed for a given date. Fans out the per-ticker
    alerts feed across the entire watchlist, prefixes each title with the
    ticker, and returns a single newsfeed sorted by publication time descending.
    """
    headers = {"X-API-Key": request.state.api_key}
    async with httpx.AsyncClient(timeout=60, headers=headers) as http:
        watchlist = await _fetch_watchlist(http)
        per_ticker = await asyncio.gather(
            *(_fetch_alerts_for(http, item["ticker"], date) for item in watchlist)
        )

    merged: list[dict] = []
    for asset, alerts in zip(watchlist, per_ticker):
        ticker = asset.get("ticker", "")
        for alert in alerts:
            item = dict(alert)
            item["title"] = f"[{ticker}] {item.get('title', '')}"
            merged.append(item)

    merged.sort(key=lambda a: a.get("date", ""), reverse=True)
    return merged


@app.get(
    "/outsampler_portfolio_kpis",
    response_model=list[MetricItem],
    openapi_extra={
        "widget_config": {
            "name": "Portfolio KPIs",
            "type": "metric",
            "gridData": {"w": 40, "h": 5},
            "source": ["Outsampler"],
            "category": "Outsampler",
            "mcp_tool": {},
        }
    },
)
async def outsampler_portfolio_kpis(
    request: Request, date: dateParam
) -> list[MetricItem]:
    """
    Header-strip KPIs for a given date — Total Tracked, Critical / Watch / Info
    counts across the watchlist, and the highest single-asset score. Mirrors the
    severity counter pills from the Outsampler Updates page.
    """
    headers = {"X-API-Key": request.state.api_key}
    async with httpx.AsyncClient(timeout=60, headers=headers) as http:
        watchlist = await _fetch_watchlist(http)
        summaries, severities = await asyncio.gather(
            asyncio.gather(
                *(_fetch_summary_for(http, item["ticker"], date) for item in watchlist)
            ),
            asyncio.gather(
                *(_fetch_severity_for(http, item["ticker"], date) for item in watchlist)
            ),
        )

    bucket_counts = Counter()
    top_score = 0.0
    top_ticker = ""
    for asset, summary, severity in zip(watchlist, summaries, severities):
        raw = float(summary.get("score") or 0.0)
        bucket_counts[severity] += 1
        if raw > top_score:
            top_score = raw
            top_ticker = asset.get("ticker", "")

    return [
        MetricItem(label="Tracked Assets", value=str(len(watchlist))),
        MetricItem(
            label="🔴 Critical",
            value=str(bucket_counts.get("Critical", 0)),
            subvalue="high-impact",
        ),
        MetricItem(
            label="🟡 Watch",
            value=str(bucket_counts.get("Watch", 0)),
            subvalue="notable",
        ),
        MetricItem(
            label="🔵 Info",
            value=str(bucket_counts.get("Info", 0)),
            subvalue="informational",
        ),
        MetricItem(
            label="Top Score",
            value=f"{top_score:.1f}" if top_score else "—",
            subvalue=top_ticker or "no signal",
        ),
    ]


@app.get(
    "/outsampler_severity_distribution",
    response_model=list[SeverityCount],
    openapi_extra={
        "widget_config": {
            "name": "Severity Distribution",
            "type": "table",
            "gridData": {"w": 20, "h": 12},
            "source": ["Outsampler"],
            "category": "Outsampler",
            "mcp_tool": {},
        }
    },
)
async def outsampler_severity_distribution(
    request: Request, date: dateParam
) -> list[SeverityCount]:
    """
    Count of watchlist assets in each severity bucket for a given date.
    Designed to render as an AG Grid bar chart via apps.json `chartView`.
    Always emits all four buckets so the chart axes stay stable.
    """
    headers = {"X-API-Key": request.state.api_key}
    async with httpx.AsyncClient(timeout=60, headers=headers) as http:
        watchlist = await _fetch_watchlist(http)
        severities = await asyncio.gather(
            *(_fetch_severity_for(http, item["ticker"], date) for item in watchlist)
        )

    counts = Counter(severities)

    return [
        SeverityCount(severity=SEVERITY_LABELS[b], count=counts.get(b, 0))
        for b in SEVERITY_ORDER
    ]


@app.get(
    "/outsampler_source_mix",
    response_model=list[SourceCount],
    openapi_extra={
        "widget_config": {
            "name": "Source Mix",
            "type": "table",
            "gridData": {"w": 20, "h": 12},
            "source": ["Outsampler"],
            "category": "Outsampler",
            "mcp_tool": {},
        }
    },
)
async def outsampler_source_mix(
    request: Request, date: dateParam
) -> list[SourceCount]:
    """
    Count of alerts grouped by source provider (BENZINGA NEWS, BENZINGA PR,
    SEC etc.) across the entire watchlist for a given date. Designed to render
    as an AG Grid donut/pie chart via apps.json `chartView`. Mirrors the
    Filings / Press / News filter on the Outsampler Updates page.
    """
    headers = {"X-API-Key": request.state.api_key}
    async with httpx.AsyncClient(timeout=60, headers=headers) as http:
        watchlist = await _fetch_watchlist(http)
        per_ticker = await asyncio.gather(
            *(_fetch_alerts_for(http, item["ticker"], date) for item in watchlist)
        )

    counts: Counter[str] = Counter()
    for alerts in per_ticker:
        for alert in alerts:
            counts[_extract_source(alert)] += 1

    return [
        SourceCount(source=src, count=n)
        for src, n in counts.most_common()
    ]
