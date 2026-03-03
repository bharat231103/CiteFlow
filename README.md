<p align="center">
  <img src="https://img.shields.io/badge/CITEFLOW-AI%20Writing%20Assistant-6366f1?style=for-the-badge&logo=openai&logoColor=white" alt="CITEFLOW"/>
</p>

<h1 align="center">🔗 CITEFLOW</h1>

<p align="center">
  <strong>AI-powered document suggestions with structured citation metadata</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI"/>
  <img src="https://img.shields.io/badge/LangGraph-121212?style=flat-square&logo=chainlink&logoColor=white" alt="LangGraph"/>
  <img src="https://img.shields.io/badge/OpenAI-412991?style=flat-square&logo=openai&logoColor=white" alt="OpenAI"/>
  <img src="https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker"/>
  <img src="https://img.shields.io/badge/Qdrant-FF4F64?style=flat-square&logoColor=white" alt="Qdrant"/>
</p>

---

## 📖 Overview

**CITEFLOW** is an intelligent document writing assistant that provides real-time content suggestions backed by verified, structured academic citations. Every suggestion comes with full metadata — authors, year, title, DOI, abstract, publication venue — enabling in-text citations, hover previews, reference list generation, and APA/MLA formatting.

---

## 🚀 Quick Start

### Prerequisites

- Docker & Docker Compose
- OpenAI API Key

### Setup

```bash
git clone https://github.com/yourusername/citeflow.git
cd citeflow

cp env.template .env
# Edit .env and set your OPENAI_API_KEY

docker compose up -d
```

### Verify

```bash
docker compose ps
```

| Service | Port | Description |
|---------|------|-------------|
| `recommendation-agent` | 8000 | Main API & WebSocket |
| `qdrant-digitrix` | 6333 | Vector Database |
| `searxng-digitrix` | 8080 | Meta Search Engine |
| `firecrawl-api-digitrix` | 3002 | Web Scraper |
| `playwright-digitrix` | 3000 | Browser Rendering |

---

## 💡 WebSocket API

### Endpoint

```
ws://localhost:8000/suggest/{document_id}
```

`document_id` — Any unique string identifying the document being edited. Each document gets its own isolated knowledge base.

---

### Connection

On connecting, the server sends a confirmation message:

```json
{
  "status": "connected",
  "message": "Connected to CITEFLOW for document: my-doc-123",
  "document_id": "my-doc-123"
}
```

---

### Request Format

Send a JSON message with the document context:

```json
{
  "title": "The History of Qutub Minar",
  "heading": "Introduction",
  "content": "The Qutub Minar, a UNESCO World Heritage Site, stands as a remarkable testament to the architectural brilliance of the era."
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | No | Document title |
| `heading` | string | No | Current section heading |
| `content` | string | **Yes** | Recent content from the document (last few sentences) |

---

### Response Format

The server returns a suggestion with **structured citation metadata**:

```json
{
  "suggestion": "Constructed in 1193 by Qutb ud-Din Aibak, the tower was later completed by his successor Iltutmish, reaching a height of 72.5 meters.",
  "citations": [
    {
      "id": "cite_1",
      "inText": "Asher, 2020",
      "type": "Article",
      "articleType": "Journal",
      "title": "The Qutb Complex: Architecture and History of the Delhi Sultanate",
      "shortTitle": "",
      "abstract": "This paper examines the architectural evolution of the Qutb complex...",
      "publication": "Journal of Islamic Architecture",
      "year": 2020,
      "month": 6,
      "day": 15,
      "authors": [
        { "family": "Asher", "given": "Catherine B." }
      ],
      "identifiers": {
        "doi": "10.1234/jia.2020.0042",
        "url": "https://example.com/article"
      }
    },
    {
      "id": "cite_2",
      "inText": "Unknown, n.d.",
      "type": "Webpage",
      "articleType": "",
      "title": "Qutb Minar",
      "shortTitle": "",
      "abstract": "Qutb Minar is a minaret that forms part of the Qutb complex...",
      "publication": "Wikipedia",
      "year": null,
      "month": null,
      "day": null,
      "authors": [],
      "identifiers": {
        "doi": "",
        "url": "https://en.wikipedia.org/wiki/Qutb_Minar"
      }
    }
  ]
}
```

---

### Citation Object Schema

Each citation object in the `citations` array follows this schema:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique citation ID within the response (`cite_1`, `cite_2`, ...) |
| `inText` | string | Pre-formatted in-text citation (`"Shen et al., 2025"`, `"Author & Author, 2020"`, `"Unknown, n.d."`) |
| `type` | string | Citation type: `Article`, `Book`, `Webpage`, `ConferencePaper`, `Thesis`, `Dataset`, `Report` |
| `articleType` | string | Sub-type: `Journal`, `Preprint`, `Conference`, `BookChapter`, `Book`, `Thesis`, `Dataset`, `Report`, or `""` |
| `title` | string | Full title of the source |
| `shortTitle` | string | Abbreviated title (reserved for future use) |
| `abstract` | string | Abstract or summary of the source (up to 1000 chars) |
| `publication` | string | Journal, publisher, or venue name |
| `year` | int \| null | Publication year |
| `month` | int \| null | Publication month (1-12) |
| `day` | int \| null | Publication day (1-31) |
| `authors` | array | List of author objects |
| `authors[].family` | string | Author's family/last name |
| `authors[].given` | string | Author's given/first name |
| `identifiers` | object | Identifier URLs |
| `identifiers.doi` | string | DOI string (e.g. `"10.1234/example"`) or `""` |
| `identifiers.url` | string | Source URL |

---

### In-Text Citation Format

The `inText` field is auto-generated following academic conventions:

| Authors | Format | Example |
|---------|--------|---------|
| 1 author | `Family, Year` | `Asher, 2020` |
| 2 authors | `Family & Family, Year` | `Asher & Koch, 2020` |
| 3+ authors | `Family et al., Year` | `Shen et al., 2025` |
| No authors | `Unknown, Year` | `Unknown, 2023` |
| No year | `Family, n.d.` | `Asher, n.d.` |

---

## ⚡ Session Lifecycle

```
CONNECT  ws://host:8000/suggest/{doc_id}
   │
   ▼
MESSAGE #1 ─── RESEARCH PATH (~15-30s) ───────────────▶
   │   Web Search → Scrape → Store in Qdrant → Query
   │   → Generate Suggestion → Enrich Citations via
   │     CrossRef / arXiv / OpenAlex / GPT-4o fallback
   │
   │   ✅ doc_id marked as "initialized"
   ▼
MESSAGE #2+ ─── FAST PATH (~3-5s) ────────────────────▶
   │   Query Qdrant → Generate Suggestion
   │   → Enrich Citations (cached URLs resolve instantly)
   ▼
DISCONNECT
   │
   ▼
CLEANUP ── Delete doc_id vectors from Qdrant ─────────▶
```

- **1st message**: Full research pipeline — searches the web, scrapes pages, stores in vector DB, then generates a suggestion with enriched citations. Takes ~15-30 seconds.
- **2nd+ messages**: Fast path — only queries the existing knowledge base. Previously enriched citation metadata is cached. Takes ~3-5 seconds.
- **On disconnect**: The document's Qdrant collection and citation cache are deleted.

---

## 🔬 Citation Enrichment Pipeline

For each raw URL returned by the AI agent, the backend resolves structured metadata using this priority chain:

| Priority | Condition | Source | What It Returns |
|----------|-----------|--------|-----------------|
| 1 | DOI found in URL | **CrossRef API** | Title, authors, year, journal, abstract, DOI |
| 2 | arXiv URL detected | **arXiv API** | Title, authors, year, abstract, arXiv DOI |
| 3 | Any URL | **OpenAlex API** | Title, authors, year, publication, abstract |
| 4 | All above fail | **GPT-4o-mini** | Extracts metadata from page HTML |
| 5 | Everything fails | **Minimal** | URL-only Webpage citation |

All enrichment results are cached per session, so the fast path never re-fetches the same URL.

---

## 📝 Usage Examples

### Python

```python
import asyncio
import websockets
import json

async def get_suggestion():
    uri = "ws://localhost:8000/suggest/my-doc-123"

    async with websockets.connect(uri) as ws:
        # Wait for connection confirmation
        print(await ws.recv())

        # Send document context
        await ws.send(json.dumps({
            "title": "The History of Qutub Minar",
            "heading": "Introduction",
            "content": "The Qutub Minar stands as one of India's most iconic monuments."
        }))

        # Receive structured suggestion + citations
        response = json.loads(await ws.recv())

        print(f"Suggestion: {response['suggestion']}")
        for cite in response['citations']:
            print(f"  [{cite['id']}] {cite['inText']} — {cite['title']}")
            print(f"         Type: {cite['type']} | DOI: {cite['identifiers']['doi']}")
            print(f"         Authors: {', '.join(a['given'] + ' ' + a['family'] for a in cite['authors'])}")

asyncio.run(get_suggestion())
```

### JavaScript

```javascript
const ws = new WebSocket('ws://localhost:8000/suggest/my-doc-123');

ws.onopen = () => {
  ws.send(JSON.stringify({
    title: "The History of Qutub Minar",
    heading: "Introduction",
    content: "The Qutub Minar stands as one of India's most iconic monuments."
  }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);

  // Skip connection confirmation
  if (data.status === 'connected') return;

  console.log('Suggestion:', data.suggestion);

  data.citations.forEach(cite => {
    console.log(`[${cite.id}] ${cite.inText}`);
    console.log(`  Title: ${cite.title}`);
    console.log(`  Type: ${cite.type} (${cite.articleType})`);
    console.log(`  Year: ${cite.year}`);
    console.log(`  DOI: ${cite.identifiers.doi}`);
    console.log(`  URL: ${cite.identifiers.url}`);
    console.log(`  Authors:`, cite.authors.map(a => `${a.given} ${a.family}`).join(', '));
    console.log(`  Abstract: ${cite.abstract?.substring(0, 100)}...`);
  });
};
```

---

## 🛠️ REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Service info |
| `GET` | `/health` | Health check with active/initialized session counts |

### Health Check Response

```json
{
  "status": "healthy",
  "active_sessions": 2,
  "initialized_sessions": 1,
  "services": {
    "qdrant": "http://qdrant-digitrix:6333",
    "searxng": "http://searxng-digitrix:8080",
    "firecrawl": "http://firecrawl-api-digitrix:3002"
  }
}
```

---

## 🏗️ Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                              CITEFLOW                                  │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                 Recommendation Agent (FastAPI)                   │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │  │
│  │  │WebSocket │→ │LangGraph │→ │ GPT-4o   │→ │  Citation     │  │  │
│  │  │Handler   │  │Agent     │  │ mini     │  │  Enricher     │  │  │
│  │  └──────────┘  └──────────┘  └──────────┘  └───────┬───────┘  │  │
│  └─────────────────────────────────────────────────────┼──────────┘  │
│                              │                         │              │
│         ┌────────────────────┼──────────┐    ┌─────────┼─────────┐  │
│         ▼                    ▼          ▼    ▼         ▼         ▼  │
│  ┌───────────┐  ┌───────────┐  ┌──────────┐ ┌────────┐ ┌───────┐  │
│  │  SearXNG  │  │ Firecrawl │  │  Qdrant  │ │CrossRef│ │arXiv  │  │
│  │  :8080    │  │  :3002    │  │  :6333   │ │  API   │ │ API   │  │
│  └───────────┘  └───────────┘  └──────────┘ ├────────┤ └───────┘  │
│                                              │OpenAlex│            │
│                                              │  API   │            │
│                                              └────────┘            │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 📁 Project Structure

```
citeflow/
├── docker-compose.yml              # All 9 services orchestration
├── env.template                    # Environment variable template
├── README.md                       # This file
├── test_ws.html                    # Browser-based WebSocket test UI
│
├── Recommendation Agent/           # Main Python application
│   ├── Dockerfile
│   ├── main.py                     # FastAPI app, WebSocket handler, session management
│   ├── requirements.txt
│   └── utils/
│       ├── agent.py                # LangGraph agent (research + fast paths)
│       ├── citation_metadata.py    # Citation enrichment (CrossRef/arXiv/OpenAlex/LLM)
│       ├── crawl_ops.py            # Firecrawl web scraping
│       ├── embeddings.py           # OpenAI text-embedding-3-small
│       ├── qdrant_ops.py           # Qdrant vector DB operations
│       └── search_ops.py           # SearXNG meta-search
│
├── firecrawl/                      # Firecrawl source (built from source)
│   └── apps/
│       ├── api/
│       ├── nuq-postgres/
│       └── playwright-service-ts/
│
└── searxng-docker/                 # SearXNG configuration
    └── searxng/
        ├── settings.yml
        └── limiter.toml
```

---

## ⚙️ Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `OPENAI_API_KEY` | OpenAI API key for GPT-4o-mini & embeddings | **Yes** |
| `USE_DB_AUTHENTICATION` | Firecrawl auth toggle (default: `false`) | No |

---

## 🛑 Stop / Cleanup

```bash
# Stop all services
docker compose down

# Stop and remove all data (Qdrant vectors, caches)
docker compose down -v
```

---

## 📄 License

MIT License
