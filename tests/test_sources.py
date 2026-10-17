from datetime import date, timedelta

from tenderpilot.sources import _AMOUNT_PATTERN, _amount_inr, extract_tender_fields


def test_extracted_amounts_deadline_and_exact_evidence():
    deadline = date.today() + timedelta(days=15)
    text = (
        f"Tender ID: NIT-2026-1234\n"
        f"Submission deadline: {deadline.day:02d}/{deadline.month:02d}/{deadline.year}\n"
        "Minimum annual turnover shall be INR 50 lakh.\n"
        "EMD: ₹1,00,000.\n"
        "Minimum of 3 years of similar work experience is required.\n"
    )
    tender = extract_tender_fields(
        title="Road construction tender",
        snippet="",
        text=text,
        source_id="cppp",
        source_url="https://eprocure.gov.in/notice",
        fetched=True,
    )
    assert tender.tender_id == "NIT-2026-1234"
    assert tender.deadline == deadline
    assert tender.minimum_turnover_inr == 5_000_000
    assert tender.emd_inr == 100_000
    assert tender.required_experience_years == 3
    assert tender.requirements.minimum_turnover_inr.exact_clause == "Minimum annual turnover shall be INR 50 lakh."
    assert tender.record_status == "active"


def test_search_only_listing_is_unverified():
    tender = extract_tender_fields(
        title="Tender search result",
        snippet="",
        text="",
        source_id="cppp",
        source_url="https://eprocure.gov.in/notice",
        fetched=False,
    )
    assert tender.record_status == "unverified"
    assert tender.deadline is None


def test_amount_extraction_ignores_comma_only_and_normalizes_unicode_digits():
    assert _AMOUNT_PATTERN.search("EMD: ₹,, lakh") is None
    match = _AMOUNT_PATTERN.search("EMD: ₹١٠٠٠")
    assert match is not None
    assert _amount_inr(match) == 1000


def test_indian_eprocurement_table_extracts_id_dates_and_amounts():
    text = (
        "| Organisation Chain | Directorate of Health Services | Tender Reference Number |\n"
        "| Tender ID | 2019_DHS_178501_1 | Withdrawal Allowed |\n"
        "| Tender Fee in ₹ | 0.00 | Fee Payable To |\n"
        "| EMD Amount in ₹ | 12,00,000 | EMD Exemption Allowed |\n"
        "| Tender Value in ₹ | 6,00,00,000 | Product Category |\n"
        "| Bid Submission End Date | 10-Oct-2019 02:00 PM | Tender Documents |\n"
        "| Tender Category | Works |\n"
        "| Location | Delhi |\n"
    )
    tender = extract_tender_fields(
        title="eTendering System Government of NCT of Delhi",
        snippet="",
        text=text,
        source_id="delhi",
        source_url="https://govtprocurement.delhi.gov.in/nicgep/app",
        fetched=True,
    )
    assert tender.tender_id == "2019_DHS_178501_1"
    assert tender.department == "Directorate of Health Services"
    assert tender.location == "Delhi"
    assert tender.category == "Works"
    assert tender.deadline == date(2019, 10, 10)
    assert tender.record_status == "expired"
    assert tender.emd_inr == 1_200_000
    assert tender.estimated_value_inr == 60_000_000
    assert tender.requirements.tender_fee_inr.value == "₹0"


def test_id_parser_ignores_unlabeled_nit_words_and_keeps_markdown_underscores():
    tender = extract_tender_fields(
        title="eTendering System",
        snippet="",
        text="NIT terms and conditions.\n| Tender ID | 2026\\_DHS\\_178501\\_1 | Withdrawal Allowed |",
        source_id="delhi",
        source_url="https://govtprocurement.delhi.gov.in/nicgep/app",
        fetched=True,
    )
    assert tender.tender_id == "2026_DHS_178501_1"


def test_labeled_closing_date_takes_precedence_over_published_date():
    text = (
        "NIT No. 07 (2025-26)\n"
        "Tender ID\n"
        "2025_DJB_276503_8\n"
        "Published Date\n"
        "06-Aug-2025 06:55 PM\n"
        "Bid Submission End Date\n"
        "12-Aug-2025 01:15 PM\n"
        "EMD Amount in ₹\n"
        "30,000\n"
        "Tender Value in ₹\n"
        "NA\n"
    )
    tender = extract_tender_fields(
        title="eTendering System Government of NCT of Delhi",
        snippet="",
        text=text,
        source_id="delhi",
        source_url="https://govtprocurement.delhi.gov.in/nicgep/app",
        fetched=True,
    )
    assert tender.tender_id == "2025_DJB_276503_8"
    assert tender.deadline == date(2025, 8, 12)
    assert tender.emd_inr == 30_000
    assert tender.estimated_value_inr is None
