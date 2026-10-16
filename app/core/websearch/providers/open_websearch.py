from __future__ import annotations

import logging
from dataclasses import dataclass
from time import monotonic
from typing import Callable, Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.websearch.base import SearchResponse, SearchResult


# 百度、搜狗复用现有结果协议，作为前序引擎失败或结果不足时的联网入口。
OPEN_WEBSEARCH_SUPPORTED_ENGINES = {"duckduckgo", "bing", "baidu", "sogou"}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _OpenWebSearchRawResult:
    title: str
    url: str
    description: str
    source: str
    engine: str


class OpenWebSearchProvider:
    def __init__(
        self,
        *,
        engines: list[str] | tuple[str, ...] | str,
        timeout_seconds: int,
        client_factory: Optional[Callable[..., httpx.AsyncClient]] = None,
    ):
        self.engines = self._normalize_engines(engines)
        self.timeout_seconds = timeout_seconds
        self.client_factory = client_factory or httpx.AsyncClient

    async def search(
        self,
        query: str,
        *,
        count: int,
        domains: Optional[list[str]] = None,
        freshness: Optional[str] = None,
    ) -> SearchResponse:
        if not self.engines:
            raise ValueError("At least one open-webSearch engine must be configured")

        # 每个引擎只补齐当前缺口，够数后停止，避免搜索已成功仍等待其他入口。
        final_query = self._build_query(query, domains=domains)
        target_count = max(1, count)
        raw_results: list[_OpenWebSearchRawResult] = []
        failures: list[str] = []
        engine_searchers = {
            "duckduckgo": self._search_duckduckgo_html,
            "bing": self._search_bing,
            "baidu": self._search_baidu,
            "sogou": self._search_sogou,
        }

        # 网络或解析异常只终止当前引擎，保留已有结果并继续尝试；任务取消不属于 Exception，仍向上传播。
        async with self.client_factory(timeout=self.timeout_seconds, follow_redirects=True) as client:
            for engine in self.engines:
                limit = target_count - len(raw_results)
                if limit <= 0:
                    break
                started_at = monotonic()
                try:
                    engine_results = await engine_searchers[engine](client, final_query, limit)
                except Exception as exc:
                    failures.append(f"{engine}: {type(exc).__name__}: {str(exc).strip()}".rstrip(": "))
                    # Docker 标准错误日志保留引擎、异常类型和耗时，不输出查询词或请求凭据。
                    logger.warning(
                        "Web search engine failed: engine=%s exception_type=%s elapsed_ms=%d timeout_seconds=%s",
                        engine, type(exc).__name__, int((monotonic() - started_at) * 1000), self.timeout_seconds,
                    )
                    continue
                raw_results.extend(engine_results)
                if not engine_results:
                    failures.append(f"{engine}: No search results")
                    logger.warning("Web search engine returned no results: engine=%s", engine)

        # 全部不可用时不能伪装成搜索成功，聚合每个引擎的原因供服务层继续切换或向用户降级说明。
        if not raw_results:
            raise RuntimeError("No search results from open-webSearch engines: " + "; ".join(failures))

        results = [
            SearchResult(
                title=item.title,
                url=item.url,
                snippet=item.description,
                source=self._format_source(item),
            )
            for item in raw_results[:max(1, count)]
            if item.url
        ]
        return SearchResponse(provider="open_websearch", query=query, results=results)

    @staticmethod
    def _normalize_engines(engines: list[str] | tuple[str, ...] | str) -> list[str]:
        if isinstance(engines, str):
            raw_engines = engines.split(",")
        else:
            raw_engines = list(engines)
        normalized: list[str] = []
        for engine in raw_engines:
            name = str(engine or "").strip().lower()
            if name in OPEN_WEBSEARCH_SUPPORTED_ENGINES and name not in normalized:
                normalized.append(name)
        return normalized

    @staticmethod
    def _build_query(query: str, *, domains: Optional[list[str]]) -> str:
        final_query = query
        if domains:
            final_query += " " + " ".join(f"site:{domain}" for domain in domains)
        return final_query

    async def _search_duckduckgo_html(
        self,
        client: httpx.AsyncClient,
        query: str,
        limit: int,
    ) -> list[_OpenWebSearchRawResult]:
        if limit <= 0:
            return []

        # 采用 open-webSearch 的 DuckDuckGo HTML 入口；该入口不需要 API key，返回普通 HTML，适合 Python 侧直接解析。
        response = await client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": self._user_agent(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[_OpenWebSearchRawResult] = []
        for item in soup.select("div.result"):
            if "result--ad" in item.get("class", []):
                continue
            title_element = item.select_one("a.result__a")
            if title_element is None:
                continue
            title = title_element.get_text(" ", strip=True)
            url = self._normalize_duckduckgo_url(str(title_element.get("href") or ""))
            description_element = item.select_one(".result__snippet")
            source_element = item.select_one(".result__url")
            description = description_element.get_text(" ", strip=True) if description_element else ""
            source = source_element.get_text(" ", strip=True) if source_element else ""
            if title and url:
                results.append(_OpenWebSearchRawResult(title, url, description, source, "duckduckgo"))
            if len(results) >= limit:
                break
        return results

    async def _search_bing(
        self,
        client: httpx.AsyncClient,
        query: str,
        limit: int,
    ) -> list[_OpenWebSearchRawResult]:
        if limit <= 0:
            return []

        # 采用 open-webSearch 的 Bing 搜索页解析思路；只读取自然搜索结果块，忽略没有 http 链接的结果。
        response = await client.get(
            "https://www.bing.com/search",
            params={"q": query, "first": 1},
            headers={
                "User-Agent": self._user_agent(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[_OpenWebSearchRawResult] = []
        for item in soup.select("#b_results li.b_algo, #b_results > li"):
            title_element = item.select_one("h2")
            link_element = item.select_one("h2 a") or item.select_one("a")
            if title_element is None or link_element is None:
                continue
            title = title_element.get_text(" ", strip=True)
            url = str(link_element.get("href") or "")
            if not url.startswith("http"):
                continue
            snippet_element = item.select_one("p")
            source_element = item.select_one(".b_tpcn") or item.select_one("cite")
            description = snippet_element.get_text(" ", strip=True) if snippet_element else ""
            source = source_element.get_text(" ", strip=True) if source_element else ""
            if title:
                results.append(_OpenWebSearchRawResult(title, url, description, source, "bing"))
            if len(results) >= limit:
                break
        return results

    async def _search_baidu(
        self,
        client: httpx.AsyncClient,
        query: str,
        limit: int,
    ) -> list[_OpenWebSearchRawResult]:
        # 使用已实测的百度第一页请求及结果块结构，跳过没有标题链接的聚合区块。
        response = await client.get(
            "https://www.baidu.com/s",
            params={"wd": query, "pn": 0, "ie": "utf-8"},
            headers={"User-Agent": self._user_agent()},
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[_OpenWebSearchRawResult] = []
        for item in soup.select("#content_left > *"):
            title_element = item.select_one("h3 a[href]")
            if title_element is None:
                continue
            title = title_element.get_text(" ", strip=True)
            # 实际页面 mu 属性存放正文地址；缺失或无效时保留标题中的搜索跳转链接。
            url = self._http_url(str(item.get("mu") or "")) or self._http_url(
                str(title_element.get("href") or "")
            )
            if not title or not url:
                continue
            description_element = item.select_one(".c-font-normal.c-color-text")
            description = str(description_element.get("aria-label") or "") if description_element else ""
            if not description:
                description_element = item.select_one(".cos-row")
                description = description_element.get_text(" ", strip=True) if description_element else ""
            source_element = item.select_one(".cosc-source")
            source = source_element.get_text(" ", strip=True) if source_element else ""
            results.append(_OpenWebSearchRawResult(title, url, description, source, "baidu"))
            if len(results) >= limit:
                break
        return results

    async def _search_sogou(
        self,
        client: httpx.AsyncClient,
        query: str,
        limit: int,
    ) -> list[_OpenWebSearchRawResult]:
        # 使用搜狗公开搜索页；请求参数来自上游实现，摘要及来源补充本次实测页面中的结构。
        response = await client.get(
            "https://www.sogou.com/web",
            params={"query": query, "page": 1, "ie": "utf8"},
            headers={
                "User-Agent": self._user_agent(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://www.sogou.com/",
            },
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        # 验证页不能当作搜索成功，不提交验证码或尝试绕过验证。
        if soup.title and "搜狗搜索验证" in soup.title.get_text():
            raise RuntimeError("Sogou search requires verification")
        results: list[_OpenWebSearchRawResult] = []
        for item in soup.select(
            "#main .vrwrap, #main .rb, #main .result, #results .vrwrap, .results .vrwrap, .results .rb"
        ):
            title_element = item.select_one("h3 a[href], h2 a[href], .vr-title a[href], .pt a[href]")
            if title_element is None:
                continue
            title = title_element.get_text(" ", strip=True)
            # /link?url 中的不透明 token 保持原样，只将相对地址转换为可引用的绝对地址。
            href = str(title_element.get("href") or "").strip()
            url = self._http_url(href, base_url="https://www.sogou.com/web")
            if not title or not url:
                continue
            description_element = item.select_one(".fz-mid") or item.select_one(".str_info, .ft, .text-layout, p")
            source_element = item.select_one(".citeLinkClass") or item.select_one("cite, .citeurl, .g, .url")
            description = description_element.get_text(" ", strip=True) if description_element else ""
            source = source_element.get_text(" ", strip=True) if source_element else ""
            results.append(_OpenWebSearchRawResult(title, url, description, source, "sogou"))
            if len(results) >= limit:
                break
        # 仅在没有结果时检查正文验证标记，避免搜索“验证码”等主题时误判有效结果页。
        if not results and any(marker in response.text for marker in ("antispider", "请输入验证码", "访问过于频繁")):
            raise RuntimeError("Sogou search requires verification")
        return results

    @staticmethod
    def _http_url(url: str, *, base_url: Optional[str] = None) -> str:
        # 相对地址合成与解析均按条目隔离；缺失或畸形地址不影响其余结果，只有 HTTP(S) 地址进入引用。
        url = url.strip()
        if not url:
            return ""
        try:
            if base_url:
                url = urljoin(base_url, url)
            parsed = urlparse(url)
            return url if parsed.scheme in {"http", "https"} and parsed.hostname else ""
        except ValueError:
            return ""

    @staticmethod
    def _normalize_duckduckgo_url(url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url)
        if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
            values = parse_qs(parsed.query).get("uddg")
            if values:
                return unquote(values[0])
        return url

    @staticmethod
    def _format_source(item: _OpenWebSearchRawResult) -> str:
        source = item.source.strip()
        return f"{item.engine}: {source}" if source else item.engine

    @staticmethod
    def _user_agent() -> str:
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/112.0.0.0 Safari/537.36"
        )
