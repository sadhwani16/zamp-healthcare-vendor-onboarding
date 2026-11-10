"""Typed data exchanged by the vendor onboarding pipeline."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class VendorFormSubmission(BaseModel):
    company_name: str
    dba_name: str | None = None
    country: str = "US"
    physical_address: str
    city: str
    state: str
    zip_code: str
    npi_number: str
    tin_ein: str
    vendor_category: str
    tax_classification: str | None = None
    claimed_taxonomy_code: str
    requested_complexity: Literal["High-Complexity Lab", "Basic Waiver"]
    bank_routing_number: str
    bank_account_number: str
    contact_email: str
    authorized_official: str
    owners_5_percent: list[str] = Field(default_factory=list)
    prior_sanctions_disclosed: bool


class ForensicCheckResult(BaseModel):
    slot_name: str
    file_name: str
    is_scanned_pdf: bool
    ocr_engine_used: str | None = None
    ocr_confidence: float | None = Field(default=None, ge=0, le=1)
    warning_status: Literal["WARN_FIXABLE"] | None = None
    revision_count: int = Field(ge=0)
    has_hidden_revisions: bool
    revision_diffs: list[str] = Field(default_factory=list)
    producer_metadata: str
    suspicious_producer: bool
    font_anomalies: list[str] = Field(default_factory=list)
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "SCANNED_OCR"]


class DocClassificationResult(BaseModel):
    slot_name: str
    expected_doc_type: str
    detected_doc_type: str
    is_correct_doc_type: bool


class ExtractedW9(BaseModel):
    legal_name_line1: str | None = None
    dba_name_line2: str | None = None
    tax_classification: str | None = None
    state: str | None = None
    tin_ein: str | None = None
    is_signed: bool
    signature_name: str | None = None
    signature_date: date | None = None


class ExtractedCP575(BaseModel):
    notice_code: Literal["CP 575 A", "CP 575 G", "147C"] | None = None
    ein: str | None = None
    legal_name_line1: str | None = None
    dba_name_line2: str | None = None
    name_control: str | None = None
    notice_date: date | None = None
    state: str | None = None


class ExtractedBankLetter(BaseModel):
    evidence_type: Literal["bank_letter", "voided_check", "bank_statement", "bank_confirmation"] | None = None
    bank_name: str | None = None
    account_holder_name: str | None = None
    account_type: Literal["Commercial Checking", "Personal Savings"] | None = None
    routing_number: str | None = None
    account_number: str | None = None


class ExtractedCLIACertificate(BaseModel):
    clia_id: str | None = None
    facility_name: str | None = None
    state: str | None = None
    lab_director: str | None = None
    certificate_type: Literal["Accreditation", "Compliance", "Waiver"] | None = None
    effective_date: date | None = None
    expiration_date: date | None = None


class ExtractedMalpracticeCOI(BaseModel):
    insured_name: str | None = None
    medical_specialty: str | None = None
    specialty_code: str | None = None
    policy_number: str | None = None
    effective_date: date | None = None
    expiration_date: date | None = None
    each_claim_limit: int | None = Field(default=None, ge=0)
    aggregate_limit: int | None = Field(default=None, ge=0)


class VerificationCheck(BaseModel):
    stage_name: str
    check_name: str
    status: Literal["PASS", "WARN_FIXABLE", "FAIL_ELIGIBILITY", "FAIL_RISK"]
    expected_value: str | None = None
    observed_value: str | None = None
    evidence_source: str
    reasoning: str


class OnboardingRunResult(BaseModel):
    run_id: str
    timestamp: datetime
    vendor_name: str
    npi_number: str
    final_status: Literal[
        "APPROVED", "PENDING", "REJECTED: NOT ELIGIBLE", "REJECTED: RISK"
    ]
    cumulative_risk_score: int = Field(ge=0, le=100)
    decision_summary: str
    generated_communication: str
    internal_fraud_alert: str | None = None
    forensics: list[ForensicCheckResult] = Field(default_factory=list)
    classifications: list[DocClassificationResult] = Field(default_factory=list)
    checks: list[VerificationCheck] = Field(default_factory=list)
