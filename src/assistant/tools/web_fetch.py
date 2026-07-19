"""Web fetch tool — fetches and extracts readable content from URLs.

Uses httpx for fetching and BeautifulSoup for content extraction.
"""

import httpx
from ag2 import tool
from bs4 import BeautifulSoup


def web_fetch(url: str, max_chars: int = 10000) -> str:
    """Fetch a web page and extract its readable text content.

    Args:
        url: The URL to fetch.
        max_chars: Maximum characters to return (default 10000).

    Returns:
        The extracted text content from the page.
    """
    try:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=30.0,
            headers={"User-Agent": "AG2 Assistant/0.1"},
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        return f"Error fetching {url}: {e}"

    content_type = response.headers.get("content-type", "")

    if "application/json" in content_type:
        text = response.text[:max_chars]
        return f"JSON from {url}:\n\n{text}"

    if "text/plain" in content_type:
        text = response.text[:max_chars]
        return f"Content from {url}:\n\n{text}"

    # HTML — extract readable content
    soup = BeautifulSoup(response.text, "html.parser")

    # Remove non-content elements
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()

    # Try to find main content
    main = soup.find("main") or soup.find("article") or soup.find("body")
    if main is None:
        main = soup

    text = main.get_text(separator="\n", strip=True)

    # Clean up whitespace
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text = "\n".join(lines)

    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[Content truncated]"

    title = soup.title.string.strip() if soup.title and soup.title.string else url
    return f"# {title}\n\nSource: {url}\n\n{text}"


web_fetch_tool = tool(web_fetch)
