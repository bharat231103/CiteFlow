"""
Citation Metadata Enrichment
Transforms raw URLs into structured citation objects by querying
CrossRef, arXiv, OpenAlex APIs with GPT-4o-mini as a last-resort fallback.

Priority order per URL:
  1. CrossRef   (if DOI found in URL)
  2. arXiv API  (if arXiv URL detected)
  3. OpenAlex   (broad coverage, any URL)
  4. GPT-4o-mini (extract from scraped page text)
  5. Minimal    (URL-only Webpage citation)
"""

import os
import re
import json
import logging
import asyncio
from typing import Optional
from urllib.parse import urlparse, unquote

import httpx
import xmltodict
from openai import AsyncOpenAI

logger = logging.getLogger("citeflow.citations")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ═══════════════════════════════════════════════════════════════════════════════
#  URL / IDENTIFIER EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

def extract_doi_from_url(url: str) -> Optional[str]:
    """Extract a DOI from a URL if present."""
    # doi.org links
    m = re.search(r'doi\.org/(10\.\d{4,9}/[^\s&?#]+)', url)
    if m:
        return unquote(m.group(1)).rstrip(".")

    # dx.doi.org
    m = re.search(r'dx\.doi\.org/(10\.\d{4,9}/[^\s&?#]+)', url)
    if m:
        return unquote(m.group(1)).rstrip(".")

    # Embedded DOI query param
    m = re.search(r'[?&]doi=(10\.\d{4,9}/[^\s&?#]+)', url)
    if m:
        return unquote(m.group(1)).rstrip(".")

    return None


def extract_arxiv_id_from_url(url: str) -> Optional[str]:
    """Extract an arXiv paper ID from a URL."""
    # https://arxiv.org/abs/2503.08223  or  /pdf/2503.08223
    m = re.search(r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)', url)
    if m:
        return m.group(1)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  API FETCHERS
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_crossref_metadata(doi: str) -> Optional[dict]:
    """Fetch citation metadata from CrossRef by DOI."""
    crossref_url = f"https://api.crossref.org/works/{doi}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                crossref_url,
                headers={"User-Agent": "CITEFLOW/1.0 (mailto:citeflow@example.com)"},
            )
            if resp.status_code != 200:
                return None
            data = resp.json().get("message", {})

        authors = []
        for a in data.get("author", []):
            authors.append({
                "family": a.get("family", ""),
                "given": a.get("given", ""),
            })

        # Date parts
        date_parts = (
            data.get("published-print", {}).get("date-parts", [[]])
            or data.get("published-online", {}).get("date-parts", [[]])
            or data.get("created", {}).get("date-parts", [[]])
        )
        parts = date_parts[0] if date_parts else []
        year = parts[0] if len(parts) > 0 else None
        month = parts[1] if len(parts) > 1 else None
        day = parts[2] if len(parts) > 2 else None

        title_list = data.get("title", [])
        title = title_list[0] if title_list else ""

        container = data.get("container-title", [])
        publication = container[0] if container else ""

        abstract = data.get("abstract", "")
        # Strip JATS XML tags from abstract
        if abstract:
            abstract = re.sub(r"<[^>]+>", "", abstract).strip()

        cr_type = data.get("type", "")
        citation_type = _crossref_type_to_citation_type(cr_type)

        return {
            "title": title,
            "authors": authors,
            "year": year,
            "month": month,
            "day": day,
            "publication": publication,
            "abstract": abstract,
            "type": citation_type,
            "articleType": _get_article_type(cr_type),
            "doi": doi,
        }

    except Exception as e:
        logger.debug(f"CrossRef fetch failed for DOI {doi}: {e}")
        return None


async def fetch_arxiv_metadata(arxiv_id: str) -> Optional[dict]:
    """Fetch citation metadata from the arXiv API."""
    arxiv_url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(arxiv_url)
            if resp.status_code != 200:
                return None
            xml_data = xmltodict.parse(resp.text)

        entry = xml_data.get("feed", {}).get("entry", {})
        if not entry or isinstance(entry, list):
            entry = entry[0] if isinstance(entry, list) and entry else {}
        if not entry:
            return None

        title = entry.get("title", "").replace("\n", " ").strip()
        abstract = entry.get("summary", "").replace("\n", " ").strip()

        # Authors
        raw_authors = entry.get("author", [])
        if isinstance(raw_authors, dict):
            raw_authors = [raw_authors]
        authors = []
        for a in raw_authors:
            name = a.get("name", "")
            parts = name.rsplit(" ", 1)
            if len(parts) == 2:
                authors.append({"family": parts[1], "given": parts[0]})
            else:
                authors.append({"family": name, "given": ""})

        # Date
        published = entry.get("published", "")
        year, month, day = None, None, None
        if published:
            date_match = re.match(r"(\d{4})-(\d{2})-(\d{2})", published)
            if date_match:
                year = int(date_match.group(1))
                month = int(date_match.group(2))
                day = int(date_match.group(3))

        # DOI (arXiv assigns a DOI pattern)
        doi = f"10.48550/arxiv.{arxiv_id}"

        return {
            "title": title,
            "authors": authors,
            "year": year,
            "month": month,
            "day": day,
            "publication": "arXiv (Cornell University)",
            "abstract": abstract,
            "type": "Article",
            "articleType": "Preprint",
            "doi": doi,
        }

    except Exception as e:
        logger.debug(f"arXiv fetch failed for ID {arxiv_id}: {e}")
        return None


async def fetch_openalex_metadata(url: str, doi: Optional[str] = None) -> Optional[dict]:
    """Fetch citation metadata from OpenAlex by DOI or URL."""
    try:
        if doi:
            oa_url = f"https://api.openalex.org/works/doi:{doi}"
        else:
            oa_url = f"https://api.openalex.org/works?filter=locations.landing_page_url:{url}"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                oa_url,
                headers={"User-Agent": "CITEFLOW/1.0 (mailto:citeflow@example.com)"},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()

        # If we searched by URL, results are in a list
        if "results" in data:
            results = data.get("results", [])
            if not results:
                return None
            data = results[0]

        title = data.get("title", "") or ""

        # Authors
        authors = []
        for authorship in data.get("authorships", []):
            author_info = authorship.get("author", {})
            display_name = author_info.get("display_name", "")
            parts = display_name.rsplit(" ", 1)
            if len(parts) == 2:
                authors.append({"family": parts[1], "given": parts[0]})
            else:
                authors.append({"family": display_name, "given": ""})

        # Date
        pub_date = data.get("publication_date", "")
        year = data.get("publication_year")
        month, day = None, None
        if pub_date:
            date_match = re.match(r"(\d{4})-(\d{2})-(\d{2})", pub_date)
            if date_match:
                year = int(date_match.group(1))
                month = int(date_match.group(2))
                day = int(date_match.group(3))

        # Publication venue
        primary_location = data.get("primary_location", {}) or {}
        source = primary_location.get("source", {}) or {}
        publication = source.get("display_name", "")

        # Abstract (OpenAlex stores inverted index; reconstruct)
        abstract = ""
        abstract_inv = data.get("abstract_inverted_index")
        if abstract_inv and isinstance(abstract_inv, dict):
            try:
                word_positions = []
                for word, positions in abstract_inv.items():
                    for pos in positions:
                        word_positions.append((pos, word))
                word_positions.sort()
                abstract = " ".join(w for _, w in word_positions)
            except Exception:
                abstract = ""

        found_doi = data.get("doi", "")
        if found_doi and found_doi.startswith("https://doi.org/"):
            found_doi = found_doi.replace("https://doi.org/", "")

        oa_type = data.get("type", "")
        citation_type = _openalex_type_to_citation_type(oa_type)

        return {
            "title": title,
            "authors": authors,
            "year": year,
            "month": month,
            "day": day,
            "publication": publication,
            "abstract": abstract[:1000] if abstract else "",
            "type": citation_type,
            "articleType": _get_article_type_from_openalex(oa_type),
            "doi": found_doi or doi or "",
        }

    except Exception as e:
        logger.debug(f"OpenAlex fetch failed for {url}: {e}")
        return None


async def extract_metadata_via_llm(url: str, scraped_text: Optional[str] = None) -> Optional[dict]:
    """Last-resort: use GPT-4o-mini to extract citation metadata from URL or page text."""
    if not OPENAI_API_KEY:
        return None

    snippet = ""
    if scraped_text:
        snippet = scraped_text[:3000]
    else:
        # Try to fetch the page title at minimum
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "CITEFLOW/1.0"})
                if resp.status_code == 200:
                    text = resp.text[:5000]
                    # Extract title from HTML
                    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
                    if title_match:
                        snippet = f"Page title: {title_match.group(1).strip()}\n\nURL: {url}"
                    else:
                        snippet = f"URL: {url}\n\nPage content:\n{text[:2000]}"
        except Exception:
            snippet = f"URL: {url}"

    if not snippet:
        snippet = f"URL: {url}"

    try:
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.0,
            messages=[
                {"role": "system", "content": (
                    "Extract citation metadata from the given content. "
                    "Return a JSON object with these fields: "
                    "title (string), authors (array of {family, given}), "
                    "year (number or null), month (number or null), day (number or null), "
                    "publication (string, journal/publisher name), "
                    "abstract (string, brief summary), "
                    "type (one of: Article, Book, Webpage, ConferencePaper, Thesis, Dataset, Report), "
                    "doi (string or empty). "
                    "If you cannot determine a field, use empty string or null. "
                    "Return ONLY valid JSON, no markdown."
                )},
                {"role": "user", "content": snippet},
            ],
        )
        text = response.choices[0].message.content.strip()
        # Parse JSON
        json_start = text.find("{")
        json_end = text.rfind("}") + 1
        if json_start != -1 and json_end > json_start:
            parsed = json.loads(text[json_start:json_end])
            # Normalize authors
            authors = parsed.get("authors", [])
            if isinstance(authors, list):
                normalized = []
                for a in authors:
                    if isinstance(a, dict):
                        normalized.append({
                            "family": a.get("family", ""),
                            "given": a.get("given", ""),
                        })
                    elif isinstance(a, str):
                        parts = a.rsplit(" ", 1)
                        if len(parts) == 2:
                            normalized.append({"family": parts[1], "given": parts[0]})
                        else:
                            normalized.append({"family": a, "given": ""})
                parsed["authors"] = normalized
            return parsed

    except Exception as e:
        logger.debug(f"LLM metadata extraction failed for {url}: {e}")

    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  CITATION BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def build_in_text_citation(authors: list[dict], year: Optional[int]) -> str:
    """Generate in-text citation string like 'Author et al., 2025'."""
    year_str = str(year) if year else "n.d."

    if not authors:
        return f"Unknown, {year_str}"

    first_family = authors[0].get("family", "Unknown")

    if len(authors) == 1:
        return f"{first_family}, {year_str}"
    elif len(authors) == 2:
        second_family = authors[1].get("family", "")
        return f"{first_family} & {second_family}, {year_str}"
    else:
        return f"{first_family} et al., {year_str}"


def determine_citation_type(metadata: dict, url: str) -> str:
    """Determine the citation type from metadata or URL patterns."""
    if metadata.get("type"):
        return metadata["type"]

    url_lower = url.lower()

    if "arxiv.org" in url_lower:
        return "Article"
    if "doi.org" in url_lower:
        return "Article"
    if any(domain in url_lower for domain in [
        "scholar.google", "pubmed", "ncbi.nlm.nih",
        "jstor.org", "sciencedirect.com", "springer.com",
        "wiley.com", "ieee.org", "acm.org",
    ]):
        return "Article"
    if "wikipedia.org" in url_lower:
        return "Webpage"
    if any(ext in url_lower for ext in [".edu", ".ac.", "university"]):
        return "Webpage"
    if "github.com" in url_lower:
        return "Dataset"
    if "conference" in url_lower or "proceedings" in url_lower:
        return "ConferencePaper"

    return "Webpage"


def _get_article_type(crossref_type: str) -> str:
    """Map CrossRef type to articleType."""
    mapping = {
        "journal-article": "Journal",
        "proceedings-article": "Conference",
        "posted-content": "Preprint",
        "book-chapter": "BookChapter",
        "book": "Book",
        "dissertation": "Thesis",
        "dataset": "Dataset",
        "report": "Report",
        "monograph": "Book",
    }
    return mapping.get(crossref_type, "Journal")


def _crossref_type_to_citation_type(crossref_type: str) -> str:
    """Map CrossRef work type to citation type."""
    mapping = {
        "journal-article": "Article",
        "proceedings-article": "ConferencePaper",
        "posted-content": "Article",
        "book-chapter": "Book",
        "book": "Book",
        "dissertation": "Thesis",
        "dataset": "Dataset",
        "report": "Report",
        "monograph": "Book",
    }
    return mapping.get(crossref_type, "Article")


def _openalex_type_to_citation_type(oa_type: str) -> str:
    """Map OpenAlex type to citation type."""
    mapping = {
        "article": "Article",
        "book": "Book",
        "book-chapter": "Book",
        "dataset": "Dataset",
        "dissertation": "Thesis",
        "proceedings-article": "ConferencePaper",
        "report": "Report",
        "preprint": "Article",
    }
    return mapping.get(oa_type, "Article")


def _get_article_type_from_openalex(oa_type: str) -> str:
    """Map OpenAlex type to articleType."""
    mapping = {
        "article": "Journal",
        "book": "Book",
        "book-chapter": "BookChapter",
        "dataset": "Dataset",
        "dissertation": "Thesis",
        "proceedings-article": "Conference",
        "report": "Report",
        "preprint": "Preprint",
    }
    return mapping.get(oa_type, "Journal")


def _build_citation_object(
    cite_id: str,
    url: str,
    metadata: dict,
) -> dict:
    """Build the final structured citation object."""
    authors = metadata.get("authors", [])
    year = metadata.get("year")
    if isinstance(year, str):
        try:
            year = int(year)
        except (ValueError, TypeError):
            year = None

    month = metadata.get("month")
    if isinstance(month, str):
        try:
            month = int(month)
        except (ValueError, TypeError):
            month = None

    day = metadata.get("day")
    if isinstance(day, str):
        try:
            day = int(day)
        except (ValueError, TypeError):
            day = None

    citation_type = metadata.get("type") or determine_citation_type(metadata, url)
    article_type = metadata.get("articleType", "Journal")

    return {
        "id": cite_id,
        "inText": build_in_text_citation(authors, year),
        "type": citation_type,
        "articleType": article_type,
        "title": metadata.get("title", ""),
        "shortTitle": "",
        "abstract": metadata.get("abstract", ""),
        "publication": metadata.get("publication", ""),
        "year": year,
        "month": month,
        "day": day,
        "authors": authors,
        "identifiers": {
            "doi": metadata.get("doi", ""),
            "url": url,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN ENRICHMENT ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

async def enrich_single_citation(
    url: str,
    cite_id: str,
    scraped_text: Optional[str] = None,
) -> dict:
    """
    Enrich a single URL into a structured citation object.
    Tries APIs in priority order, falls back to LLM, then minimal.
    """
    metadata = None

    # 1. Try CrossRef if DOI is in the URL
    doi = extract_doi_from_url(url)
    if doi:
        logger.info(f"  [{cite_id}] DOI detected: {doi} — trying CrossRef")
        metadata = await fetch_crossref_metadata(doi)
        if metadata:
            logger.info(f"  [{cite_id}] CrossRef hit")
            return _build_citation_object(cite_id, url, metadata)

    # 2. Try arXiv API if it's an arXiv URL
    arxiv_id = extract_arxiv_id_from_url(url)
    if arxiv_id:
        logger.info(f"  [{cite_id}] arXiv ID detected: {arxiv_id} — trying arXiv API")
        metadata = await fetch_arxiv_metadata(arxiv_id)
        if metadata:
            logger.info(f"  [{cite_id}] arXiv API hit")
            return _build_citation_object(cite_id, url, metadata)

    # 3. Try OpenAlex (broad coverage)
    logger.info(f"  [{cite_id}] Trying OpenAlex for: {url[:60]}...")
    metadata = await fetch_openalex_metadata(url, doi)
    if metadata and metadata.get("title"):
        logger.info(f"  [{cite_id}] OpenAlex hit")
        return _build_citation_object(cite_id, url, metadata)

    # 4. Try LLM extraction
    logger.info(f"  [{cite_id}] Falling back to LLM extraction for: {url[:60]}...")
    metadata = await extract_metadata_via_llm(url, scraped_text)
    if metadata and metadata.get("title"):
        # Ensure type is set
        if not metadata.get("type"):
            metadata["type"] = determine_citation_type(metadata, url)
        if not metadata.get("articleType"):
            metadata["articleType"] = "Journal" if metadata["type"] == "Article" else ""
        logger.info(f"  [{cite_id}] LLM extraction successful")
        return _build_citation_object(cite_id, url, metadata)

    # 5. Minimal fallback — Webpage with URL only
    logger.warning(f"  [{cite_id}] All enrichment methods failed, using minimal Webpage citation")
    # Try to at least get a title from the URL
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")
    path_title = parsed.path.strip("/").split("/")[-1].replace("-", " ").replace("_", " ").title()

    return _build_citation_object(cite_id, url, {
        "title": path_title if path_title else domain,
        "authors": [],
        "year": None,
        "month": None,
        "day": None,
        "publication": domain,
        "abstract": "",
        "type": "Webpage",
        "articleType": "",
        "doi": "",
    })


async def enrich_citations(
    raw_urls: list[str],
    cache: dict[str, dict],
    scraped_texts: Optional[dict[str, str]] = None,
) -> list[dict]:
    """
    Enrich a list of raw URLs into structured citation objects.
    Uses a session-level cache to avoid re-fetching already-resolved URLs.

    Args:
        raw_urls: List of raw citation URLs from the agent.
        cache: Session-level cache (mutated in-place), mapping URL → citation dict.
        scraped_texts: Optional mapping of URL → scraped page text for LLM fallback.

    Returns:
        List of structured citation objects.
    """
    if not raw_urls:
        return []

    logger.info(f"Enriching {len(raw_urls)} citation(s)...")

    results = []
    tasks = []

    for i, url in enumerate(raw_urls):
        cite_id = f"cite_{i + 1}"

        # Check cache first
        if url in cache:
            logger.info(f"  [{cite_id}] Cache HIT for: {url[:60]}...")
            cached = cache[url].copy()
            cached["id"] = cite_id  # Update ID for this response
            results.append((i, cached))
            continue

        # Need to fetch — prepare async task
        scraped = (scraped_texts or {}).get(url)
        tasks.append((i, url, cite_id, scraped))

    # Fetch all uncached citations concurrently
    if tasks:
        async def _enrich_task(index, url, cite_id, scraped):
            citation = await enrich_single_citation(url, cite_id, scraped)
            cache[url] = citation  # Store in session cache
            return index, citation

        fetch_results = await asyncio.gather(
            *[_enrich_task(idx, u, cid, s) for idx, u, cid, s in tasks],
            return_exceptions=True,
        )

        for result in fetch_results:
            if isinstance(result, Exception):
                logger.error(f"Citation enrichment error: {result}")
                continue
            results.append(result)

    # Sort by original index and return
    results.sort(key=lambda x: x[0])
    citations = [r[1] for r in results]

    logger.info(f"Enrichment complete: {len(citations)} citation(s) returned")
    return citations
