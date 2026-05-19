"""AI agent to enrich trend context from web search (non-YouTube/TikTok sources)."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from openai import AuthenticationError

from src.trend_content import (
    build_text_items,
    is_video_platform_url,
    sanitize_text_urls,
    save_trend_content,
)

load_dotenv()
logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(
    r"your[_-]?(openai|api|key|secret|token|tavily)|"
    r"changeme|placeholder|insert[_-]?key|xxx+",
    re.IGNORECASE,
)

_EXCLUDE_SEARCH_DOMAINS = [
    "youtube.com",
    "youtu.be",
    "m.youtube.com",
    "tiktok.com",
]


def _is_valid_api_key(key: str | None, min_length: int = 20) -> bool:
    if not key or not key.strip():
        return False
    cleaned = key.strip()
    if len(cleaned) < min_length:
        return False
    if _PLACEHOLDER_RE.search(cleaned):
        return False
    return True


def _get_search_tool():
    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
    if _is_valid_api_key(tavily_key, min_length=10):
        try:
            from langchain_tavily import TavilySearch

            return TavilySearch(
                max_results=8,
                api_key=tavily_key,
                exclude_domains=_EXCLUDE_SEARCH_DOMAINS,
            )
        except ImportError:
            from langchain_community.tools.tavily_search import TavilySearchResults

            return TavilySearchResults(
                max_results=8,
                api_key=tavily_key,
                exclude_domains=_EXCLUDE_SEARCH_DOMAINS,
            )
    return DuckDuckGoSearchRun()


def _context_search_query(video_title: str, *, recency_days: int = 7) -> str:
    """Bias search toward articles/blogs; exclude video platforms already scraped."""
    return (
        f"{video_title} trend analysis news article blog forum discussion "
        f"-site:youtube.com -site:youtu.be -site:tiktok.com "
        f"last {recency_days} days"
    )


def _sanitize_search_result(
    result: str,
    *,
    exclude_urls: list[str] | None = None,
) -> str:
    return sanitize_text_urls(result if isinstance(result, str) else str(result), exclude_urls=exclude_urls)


def _run_search(
    query: str,
    search_tool,
    *,
    exclude_urls: list[str] | None = None,
) -> str:
    if hasattr(search_tool, "invoke"):
        result = search_tool.invoke(query)
    else:
        result = search_tool.run(query)
    return _sanitize_search_result(result, exclude_urls=exclude_urls)


def _fallback_summary(video_title: str, search_context: str) -> str:
    return (
        f"Trend context for '{video_title}':\n\n"
        f"{search_context[:2500]}"
    )


def _search_only_summary(
    video_title: str,
    search_tool,
    *,
    recency_days: int = 7,
    exclude_urls: list[str] | None = None,
) -> tuple[str, str]:
    query = _context_search_query(video_title, recency_days=recency_days)
    search_context = _run_search(query, search_tool, exclude_urls=exclude_urls)
    return _fallback_summary(video_title, search_context), search_context


def _summarize_direct(
    llm: ChatOpenAI,
    video_title: str,
    search_context: str,
) -> str:
    response = llm.invoke(
        f"Write a 1-2 paragraph trend summary for the video titled '{video_title}'.\n"
        "Use only non-YouTube, non-TikTok sources from the search results below.\n\n"
        f"Search results:\n{search_context}"
    )
    return response.content if hasattr(response, "content") else str(response)


def _summarize_with_agent(
    llm: ChatOpenAI,
    search_tool,
    video_title: str,
    *,
    exclude_urls: list[str] | None = None,
) -> str:
    from langchain_classic.agents import AgentExecutor, create_tool_calling_agent

    tools = [search_tool]
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a trend research analyst. Use search tools to gather facts "
                "from news sites, blogs, forums, and Wikipedia — NOT from YouTube or TikTok "
                "(those platforms are already covered elsewhere). "
                "Write a 1-2 paragraph summary explaining the trend, why it is popular, "
                "and key tools or topics mentioned. Do not use bullet lists.",
            ),
            ("human", "Research this video title and summarize the trend: {input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ]
    )
    agent = create_tool_calling_agent(llm, tools, prompt)
    executor = AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=False,
        max_iterations=5,
        handle_parsing_errors=True,
    )
    result = executor.invoke({"input": video_title})
    output = (result.get("output") or "").strip()
    if output:
        return _sanitize_search_result(output, exclude_urls=exclude_urls)
    search_context = _run_search(
        _context_search_query(video_title),
        search_tool,
        exclude_urls=exclude_urls,
    )
    return _fallback_summary(video_title, search_context)


def enrich_trend_context(
    video_title: str,
    trend_folder: str | Path,
    *,
    recency_days: int = 7,
    exclude_urls: list[str] | None = None,
) -> Path:
    """
    Search the web for context related to the video title and save a summary
    to trend_info.txt inside the trend folder.

    YouTube/TikTok links are excluded (including URLs from `exclude_urls`).
    """
    folder = Path(trend_folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"Trend folder does not exist: {folder}")

    scraped_video_urls = [
        u.strip()
        for u in (exclude_urls or [])
        if u and u.strip() and is_video_platform_url(u)
    ]

    output_path = folder / "trend_info.txt"
    content_path = folder / "trend_content.json"
    if output_path.exists() and output_path.stat().st_size > 0:
        if not content_path.exists():
            existing = sanitize_text_urls(
                output_path.read_text(encoding="utf-8").strip(),
                exclude_urls=scraped_video_urls,
            )
            save_trend_content(
                folder,
                build_text_items(
                    video_title,
                    existing,
                    exclude_urls=scraped_video_urls,
                ),
            )
        logger.info("Reusing existing trend content at %s", output_path)
        return output_path.resolve()

    search_tool = _get_search_tool()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    search_context = _run_search(
        _context_search_query(video_title, recency_days=recency_days),
        search_tool,
        exclude_urls=scraped_video_urls,
    )

    if not _is_valid_api_key(api_key):
        logger.info(
            "OPENAI_API_KEY is missing or still a placeholder; using web search only."
        )
        summary, search_context = _search_only_summary(
            video_title,
            search_tool,
            recency_days=recency_days,
            exclude_urls=scraped_video_urls,
        )
    else:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3, api_key=api_key)
        try:
            summary = _summarize_with_agent(
                llm,
                search_tool,
                video_title,
                exclude_urls=scraped_video_urls,
            )
        except AuthenticationError:
            logger.warning(
                "OpenAI authentication failed; check OPENAI_API_KEY in .env"
            )
            summary, search_context = _search_only_summary(
                video_title,
                search_tool,
                recency_days=recency_days,
                exclude_urls=scraped_video_urls,
            )
        except Exception as exc:
            logger.warning("Agent failed (%s); using search-only fallback", exc)
            try:
                summary = _summarize_direct(llm, video_title, search_context)
            except AuthenticationError:
                summary = _fallback_summary(video_title, search_context)
            except Exception as llm_exc:
                logger.warning("LLM summarization failed: %s", llm_exc)
                summary = _fallback_summary(video_title, search_context)

    summary = sanitize_text_urls(summary.strip(), exclude_urls=scraped_video_urls)
    search_context = sanitize_text_urls(search_context, exclude_urls=scraped_video_urls)
    output_path.write_text(summary + "\n", encoding="utf-8")
    items = build_text_items(
        video_title,
        summary,
        search_context,
        exclude_urls=scraped_video_urls,
    )
    save_trend_content(folder, items)
    logger.info(
        "Trend text context: %d reference items (YT/TikTok excluded)",
        sum(1 for i in items if i.get("type") == "reference"),
    )
    return output_path.resolve()
