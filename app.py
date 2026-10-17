from __future__ import annotations

from datetime import date, timedelta
import os
from urllib.parse import urlparse

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError
from dotenv import load_dotenv
from pydantic import ValidationError

from tenderpilot.database import (
    initialize_database,
    list_saved_tenders,
    list_tenders,
    load_profile,
    save_profile,
    save_tender,
    upsert_tender,
)
from tenderpilot.matching import check_eligibility, score_tender
from tenderpilot.models import ContractorProfile, Tender
from tenderpilot.sources import (
    SOURCES,
    SourceIntegrationError,
    extract_tender_fields,
    fetch_public_page,
    search_source,
)

load_dotenv()
st.set_page_config(page_title="TenderPilot AI", page_icon="TP", layout="wide")
initialize_database()


def _api_key() -> str:
    try:
        configured = st.secrets.get("TINYFISH_API_KEY", "")
    except (AttributeError, FileNotFoundError, StreamlitSecretNotFoundError):
        configured = ""
    return str(configured or os.getenv("TINYFISH_API_KEY", "")).strip()


def _comma_items(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _format_inr(value: float | None) -> str:
    return f"₹{value:,.0f}" if value is not None else "Not stated"


def _deadline_text(tender: Tender) -> str:
    return tender.deadline.isoformat() if tender.deadline else "Not stated"


def _eligibility(tender: Tender) -> str:
    return check_eligibility(profile, tender).overall_status


def _render_tender_card(tender: Tender) -> None:
    score, reasons = score_tender(profile, tender)
    report = check_eligibility(profile, tender)
    with st.container(border=True):
        left, right = st.columns([4, 1])
        with left:
            st.subheader(tender.title)
            st.caption(f"{tender.department or 'Issuing department not extracted'} · {tender.source_name} · {tender.location or 'Location not stated'}")
        with right:
            st.metric("Relevance fit", f"{score}/100")
            st.caption(f"Eligibility: {report.overall_status}")
        st.write(f"**Deadline:** {_deadline_text(tender)} · **Estimated value:** {_format_inr(tender.estimated_value_inr)} · **EMD:** {_format_inr(tender.emd_inr)}")
        st.caption(f"Listing status: {tender.record_status.title()} · A fit score is not eligibility or a probability of winning.")
        st.write("**Why it matches:** " + " ".join(reasons))
        if tender.summary:
            st.write(tender.summary[:500])
        st.markdown(f"[Open original tender source]({tender.source_url})")
        if st.button("Open tender details", key=f"open-{tender.dedupe_key}"):
            st.session_state["selected_tender"] = tender.dedupe_key
            st.session_state["page"] = "Find Tenders"
            st.rerun()


def _filtered_tenders(rows: list[Tender]) -> list[Tender]:
    with st.expander("Filter tenders", expanded=True):
        location = st.text_input("Location contains", key="filter_location").strip().casefold()
        categories = sorted({t.category for t in rows if t.category})
        category = st.selectbox("Category", ["All"] + categories, key="filter_category")
        departments = sorted({t.department for t in rows if t.department})
        department = st.selectbox("Department", ["All"] + departments, key="filter_department")
        minimum_value, maximum_value = st.columns(2)
        with minimum_value:
            min_value = st.number_input("Minimum estimated value (₹)", min_value=0, value=0, step=100_000, key="filter_min_value")
        with maximum_value:
            max_value = st.number_input("Maximum estimated value (₹; 0 = no limit)", min_value=0, value=0, step=100_000, key="filter_max_value")
        deadline_filter = st.date_input("Closing on or before", value=None, key="filter_deadline")
    result = rows
    if location:
        result = [item for item in result if location in item.location.casefold()]
    if category != "All":
        result = [item for item in result if item.category == category]
    if department != "All":
        result = [item for item in result if item.department == department]
    value_filter_active = min_value > 0 or max_value > 0
    result = [
        item for item in result
        if (item.estimated_value_inr is None and not value_filter_active)
        or (item.estimated_value_inr >= min_value and (max_value == 0 or item.estimated_value_inr <= max_value))
    ]
    if isinstance(deadline_filter, date):
        result = [item for item in result if item.deadline is not None and item.deadline <= deadline_filter]
    return result


def _discover_tenders(api_key: str, limit: int) -> None:
    status = st.status("Searching configured source websites…", expanded=True)
    fetched_count = 0
    errors: list[str] = []
    for source in SOURCES:
        try:
            results = search_source(source, api_key, limit=limit)
        except SourceIntegrationError as exc:
            errors.append(str(exc))
            status.write(f"Unavailable: {source.name} — {exc}")
            continue
        status.write(f"{source.name}: {len(results)} public search result(s).")
        for result in results:
            try:
                fetched_title, page_text, resolved_url = fetch_public_page(result["url"], api_key)
                tender = extract_tender_fields(
                    title=fetched_title or result["title"],
                    snippet=result["snippet"],
                    text=page_text,
                    source_id=source.source_id,
                    source_url=result["url"],
                    resolved_url=resolved_url,
                    fetched=True,
                )
                fetched_count += 1
            except SourceIntegrationError as exc:
                errors.append(f"{source.name}: {result['url']} — {exc}")
                tender = extract_tender_fields(
                    title=result["title"],
                    snippet=result["snippet"],
                    text="",
                    source_id=source.source_id,
                    source_url=result["url"],
                    fetched=False,
                )
            upsert_tender(tender)
    status.update(label="Source search complete", state="complete", expanded=False)
    st.success(f"Stored or refreshed {fetched_count} fetched page(s); search-only results remain unverified.")
    if errors:
        with st.expander(f"{len(errors)} source/page access issue(s)"):
            for error in errors:
                st.warning(error)


def _manual_submission(api_key: str) -> None:
    st.subheader("Add a tender URL")
    with st.form("manual_tender_url"):
        url = st.text_input("Public tender page or PDF URL")
        uploaded_pdf = st.file_uploader("Optional tender PDF copy (extracted locally)", type=["pdf"])
        submit = st.form_submit_button("Fetch and add")
    if not submit:
        return
    if not url.strip():
        st.error("Enter the original public tender URL so the listing can link back to its source.")
        return
    if not api_key and uploaded_pdf is None:
        st.error("Set TINYFISH_API_KEY to fetch a public tender page. CAPTCHA and other access restrictions are not bypassed.")
        return
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            st.error("Enter an absolute public HTTP(S) URL.")
            return
        matched_sources = [
            source
            for source in SOURCES
            if parsed.hostname == urlparse(source.base_url).hostname
            or parsed.hostname.endswith("." + (urlparse(source.base_url).hostname or ""))
        ]
        matched_source = matched_sources[0] if matched_sources else None
        if uploaded_pdf is not None:
            from tenderpilot.sources import extract_pdf_text

            fetched_title = uploaded_pdf.name
            page_text = extract_pdf_text(uploaded_pdf.getvalue())
            resolved_url = url.strip()
            page_fetched = False
        else:
            fetched_title, page_text, resolved_url = fetch_public_page(url.strip(), api_key)
            page_fetched = True
        tender = extract_tender_fields(
            title=fetched_title or parsed.path.rsplit("/", 1)[-1] or parsed.hostname,
            snippet="",
            text=page_text,
            source_id=matched_source.source_id if matched_source else "manual",
            source_url=url.strip(),
            resolved_url=resolved_url,
            fetched=page_fetched,
        )
        upsert_tender(tender)
    except (ValueError, SourceIntegrationError) as exc:
        st.error(f"Could not add this tender: {exc}")
        return
    if uploaded_pdf is not None:
        st.success("Tender PDF extracted locally and stored as an unverified listing. Check its contents and source link.")
    else:
        st.success("Public page fetched and stored. Extracted conditions retain their source clauses; verify them against the official notice.")


def _details(tender: Tender) -> None:
    score, reasons = score_tender(profile, tender)
    report = check_eligibility(profile, tender)
    st.title(tender.title)
    st.caption(f"{tender.source_name} · {tender.department or 'Issuing department not extracted'} · Listing: {tender.record_status.title()}")
    st.markdown(f"[Open official source]({tender.source_url})")
    if tender.resolved_url and tender.resolved_url != tender.source_url:
        st.caption(f"Fetched page redirected to: {tender.resolved_url}")
    st.write(tender.summary or "No page summary was available from the public source.")
    columns = st.columns(4)
    columns[0].metric("Submission deadline", _deadline_text(tender))
    columns[1].metric("Estimated value", _format_inr(tender.estimated_value_inr))
    columns[2].metric("EMD", _format_inr(tender.emd_inr))
    columns[3].metric("Eligibility status", report.overall_status)
    st.subheader("Contractor match")
    st.metric("Relevance fit (not eligibility)", f"{score}/100")
    for reason in reasons:
        st.write(f"- {reason}")

    st.subheader("Eligibility check")
    st.caption("Statuses are evidence-based. Missing or ambiguous information is REVIEW; profile figures are self-reported.")
    st.dataframe(
        [
            {
                "Requirement": item.requirement,
                "Tender condition": item.tender_condition,
                "Contractor profile / next step": item.contractor_evidence,
                "Result": item.status,
                "Exact source clause": item.exact_clause or "No clause extracted",
                "Evidence source": item.source_url or tender.source_url,
            }
            for item in report.results
        ],
        width="stretch",
        hide_index=True,
    )
    with st.expander("Extracted tender conditions and source evidence", expanded=True):
        requirements = tender.requirements
        for label, item in [
            ("Minimum turnover", requirements.minimum_turnover_inr),
            ("Minimum experience", requirements.minimum_experience_years),
            ("Operating-location restriction", requirements.operating_location),
            ("EMD", requirements.emd_inr),
            ("Tender fee", requirements.tender_fee_inr),
            ("Estimated value", requirements.estimated_value_inr),
        ]:
            if item:
                st.markdown(f"**{label}:** {item.value}  \n> {item.exact_clause}")
        for label, items in [
            ("Required certifications", requirements.required_certifications),
            ("Technical conditions", requirements.technical_requirements),
            ("Mandatory documents", requirements.mandatory_documents),
        ]:
            if items:
                st.markdown(f"**{label}**")
                for item in items:
                    st.markdown(f"- {item.exact_clause}")
    if tender.evidence_text:
        with st.expander("Fetched source text"):
            st.text(tender.evidence_text[:30_000])
    if st.button("Unsave tender" if tender.saved else "Save tender", key=f"save-detail-{tender.dedupe_key}"):
        save_tender(tender.dedupe_key, not tender.saved)
        st.rerun()
    if tender.corrigenda:
        st.subheader("Recorded updates / corrigenda")
        for update in tender.corrigenda:
            st.write(f"- {update}")


profile = load_profile()
tenders = list_tenders()
page_names = [
    "Dashboard",
    "Find Tenders",
    "My Contractor Profile",
    "Matched Tenders",
    "Eligibility Checker",
    "Saved Tenders",
]
with st.sidebar:
    st.title("TenderPilot AI")
    st.caption("Tender discovery and evidence-based contractor matching")
    selected_page = st.radio(
        "Navigation",
        page_names,
        index=page_names.index(st.session_state.get("page", "Dashboard")),
        label_visibility="collapsed",
        key="page",
    )
    st.divider()
    st.caption(f"Contractor: {profile.company_name or 'Profile not set up'}")

if selected_page == "Dashboard":
    st.title("Tender dashboard")
    score_rows = [(item, score_tender(profile, item)[0]) for item in tenders]
    relevant = [item for item, score in score_rows if score >= 40]
    needs_review = [item for item in tenders if _eligibility(item) == "REVIEW"]
    upcoming = [
        item for item in tenders
        if item.record_status == "active" and item.deadline and date.today() <= item.deadline <= date.today() + timedelta(days=30)
    ]
    cards = st.columns(4)
    cards[0].metric("Collected tenders", len(tenders))
    cards[1].metric("Relevant matches", len(relevant))
    cards[2].metric("Eligibility review", len(needs_review))
    cards[3].metric("Verified deadlines (30 days)", len(upcoming))
    st.info("Tenders are sourced from public listings. Search-only records are unverified; check the official notice before acting.")
    if not tenders:
        st.write("No tender records have been added yet. Configure TinyFish and use Find Tenders, or submit an official tender URL.")
    for item in sorted(relevant or tenders, key=lambda tender: score_tender(profile, tender)[0], reverse=True)[:8]:
        _render_tender_card(item)

elif selected_page == "Find Tenders":
    st.title("Find tenders")
    api_key = _api_key()
    st.caption("Search can return only publicly indexed listings; each page is fetched separately. Unavailable listings can be added manually when accessible.")
    with st.expander("Configured source registry"):
        for source in SOURCES:
            st.markdown(f"- [{source.name}]({source.listing_url}) — automated access not pre-verified. {source.access_note}")
    if not api_key:
        st.warning("TinyFish is not configured. Add TINYFISH_API_KEY in the environment or Streamlit secrets to enable Search and Fetch.")
    else:
        per_source = st.number_input("Search results per source", min_value=1, max_value=10, value=3, step=1)
        if st.button("Search configured sources"):
            _discover_tenders(api_key, int(per_source))
            st.rerun()
    _manual_submission(api_key)
    st.subheader("Central tender listings")
    filtered = _filtered_tenders(tenders)
    if not filtered:
        st.write("No matching tender records. Run a source search or add an official URL.")
    for tender in filtered:
        _render_tender_card(tender)
    if tenders:
        st.subheader("Tender details")
        selected_key = st.selectbox(
            "Select a tender",
            [tender.dedupe_key for tender in tenders],
            format_func=lambda key: next((item.title for item in tenders if item.dedupe_key == key), key),
            index=next((index for index, item in enumerate(tenders) if item.dedupe_key == st.session_state.get("selected_tender")), 0),
        )
        selected_tender = next(item for item in tenders if item.dedupe_key == selected_key)
        _details(selected_tender)

elif selected_page == "My Contractor Profile":
    st.title("My contractor profile")
    st.caption("Profile information is stored in the local SQLite database. Turnover, experience and certifications are treated as self-reported.")
    with st.form("contractor_profile"):
        company_name = st.text_input("Contractor or company name", value=profile.company_name)
        business_category = st.text_input("Business category", value=profile.business_category)
        services = st.text_input("Services (comma-separated)", value=", ".join(profile.services))
        operating_locations = st.text_input("Operating locations (comma-separated)", value=", ".join(profile.operating_locations))
        left, right = st.columns(2)
        turnover = left.text_input("Annual turnover in ₹ (optional)", value=str(profile.annual_turnover_inr or ""))
        experience = right.text_input("Years of experience (optional)", value=str(profile.years_experience or ""))
        projects = left.text_input("Previous completed projects (optional)", value=str(profile.completed_projects or ""))
        emd_budget = right.text_input("Available EMD budget in ₹ (optional)", value=str(profile.emd_budget_inr or ""))
        certifications = st.text_input("Certifications and registrations (comma-separated)", value=", ".join(profile.certifications))
        certs_complete = st.checkbox("This is a complete list of my certifications", value=profile.certifications_complete)
        registration_class = st.text_input("Contractor registration class (optional)", value=profile.contractor_registration_class)
        submitted = st.form_submit_button("Save contractor profile")
    if submitted:
        try:
            def optional_number(value: str, label: str) -> float | None:
                if not value.strip():
                    return None
                number = float(value.replace(",", "").strip())
                if number < 0:
                    raise ValueError(f"{label} must be zero or greater.")
                return number

            profile = ContractorProfile(
                company_name=company_name,
                business_category=business_category,
                services=_comma_items(services),
                operating_locations=_comma_items(operating_locations),
                annual_turnover_inr=optional_number(turnover, "Annual turnover"),
                years_experience=optional_number(experience, "Years of experience"),
                completed_projects=optional_number(projects, "Completed projects"),
                certifications=_comma_items(certifications),
                certifications_complete=certs_complete,
                contractor_registration_class=registration_class,
                emd_budget_inr=optional_number(emd_budget, "EMD budget"),
            )
            save_profile(profile)
        except (ValueError, ValidationError) as exc:
            st.error(f"Profile was not saved: {exc}")
        else:
            st.success("Contractor profile saved.")
            st.rerun()

elif selected_page == "Matched Tenders":
    st.title("Matched tenders")
    matched = sorted(tenders, key=lambda tender: score_tender(profile, tender)[0], reverse=True)
    matched = [tender for tender in matched if score_tender(profile, tender)[0] >= 20]
    if not profile.company_name:
        st.info("Add a contractor profile to improve matching.")
    for tender in matched:
        _render_tender_card(tender)
    if not matched:
        st.write("No tender currently meets the basic relevance threshold. Search sources and complete your profile.")

elif selected_page == "Eligibility Checker":
    st.title("Eligibility checker")
    st.caption("Eligibility is evaluated independently from relevance. REVIEW means evidence is missing or requires manual confirmation.")
    if not tenders:
        st.write("Add a tender listing before checking eligibility.")
    else:
        key = st.selectbox(
            "Tender to check",
            [tender.dedupe_key for tender in tenders],
            format_func=lambda item_key: next(item.title for item in tenders if item.dedupe_key == item_key),
            key="eligibility_tender",
        )
        _details(next(item for item in tenders if item.dedupe_key == key))

elif selected_page == "Saved Tenders":
    st.title("Saved tenders")
    saved_tenders = list_saved_tenders()
    if not saved_tenders:
        st.write("No saved tenders yet. Save a tender from its card or details.")
    for tender in saved_tenders:
        _render_tender_card(tender)
