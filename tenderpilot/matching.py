from __future__ import annotations

import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from tenderpilot.models import (
    ContractorProfile,
    EligibilityReport,
    RequirementResult,
    Tender,
)

_CERTIFICATION_STOPWORDS = {
    "a", "an", "and", "along", "certificate", "certification", "copy", "contractor",
    "for", "is", "mandatory", "must", "of", "possess", "provided", "required",
    "shall", "submit", "the", "valid", "with",
}


def _similarity(left: str, right: str) -> float:
    if not left.strip() or not right.strip():
        return 0.0
    try:
        matrix = TfidfVectorizer(stop_words="english").fit_transform([left, right])
    except ValueError:
        return 0.0
    return float(cosine_similarity(matrix[0:1], matrix[1:2])[0][0])


def _contains_location(condition: str, locations: list[str]) -> bool:
    normalized = condition.casefold()
    return any(location.casefold() in normalized for location in locations if location.strip())


def _certification_tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9]+", value)
        if token.casefold() not in _CERTIFICATION_STOPWORDS
    }


def score_tender(profile: ContractorProfile, tender: Tender) -> tuple[int, list[str]]:
    """Return an explainable fit score; it is not eligibility or win probability."""
    service_text = " ".join([profile.business_category, *profile.services]).strip()
    tender_text = " ".join([tender.title, tender.category, tender.summary]).strip()
    similarity = _similarity(service_text, tender_text)

    score = round(similarity * 35)
    reasons: list[str] = []
    if similarity >= 0.12:
        reasons.append(f"Service description similarity contributes {round(similarity * 35)}/35 points.")

    if profile.business_category and tender.category:
        category_match = profile.business_category.casefold() in tender.category.casefold() or tender.category.casefold() in profile.business_category.casefold()
        if category_match:
            score += 20
            reasons.append("Business category matches the extracted tender category (+20).")

    if profile.operating_locations and tender.location:
        if _contains_location(tender.location, profile.operating_locations):
            score += 15
            reasons.append("An operating location matches the stated tender location (+15).")
        else:
            reasons.append("No operating location matches the stated tender location.")

    if tender.minimum_turnover_inr is not None and profile.annual_turnover_inr is not None:
        if profile.annual_turnover_inr >= tender.minimum_turnover_inr:
            score += 10
            reasons.append("Reported turnover meets the extracted minimum (+10).")
        else:
            reasons.append("Reported turnover is below the extracted minimum.")

    if tender.required_experience_years is not None and profile.years_experience is not None:
        if profile.years_experience >= tender.required_experience_years:
            score += 10
            reasons.append("Reported experience meets the extracted minimum (+10).")
        else:
            reasons.append("Reported experience is below the extracted minimum.")

    if tender.estimated_value_inr is not None and profile.annual_turnover_inr is not None:
        if tender.estimated_value_inr <= profile.annual_turnover_inr * 5:
            score += 10
            reasons.append("Estimated value is within five times reported annual turnover (+10).")
        else:
            reasons.append("Estimated value is more than five times reported annual turnover.")
    elif tender.estimated_value_inr is None or profile.annual_turnover_inr is None:
        reasons.append("Contract value compatibility could not be assessed from available figures.")

    if not reasons:
        reasons.append("Insufficient profile or tender information for a strong match explanation.")
    return min(100, max(0, score)), reasons


def check_eligibility(profile: ContractorProfile, tender: Tender) -> EligibilityReport:
    """Evaluate extracted mandatory requirements; absent evidence always means REVIEW."""
    results: list[RequirementResult] = []

    def add_result(
        requirement: str,
        condition: str,
        profile_evidence: str,
        status: str,
        exact_clause: str = "",
        source_url: str = "",
    ) -> None:
        results.append(RequirementResult(
            requirement=requirement,
            tender_condition=condition,
            contractor_evidence=profile_evidence,
            status=status,  # type: ignore[arg-type]
            exact_clause=exact_clause,
            source_url=source_url,
        ))

    turnover = tender.requirements.minimum_turnover_inr
    if turnover:
        if profile.annual_turnover_inr is None:
            status, evidence = "REVIEW", "Annual turnover not provided."
        elif tender.minimum_turnover_inr is None:
            status, evidence = "REVIEW", "Tender clause was captured, but a numeric turnover threshold could not be normalized."
        elif profile.annual_turnover_inr >= tender.minimum_turnover_inr:
            status, evidence = "PASS", f"Self-reported annual turnover ₹{profile.annual_turnover_inr:,.0f}."
        else:
            status, evidence = "FAIL", f"Self-reported annual turnover ₹{profile.annual_turnover_inr:,.0f} is below the stated requirement."
        add_result("Minimum turnover", turnover.value, evidence, status, turnover.exact_clause, turnover.source_url)

    experience = tender.requirements.minimum_experience_years
    if experience:
        if profile.years_experience is None:
            status, evidence = "REVIEW", "Years of experience not provided."
        elif tender.required_experience_years is None:
            status, evidence = "REVIEW", "Tender clause was captured, but a numeric experience threshold could not be normalized."
        elif profile.years_experience >= tender.required_experience_years:
            status, evidence = "PASS", f"Self-reported experience: {profile.years_experience:g} years."
        else:
            status, evidence = "FAIL", f"Self-reported experience: {profile.years_experience:g} years."
        add_result("Minimum experience", experience.value, evidence, status, experience.exact_clause, experience.source_url)

    projects = tender.requirements.minimum_completed_projects
    if projects:
        if profile.completed_projects is None:
            status, evidence = "REVIEW", "Completed project count not provided."
        else:
            project_count = re.search(r"\d+", projects.value)
            if project_count and profile.completed_projects >= int(project_count.group()):
                status, evidence = "PASS", f"Self-reported completed projects: {profile.completed_projects}."
            elif project_count:
                status, evidence = "FAIL", f"Self-reported completed projects: {profile.completed_projects}."
            else:
                status, evidence = "REVIEW", "Tender clause was captured, but its project-count threshold could not be normalized."
        add_result("Completed projects", projects.value, evidence, status, projects.exact_clause, projects.source_url)

    registration = tender.requirements.registration_class
    if registration:
        if not profile.contractor_registration_class:
            status, evidence = "REVIEW", "Contractor registration class not provided."
        elif re.search(rf"\b{re.escape(profile.contractor_registration_class)}\b", registration.value, re.IGNORECASE):
            status, evidence = "PASS", f"Listed registration class: {profile.contractor_registration_class}."
        else:
            status, evidence = "REVIEW", "Registration class differs from the clause; equivalence or eligibility requires manual verification."
        add_result("Contractor registration class", registration.value, evidence, status, registration.exact_clause, registration.source_url)

    for certificate in tender.requirements.required_certifications:
        required_tokens = _certification_tokens(certificate.value)
        matched = next(
            (
                item
                for item in profile.certifications
                if required_tokens
                and required_tokens.issubset(_certification_tokens(item))
            ),
            None,
        )
        if matched:
            status, evidence = "PASS", f"Listed in contractor profile: {matched}."
        elif profile.certifications_complete:
            status, evidence = "FAIL", "Contractor confirmed the certification list is complete; required certification is absent."
        else:
            status, evidence = "REVIEW", "Certification not listed; absence has not been confirmed."
        add_result("Required certification", certificate.value, evidence, status, certificate.exact_clause, certificate.source_url)

    location = tender.requirements.operating_location
    if location:
        if not profile.operating_locations:
            status, evidence = "REVIEW", "Operating locations not provided."
        elif _contains_location(location.value, profile.operating_locations):
            status, evidence = "PASS", "A listed operating location matches the tender clause."
        else:
            status, evidence = "FAIL", "No listed operating location matches the stated tender condition."
        add_result("Operating location", location.value, evidence, status, location.exact_clause, location.source_url)

    emd = tender.requirements.emd_inr
    if emd:
        if profile.emd_budget_inr is None:
            status, evidence = "REVIEW", "Available EMD budget not provided."
        elif tender.emd_inr is None:
            status, evidence = "REVIEW", "EMD clause was captured, but a numeric amount could not be normalized."
        elif profile.emd_budget_inr >= tender.emd_inr:
            status, evidence = "PASS", f"Reported available EMD budget ₹{profile.emd_budget_inr:,.0f}."
        else:
            status, evidence = "FAIL", f"Reported available EMD budget ₹{profile.emd_budget_inr:,.0f} is below the EMD."
        add_result("EMD budget", emd.value, evidence, status, emd.exact_clause, emd.source_url)

    fee = tender.requirements.tender_fee_inr
    if fee:
        add_result(
            "Tender fee",
            fee.value,
            "Payment readiness is not recorded in the contractor profile; verify payment amount and conditions.",
            "REVIEW",
            fee.exact_clause,
            fee.source_url,
        )

    for technical in tender.requirements.technical_requirements:
        add_result(
            "Technical requirement",
            technical.value,
            "Technical capability evidence is not recorded in the contractor profile; verify against the notice.",
            "REVIEW",
            technical.exact_clause,
            technical.source_url,
        )

    for document in tender.requirements.mandatory_documents:
        add_result(
            "Mandatory document",
            document.value,
            "Document readiness is not recorded in the contractor profile; prepare and verify this document.",
            "REVIEW",
            document.exact_clause,
            document.source_url,
        )

    if not results:
        add_result(
            "Tender requirements",
            "No verified mandatory requirements were extracted.",
            "The source has no extracted eligibility evidence; review the official notice.",
            "REVIEW",
            "",
            tender.source_url,
        )
    return EligibilityReport(results=results)
