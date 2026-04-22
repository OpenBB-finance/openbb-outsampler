import json
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
    "http://localhost",
]


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


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=True,
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=6775)
