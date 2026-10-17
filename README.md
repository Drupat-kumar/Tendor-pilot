# TenderPilot AI

TenderPilot AI is a Streamlit MVP for public tender discovery, contractor-to-tender fit scoring, and evidence-based eligibility review. It is a decision-support tool, not a bid submission service or a guarantee of eligibility or contract award.

## Features

- TinyFish Search and Fetch adapters for CPPP, Government ePublishing, GeM, Delhi eProcurement, and Indian Railways IREPS.
- Manual public tender URL intake; search-only records remain explicitly **unverified** when their pages cannot be fetched.
- Pydantic-validated tender records, exact source clauses for extracted requirements, and accessible-PDF text extraction via `pypdf`.
- Contractor profiles, tender records, and saved tenders stored in SQLite.
- Deduplication by source plus tender ID (or source plus URL if the ID is unavailable).
- Search and filters for location, category, department, tender value, and closing date.
- Explainable TF-IDF similarity and business-rule relevance score, kept separate from PASS / FAIL / REVIEW eligibility results.
- No sample/live tender fixtures are included. Missing, ambiguous, or inaccessible evidence is not inferred.

## Setup

Requires Python 3.10 or newer. Run commands from the project directory.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `TINYFISH_API_KEY` in the current shell, your environment, or Streamlit secrets:

```powershell
$env:TINYFISH_API_KEY = "your TinyFish API key"
```

Alternatively, put `TINYFISH_API_KEY=your-key` in the local `.env` file; the app loads it automatically. The `.env` file is ignored by Git.

TinyFish documents REST API authentication via the `X-API-Key` header. Search uses `GET https://api.search.tinyfish.ai` with a `query` parameter; Fetch uses `POST https://api.fetch.tinyfish.ai` with a JSON `urls` array. Search results are discovery leads, not verified tender details. Fetch failures and source restrictions are shown rather than worked around.

Run:

```powershell
streamlit run app.py
```

Open the local Streamlit URL printed in the terminal. Create a contractor profile, then use **Find Tenders** to search available public listings or submit a public tender URL. A missing API key does not disable profile, saved-tender, or previously collected tender workflows.

## Tender evidence and status

- **Active**: a page was fetched and a future submission deadline was explicitly extracted.
- **Expired**: an extracted submission deadline is in the past.
- **Unverified**: the listing is search-only, has no extracted deadline, or was otherwise not verifiable from the retrieved public text.
- A successful page fetch does not make every extracted requirement legally verified; always compare source clauses with the official notice and corrigenda.
- Monetary amounts, dates, experience, certifications, and conditions are displayed only when a source-text pattern provides evidence. Unparsed fields remain blank.
- Eligibility PASS / FAIL decisions compare tender evidence with the contractor's self-reported profile. Missing profile evidence or ambiguous conditions result in REVIEW. Document readiness is REVIEW until separately confirmed.
- A relevance score is not eligibility and is not a probability of winning. The value-fit heuristic (estimated value within 5× reported annual turnover) is a transparent discovery preference only, not an eligibility rule.

## Database

SQLite defaults to `data/tenderpilot.sqlite3`. Override the location with `TENDERPILOT_DB_PATH`. The app creates tables on startup. Back up the SQLite file to preserve profiles and saved tenders.

## Tests

```powershell
python -m pytest
```

Tests cover tender evidence extraction, unverified listings, profile persistence, source-aware deduplication, saved state, and eligibility outcomes. The tests do not call TinyFish or any tender website.

## Deployment

Deploy on a Python host that supports Streamlit, provides persistent storage for SQLite, and allows outbound HTTPS. Set `TINYFISH_API_KEY` and optionally `TENDERPILOT_DB_PATH` as deployment environment variables or secret values; never commit API keys or `.env`. A minimal container command is:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
```

Use a persistent mounted volume for `data/` and restrict application access appropriately for contractor data. TinyFish Search/Fetch limits and source site availability are external dependencies. This app does not solve CAPTCHA, sign in to restricted portals, or bypass access controls.

## Verified API references

- [TinyFish Search API](https://docs.tinyfish.ai/search-api)
- [TinyFish Search API reference](https://docs.tinyfish.ai/search-api/reference)
- [TinyFish Fetch API](https://docs.tinyfish.ai/fetch-api)
- [TinyFish Fetch API reference](https://docs.tinyfish.ai/fetch-api/reference)
- [TinyFish Authentication](https://docs.tinyfish.ai/authentication)
