from __future__ import annotations

from datetime import date, datetime
import io
import ipaddress
import re
import unicodedata
from typing import Any
from urllib.parse import urlparse

import requests
from pydantic import ValidationError
from pypdf import PdfReader
from pypdf.errors import PdfReadError, PdfStreamError

from tenderpilot.models import (
    EvidenceRequirement,
    SourceDefinition,
    Tender,
    TenderRequirements,
)

SEARCH_ENDPOINT = "https://api.search.tinyfish.ai"
FETCH_ENDPOINT = "https://api.fetch.tinyfish.ai"
REQUEST_TIMEOUT_SECONDS = 50
MAX_CONTENT_CHARS = 150_000

SOURCES = [
    SourceDefinition(
        source_id="cppp",
        name="CPPP",
        base_url="https://eprocure.gov.in/eprocure/app",
        search_domain="eprocure.gov.in",
        listing_url="https://eprocure.gov.in/eprocure/app",
        automated_access="unverified",
        access_note="Search and public page fetch are attempted only; access is never bypassed.",
    ),
    SourceDefinition(
        source_id="epublishing",
        name="Government ePublishing",
        base_url="https://www.eprocure.gov.in/epublish/app",
        search_domain="eprocure.gov.in",
        listing_url="https://www.eprocure.gov.in/epublish/app",
        automated_access="unverified",
        access_note="Search and public page fetch are attempted only; access is never bypassed.",
    ),
    SourceDefinition(
        source_id="gem",
        name="GeM",
        base_url="https://gem.gov.in/",
        search_domain="gem.gov.in",
        listing_url="https://gem.gov.in/",
        automated_access="unverified",
        access_note="Search and public page fetch are attempted only; access is never bypassed.",
    ),
    SourceDefinition(
        source_id="delhi",
        name="Delhi eProcurement",
        base_url="https://govtprocurement.delhi.gov.in/",
        search_domain="govtprocurement.delhi.gov.in",
        listing_url="https://govtprocurement.delhi.gov.in/",
        automated_access="unverified",
        access_note="Search and public page fetch are attempted only; access is never bypassed.",
    ),
    SourceDefinition(
        source_id="ireps",
        name="Indian Railways IREPS",
        base_url="https://www.ireps.gov.in/",
        search_domain="ireps.gov.in",
        listing_url="https://www.ireps.gov.in/",
        automated_access="unverified",
        access_note="Search and public page fetch are attempted only; access is never bypassed.",
    ),
]
SOURCE_BY_ID = {source.source_id: source for source in SOURCES}

_AMOUNT_PATTERN = re.compile(
    r"(?:₹\s*|INR\s*|Rs\.?\s*)((?=[\d,]*\d)[\d,]+(?:\.\d+)?)\s*(crore|cr|lakh|lac|lakhs|lacs|million|thousand|k)?",
    re.IGNORECASE,
)
_DATE_PATTERNS = (
    re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b"),
    re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b"),
    re.compile(r"\b(\d{1,2})[-\s]([A-Za-z]{3,9})[-,\s]+(20\d{2})\b"),
)
_ID_PATTERN = re.compile(
    r"(?:^|\|)\s*(Tender\s+(?:ID|No\.?|Number)|NIT\s+(?:No\.?|Number)|"
    r"Bid\s+(?:No\.?|Number))\s*(?::|#|\|)?\s*([^\s|]+)",
    re.IGNORECASE | re.MULTILINE,
)


class SourceIntegrationError(RuntimeError):
    """Raised when a TinyFish API request fails or returns an invalid response."""


def search_source(source: SourceDefinition, api_key: str, limit: int = 5) -> list[dict[str, str]]:
    query = (
        f"site:{source.search_domain} tender notice procurement bid India "
        f"{date.today().year}"
    )
    try:
        response = requests.get(
            SEARCH_ENDPOINT,
            params={
                "query": query,
                "location": "IN",
                "language": "en",
                "purpose": f"Find publicly listed government tender notices on {source.name}",
            },
            headers={"X-API-Key": api_key},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise SourceIntegrationError(f"{source.name} search failed: {exc}") from exc

    if not isinstance(payload, dict):
        raise SourceIntegrationError(f"{source.name} search response must be a JSON object")
    rows = payload.get("results")
    if not isinstance(rows, list):
        raise SourceIntegrationError(f"{source.name} search response omitted results[]")
    results: list[dict[str, str]] = []
    source_host = urlparse(source.base_url).hostname or ""
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        url = row.get("url")
        title = row.get("title")
        if not isinstance(url, str) or not isinstance(title, str):
            continue
        host = (urlparse(url).hostname or "").lower()
        if host != source_host and not host.endswith(f".{source_host}"):
            continue
        results.append({
            "url": url,
            "title": title.strip(),
            "snippet": str(row.get("snippet") or "").strip(),
        })
    return results


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SourceIntegrationError("Only absolute HTTP(S) URLs can be fetched.")
    if parsed.username or parsed.password:
        raise SourceIntegrationError("URLs containing embedded credentials are not supported.")
    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise SourceIntegrationError("Local or internal hostnames cannot be fetched.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if not address.is_global:
        raise SourceIntegrationError("Private or reserved IP addresses cannot be fetched.")


def fetch_public_page(url: str, api_key: str) -> tuple[str, str, str]:
    """Return page title, extracted text, and resolved public URL."""
    _validate_public_url(url)
    try:
        response = requests.post(
            FETCH_ENDPOINT,
            json={"urls": [url], "format": "markdown", "purpose": "Read publicly accessible tender details"},
            headers={"X-API-Key": api_key},
            timeout=REQUEST_TIMEOUT_SECONDS + 10,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise SourceIntegrationError(f"TinyFish Fetch failed: {exc}") from exc

    if not isinstance(payload, dict):
        raise SourceIntegrationError("TinyFish Fetch response must be a JSON object")
    results = payload.get("results")
    if isinstance(results, list) and results:
        page = results[0]
        if isinstance(page, dict) and isinstance(page.get("text"), str):
            return (
                str(page.get("title") or ""),
                page["text"][:MAX_CONTENT_CHARS],
                str(page.get("final_url") or page.get("url") or url),
            )
    errors = payload.get("errors")
    detail = errors[0].get("error", "No page content returned") if isinstance(errors, list) and errors else "No page content returned"
    raise SourceIntegrationError(f"TinyFish Fetch could not access the page: {detail}")


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract text from an accessible PDF document with pypdf."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)[:MAX_CONTENT_CHARS]
    except (PdfReadError, PdfStreamError, ValueError, OSError) as exc:
        raise SourceIntegrationError(f"Could not parse the supplied PDF: {exc}") from exc


def fetch_pdf_if_accessible(url: str) -> str | None:
    """Read a directly accessible PDF only; access restrictions are not bypassed."""
    _validate_public_url(url)
    if not urlparse(url).path.lower().endswith(".pdf"):
        return None
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS, allow_redirects=True)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SourceIntegrationError(f"Public PDF request failed: {exc}") from exc
    if "application/pdf" not in response.headers.get("Content-Type", "").lower() and not response.content.startswith(b"%PDF"):
        return None
    try:
        return extract_pdf_text(response.content)
    except SourceIntegrationError:
        raise


def _amount_inr(match: re.Match[str]) -> float:
    raw_value = match.group(1).replace(",", "")
    normalized_value = "".join(
        str(unicodedata.decimal(character)) if character.isdecimal() else character
        for character in raw_value
    )
    value = float(normalized_value)
    unit = (match.group(2) or "").casefold()
    multiplier = {
        "crore": 10_000_000,
        "cr": 10_000_000,
        "lakh": 100_000,
        "lac": 100_000,
        "lakhs": 100_000,
        "lacs": 100_000,
        "million": 1_000_000,
        "thousand": 1_000,
        "k": 1_000,
    }.get(unit, 1)
    return value * multiplier


def _table_value(text: str, label: str) -> str:
    normalized = re.sub(r"\s+", " ", text)
    match = re.search(
        rf"(?:^|\|)\s*{re.escape(label)}\s*\|\s*([^|\n]+)",
        normalized,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip().replace("\\_", "_")
    lines = [line.strip().strip("|").strip() for line in text.splitlines()]
    for index, line in enumerate(lines[:-1]):
        if line.casefold() == label.casefold():
            value = lines[index + 1].strip().strip("|").strip()
            return value.replace("\\_", "_")
    return ""


def _amount_value(text: str) -> float | None:
    match = _AMOUNT_PATTERN.search(text)
    if match:
        return _amount_inr(match)
    match = re.search(
        r"(?<![A-Za-z])([\d,]+(?:\.\d+)?)\s*"
        r"(crore|cr|lakh|lac|lakhs|lacs|million|thousand|k)?\b",
        text,
        re.IGNORECASE,
    )
    return _amount_inr(match) if match else None


def _date_value(text: str) -> date | None:
    for pattern_index, pattern in enumerate(_DATE_PATTERNS):
        match = pattern.search(text)
        if not match:
            continue
        try:
            if pattern_index == 0:
                day, month, year = (int(part) for part in match.groups())
                return date(year, month, day)
            if pattern_index == 1:
                year, month, day = (int(part) for part in match.groups())
                return date(year, month, day)
            day, month, year = match.groups()
            try:
                parsed = datetime.strptime(f"{day} {month} {year}", "%d %b %Y")
            except ValueError:
                parsed = datetime.strptime(f"{day} {month} {year}", "%d %B %Y")
            return parsed.date()
        except (ValueError, OverflowError):
            continue
    return None


def _evidence(value: str, clause: str, url: str) -> EvidenceRequirement:
    return EvidenceRequirement(value=value, exact_clause=clause, mandatory=True, source_url=url)


def _clauses(text: str) -> list[str]:
    return [line.strip(" \t-*•") for line in re.split(r"[\n\r]+|(?<=[.;])\s+(?=[A-Z₹])", text) if line.strip()]


def extract_tender_fields(
    *,
    title: str,
    snippet: str,
    text: str,
    source_id: str,
    source_url: str,
    resolved_url: str | None = None,
    fetched: bool = False,
) -> Tender:
    """Extract only fields with matching source text; no missing values are inferred."""
    content = text[:MAX_CONTENT_CHARS]
    clauses = _clauses(content)
    all_text = f"{title}\n{snippet}\n{content}"

    id_candidates = list(_ID_PATTERN.finditer(all_text))
    id_candidates.sort(key=lambda match: 0 if match.group(1).casefold() == "tender id" else 1)
    tender_id = ""
    for candidate in id_candidates:
        value = candidate.group(2).replace("\\_", "_").rstrip(".,")
        if re.search(r"\d", value):
            tender_id = value
            break
    deadline_text = (
        _table_value(content, "Bid Submission End Date")
        or _table_value(content, "Submission Deadline")
        or _table_value(content, "Closing Date")
    )
    deadline: date | None = _date_value(deadline_text) if deadline_text else None
    for clause in clauses:
        if deadline:
            break
        if not re.search(r"deadline|closing date|submission date|bid submission", clause, re.IGNORECASE):
            continue
        deadline = _date_value(clause)
        if deadline:
            break

    requirements = TenderRequirements()
    estimated_value: float | None = None
    emd_amount: float | None = None
    turnover: float | None = None
    experience: float | None = None
    completed_projects: int | None = None
    location = ""
    certifications: list[str] = []
    documents: list[EvidenceRequirement] = []
    technical: list[EvidenceRequirement] = []
    department = (
        _table_value(content, "Organisation Chain")
        or _table_value(content, "Department")
        or _table_value(content, "Issuing Authority")
    )
    category = _table_value(content, "Tender Category") or _table_value(content, "Product Category")
    tender_title = _table_value(content, "Title") or title
    location_field = _table_value(content, "Location")
    corrigenda: list[str] = []

    for clause in clauses:
        lower = clause.casefold()
        labeled_value = re.match(
            r"^(?:issuing\s+authority|department|organisation|organization|department/organisation|"
            r"tender\s+category|category|nature\s+of\s+work)\s*[:\-]\s*(.+)$",
            clause,
            re.IGNORECASE,
        )
        if labeled_value:
            label = clause.split(labeled_value.group(1), 1)[0].casefold()
            if "category" in label or "nature of work" in label:
                category = category or labeled_value.group(1).strip()
            else:
                department = department or labeled_value.group(1).strip()
        if re.search(r"\b(?:corrigendum|corrigenda|amendment)\b", lower):
            corrigenda.append(clause)
        amount_match = _AMOUNT_PATTERN.search(clause)
        if amount_match:
            amount = _amount_inr(amount_match)
            if re.search(r"\b(?:emd|earnest money|bid security)\b", lower) and emd_amount is None:
                emd_amount = amount
                requirements.emd_inr = _evidence(f"₹{amount:,.0f}", clause, source_url)
            elif re.search(r"\b(?:annual|average|minimum|required)\b.*\bturnover\b|\bturnover\b.*\b(?:minimum|at least|required)\b", lower) and turnover is None:
                turnover = amount
                requirements.minimum_turnover_inr = _evidence(f"₹{amount:,.0f}", clause, source_url)
            elif re.search(r"estimated (?:cost|value)|tender value|contract value", lower) and estimated_value is None:
                estimated_value = amount
                requirements.estimated_value_inr = _evidence(f"₹{amount:,.0f}", clause, source_url)
            elif re.search(r"tender fee|document fee|processing fee", lower) and requirements.tender_fee_inr is None:
                requirements.tender_fee_inr = _evidence(f"₹{amount:,.0f}", clause, source_url)

        experience_match = re.search(
            r"(?:minimum|min\.?|at least|required)\s+(?:of\s+)?(\d+(?:\.\d+)?)\s+years?\s+(?:of\s+)?(?:similar\s+)?(?:work\s+)?experience",
            clause,
            re.IGNORECASE,
        )
        if experience_match and experience is None:
            experience = float(experience_match.group(1))
            requirements.minimum_experience_years = _evidence(f"{experience:g} years", clause, source_url)

        project_match = re.search(
            r"(?:minimum|min\.?|at least|required)\s+(\d+)\s+(?:similar\s+)?(?:completed\s+)?projects?",
            clause,
            re.IGNORECASE,
        )
        if project_match and completed_projects is None:
            completed_projects = int(project_match.group(1))
            requirements.minimum_completed_projects = _evidence(
                f"{completed_projects} completed projects", clause, source_url
            )

        if re.search(r"mandatory|required|shall submit|must submit", lower):
            if re.search(r"registration.{0,30}class|class.{0,30}registration", lower):
                requirements.registration_class = _evidence(clause, clause, source_url)
            elif re.search(r"certificate|certification|registration", lower):
                certifications.append(clause)
                requirements.required_certifications.append(_evidence(clause, clause, source_url))
            if re.search(r"\b(?:document|declaration|undertaking|certificate|proof)\b", lower):
                documents.append(_evidence(clause, clause, source_url))
            if re.search(r"technical|specification|standard|compliance", lower):
                technical.append(_evidence(clause, clause, source_url))

        if not location and re.search(r"work location|place of work|project location|work shall be at", lower):
            location = clause
        if (
            requirements.operating_location is None
            and re.search(r"\b(?:bidder|contractor|agency|firm)\b", lower)
            and re.search(r"(?:located|registered|operating|based)\s+(?:in|at|within)", lower)
        ):
            requirements.operating_location = _evidence(clause, clause, source_url)

    if requirements.estimated_value_inr is None:
        value_match = re.search(r"(?:estimated (?:cost|value)|tender value|contract value)[^\n.;]{0,120}", all_text, re.IGNORECASE)
        if value_match:
            amount_match = _AMOUNT_PATTERN.search(value_match.group(0))
            if amount_match:
                estimated_value = _amount_inr(amount_match)
                requirements.estimated_value_inr = _evidence(f"₹{estimated_value:,.0f}", value_match.group(0), source_url)

    for label, requirement_name in [
        ("EMD Amount in ₹", "emd"),
        ("Tender Fee in ₹", "fee"),
        ("Tender Value in ₹", "value"),
    ]:
        raw_value = _table_value(content, label)
        amount = _amount_value(raw_value) if raw_value else None
        if amount is None:
            continue
        evidence = _evidence(f"₹{amount:,.0f}", f"{label}: {raw_value}", source_url)
        if requirement_name == "emd":
            emd_amount = amount
            requirements.emd_inr = evidence
        elif requirement_name == "fee":
            requirements.tender_fee_inr = evidence
        else:
            estimated_value = amount
            requirements.estimated_value_inr = evidence

    work_description = _table_value(content, "Work Description")
    if work_description:
        snippet = work_description

    requirements.mandatory_documents = documents
    requirements.technical_requirements = technical
    source = SOURCE_BY_ID.get(source_id)
    record_status = "active" if fetched and deadline and deadline >= date.today() else "expired" if deadline and deadline < date.today() else "unverified"

    return Tender.model_validate({
        "tender_id": tender_id,
        "title": tender_title or source_url,
        "department": department,
        "category": category,
        "source_id": source_id,
        "source_name": source.name if source else "Manual submission",
        "source_url": source_url,
        "resolved_url": resolved_url or source_url,
        "location": location or location_field,
        "estimated_value_inr": estimated_value,
        "emd_inr": emd_amount,
        "deadline": deadline,
        "required_experience_years": experience,
        "minimum_turnover_inr": turnover,
        "mandatory_certifications": certifications,
        "summary": snippet.strip() or content[:600],
        "requirements": requirements,
        "evidence_text": content,
        "fetched_at": datetime.now().astimezone() if fetched else None,
        "corrigenda": list(dict.fromkeys(corrigenda)),
        "record_status": record_status,
    })


def validate_tender_payload(payload: dict[str, Any]) -> Tender:
    """Public Pydantic validation boundary for records imported outside adapters."""
    try:
        return Tender.model_validate(payload)
    except ValidationError:
        raise
