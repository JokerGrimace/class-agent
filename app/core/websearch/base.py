from dataclasses import asdict, dataclass
from typing import Optional, Protocol


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str

    def to_citation(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class SearchResponse:
    provider: str
    query: str
    results: list[SearchResult]

    @property
    def citations(self) -> list[dict[str, str]]:
        return [item.to_citation() for item in self.results]


class WebSearchProvider(Protocol):
    async def search(
        self,
        query: str,
        *,
        count: int,
        domains: Optional[list[str]] = None,
        freshness: Optional[str] = None,
    ) -> SearchResponse:
        ...
