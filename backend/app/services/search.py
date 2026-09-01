"""Web search, on a budget.

Search is gated, budgeted, and cached rather than sprinkled. Three things make
that real rather than aspirational:

**A per-run budget.** `MAX_SEARCHES_PER_RUN` is enforced here, not just asked
for in a prompt. An agent that has spent its budget gets told so and carries on
without enrichment.

**A cache.** Re-running the same meeting, which happens constantly while
developing and evaluating, costs nothing the second time. Tavily's free tier is
1,000 credits a month and an evaluation sweep would eat it in an afternoon.

**Degrading rather than failing.** With no key, or with credits exhausted,
search returns an empty result and says why. Enrichment is a nice-to-have; a
meeting still gets processed without it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.logging import get_logger
from app.models.domain import SearchCacheEntry
from app.services import trace

log = get_logger(__name__)


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    content: str
    score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "url": self.url, "content": self.content, "score": self.score}


@dataclass(frozen=True)
class SearchResult:
    query: str
    hits: list[SearchHit]
    source: str
    """Where the answer came from: tavily, cache, or a reason it is empty."""

    @property
    def citations(self) -> list[str]:
        return [hit.url for hit in self.hits]

    def as_context(self, limit: int = 3) -> str:
        """Compact form for a prompt, with the URL attached to each claim.

        The URL travels with the text so an agent quoting a fact cannot lose
        the source, which is what makes an enriched recap checkable.
        """
        if not self.hits:
            return f"No results ({self.source})."
        return "\n\n".join(
            f"{hit.title}\n{hit.content[:400]}\nSource: {hit.url}" for hit in self.hits[:limit]
        )


def _fingerprint(query: str) -> str:
    return hashlib.sha256(query.strip().lower().encode()).hexdigest()


@dataclass
class SearchService:
    """One search budget, shared by every agent in a run."""

    settings: Settings
    session: AsyncSession | None = None
    _spent: int = field(default=0, init=False)

    @property
    def budget_remaining(self) -> int:
        return max(0, self.settings.max_searches_per_run - self._spent)

    @property
    def available(self) -> bool:
        return self.settings.tavily_api_key is not None

    async def search(self, query: str, *, depth: str = "basic") -> SearchResult:
        query = query.strip()
        if not query:
            return SearchResult(query, [], "empty query")

        if cached := await self._from_cache(query):
            trace.record("researcher", "tool_call", payload={"query": query, "cache": True})
            return cached

        if not self.available:
            return SearchResult(query, [], "search is not configured")

        if self.budget_remaining == 0:
            log.info("search.budget_exhausted", query=query)
            return SearchResult(query, [], "search budget for this run is spent")

        self._spent += 1
        try:
            hits = await self._tavily(query, depth)
        except Exception as exc:
            log.warning("search.failed", query=query, error=str(exc)[:200])
            trace.record("researcher", "error", payload={"query": query, "error": str(exc)[:200]})
            return SearchResult(query, [], f"search failed: {type(exc).__name__}")

        result = SearchResult(query, hits, "tavily")
        await self._to_cache(result)
        trace.record(
            "researcher",
            "tool_call",
            payload={"query": query, "hits": len(hits), "budget_left": self.budget_remaining},
        )
        return result

    async def _tavily(self, query: str, depth: str) -> list[SearchHit]:
        from langchain_tavily import TavilySearch

        assert self.settings.tavily_api_key is not None
        tool = TavilySearch(
            max_results=5,
            search_depth=depth,
            tavily_api_key=self.settings.tavily_api_key.get_secret_value(),
        )
        raw = await tool.ainvoke({"query": query})
        results = raw.get("results", []) if isinstance(raw, dict) else []
        return [
            SearchHit(
                title=item.get("title", ""),
                url=item.get("url", ""),
                content=item.get("content", ""),
                score=float(item.get("score", 0.0)),
            )
            for item in results
        ]

    async def _from_cache(self, query: str) -> SearchResult | None:
        if self.session is None:
            return None

        cutoff = datetime.now(UTC) - timedelta(seconds=self.settings.search_cache_ttl_seconds)
        entry = await self.session.scalar(
            select(SearchCacheEntry).where(
                SearchCacheEntry.query_hash == _fingerprint(query),
                SearchCacheEntry.fetched_at >= cutoff,
            )
        )
        if entry is None:
            return None
        return SearchResult(query, [SearchHit(**hit) for hit in entry.results], "cache")

    async def _to_cache(self, result: SearchResult) -> None:
        if self.session is None or not result.hits:
            return

        # Upsert rather than insert: an expired entry for this query already
        # exists, and a plain insert would collide on the primary key.
        await self.session.execute(
            insert(SearchCacheEntry)
            .values(
                query_hash=_fingerprint(result.query),
                query=result.query,
                provider=result.source,
                results=[hit.as_dict() for hit in result.hits],
                fetched_at=datetime.now(UTC),
            )
            .on_conflict_do_update(
                index_elements=[SearchCacheEntry.query_hash],
                set_={
                    "results": [hit.as_dict() for hit in result.hits],
                    "fetched_at": datetime.now(UTC),
                },
            )
        )
