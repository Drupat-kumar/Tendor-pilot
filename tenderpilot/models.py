from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContractorProfile(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    company_name: str = ""
    business_category: str = ""
    services: list[str] = Field(default_factory=list)
    operating_locations: list[str] = Field(default_factory=list)
    annual_turnover_inr: float | None = Field(default=None, ge=0)
    years_experience: float | None = Field(default=None, ge=0)
    completed_projects: int | None = Field(default=None, ge=0)
    certifications: list[str] = Field(default_factory=list)
    certifications_complete: bool = False
    contractor_registration_class: str = ""
    emd_budget_inr: float | None = Field(default=None, ge=0)


class EvidenceRequirement(BaseModel):
    value: str
    exact_clause: str
    mandatory: bool = True
    source_url: str


class TenderRequirements(BaseModel):
    minimum_turnover_inr: EvidenceRequirement | None = None
    minimum_experience_years: EvidenceRequirement | None = None
    minimum_completed_projects: EvidenceRequirement | None = None
    required_certifications: list[EvidenceRequirement] = Field(default_factory=list)
    registration_class: EvidenceRequirement | None = None
    operating_location: EvidenceRequirement | None = None
    emd_inr: EvidenceRequirement | None = None
    estimated_value_inr: EvidenceRequirement | None = None
    tender_fee_inr: EvidenceRequirement | None = None
    technical_requirements: list[EvidenceRequirement] = Field(default_factory=list)
    mandatory_documents: list[EvidenceRequirement] = Field(default_factory=list)


class Tender(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    tender_id: str = ""
    title: str
    department: str = ""
    source_id: str
    source_name: str
    source_url: str
    resolved_url: str = ""
    location: str = ""
    category: str = ""
    estimated_value_inr: float | None = Field(default=None, ge=0)
    emd_inr: float | None = Field(default=None, ge=0)
    deadline: date | None = None
    required_experience_years: float | None = Field(default=None, ge=0)
    minimum_turnover_inr: float | None = Field(default=None, ge=0)
    mandatory_certifications: list[str] = Field(default_factory=list)
    summary: str = ""
    requirements: TenderRequirements = Field(default_factory=TenderRequirements)
    evidence_text: str = ""
    fetched_at: datetime | None = None
    corrigenda: list[str] = Field(default_factory=list)
    record_status: Literal["active", "expired", "unverified"] = "unverified"
    saved: bool = False

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Tender source URL must be an absolute HTTP(S) URL")
        return value

    @property
    def dedupe_key(self) -> str:
        tender_id = self.tender_id.casefold().strip()
        return f"{self.source_id}:{tender_id}" if tender_id else f"{self.source_id}:{self.source_url.casefold()}"


class SourceDefinition(BaseModel):
    source_id: str
    name: str
    base_url: str
    search_domain: str
    listing_url: str
    automated_access: Literal["available", "unverified"]
    access_note: str


class RequirementResult(BaseModel):
    requirement: str
    tender_condition: str
    contractor_evidence: str
    status: Literal["PASS", "FAIL", "REVIEW"]
    exact_clause: str = ""
    source_url: str = ""


class EligibilityReport(BaseModel):
    results: list[RequirementResult] = Field(default_factory=list)

    @property
    def overall_status(self) -> Literal["PASS", "FAIL", "REVIEW"]:
        if any(result.status == "FAIL" for result in self.results):
            return "FAIL"
        if any(result.status == "REVIEW" for result in self.results):
            return "REVIEW"
        return "PASS" if self.results else "REVIEW"
