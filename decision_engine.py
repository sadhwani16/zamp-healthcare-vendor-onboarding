"""External checks, cross-document rules, and hard-precedence adjudication."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from core import compare_names, normalize_business_name, validate_aba_routing, validate_npi_luhn
from credibility_and_decision import adjudicate_submission, screen_oig_exclusions


NPPES_URL = "https://npiregistry.cms.hhs.gov/api/"
NPI_CACHE = Path(__file__).resolve().parent / "data" / "npi_cache.json"
CLIA_STATES = dict(zip(
    (f"{number:02d}" for number in range(1, 54)),
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN "
    "MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA PR RI SC SD TN TX UT VT VI "
    "VA WA WV WI WY".split(),
))


def check(name: str, outcome: str, reason: str, *, stage: str = "Stage 3",
          expected: Any = None, actual: Any = None, source: str = "submission.json") -> dict:
    return {"stage": stage, "check_name": name, "outcome": outcome,
            "reasoning": reason, "expected": None if expected is None else str(expected),
            "actual": None if actual is None else str(actual), "evidence_source": source}


def _cache_read(npi: str) -> dict | None:
    try:
        entry = json.loads(NPI_CACHE.read_text(encoding="utf-8"))[npi]
        fetched = datetime.fromisoformat(entry["fetched_at"])
        if entry.get("source") == "CMS NPPES API" and fetched.tzinfo and \
           datetime.now(timezone.utc) - fetched <= timedelta(days=30):
            return entry["response"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _cache_write(npi: str, response: dict) -> None:
    try:
        cache = json.loads(NPI_CACHE.read_text(encoding="utf-8")) if NPI_CACHE.exists() else {}
        cache[npi] = {"source": "CMS NPPES API", "fetched_at": datetime.now(timezone.utc).isoformat(),
                      "response": response}
        NPI_CACHE.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=NPI_CACHE.parent,
                                             prefix=".nppes.", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(cache, handle, indent=2)
            os.replace(temporary, NPI_CACHE)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError):
        pass


def verify_nppes(npi: str, claimed_taxonomy: str) -> list[dict]:
    """Check a live NPPES response, using only a recent authentic API cache offline."""
    url = f"{NPPES_URL}?number={npi}&version=2.1"
    source = url
    try:
        response = requests.get(NPPES_URL, params={"number": npi, "version": "2.1"},
                                timeout=(4, 10))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "result_count" not in payload:
            raise ValueError("Malformed NPPES response")
        if not isinstance(payload.get("results", []), list):
            raise ValueError("Malformed NPPES results")
        count = int(payload["result_count"])
        if count < 0 or count != len(payload.get("results", [])):
            raise ValueError("Inconsistent NPPES result count")
        if count and not any(isinstance(item, dict) and
                             str(item.get("number")) == npi
                             for item in payload["results"]):
            raise ValueError("NPPES response omitted the requested NPI")
        _cache_write(npi, payload)
    except (requests.RequestException, ValueError) as exc:
        payload = _cache_read(npi)
        source = "CMS NPPES API 30-day cache"
        if payload is None:
            return [check("NPPES availability", "WARN_FIXABLE",
                          f"NPPES lookup is unavailable: {type(exc).__name__}: {exc}",
                          stage="Stage 4", source=url)]
    results = payload.get("results") or []
    if not isinstance(results, list):
        return [check("NPPES response", "WARN_FIXABLE", "Registry results were malformed.",
                      stage="Stage 4", source=source)]
    try:
        result_count = int(payload.get("result_count", 0))
    except (TypeError, ValueError):
        return [check("NPPES response", "WARN_FIXABLE", "Registry result count was malformed.",
                      stage="Stage 4", source=source)]
    record = next((item for item in results if isinstance(item, dict)
                   and str(item.get("number")) == npi), None)
    if result_count == 0:
        return [check("NPPES registration", "FAIL_ELIGIBILITY",
                      "NPI not found in the federal registry.", stage="Stage 4",
                      expected=npi, actual="not found", source=source)]
    if record is None:
        return [check("NPPES response", "WARN_FIXABLE",
                      "Registry response did not include the requested NPI.",
                      stage="Stage 4", expected=npi, actual="missing", source=source)]
    basic = record.get("basic") or {}
    if not isinstance(basic, dict):
        basic = {}
    status = str(basic.get("status") or "")
    entity = str(record.get("enumeration_type") or "")
    taxonomies = record.get("taxonomies") or []
    if not isinstance(taxonomies, list):
        taxonomies = []
    codes = [str(item.get("code")) for item in taxonomies
             if isinstance(item, dict) and item.get("code")]
    checks = [
        check("NPPES active status", "PASS" if status == "A" else "FAIL_ELIGIBILITY",
              "NPI active status checked.", stage="Stage 4", expected="A", actual=status, source=source),
        check("NPPES entity type", "PASS" if entity == "NPI-2" else "FAIL_ELIGIBILITY",
              "An organization must have an NPI-2 identifier.", stage="Stage 4",
              expected="NPI-2", actual=entity, source=source),
        check("NPPES taxonomy", "PASS" if claimed_taxonomy in codes else "FAIL_ELIGIBILITY",
              "Claimed service taxonomy checked against the registry.", stage="Stage 4",
              expected=claimed_taxonomy, actual=", ".join(codes), source=source),
    ]
    official = " ".join(str(basic.get(key) or "").strip() for key in
                        ("authorized_official_first_name", "authorized_official_last_name")).strip()
    if official:
        checks.append(check("NPPES authorized official", "PASS", "Official name available for exclusion screening.",
                            stage="Stage 4", actual=official, source=source))
    organization = str(basic.get("organization_name") or "")
    if organization:
        checks.append(check("NPPES organization name", "PASS", "Registry legal name recorded for comparison.",
                            stage="Stage 4", actual=organization, source=source))
    parent_legal = str(basic.get("parent_organization_legal_business_name") or "")
    if parent_legal:
        checks.append(check("NPPES parent legal name", "PASS",
                            "Parent legal business name recorded for subpart comparison.",
                            stage="Stage 4", actual=parent_legal, source=source))
    return checks


def screen_oig(names: list[str], oig_csv_path: str) -> list[dict]:
    """Pandas screening with an explicit check per person or business name."""
    return [{**item, "stage": "Stage 4", "expected": item.get("expected_value"),
             "actual": item.get("observed_value")}
            for item in screen_oig_exclusions(names, oig_csv_path)]


def adjudicate(checks: list[dict]) -> tuple[str, str, str]:
    """Risk > eligibility > fixable > approved, independent of any score."""
    return adjudicate_submission(checks)


def _name_check(label: str, observed: str | None, anchors: list[str | None], source: str) -> dict:
    if not observed:
        return check(label, "WARN_FIXABLE", "The name could not be read.",
                     expected=" / ".join(value for value in anchors if value), source=source)
    statuses = [compare_names(observed, anchor) for anchor in anchors if anchor]
    if "PASS" in statuses:
        outcome = "PASS"
    elif "WARN_FIXABLE" in statuses:
        outcome = "WARN_FIXABLE"
    else:
        outcome = "FAIL_RISK"
    return check(label, outcome, "Name compared with the application and tax documents.",
                 expected=" / ".join(value for value in anchors if value), actual=observed, source=source)


def _expiration(label: str, value: str | None, source: str) -> dict:
    try:
        expiry = date.fromisoformat(value or "")
    except ValueError:
        expiry = None
    return check(label, "PASS" if expiry and expiry > date.today() else "WARN_FIXABLE",
                 "Expiration must be later than today.", expected=f"> {date.today()}",
                 actual=value, source=source)


def _name_control_candidates(legal_name: str | None, dba_name: str | None) -> set[str]:
    """Conservative IRS-style candidates; a mismatch still needs human review."""
    candidates: set[str] = set()
    for name in (legal_name, dba_name):
        if not name:
            continue
        words = re.findall(r"[A-Z0-9&-]+", name.upper())
        if len(words) > 2 and words[0] == "THE":
            words = words[1:]
        significant = "".join(words)
        if significant:
            candidates.add(significant[:4])
    return candidates


def cross_document_checks(form: dict, documents: dict[str, dict]) -> list[dict]:
    """Apply the PRD's format, identity, entity, license, and coverage rules."""
    checks: list[dict] = []
    company = form.get("company_name") or ""
    dba = form.get("dba_name")
    ein = form.get("tin_ein") or ""
    npi = form.get("npi_number") or ""
    routing = form.get("bank_routing_number") or ""
    state = (form.get("state") or "").upper()
    complexity = form.get("requested_complexity") or ""
    fields = {slot: (documents.get(slot) or {}).get("extracted") for slot in
              ("w9", "cp575", "bank_letter", "clia_certificate", "coi_insurance")}
    w9, cp, bank, clia, coi = (fields[key] for key in fields)
    valid_ein = bool(re.fullmatch(r"\d{2}-\d{7}", ein)) and not ein.startswith("00-")
    checks.extend([
        check("US EIN format", "PASS" if valid_ein else "WARN_FIXABLE",
              "EIN must use XX-XXXXXXX and cannot start with 00.", expected="valid US EIN", actual=ein),
        check("NPI Luhn checksum", "PASS" if validate_npi_luhn(npi) else "WARN_FIXABLE",
              "NPI check digit validated.", expected="valid 10-digit NPI", actual=npi),
        check("ABA routing checksum", "PASS" if validate_aba_routing(routing) else "WARN_FIXABLE",
              "Routing checksum validated.", expected="valid nine-digit ABA", actual=routing),
    ])
    legal = (w9 or {}).get("legal_name_line1") or (cp or {}).get("legal_name_line1") or company
    anchors = [company, dba, legal]
    if w9:
        w9_name = w9.get("legal_name_line1")
        dba_link = bool(w9.get("dba_name_line2") and
                        compare_names(w9.get("dba_name_line2"), company) == "PASS")
        checks.append(_name_check("W-9 legal name", w9_name,
                                  [company, (cp or {}).get("legal_name_line1") if dba_link else None], "w9_form.pdf"))
        if dba_link and w9_name and checks[-1]["outcome"] == "FAIL_RISK":
            checks[-1]["outcome"] = "PASS"
            checks[-1]["reasoning"] = "W-9 Line 2 explicitly links the form DBA to its Line 1 legal name."
        checks.append(check("W-9 signature", "PASS" if w9.get("is_signed") else "WARN_FIXABLE",
                            "A signed W-9 is required.", expected="signed",
                            actual="signed" if w9.get("is_signed") else "unsigned", source="w9_form.pdf"))
        w9_ein = re.sub(r"\D", "", w9.get("tin_ein") or "")
        checks.append(check("W-9 EIN", "PASS" if w9_ein == re.sub(r"\D", "", ein) else
                            "FAIL_RISK" if w9_ein else "WARN_FIXABLE",
                            "W-9 EIN must match the application.", expected=ein,
                            actual=w9.get("tin_ein"), source="w9_form.pdf"))
        classification = w9.get("tax_classification")
        if classification and form.get("tax_classification"):
            corporate = "CORP" in str(form["tax_classification"]).upper()
            match = corporate == ("CORP" in classification.upper())
            checks.append(check("W-9 business type", "PASS" if match else "FAIL_ELIGIBILITY",
                                "Tax classification must match the application.",
                                expected=form["tax_classification"], actual=classification, source="w9_form.pdf"))
    if cp:
        evidence_label = "IRS 147C" if cp.get("notice_code") == "147C" else "CP-575"
        checks.append(_name_check(f"{evidence_label} legal name", cp.get("legal_name_line1"),
                                  [company, legal], "irs_cp575.pdf"))
        cp_ein = re.sub(r"\D", "", cp.get("ein") or "")
        checks.append(check(f"{evidence_label} EIN", "PASS" if cp_ein == re.sub(r"\D", "", ein) else
                            "FAIL_RISK" if cp_ein else "WARN_FIXABLE",
                            "IRS EIN evidence must match the application.", expected=ein,
                            actual=cp.get("ein"), source="irs_cp575.pdf"))
        if cp.get("notice_code") != "147C":
            control_candidates = _name_control_candidates(cp.get("legal_name_line1"),
                                                           cp.get("dba_name_line2"))
            control = (cp.get("name_control") or "").upper()
            checks.append(check("CP-575 name control", "PASS" if control in control_candidates else
                                "WARN_FIXABLE",
                                "Compare the IRS notice name control with the legal and trade names; "
                                "unusual IRS naming rules require review.",
                                expected=" / ".join(sorted(control_candidates)) or "readable name",
                                actual=control or None, source="irs_cp575.pdf"))
        notice = cp.get("notice_code")
        checks.append(check("IRS EIN evidence type" if notice == "147C" else "CP-575 notice code",
                            "PASS" if notice in {"CP 575 A", "CP 575 G", "147C"} else "WARN_FIXABLE",
                            "Recognized IRS EIN evidence; document type does not establish corporate tax status.",
                            expected="CP-575 or 147C", actual=notice,
                            source="irs_cp575.pdf"))
    if bank:
        checks.append(_name_check("Bank account holder", bank.get("account_holder_name"), anchors,
                                  "bank_eft_letter.pdf"))
        for label, expected, observed in (
            ("Bank routing number", routing, bank.get("routing_number")),
            ("Bank account number", form.get("bank_account_number"), bank.get("account_number")),
        ):
            checks.append(check(label, "PASS" if observed == expected else
                                "FAIL_RISK" if observed else "WARN_FIXABLE",
                                "Bank details must match the application.", expected=expected,
                                actual=observed, source="bank_eft_letter.pdf"))
        checks.append(check("Bank account type", "PASS" if bank.get("account_type") == "Commercial Checking" else
                            "FAIL_RISK" if bank.get("account_type") else "WARN_FIXABLE",
                            "Vendor EFT requires a commercial checking account.",
                            expected="Commercial Checking", actual=bank.get("account_type"), source="bank_eft_letter.pdf"))
    if clia:
        checks.append(_name_check("CLIA facility name", clia.get("facility_name"), anchors,
                                  "clia_certificate.pdf"))
        clia_id = clia.get("clia_id") or ""
        prefix_state = CLIA_STATES.get(clia_id[:2])
        checks.append(check("CLIA state prefix", "PASS" if prefix_state == state else
                            "FAIL_ELIGIBILITY" if prefix_state else "WARN_FIXABLE",
                            "CLIA state prefix must match the application.", expected=state,
                            actual=f"{clia_id[:2]}={prefix_state}" if prefix_state else clia_id,
                            source="clia_certificate.pdf"))
        cert = clia.get("certificate_type")
        scope_ok = cert and not (complexity == "High-Complexity Lab" and cert == "Waiver")
        checks.append(check("CLIA scope", "PASS" if scope_ok else
                            "FAIL_ELIGIBILITY" if cert else "WARN_FIXABLE",
                            "A waiver does not authorize high-complexity testing.",
                            expected=complexity, actual=cert, source="clia_certificate.pdf"))
        checks.append(_expiration("CLIA expiration", clia.get("expiration_date"), "clia_certificate.pdf"))
    if coi:
        checks.append(_name_check("COI insured name", coi.get("insured_name"), anchors,
                                  "malpractice_coi.pdf"))
        specialty = coi.get("medical_specialty") or ""
        if complexity == "High-Complexity Lab":
            lab = "LAB" in specialty.upper() or "PATHOLOGY" in specialty.upper()
            checks.append(check("COI lab specialty", "PASS" if lab else
                                "FAIL_ELIGIBILITY" if specialty else "WARN_FIXABLE",
                                "Insurance must cover clinical laboratory services.",
                                expected="Clinical Laboratory", actual=specialty, source="malpractice_coi.pdf"))
        for name, field, minimum in (("COI each-claim limit", "each_claim_limit", 1_000_000),
                                     ("COI aggregate limit", "aggregate_limit", 3_000_000)):
            value = coi.get(field)
            checks.append(check(name, "PASS" if value is not None and value >= minimum else
                                "FAIL_ELIGIBILITY" if value is not None else "WARN_FIXABLE",
                                "Coverage must meet the minimum.", expected=f"${minimum:,}",
                                actual=f"${value:,}" if value is not None else None,
                                source="malpractice_coi.pdf"))
        checks.append(_expiration("COI expiration", coi.get("expiration_date"), "malpractice_coi.pdf"))
    return checks
