from tenderpilot.matching import check_eligibility, score_tender
from tenderpilot.models import ContractorProfile, EvidenceRequirement, Tender, TenderRequirements


def tender_with(**kwargs) -> Tender:
    defaults = {
        "title": "Road construction and maintenance",
        "source_id": "cppp",
        "source_name": "CPPP",
        "source_url": "https://eprocure.gov.in/eprocure/app",
    }
    defaults.update(kwargs)
    return Tender.model_validate(defaults)


def test_fit_score_is_explainable_and_distinct_from_eligibility():
    profile = ContractorProfile(
        business_category="Road construction",
        services=["road resurfacing", "highway maintenance"],
        annual_turnover_inr=8_000_000,
    )
    tender = tender_with(
        summary="Road construction and highway maintenance services.",
        minimum_turnover_inr=5_000_000,
        requirements=TenderRequirements(
            minimum_turnover_inr=EvidenceRequirement(
                value="₹50 lakh",
                exact_clause="Minimum annual turnover shall be ₹50 lakh.",
                source_url="https://eprocure.gov.in/notice/1",
            ),
        ),
    )
    score, reasons = score_tender(profile, tender)
    report = check_eligibility(profile, tender)
    assert 0 <= score <= 100
    assert reasons
    assert report.overall_status == "PASS"
    assert "probability" not in " ".join(reasons).lower()


def test_absent_turnover_evidence_is_review():
    tender = tender_with(
        minimum_turnover_inr=500_000,
        requirements=TenderRequirements(
            minimum_turnover_inr=EvidenceRequirement(
                value="₹5 lakh",
                exact_clause="Minimum annual turnover of ₹5 lakh is required.",
                source_url="https://eprocure.gov.in/notice/2",
            ),
        ),
    )
    result = check_eligibility(ContractorProfile(), tender)
    assert result.overall_status == "REVIEW"
    assert result.results[0].exact_clause == "Minimum annual turnover of ₹5 lakh is required."


def test_below_threshold_fails_and_missing_certificate_stays_review():
    tender = tender_with(
        minimum_turnover_inr=1_000_000,
        mandatory_certifications=["ISO 9001 certification is mandatory."],
        requirements=TenderRequirements(
            minimum_turnover_inr=EvidenceRequirement(
                value="₹10 lakh",
                exact_clause="Minimum turnover shall be ₹10 lakh.",
                source_url="https://eprocure.gov.in/notice/3",
            ),
            required_certifications=[
                EvidenceRequirement(
                    value="ISO 9001 certification is mandatory.",
                    exact_clause="ISO 9001 certification is mandatory.",
                    source_url="https://eprocure.gov.in/notice/3",
                ),
            ],
        ),
    )
    result = check_eligibility(ContractorProfile(annual_turnover_inr=500_000), tender)
    assert [item.status for item in result.results] == ["FAIL", "REVIEW"]
    assert result.overall_status == "FAIL"


def test_explicitly_complete_certificate_list_can_fail():
    tender = tender_with(requirements=TenderRequirements(
        required_certifications=[
            EvidenceRequirement(
                value="ISO 9001",
                exact_clause="ISO 9001 certificate required.",
                source_url="https://eprocure.gov.in/notice/4",
            ),
        ],
    ))
    result = check_eligibility(ContractorProfile(certifications_complete=True), tender)
    assert result.results[0].status == "FAIL"


def test_partial_certification_name_does_not_create_false_pass():
    tender = tender_with(requirements=TenderRequirements(
        required_certifications=[
            EvidenceRequirement(
                value="A valid ISO 9001 certification is mandatory.",
                exact_clause="A valid ISO 9001 certification is mandatory.",
                source_url="https://eprocure.gov.in/notice/6",
            ),
        ],
    ))
    result = check_eligibility(ContractorProfile(certifications=["ISO"]), tender)
    assert result.results[0].status == "REVIEW"


def test_location_work_site_is_not_an_eligibility_restriction():
    from tenderpilot.sources import extract_tender_fields

    parsed = extract_tender_fields(
        title="Road work",
        snippet="",
        text="Work location: Delhi.",
        source_id="cppp",
        source_url="https://eprocure.gov.in/notice/5",
        fetched=True,
    )
    assert parsed.location == "Work location: Delhi."
    assert parsed.requirements.operating_location is None
