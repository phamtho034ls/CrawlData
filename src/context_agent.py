"""AI agent to enrich trend context from web search."""

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

from src.trend_content import build_text_items, save_trend_content

load_dotenv()
logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(
    r"your[_-]?(openai|api|key|secret|token|tavily)|"
    r"changeme|placeholder|insert[_-]?key|xxx+",
    re.IGNORECASE,
)


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

            return TavilySearch(max_results=5, api_key=tavily_key)
        except ImportError:
            from langchain_community.tools.tavily_search import TavilySearchResults

            return TavilySearchResults(max_results=5, api_key=tavily_key)
    return DuckDuckGoSearchRun()


def _run_search(query: str, search_tool) -> str:
    if hasattr(search_tool, "invoke"):
        result = search_tool.invoke(query)
    else:
        result = search_tool.run(query)
    return result if isinstance(result, str) else str(result)


def _fallback_summary(video_title: str, search_context: str) -> str:
    return (
        f"Trend context for '{video_title}':\n\n"
        f"{search_context[:2500]}"
    )


def _search_only_summary(
    video_title: str, search_tool, *, recency_days: int = 7
) -> tuple[str, str]:
    query = (
        f"{video_title} viral trend short form video context "
        f"last {recency_days} days"
    )
    search_context = _run_search(query, search_tool)
    return _fallback_summary(video_title, search_context), search_context


def _summarize_direct(llm: ChatOpenAI, video_title: str, search_context: str) -> str:
    response = llm.invoke(
        f"Write a 1-2 paragraph trend summary for the video titled '{video_title}'.\n\n"
        f"Search results:\n{search_context}"
    )
    return response.content if hasattr(response, "content") else str(response)


def _summarize_with_agent(
    llm: ChatOpenAI,
    search_tool,
    video_title: str,
) -> str:
    from langchain_classic.agents import AgentExecutor, create_tool_calling_agent

    tools = [search_tool]
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a trend research analyst. Use search tools to gather "
                "facts, then write a 1-2 paragraph summary explaining the trend, "
                "why it is popular, and key tools or topics mentioned. "
                "Do not use bullet lists.",
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
        return output
    search_context = _run_search(video_title, search_tool)
    return _fallback_summary(video_title, search_context)


def enrich_trend_context(
    video_title: str,
    trend_folder: str | Path,
    *,
    recency_days: int = 7,
) -> Path:
    """
    Search the web for context related to the video title and save a summary
    to trend_info.txt inside the trend folder.
    """
    folder = Path(trend_folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"Trend folder does not exist: {folder}")

    output_path = folder / "trend_info.txt"
    content_path = folder / "trend_content.json"
    if output_path.exists() and output_path.stat().st_size > 0:
        if not content_path.exists():
            existing = output_path.read_text(encoding="utf-8").strip()
            save_trend_content(
                folder,
                build_text_items(video_title, existing),
            )
        logger.info("Reusing existing trend content at %s", output_path)
        return output_path.resolve()

    search_tool = _get_search_tool()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    search_context = _run_search(
        f"{video_title} viral trend short form video context last {recency_days} days",
        search_tool,
    )

    if not _is_valid_api_key(api_key):
        logger.info(
            "OPENAI_API_KEY is missing or still a placeholder; using web search only."
        )
        summary, search_context = _search_only_summary(
            video_title, search_tool, recency_days=recency_days
        )
    else:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3, api_key=api_key)
        try:
            summary = _summarize_with_agent(llm, search_tool, video_title)
        except AuthenticationError:
            logger.warning(
                "OpenAI authentication failed; check OPENAI_API_KEY in .env"
            )
            summary, search_context = _search_only_summary(
                video_title, search_tool, recency_days=recency_days
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

    summary = summary.strip()
    output_path.write_text(summary + "\n", encoding="utf-8")
    items = build_text_items(video_title, summary, search_context)
    save_trend_content(folder, items)
    return output_path.resolve()
