"""Structured field parsers for the five healthcare vendor document types."""

from __future__ import annotations

import re
from datetime import date, datetime

from models import (
    ExtractedBankLetter,
    ExtractedCLIACertificate,
    ExtractedCP575,
    ExtractedMalpracticeCOI,
    ExtractedW9,
)

def _field(text: str, *labels: str) -> str | None:
    for label in labels:
        pattern = rf"(?im)^[ \t]*(?:{label})[ \t]*(?:[:#]|[-–][ \t]+|[ \t]{{2,}})[ \t]*(.+?)[ \t]*$"
        match = re.search(pattern, text)
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return None


def _field_or_next_line(text: str, *labels: str) -> str | None:
    value = _field(text, *labels)
    if value:
        return value
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines[:-1]):
        if any(re.fullmatch(rf"(?:{label})\s*:?(?:\s*)", line, re.I) for label in labels):
            next_line = lines[index + 1]
            if ":" not in next_line:
                return next_line
    return None


def _date(value: str | None) -> date | None:
    if not value:
        return None
    match = re.search(
        r"\b(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|"
        r"[A-Za-z]+\s+\d{1,2},?\s+\d{4})\b", value
    )
    if not match:
        return None
    token = match.group().replace(".", "/")
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(token, fmt).date()
        except ValueError:
            pass
    return None


def _ein(text: str) -> str | None:
    match = re.search(r"\b\d{2}-?\d{7}\b", text)
    return match.group() if match else None


def _money(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"\$?\s*([\d,]+)", value)
    return int(match.group(1).replace(",", "")) if match else None


def parse_w9(text: str) -> ExtractedW9:
    """Parse labeled W-9 fields; unavailable values remain None."""
    # A filled official W-9 often puts overlay values at the end of PyMuPDF's
    # text stream, after the printed form. Handle that layout explicitly.
    if "Form W-9 (Rev. 3-2024)" in text:
        overlay = text.rsplit("Form W-9 (Rev. 3-2024)", 1)[-1]
        lines = [line.strip() for line in overlay.splitlines()
                 if line.strip() and line.strip().casefold() != "instance"
                 and not line.startswith("SAMPLE  |")]
        if lines:
            date_index = next((i for i, line in enumerate(lines) if _date(line)), None)
            signature = lines[date_index - 1] if date_index is not None and date_index > 0 else None
            location = next((line for line in lines if re.search(r",\s*[A-Z]{2}\s+\d{5}", line)), "")
            state_match = re.search(r",\s*([A-Z]{2})\s+\d{5}", location)
            return ExtractedW9(
                legal_name_line1=lines[0],
                dba_name_line2=lines[1] if len(lines) > 1 and lines[1] != "X" else None,
                tax_classification="C Corporation" if "X" in lines else None,
                state=state_match.group(1) if state_match else None,
                # The official form prints each EIN digit in its own box.
                # The PDF-aware reader supplies the value from those boxes.
                tin_ein=None,
                is_signed=bool(signature),
                signature_name=signature,
                signature_date=_date(lines[date_index]) if date_index is not None else None,
            )
    signature = _field(text, r"Signature(?:\s+Name)?", r"Signed\s+By")
    signed_field = _field(text, r"Is\s+Signed", r"Signed")
    is_signed = bool(signature and not re.fullmatch(r"[_\-\s]+|unsigned|n/?a", signature, re.I))
    if signed_field and signed_field.lower() in {"no", "false", "unsigned"}:
        is_signed = False
    elif signed_field and signed_field.lower() in {"yes", "true"}:
        is_signed = True
    return ExtractedW9(
        legal_name_line1=_field(text, r"(?:Line\s*1\s*[-–:]?\s*)?(?:Legal\s+Name|Name)", r"Line\s*1"),
        dba_name_line2=_field(text, r"(?:Line\s*2\s*[-–:]?\s*)?(?:DBA|Business\s+Name)", r"Line\s*2"),
        tax_classification=_field(text, r"(?:Federal\s+)?Tax\s+Classification", r"Business\s+Type"),
        state=_field(text, r"State"),
        tin_ein=_field(text, r"(?:TIN|EIN|Tax\s+ID)") or _ein(text),
        is_signed=is_signed,
        signature_name=signature if is_signed else None,
        signature_date=_date(_field(text, r"Signature\s+Date", r"Date")),
    )


def parse_cp575(text: str) -> ExtractedCP575:
    """Parse CP-575 or an IRS 147C EIN verification letter."""
    notice_match = re.search(r"\bCP\s*575\s*[AG]\b|\b147\s*C\b", text, re.I)
    notice_code = re.sub(r"\s+", " ", notice_match.group().upper()) if notice_match else None
    if notice_code and notice_code.replace(" ", "") == "147C":
        notice_code = "147C"
    if "Number of this notice:" in text and "Name Control:" in text:
        tail = text.rsplit("Employer Identification Number:", 1)[-1]
        lines = [line.strip() for line in tail.splitlines() if line.strip()]
        if len(lines) >= 3 and re.fullmatch(r"\d{2}-\d{7}", lines[0]):
            dba = lines[2] if not re.match(r"^\d", lines[2]) else None
            location = next((line for line in lines[:6]
                             if re.search(r",\s*[A-Z]{2}\s+\d{5}", line)), "")
            state_match = re.search(r",\s*([A-Z]{2})\s+\d{5}", location)
            date_match = re.search(r"\b\d{2}/\d{2}/\d{4}\b", text)
            control_match = re.search(r"(?im)^Name Control:\s*([A-Z0-9]{4})\b", text)
            return ExtractedCP575(
                notice_code=notice_code,
                ein=lines[0],
                legal_name_line1=lines[1],
                dba_name_line2=dba,
                name_control=control_match.group(1) if control_match else None,
                notice_date=_date(date_match.group() if date_match else None),
                state=state_match.group(1) if state_match else None,
            )
    return ExtractedCP575(
        notice_code=notice_code,
        ein=_field_or_next_line(text, r"(?:Employer\s+Identification\s+Number|EIN|Tax\s+ID)") or _ein(text),
        legal_name_line1=_field(text, r"(?:Legal\s+Name|Line\s*1|Name)"),
        dba_name_line2=_field(text, r"(?:DBA|Business\s+Name|Line\s*2)"),
        name_control=_field(text, r"(?:Name\s+Control(?:\s+Code)?|4[- ]Letter\s+Code)"),
        notice_date=_date(_field(text, r"(?:Notice\s+Date|Date)")),
        state=_field(text, r"State"),
    )


def parse_bank_letter(text: str) -> ExtractedBankLetter:
    """Parse common bank evidence layouts without inventing account digits."""
    upper = text.upper()
    evidence_type = ("voided_check" if "VOID" in upper and "CHECK" in upper else
                     "bank_statement" if "BANK STATEMENT" in upper or "ACCOUNT STATEMENT" in upper else
                     "bank_confirmation" if "ACCOUNT CONFIRMATION" in upper or "DIRECT DEPOSIT" in upper else
                     "bank_letter" if "BANK" in upper and "ACCOUNT" in upper else None)
    account_type = _field(text, r"Account\s+Type")
    if not account_type:
        account_type = "Commercial Checking" if re.search(r"\b(?:BUSINESS|COMMERCIAL)\s+CHECKING\b", text, re.I) else None
    if account_type:
        account_type = next(
            (kind for kind in ("Commercial Checking", "Personal Savings")
             if kind.lower() in account_type.lower()),
            "Commercial Checking" if re.search(r"\b(?:BUSINESS|COMMERCIAL)\s+CHECKING\b", account_type, re.I)
            else None,
        )
    account = _field(text, r"(?:Account\s+Number|Checking\s+Account|Account\s*#)")
    if account and (re.search(r"[xX*•]", account) or re.search(r"\b(?:ending|last\s+four|masked)\b", account, re.I)):
        account = None
    header_bank = next((line.strip() for line in text.splitlines()[:8]
                        if re.fullmatch(r"[\w &,.'-]{3,80}\b(?:Bank|Credit Union)\b", line.strip(), re.I)
                        and not re.search(r"\b(?:statement|account|routing|number)\b", line, re.I)), None)
    return ExtractedBankLetter(
        evidence_type=evidence_type,
        bank_name=_field(text, r"Bank\s+Name", r"Financial\s+Institution") or header_bank,
        account_holder_name=_field(text, r"(?:Account\s+Holder(?:\s+Name)?|Account\s+Name|Account\s+Owner|Payee|Customer\s+Name)"),
        account_type=account_type,
        routing_number=_field(text, r"(?:Routing(?:\s+Number)?|ABA(?:\s+Number)?|Routing\s*/\s*Transit)"),
        account_number=account,
    )


def parse_clia_ocr(text: str) -> ExtractedCLIACertificate:
    """Parse OCR text from a scanned CLIA certificate."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    # The source certificate has two columns. EasyOCR's coordinate-preserving
    # text puts the facility and CLIA ID on the same visual row, separated by
    # several spaces. Split those rows before looking for labels and values.
    rows = [re.split(r"\s{3,}", line) for line in lines]

    def after_heading(heading: str, column: int = 0) -> str | None:
        for index, row in enumerate(rows[:-1]):
            if any(re.fullmatch(heading, cell, re.I) for cell in row):
                following = rows[index + 1]
                if column < len(following):
                    return following[column]
        return None

    clia_match = re.search(r"\b\d{2}D\d{7}\b", text, re.I)
    clia_id = clia_match.group().upper() if clia_match else None
    id_line = clia_match.group() if clia_match else None
    if clia_id is None:
        # EasyOCR sometimes reads the fixed D in a CLIA ID as 0. Only correct
        # an all-digit candidate in the ID column beneath its heading.
        id_column = next((j for row in rows for j, cell in enumerate(row)
                          if re.fullmatch(r"CLIA\s+ID\s+NUMBER", cell, re.I)), None)
        if id_column is not None:
            id_line = after_heading(r"CLIA\s+ID\s+NUMBER", id_column)
            if id_line and re.fullmatch(r"\d{10}", id_line):
                clia_id = id_line[:2] + "D" + id_line[3:]
    certificate = _field_or_next_line(text, r"Certificate\s+Type")
    if not certificate:
        match = re.search(r"\b(?:Certificate\s+of\s+)?(Accreditation|Compliance|Waiver)\b", text, re.I)
        certificate = match.group(1) if match else None
    certificate_type = next(
        (kind for kind in ("Accreditation", "Compliance", "Waiver")
         if certificate and kind.lower() in certificate.lower()), None
    )
    # On a two-column scanned certificate, EasyOCR reads the header row before
    # either value. Use the CLIA ID as an anchor for the adjacent facility line.
    id_index = next((i for i, row in enumerate(rows)
                     if id_line and id_line in row), None)
    layout_facility = (rows[id_index][0] if id_index is not None and
                       len(rows[id_index]) > 1 else None)
    if layout_facility and re.search(r"(?:CLIA|NUMBER|DATE|DIRECTOR)", layout_facility, re.I):
        layout_facility = None
    location = (rows[id_index + 1][0] if id_index is not None and
                id_index + 1 < len(rows) else "")
    state_match = re.search(r"[,;]\s*([A-Z]{2})\b", location)

    def date_after(label: str) -> date | None:
        index = next((i for i, line in enumerate(lines) if re.fullmatch(label, line, re.I)), None)
        if index is None:
            return None
        return next((parsed for line in lines[index + 1:index + 5]
                     if (parsed := _date(line))), None)

    director = _field_or_next_line(text, r"(?:Laboratory\s+Director|Lab\s+Director)")
    if director and re.search(r"(?:DATE|DIRECTOR|EXPIRATION)", director, re.I):
        director = None
    if not director:
        candidate = after_heading(r"(?:LABORATORY|LAB)\s+DIRECTOR")
        if candidate and not _date(candidate):
            director = candidate
    effective_column = next((j for row in rows for j, cell in enumerate(row)
                             if re.fullmatch(r"EFFECTIVE\s+DATE", cell, re.I)), 0)
    expiration_column = next((j for row in rows for j, cell in enumerate(row)
                              if re.fullmatch(r"EXPIRATION\s+DATE", cell, re.I)), 0)
    return ExtractedCLIACertificate(
        clia_id=clia_id,
        facility_name=_field_or_next_line(text, r"(?:Facility\s+Name|Laboratory\s+Name)") or layout_facility,
        state=_field_or_next_line(text, r"(?:Laboratory\s+State|State)") or
              (state_match.group(1) if state_match else None),
        lab_director=director,
        certificate_type=certificate_type,
        effective_date=_date(_field_or_next_line(text, r"Effective\s+Date")) or
                       _date(after_heading(r"EFFECTIVE\s+DATE", effective_column)) or
                       date_after(r"EFFECTIVE\s+DATE"),
        expiration_date=_date(_field_or_next_line(text, r"(?:Expiration|Expiry|Expires?)\s*Date", r"Expires?")) or
                        _date(after_heading(r"EXPIRATION\s+DATE", expiration_column)) or
                        date_after(r"EXPIRATION\s+DATE"),
    )


def parse_coi(text: str) -> ExtractedMalpracticeCOI:
    """Parse medical specialty, policy dates, and coverage limits from a COI."""
    # ACORD-style tables are commonly extracted by PyMuPDF one cell per line.
    # The first professional-liability row carries the relevant policy dates.
    policy_row = re.search(
        r"(?im)^Professional\s+Liability\s*\n([^\n]+)\n"
        r"(\d{1,2}/\d{1,2}/\d{4})\n(\d{1,2}/\d{1,2}/\d{4})",
        text,
    )
    return ExtractedMalpracticeCOI(
        insured_name=_field_or_next_line(text, r"(?:Insured\s+Name|Named\s+Insured|Insured)"),
        medical_specialty=_field_or_next_line(text, r"(?:Medical\s+Specialty|Specialty)"),
        specialty_code=_field(text, r"Specialty\s+Code", r"Code"),
        policy_number=_field(text, r"Policy\s+Number") or (policy_row.group(1) if policy_row else None),
        effective_date=_date(_field(text, r"(?:Policy\s+)?Effective\s+Date") or
                             (policy_row.group(2) if policy_row else None)),
        expiration_date=_date(_field(text, r"(?:Policy\s+)?(?:Expiration|Expiry)\s+Date") or
                              (policy_row.group(3) if policy_row else None)),
        each_claim_limit=_money(_field(text, r"(?:Each\s+Claim|Per\s+Claim)(?:\s+Limit)?")),
        aggregate_limit=_money(_field(text, r"(?:Aggregate|General\s+Aggregate)(?:\s+Limit)?")),
    )
