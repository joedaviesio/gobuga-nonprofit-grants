"""Tools that bots can call during their agentic loop (multi-tenant)."""

import json
import os
import httpx
from dotenv import load_dotenv
from orchestrator.evidence import save_evidence

load_dotenv()


# --- Tool definitions (sent to the API) ---

TOOL_DEFINITIONS = {
    "web_fetch": {
        "name": "web_fetch",
        "description": "Fetch the text content of a web page. Returns the first 8000 characters of visible text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch",
                }
            },
            "required": ["url"],
        },
    },
    "web_search": {
        "name": "web_search",
        "description": "Search the web using Tavily. Returns relevant results with extracted page content, URLs, and relevance scores. Supports all languages including Romanian and French.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query (works in any language)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of results to return (default 8, max 10)",
                },
            },
            "required": ["query"],
        },
    },
    "save_evidence": {
        "name": "save_evidence",
        "description": "Save an evidence item to the tamper-evident store. Use this to record findings, observations, or analytical conclusions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short title for this evidence item",
                },
                "type": {
                    "type": "string",
                    "enum": ["grant_opportunity", "donor_intel", "deadline", "analysis", "recommendation"],
                    "description": "Type of evidence",
                },
                "content": {
                    "type": "string",
                    "description": "The evidence content — what was found or concluded",
                },
                "source_url": {
                    "type": "string",
                    "description": "Source URL if applicable",
                },
                "severity": {
                    "type": "string",
                    "enum": ["high", "medium", "low", "info"],
                    "description": "Priority/severity of this finding",
                },
            },
            "required": ["title", "type", "content", "source_url"],
        },
    },
}


# --- Tool handlers ---

def handle_web_fetch(args: dict) -> str:
    """Fetch a URL and return text content."""
    url = args["url"]
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "GoBuga-GrantBot/0.1"})
            resp.raise_for_status()
            text = resp.text
            import re
            text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            return text[:8000]
    except Exception as e:
        return f"Error fetching {url}: {e}"


def handle_web_search(args: dict) -> str:
    """Search using Tavily and return results with extracted content."""
    query = args["query"]
    max_results = args.get("max_results", 8)

    tavily_key = os.getenv("TAVILY_API_KEY")
    if not tavily_key:
        return _fallback_ddg_search(query)

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=tavily_key)
        response = client.search(
            query=query,
            max_results=min(max_results, 10),
            include_raw_content=False,
        )

        results = response.get("results", [])
        if not results:
            return f"No results found for: {query}"

        output = []
        for r in results:
            title = r.get("title", "No title")
            url = r.get("url", "")
            content = r.get("content", "")[:500]
            score = r.get("score", 0)
            output.append(f"- **[{title}]({url})** (relevance: {score:.2f})\n  {content}")

        return "\n\n".join(output)
    except Exception as e:
        return f"Tavily search error: {e}. Falling back to DuckDuckGo.\n\n" + _fallback_ddg_search(query)


def _fallback_ddg_search(query: str) -> str:
    """Fallback to DuckDuckGo if Tavily is unavailable."""
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "GoBuga-GrantBot/0.1"},
            )
            resp.raise_for_status()
            import re
            results = re.findall(
                r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</span>',
                resp.text,
                re.DOTALL,
            )
            if not results:
                return f"No results found for: {query}"
            output = []
            for url, title, snippet in results[:8]:
                title = re.sub(r'<[^>]+>', '', title).strip()
                snippet = re.sub(r'<[^>]+>', '', snippet).strip()
                output.append(f"- [{title}]({url})\n  {snippet}")
            return "\n\n".join(output)
    except Exception as e:
        return f"Search error: {e}"


def handle_save_evidence(args: dict, org_id: str, agent_id: str, date: str) -> str:
    """Save evidence and return confirmation. Dispatches to the platform-sweep
    store if `org_id` carries the sweep prefix."""
    from orchestrator.sweep_evidence import is_sweep_org_id, save_sweep_evidence
    if is_sweep_org_id(org_id):
        record = save_sweep_evidence(org_id, agent_id, date, args)
    else:
        record = save_evidence(org_id, agent_id, date, args)
    return json.dumps({"saved": True, "id": record["id"], "hash": record["hash"]})


TOOL_HANDLERS = {
    "web_fetch": handle_web_fetch,
    "web_search": handle_web_search,
    "save_evidence": handle_save_evidence,
}
