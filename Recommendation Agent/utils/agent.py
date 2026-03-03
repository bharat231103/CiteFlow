"""
LangGraph Recommendation Agent
Orchestrates web search, scraping, storage, and suggestion generation
with mandatory citation enforcement.

Architecture:
  ┌──────────────────────────────────────────────────────────────────┐
  │                    CITEFLOW Agent Paths                          │
  │                                                                  │
  │  1st message (research):                                        │
  │    Search Web → Scrape URLs → Store in Qdrant → Query → Suggest │
  │    (~15-30 seconds)                                             │
  │                                                                  │
  │  Subsequent messages (fast):                                    │
  │    Query Qdrant → Suggest                                       │
  │    (~2-4 seconds)                                               │
  │                                                                  │
  │  On disconnect:                                                 │
  │    Delete doc_id collection from Qdrant                         │
  └──────────────────────────────────────────────────────────────────┘
"""

import os
import json
import logging
from typing import Annotated, TypedDict

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from utils.search_ops import search_web
from utils.crawl_ops import scrape_url
from utils.qdrant_ops import (
    store_documents,
    search_qdrant,
    ensure_collection,
)
from utils.citation_metadata import enrich_citations

logger = logging.getLogger("citeflow.agent")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


# ═══════════════════════════════════════════════════════════════════════════════
#  SHARED: State, Tools, LLM
# ═══════════════════════════════════════════════════════════════════════════════

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    collection_name: str
    document_title: str
    document_heading: str
    document_content: str
    suggestion: str
    citations: list[str]
    retry_count: int


# ─── Tools (used by the research agent) ──────────────────────────────────────

@tool
async def web_search(query: str) -> str:
    """Search the web for information related to the query using SearXNG meta-search engine.
    Returns a list of search results with titles, URLs, and snippets.
    Use this to find relevant sources on a topic."""
    results = await search_web(query, num_results=5)
    if not results:
        return "No search results found."
    return json.dumps(results, indent=2)


@tool
async def scrape_webpage(url: str) -> str:
    """Scrape and extract the main content from a web page URL using Firecrawl.
    Returns the markdown content of the page.
    Use this to get detailed information from a specific URL found in search results."""
    content = await scrape_url(url)
    if not content:
        return f"Failed to scrape content from {url}"
    return content


@tool
async def store_in_knowledge_base(
    collection_name: str,
    texts: list[str],
    urls: list[str],
) -> str:
    """Store scraped text content and their source URLs in the vector knowledge base (Qdrant).
    This allows later retrieval of the stored content via semantic search.
    You MUST store content before searching for it."""
    success = await store_documents(collection_name, texts, urls)
    if success:
        return f"Successfully stored {len(texts)} documents in knowledge base '{collection_name}'."
    return "Failed to store documents in knowledge base."


@tool
async def search_knowledge_base(collection_name: str, query: str) -> str:
    """Search the vector knowledge base (Qdrant) for content relevant to the query.
    Returns stored text chunks with their source URLs and relevance scores.
    You MUST call this tool before generating any suggestion to get citations."""
    results = await search_qdrant(collection_name, query, top_k=5)
    if not results:
        return "No relevant content found in knowledge base."
    return json.dumps(results, indent=2)


# ─── Tools list ──────────────────────────────────────────────────────────────

RESEARCH_TOOLS = [web_search, scrape_webpage, store_in_knowledge_base, search_knowledge_base]
FAST_TOOLS = [search_knowledge_base]


# ═══════════════════════════════════════════════════════════════════════════════
#  PATH 1: RESEARCH AGENT  (1st message — full search+scrape+store+query)
# ═══════════════════════════════════════════════════════════════════════════════

RESEARCH_SYSTEM_PROMPT = """You are CITEFLOW, an AI writing assistant that provides factually grounded next-sentence suggestions with mandatory citations.

## Your Task
Given a document's title, current heading, and recent content, suggest the NEXT SENTENCE the author should write.

## MANDATORY Process (You MUST follow ALL steps):

1. **Search the Web**: Use `web_search` to find relevant, current information about the topic.
2. **Scrape Content**: Use `scrape_webpage` to extract detailed content from the top 2-3 most relevant URLs.
3. **Store in Knowledge Base**: Use `store_in_knowledge_base` to save the scraped content with source URLs into the given collection.
4. **Search Knowledge Base**: Use `search_knowledge_base` to retrieve the most relevant stored content. THIS STEP IS MANDATORY.
5. **Generate Suggestion**: Based ONLY on the retrieved content, generate your next-sentence suggestion.

## CRITICAL RULES:
- You MUST call `search_knowledge_base` before generating your final response.
- Your suggestion MUST be based on information retrieved from the knowledge base.
- You MUST include citations (source URLs) from the knowledge base results.
- NEVER generate a suggestion without first searching the knowledge base.
- Your final response MUST be a JSON object with exactly two fields: "suggestion" and "citations".

## Response Format (FINAL response only):
```json
{
    "suggestion": "Your suggested next sentence here.",
    "citations": ["https://source1.com", "https://source2.com"]
}
```

Remember: The suggestion should naturally follow the existing content and be appropriate for the given heading.
"""


def create_research_agent():
    """Create the full research agent (search → scrape → store → query → suggest)."""

    llm = ChatOpenAI(
        model="gpt-4o-mini",
        api_key=OPENAI_API_KEY,
        temperature=0.3,
    )
    llm_with_tools = llm.bind_tools(RESEARCH_TOOLS)

    async def agent_node(state: AgentState):
        messages = state["messages"]
        response = await llm_with_tools.ainvoke(messages)
        return {"messages": [response]}

    async def enforce_citations(state: AgentState):
        messages = state["messages"]
        last_message = messages[-1]
        if isinstance(last_message, AIMessage) and not last_message.tool_calls:
            kb_searched = any(
                tc["name"] == "search_knowledge_base"
                for msg in messages if isinstance(msg, AIMessage) and msg.tool_calls
                for tc in msg.tool_calls
            )
            if not kb_searched and state.get("retry_count", 0) < 2:
                logger.warning("Citation enforcement: Agent skipped search_knowledge_base. Retrying.")
                return {
                    "messages": [HumanMessage(content=(
                        "IMPORTANT: You MUST call the `search_knowledge_base` tool before providing "
                        "your final answer. Search the knowledge base now for citations."
                    ))],
                    "retry_count": state.get("retry_count", 0) + 1,
                }
        return {}

    def should_continue(state: AgentState) -> str:
        messages = state["messages"]
        last_message = messages[-1]
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            return "tools"
        if isinstance(last_message, AIMessage) and not last_message.tool_calls:
            kb_searched = any(
                tc["name"] == "search_knowledge_base"
                for msg in messages if isinstance(msg, AIMessage) and msg.tool_calls
                for tc in msg.tool_calls
            )
            if not kb_searched and state.get("retry_count", 0) < 2:
                return "enforce_citations"
        return END

    tool_node = ToolNode(RESEARCH_TOOLS)
    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.add_node("enforce_citations", enforce_citations)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent", should_continue,
        {"tools": "tools", "enforce_citations": "enforce_citations", END: END},
    )
    graph.add_edge("tools", "agent")
    graph.add_edge("enforce_citations", "agent")
    return graph.compile()


# ═══════════════════════════════════════════════════════════════════════════════
#  PATH 2: FAST AGENT  (subsequent messages — only query Qdrant → suggest)
# ═══════════════════════════════════════════════════════════════════════════════

FAST_SYSTEM_PROMPT = """You are CITEFLOW, an AI writing assistant that provides factually grounded next-sentence suggestions with mandatory citations.

## Your Task
Given a document's title, current heading, and recent content, suggest the NEXT SENTENCE the author should write.

## Your Process
The knowledge base for this document has ALREADY been populated with research from previous queries.
You only need to:

1. **Search Knowledge Base**: Use `search_knowledge_base` to retrieve relevant stored content with source URLs. THIS IS MANDATORY.
2. **Generate Suggestion**: Based ONLY on the retrieved content, generate your next-sentence suggestion.

## CRITICAL RULES:
- You MUST call `search_knowledge_base` before generating your final response.
- Your suggestion MUST be based on information retrieved from the knowledge base.
- You MUST include citations (source URLs) from the knowledge base results.
- NEVER generate a suggestion without first searching the knowledge base.
- Your final response MUST be a JSON object with exactly two fields: "suggestion" and "citations".
- Do NOT call web_search or scrape_webpage — the knowledge base is already populated.

## Response Format (FINAL response only):
```json
{
    "suggestion": "Your suggested next sentence here.",
    "citations": ["https://source1.com", "https://source2.com"]
}
```

Remember: The suggestion should naturally follow the existing content and be appropriate for the given heading.
"""


def create_fast_agent():
    """Create the fast-path agent (only query Qdrant → suggest)."""

    llm = ChatOpenAI(
        model="gpt-4o-mini",
        api_key=OPENAI_API_KEY,
        temperature=0.3,
    )
    llm_with_tools = llm.bind_tools(FAST_TOOLS)

    async def agent_node(state: AgentState):
        messages = state["messages"]
        response = await llm_with_tools.ainvoke(messages)
        return {"messages": [response]}

    async def enforce_citations(state: AgentState):
        messages = state["messages"]
        last_message = messages[-1]
        if isinstance(last_message, AIMessage) and not last_message.tool_calls:
            kb_searched = any(
                tc["name"] == "search_knowledge_base"
                for msg in messages if isinstance(msg, AIMessage) and msg.tool_calls
                for tc in msg.tool_calls
            )
            if not kb_searched and state.get("retry_count", 0) < 2:
                logger.warning("Fast path: enforcing search_knowledge_base call.")
                return {
                    "messages": [HumanMessage(content=(
                        "IMPORTANT: You MUST call `search_knowledge_base` before answering. "
                        "Search the knowledge base now."
                    ))],
                    "retry_count": state.get("retry_count", 0) + 1,
                }
        return {}

    def should_continue(state: AgentState) -> str:
        messages = state["messages"]
        last_message = messages[-1]
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            return "tools"
        if isinstance(last_message, AIMessage) and not last_message.tool_calls:
            kb_searched = any(
                tc["name"] == "search_knowledge_base"
                for msg in messages if isinstance(msg, AIMessage) and msg.tool_calls
                for tc in msg.tool_calls
            )
            if not kb_searched and state.get("retry_count", 0) < 2:
                return "enforce_citations"
        return END

    tool_node = ToolNode(FAST_TOOLS)
    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.add_node("enforce_citations", enforce_citations)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent", should_continue,
        {"tools": "tools", "enforce_citations": "enforce_citations", END: END},
    )
    graph.add_edge("tools", "agent")
    graph.add_edge("enforce_citations", "agent")
    return graph.compile()


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINTS
# ═══════════════════════════════════════════════════════════════════════════════

_research_agent = None
_fast_agent = None


def get_research_agent():
    """Get or create the singleton research agent."""
    global _research_agent
    if _research_agent is None:
        _research_agent = create_research_agent()
    return _research_agent


def get_fast_agent():
    """Get or create the singleton fast agent."""
    global _fast_agent
    if _fast_agent is None:
        _fast_agent = create_fast_agent()
    return _fast_agent


def _parse_agent_raw_response(result: dict) -> dict:
    """Extract suggestion + raw URL citations from agent's final message."""
    final_message = result["messages"][-1]

    if isinstance(final_message, AIMessage):
        response_text = final_message.content
        try:
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start != -1 and json_end > json_start:
                parsed = json.loads(response_text[json_start:json_end])
                suggestion = parsed.get("suggestion", "")
                citations = parsed.get("citations", [])
                if suggestion:
                    if not citations:
                        logger.warning("Agent returned suggestion without citations!")
                    return {"suggestion": suggestion, "raw_urls": citations}
        except json.JSONDecodeError:
            logger.warning("Could not parse agent response as JSON")

        return {"suggestion": response_text, "raw_urls": []}

    return {"suggestion": "Unable to generate a suggestion at this time.", "raw_urls": []}


async def _enrich_and_build_response(
    raw_result: dict,
    citation_cache: dict[str, dict],
) -> dict:
    """
    Take the agent's raw response (suggestion + raw_urls) and enrich
    the URLs into structured citation objects using the session cache.
    """
    suggestion = raw_result.get("suggestion", "")
    raw_urls = raw_result.get("raw_urls", [])

    if not raw_urls:
        return {"suggestion": suggestion, "citations": []}

    # Enrich raw URLs into structured citation metadata
    enriched_citations = await enrich_citations(raw_urls, citation_cache)

    return {"suggestion": suggestion, "citations": enriched_citations}


async def get_suggestion_with_research(
    document_id: str,
    title: str,
    heading: str,
    content: str,
    citation_cache: dict[str, dict] | None = None,
) -> dict:
    """
    FIRST-CALL path: Full research pipeline.
    Searches the web, scrapes pages, stores in Qdrant, queries, then suggests.
    Returns structured citation metadata objects.
    Takes ~15-30 seconds.
    """
    if citation_cache is None:
        citation_cache = {}

    agent = get_research_agent()
    collection_name = f"doc_{document_id}"
    ensure_collection(collection_name)

    user_message = f"""Document Title: {title}
Current Heading: {heading}
Recent Content: {content}

Please search the web for relevant information about this topic, scrape the most relevant sources, store them in the knowledge base (collection: "{collection_name}"), then search the knowledge base and provide a next-sentence suggestion with citations."""

    initial_state = {
        "messages": [
            SystemMessage(content=RESEARCH_SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ],
        "collection_name": collection_name,
        "document_title": title,
        "document_heading": heading,
        "document_content": content,
        "suggestion": "",
        "citations": [],
        "retry_count": 0,
    }

    try:
        result = await agent.ainvoke(initial_state, config={"recursion_limit": 100000})
        raw_result = _parse_agent_raw_response(result)
        return await _enrich_and_build_response(raw_result, citation_cache)
    except Exception as e:
        logger.error(f"Research agent error: {e}", exc_info=True)
        return {"suggestion": f"Error generating suggestion: {str(e)}", "citations": []}


async def get_suggestion_fast(
    document_id: str,
    title: str,
    heading: str,
    content: str,
    citation_cache: dict[str, dict] | None = None,
) -> dict:
    """
    FAST path: Only queries the existing Qdrant knowledge base and generates a suggestion.
    Returns structured citation metadata objects. Cached URLs resolve instantly.
    Takes ~2-4 seconds.
    """
    if citation_cache is None:
        citation_cache = {}

    agent = get_fast_agent()
    collection_name = f"doc_{document_id}"

    user_message = f"""Document Title: {title}
Current Heading: {heading}
Recent Content: {content}

The knowledge base (collection: "{collection_name}") already contains researched content for this document. Search it for relevant information and provide a next-sentence suggestion with citations."""

    initial_state = {
        "messages": [
            SystemMessage(content=FAST_SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ],
        "collection_name": collection_name,
        "document_title": title,
        "document_heading": heading,
        "document_content": content,
        "suggestion": "",
        "citations": [],
        "retry_count": 0,
    }

    try:
        result = await agent.ainvoke(initial_state, config={"recursion_limit": 100000})
        raw_result = _parse_agent_raw_response(result)
        return await _enrich_and_build_response(raw_result, citation_cache)
    except Exception as e:
        logger.error(f"Fast agent error: {e}", exc_info=True)
        return {"suggestion": f"Error generating suggestion: {str(e)}", "citations": []}
