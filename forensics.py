"""Native PDF evidence, local EasyOCR scans, and OpenAI interpretation."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Callable

import fitz

from openai_models import analyze_document, extract_evidence_fields
from ocr_engine import read_scanned_pages, reader as _easyocr_reader

from document_parsers import (
    parse_bank_letter, parse_clia_ocr, parse_coi, parse_cp575, parse_w9,
)
from w9_layout import read_ein_from_cells


SLOT_TYPES = {
    "w9": parse_w9,
    "cp575": parse_cp575,
    "bank_letter": parse_bank_letter,
    "clia_certificate": parse_clia_ocr,
    "coi_insurance": parse_coi,
}
EDITORS = ("ilovepdf", "canva", "photoshop", "sejda", "foxit")
REQUIRED_FIELDS = {
    "w9": ("legal_name_line1", "tax_classification", "tin_ein", "signature_name", "signature_date"),
    "cp575": ("notice_code", "ein", "legal_name_line1", "notice_date"),
    "bank_letter": ("bank_name", "account_holder_name", "account_type", "routing_number", "account_number"),
    "clia_certificate": ("clia_id", "facility_name", "state", "lab_director",
                         "certificate_type", "expiration_date"),
    "coi_insurance": ("insured_name", "medical_specialty", "policy_number",
                      "effective_date", "expiration_date", "each_claim_limit", "aggregate_limit"),
}
OCR_REVIEW_THRESHOLD = 0.75
SENSITIVE_LABEL = re.compile(
    r"(?:Account Holder Name|Account Number|Routing Number|Beneficiary|"
    r"Employer Identification Number|Name Control|Facility Name|Lab Director|"
    r"Laboratory Director|Insured Name)\s*:", re.I,
)


def _check(name: str, outcome: str, reason: str, *, expected=None, actual=None, source="PDF") -> dict:
    return {
        "stage": "Stage 2" if name in {
            "PDF editor metadata", "PDF revision text", "PDF font consistency"
        } else "Stage 1",
        "check_name": name, "outcome": outcome, "reasoning": reason,
        "expected": expected, "actual": actual, "evidence_source": source,
    }


def _text(pdf: fitz.Document) -> str:
    return "\n".join(page.get_text() for page in pdf).strip()


def _revision_ends(pdf_bytes: bytes) -> list[int]:
    """Follow the final startxref /Prev chain to reconstruct true PDF saves.

    Some linearized PDFs contain more than one %%EOF marker without an
    incremental edit. Risk Hunter follows the cross-reference chain rather
    than counting markers, so the same approach is used here.
    """
    starts = list(re.finditer(rb"startxref\s+(\d+)\s*%%EOF", pdf_bytes))
    if not starts:
        return []
    offset = int(starts[-1].group(1))
    seen: set[int] = set()
    ends: list[int] = []
    while offset not in seen and 0 <= offset < len(pdf_bytes):
        seen.add(offset)
        eof = re.search(rb"%%EOF", pdf_bytes[offset:])
        if eof is None:
            break
        end = offset + eof.end()
        ends.append(end)
        previous = re.search(rb"/Prev\s+(\d+)", pdf_bytes[offset:end])
        if previous is None:
            break
        offset = int(previous.group(1))
    return sorted(set(ends))


def _revision_differences(pdf_bytes: bytes) -> tuple[int, list[str]]:
    ends = _revision_ends(pdf_bytes)
    if len(ends) <= 1:
        return max(1, len(ends)), []
    first_end = ends[0]
    try:
        with fitz.open(stream=pdf_bytes[:first_end], filetype="pdf") as first, \
             fitz.open(stream=pdf_bytes, filetype="pdf") as final:
            before = [line.strip() for line in _text(first).splitlines() if line.strip()]
            after = [line.strip() for line in _text(final).splitlines() if line.strip()]
    except Exception:
        # A damaged historical byte prefix is review evidence, but it is not
        # proof that its text changed.
        return len(ends), []
    removed, added = [], []
    for tag, i, j, k, m in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag != "equal":
            removed.extend(before[i:j])
            added.extend(after[k:m])
    diffs = []
    for old in removed[:]:
        label = old.partition(":")[0].strip().casefold() if ":" in old else ""
        match = next((new for new in added if label and new.casefold().startswith(label + ":")), None)
        if match:
            diffs.append(f"BEFORE: {old} -> AFTER: {match}")
            removed.remove(old)
            added.remove(match)
    diffs.extend(f"BEFORE: {old} -> AFTER: [none]" for old in removed)
    diffs.extend(f"BEFORE: [none] -> AFTER: {new}" for new in added)
    return len(ends), diffs


def _family(font: str) -> str:
    name = font.split("+", 1)[-1].lower()
    return re.sub(r"(?:bold|italic|oblique|regular|roman|psmt|mt)$", "", name)


def _font_anomalies(pdf: fitz.Document) -> list[str]:
    anomalies = []
    for page_no, page in enumerate(pdf, 1):
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = [span for span in line.get("spans", []) if span.get("text", "").strip()]
                line_text = "".join(span["text"] for span in spans)
                label = SENSITIVE_LABEL.search(line_text)
                if not label or len(spans) < 2:
                    continue
                label_span = next((span for span in spans if ":" in span["text"]), spans[0])
                value_spans = [span for span in spans if span["bbox"][0] >= label_span["bbox"][2] - 2
                               and span is not label_span]
                for value in value_spans:
                    overlap = min(label_span["bbox"][3], value["bbox"][3]) - \
                              max(label_span["bbox"][1], value["bbox"][1])
                    if overlap <= 0:
                        continue
                    if (_family(label_span["font"]) != _family(value["font"]) or
                        abs(label_span["size"] - value["size"]) > 0.75):
                        anomalies.append(f"Page {page_no}: {line_text.strip()}")
                        break
    return anomalies


def inspect_native_pdf(pdf_bytes: bytes) -> dict:
    """Return PDF evidence and explicit checks for editor, revision, and font signals."""
    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF-"):
        raise ValueError("The supplied bytes are not a PDF")
    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        if len(pdf) == 0:
            raise ValueError("The PDF has no pages")
        page_count = len(pdf)
        metadata = pdf.metadata or {}
        producer = str(metadata.get("producer") or "")
        creator = str(metadata.get("creator") or "")
        text_pages = [bool(page.get_text().strip()) for page in pdf]
        image_pages = [bool(page.get_images()) for page in pdf]
        scanned = all(not has_text and has_image for has_text, has_image
                      in zip(text_pages, image_pages))
        text_layer = ("scanned" if scanned else "native" if all(text_pages) else
                      "mixed" if any(text_pages) and any(image_pages) else "empty")
        anomalies = [] if scanned else _font_anomalies(pdf)
        text = "" if scanned else _text(pdf)
    revision_count, diffs = _revision_differences(pdf_bytes)
    editor = next((tool for tool in EDITORS if tool in f"{producer} {creator}".lower()), None)
    material_revision = any(re.search(
        r"(?:Account Holder|Account Number|Routing Number|Beneficiary|Employer Identification Number|EIN)",
        item, re.I) for item in diffs)
    checks = [
        _check("PDF editor metadata", "WARN_FIXABLE" if editor else "PASS",
               "PDF creator/producer reports an editor; review this signal with document content." if editor else
               "No listed editing application appears in PDF metadata.",
               expected="issuer metadata", actual=f"Producer: {producer}; Creator: {creator}"),
        _check("PDF revision text", "FAIL_RISK" if material_revision else
               "WARN_FIXABLE" if diffs else "PASS",
               "A payment or tax identifier changed between revisions." if material_revision else
               "Earlier and final PDF text differ; review the changes." if diffs else
               "No changed text was found across saved revisions.",
               expected="unchanged text", actual="; ".join(diffs) if diffs else "none"),
        _check("PDF font consistency", "WARN_FIXABLE" if anomalies else "PASS",
               "A value span differs from its label's font or size; review with other evidence." if anomalies else
               "No sensitive label/value font anomaly was found.",
               expected="consistent label/value style", actual="; ".join(anomalies[:5]) if anomalies else "none"),
    ]
    return {
        "is_scanned_pdf": scanned, "text_layer": text_layer, "page_count": page_count,
        "revision_count": revision_count,
        "revision_diffs": diffs, "producer": producer, "creator": creator,
        "font_anomalies": anomalies, "text": text, "checks": checks,
    }


def _local_classify(text: str) -> str:
    """Conservative content classifier used without an API call."""
    content = text.upper()
    if "INVOICE" in content and "INVOICE NUMBER" in content:
        return "wrong_document"
    if "CERTIFICATE OF LIABILITY INSURANCE" in content or (
        "PROFESSIONAL LIABILITY" in content and "POLICY NUMBER" in content
    ):
        return "coi_insurance"
    if "CLIA" in content and ("CERTIFICATE" in content or "LABORATORY" in content):
        return "clia_certificate"
    if "CP 575" in content and "EMPLOYER IDENTIFICATION" in content:
        return "cp575"
    if re.search(r"\b147\s*C\b", content) and ("INTERNAL REVENUE SERVICE" in content or
                                             "EMPLOYER IDENTIFICATION" in content):
        return "cp575"
    if "FORM W-9" in content or "FORM  W-9" in content:
        return "w9"
    if (("ACCOUNT" in content and "ROUTING" in content and
         ("BANK" in content or "TREASURY" in content)) or
        ("VOID" in content and "CHECK" in content and "ROUTING" in content) or
        ("BANK STATEMENT" in content or "ACCOUNT STATEMENT" in content)):
        return "bank_letter"
    return "wrong_document"


def process_document(
    slot_name: str, pdf_bytes: bytes | None, *,
    classifier: Callable[[str], str] | None = None,
    include_forensics: bool = True,
) -> dict:
    """Detect the text layer, OCR when needed, classify, and parse one slot."""
    if slot_name not in SLOT_TYPES:
        raise ValueError(f"Unknown document slot: {slot_name}")
    result = {"slot_name": slot_name, "classification": None,
              "classification_source": None, "text": "", "extracted": None,
              "forensics": None, "checks": []}
    if pdf_bytes is None:
        result["checks"].append(_check("Missing file in slot", "WARN_FIXABLE",
                                        f"Upload the missing {slot_name} PDF.", source=slot_name))
        return result
    try:
        if not pdf_bytes.startswith(b"%PDF-"):
            raise ValueError("The supplied bytes are not a PDF")
        if include_forensics:
            forensic = inspect_native_pdf(pdf_bytes)
            with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
                image_only_pages = [i for i, page in enumerate(pdf)
                                    if not page.get_text().strip() and bool(page.get_images())]
        else:
            with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
                if not len(pdf):
                    raise ValueError("The PDF has no pages")
                page_texts = [page.get_text().strip() for page in pdf]
                image_only_pages = [i for i, page in enumerate(pdf)
                                    if not page_texts[i] and bool(page.get_images())]
                scanned = len(image_only_pages) == len(pdf)
                text_layer = ("scanned" if scanned else "native" if all(page_texts) else
                              "mixed" if image_only_pages else "empty")
                forensic = {"is_scanned_pdf": scanned, "text_layer": text_layer,
                            "page_count": len(pdf), "text": "\n".join(page_texts), "checks": []}
        result["forensics"] = {key: value for key, value in forensic.items() if key not in ("text", "checks")}
        result["checks"].append(_check(
            "PDF text layer", "PASS",
            "Image-only pages need OCR." if image_only_pages else
            "Selectable native PDF text is available.",
            expected="readable PDF", actual=forensic["text_layer"], source=slot_name))
        result["checks"].extend({**check, "evidence_source": slot_name} for check in forensic["checks"])
        text = forensic["text"]
        if image_only_pages:
            ocr = read_scanned_pages(pdf_bytes, image_only_pages)
            text = "\n".join(part for part in (text, ocr["text"]) if part)
            result["forensics"].update({key: value for key, value in ocr.items() if key != "text"})
            if ocr["sharpness"] < 80:
                result["checks"].append(_check("Scan blurry", "WARN_FIXABLE",
                                                "Resubmit a sharper scan.", expected=">= 80",
                                                actual=f"{ocr['sharpness']:.1f}", source=slot_name))
            result["checks"].append(_check(
                "OCR confidence", "PASS" if ocr["ocr_confidence"] >= OCR_REVIEW_THRESHOLD else "WARN_FIXABLE",
                "Median EasyOCR text confidence is a scan-quality signal; low confidence requires review.",
                expected=f">= {OCR_REVIEW_THRESHOLD:.2f}", actual=f"{ocr['ocr_confidence']:.2f}", source=slot_name))
        result["text"] = text
        if not text.strip():
            result["checks"].append(_check("Document text", "WARN_FIXABLE",
                                            "No readable text could be extracted.", source=slot_name))
            return result
        model_analysis = None
        if classifier is not None:
            detected = classifier(text)
            result["classification_source"] = "Local document title and structure rules"
            if (detected == "wrong_document" and
                not re.search(r"(?im)^\s*INVOICE\b", text) and
                not (image_only_pages and result["forensics"].get("sharpness", 0) < 80)):
                try:
                    model_analysis = analyze_document(text, slot_name)
                    if model_analysis["document_type"] != "wrong_document":
                        detected = model_analysis["document_type"]
                        result["classification_source"] = "OpenAI on ambiguous content"
                        result["checks"].append(_check(
                            "Ambiguous document classification", "WARN_FIXABLE",
                            "The title rules were inconclusive; review the OpenAI classification.",
                            expected="recognizable document title", actual=detected,
                            source=slot_name))
                except Exception as exc:
                    result["checks"].append(_check(
                        "OpenAI classifier availability", "WARN_FIXABLE",
                        f"Ambiguous document could not be checked by OpenAI: {type(exc).__name__}: {exc}",
                        expected="OpenAI response", actual=detected, source=slot_name))
        else:
            try:
                model_analysis = analyze_document(text, slot_name)
                detected = model_analysis["document_type"]
                result["classification_source"] = "OpenAI"
                rule_type = _local_classify(text)
                if detected != rule_type:
                    result["checks"].append(_check(
                        "Classification disagreement", "WARN_FIXABLE",
                        "OpenAI and content rules disagree; review the uploaded PDF.",
                        expected=rule_type, actual=detected, source=slot_name))
                if re.search(r"(?im)^\s*INVOICE\b", text):
                    # A small model can be distracted by an invoice instruction
                    # to attach a W-9. Its document title is stronger evidence.
                    detected = "wrong_document"
                    result["classification_source"] = "Invoice title (OpenAI reviewed)"
                if model_analysis.get("field_model_error"):
                    result["checks"].append(_check(
                        "OpenAI field suggestions", "WARN_FIXABLE",
                        "Optional OpenAI field suggestions were unavailable; "
                        "the document parser was used.",
                        actual=model_analysis["field_model_error"], source=slot_name))
            except Exception as exc:
                detected = _local_classify(text)
                result["classification_source"] = "Content rules (provisional)"
                result["checks"].append(_check(
                    "OpenAI classifier availability", "WARN_FIXABLE",
                    f"OpenAI could not be reached; content rules classified provisionally: {type(exc).__name__}: {exc}",
                    expected="OpenAI response", actual=detected, source=slot_name))
        result["classification"] = detected
        if detected != slot_name:
            result["checks"].append(_check("Wrong document in slot", "WARN_FIXABLE",
                                            "Upload the requested document in this slot.",
                                            expected=slot_name, actual=detected, source=slot_name))
            return result
        result["checks"].append(_check("Document classification", "PASS",
                                        "Document content matches the upload slot.",
                                        expected=slot_name, actual=detected,
                                        source=result["classification_source"]))
        parsed = SLOT_TYPES[slot_name](text)
        if slot_name == "w9":
            with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
                w9_ein, ein_reason = read_ein_from_cells(pdf[0])
            parsed = parsed.model_copy(update={"tin_ein": w9_ein})
            result["checks"].append(_check(
                "W-9 EIN boxes", "PASS" if w9_ein else "WARN_FIXABLE",
                ein_reason, expected="one digit in each of nine Part I EIN boxes",
                actual=w9_ein or "unreadable", source=slot_name))
        if slot_name in {"bank_letter", "cp575"}:
            candidate = parsed.model_dump(mode="python")
            needed = [field for field in REQUIRED_FIELDS[slot_name]
                      if candidate.get(field) in (None, "")]
            if needed:
                try:
                    suggestion = extract_evidence_fields(text, slot_name, needed)
                    folded_text = re.sub(r"\s+", " ", text).casefold()
                    for field in needed:
                        value = suggestion["fields"].get(field)
                        quote = suggestion["evidence"].get(field)
                        if not isinstance(value, str) or not isinstance(quote, str):
                            continue
                        quote_norm = re.sub(r"\s+", " ", quote).strip().casefold()
                        value_norm = re.sub(r"\s+", " ", value).strip().casefold()
                        if field == "account_number" and (
                            not re.fullmatch(r"\d{4,17}", value.strip()) or
                            re.search(r"(?:[x*•]|ending|masked|last\s+four)", quote, re.I)
                        ):
                            continue
                        if quote_norm and quote_norm in folded_text and value_norm in quote_norm:
                            candidate[field] = value.strip()
                    parsed = type(parsed).model_validate(candidate)
                    supported = [field for field in needed if candidate.get(field)]
                    result["checks"].append(_check(
                        "OpenAI evidence extraction", "PASS" if supported else "WARN_FIXABLE",
                        "Model suggestions were accepted only where an exact source quote and field schema supported them."
                        if supported else "No supported field suggestion was found; review the document layout.",
                        actual=", ".join(supported) or "no supported fields",
                        source=slot_name))
                except Exception as exc:
                    result["checks"].append(_check(
                        "OpenAI evidence extraction", "WARN_FIXABLE",
                        f"Variable-layout evidence needs review: {type(exc).__name__}: {exc}",
                        source=slot_name))
        if model_analysis:
            candidate = parsed.model_dump(mode="python")
            evidence = model_analysis["evidence"]
            for field, value in model_analysis["fields"].items():
                quote = evidence.get(field)
                if (field in candidate and field != "is_signed" and
                    not (slot_name == "w9" and field == "tin_ein") and
                    candidate[field] in (None, "") and value not in (None, "") and
                    isinstance(quote, str) and quote.strip() and
                    re.sub(r"\s+", " ", quote).casefold() in
                    re.sub(r"\s+", " ", text).casefold()):
                    candidate[field] = value
            try:
                parsed = type(parsed).model_validate(candidate)
            except Exception:
                result["checks"].append(_check(
                    "Model field validation", "WARN_FIXABLE",
                    "OpenAI suggestions did not satisfy the document field schema; "
                    "rule-extracted fields were retained.", source=slot_name))
        result["extracted"] = parsed.model_dump(mode="json")
        if (slot_name == "clia_certificate" and result["extracted"].get("clia_id") and
                result["extracted"]["clia_id"] not in text.upper()):
            clia_id = result["extracted"]["clia_id"]
            raw_candidate = clia_id[:2] + "0" + clia_id[3:]
            normalized = (bool(image_only_pages) and raw_candidate in text and
                          result["forensics"].get("ocr_confidence", 0) >= OCR_REVIEW_THRESHOLD and
                          result["forensics"].get("sharpness", 0) >= 80 and
                          all(result["extracted"].get(field) not in (None, "")
                              for field in REQUIRED_FIELDS["clia_certificate"]))
            result["checks"].append(_check(
                "CLIA ID OCR ambiguity", "PASS" if normalized else "WARN_FIXABLE",
                "The CLIA ID uses a fixed D in position three. The OCR 0 was normalized with all "
                "other digits unchanged; the scan and required fields passed review gates." if normalized else
                "EasyOCR may have read the fixed D as a digit; verify the printed identifier.",
                expected="printed CLIA ID", actual=f"OCR {raw_candidate} -> {clia_id}" if normalized else clia_id,
                source=slot_name))
        populated = [name for name, value in result["extracted"].items()
                     if value is not None and value != ""]
        missing = [field for field in REQUIRED_FIELDS[slot_name]
                   if result["extracted"].get(field) in (None, "")]
        result["checks"].append(_check(
            "Required field extraction", "WARN_FIXABLE" if missing else "PASS",
            "Read all required structured fields." if not missing else
            "Resubmit or review fields that could not be read: " + ", ".join(missing),
            expected=", ".join(REQUIRED_FIELDS[slot_name]),
            actual=", ".join(populated) or "none", source=slot_name))
    except Exception as exc:
        result["checks"].append(_check("Document processing", "WARN_FIXABLE",
                                        f"Could not verify this document: {type(exc).__name__}: {exc}",
                                        source=slot_name))
    return result
