"""Flask API for complete packet verification and audit history."""

from __future__ import annotations

import json
import os
import hashlib
import io
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import fitz
from flask import Flask, Response, jsonify, request, send_file, stream_with_context
from flask_cors import CORS
from pydantic import ValidationError

from core import DB_PATH, compare_names, get_run, list_runs, save_run
from decision_engine import adjudicate, check, cross_document_checks, screen_oig, verify_nppes
from forensics import _local_classify, inspect_native_pdf, process_document
from openai_models import draft_vendor_message
from models import VendorFormSubmission


ROOT = Path(__file__).resolve().parent
PACKETS = ROOT / "data" / "packets_current"
OIG_CSV = ROOT / "data" / "oig_exclusions.csv"
FILES = {
    "w9": "w9_form.pdf", "cp575": "irs_cp575.pdf",
    "bank_letter": "bank_eft_letter.pdf", "clia_certificate": "clia_certificate.pdf",
    "coi_insurance": "malpractice_coi.pdf",
}
CASES = {
    "case_1": "Quest Diagnostics · Clean packet",
    "case_2": "BioReference · Bank Revision",
    "case_3": "Allied Clinical · Exclusion Name Match",
    "case_4": "Apria Healthcare · Scope Conflict",
    "case_5a": "Sunrise Medical · Initial Submission",
    "case_5b": "Sunrise Medical · Corrected Resubmission",
}
REQUIRED_CONTENT = {
    "w9": "Legal name, DBA, tax class, EIN, and signature",
    "cp575": "IRS CP-575 or 147C: EIN, legal name, notice date; name control if printed",
    "bank_letter": "Bank letter, statement, voided check, or confirmation: bank, holder, account type, routing, and full account number",
    "clia_certificate": "CLIA ID, facility, state, director, scope, and expiration",
    "coi_insurance": "Insured, medical specialty, policy dates, and $1M/$3M limits",
}


def _case_folder(case: str) -> Path:
    if case not in CASES:
        raise ValueError("Select a valid packet case")
    folder = PACKETS / case
    if not folder.is_dir():
        raise FileNotFoundError(f"Packet {case} has not been generated")
    return folder


def _preview_form(form: dict) -> dict:
    shown = dict(form)
    for field in ("tin_ein", "bank_account_number"):
        if shown.get(field):
            shown[field] = f"••••{str(shown[field])[-4:]}"
    return shown


def _pdf_summary(data: bytes | None) -> dict:
    if data is None:
        return {"present": False, "bytes": 0, "pages": 0, "text_layer": "missing"}
    summary = {"present": True, "bytes": len(data),
               "sha256": hashlib.sha256(data).hexdigest()}
    try:
        with fitz.open(stream=data, filetype="pdf") as pdf:
            summary["pages"] = len(pdf)
            text_pages = sum(bool(page.get_text().strip()) for page in pdf)
            image_pages = sum(bool(page.get_images()) for page in pdf)
            summary["text_layer"] = (
                "native" if text_pages == len(pdf) and len(pdf) else
                "scanned" if text_pages == 0 and image_pages == len(pdf) and len(pdf) else
                "mixed or empty"
            )
    except Exception as exc:
        summary.update({"pages": 0, "text_layer": "unreadable", "error": str(exc)})
    return summary


def _pdf_preview(data: bytes, page_number: int) -> tuple[bytes, int]:
    if not data.startswith(b"%PDF-"):
        raise ValueError("The uploaded file is not a PDF")
    with fitz.open(stream=data, filetype="pdf") as pdf:
        if not 0 <= page_number < len(pdf):
            raise ValueError("The requested page does not exist")
        pixmap = pdf[page_number].get_pixmap(dpi=150, alpha=False)
        return pixmap.tobytes("png"), len(pdf)


def _read_request() -> tuple[dict, dict[str, bytes | None], str | None, str | None, dict[str, str]]:
    if request.is_json:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError("A JSON object is required")
        case = body.get("case")
        if case not in CASES:
            raise ValueError("Select a valid packet case")
        folder = _case_folder(case)
        form = json.loads((folder / "submission.json").read_text(encoding="utf-8"))
        files = {slot: (folder / filename).read_bytes() if (folder / filename).is_file() else None
                 for slot, filename in FILES.items()}
        return form, files, case, body.get("parent_run_id"), dict(FILES)
    if request.mimetype != "multipart/form-data":
        raise ValueError("Send JSON with a case or multipart/form-data with submission and PDFs")
    case = request.form.get("case") or None
    if case:
        folder = _case_folder(case)
        form = json.loads((folder / "submission.json").read_text(encoding="utf-8"))
        files = {slot: (folder / filename).read_bytes() if (folder / filename).is_file() else None
                 for slot, filename in FILES.items()}
    else:
        form, files = {}, {slot: None for slot in FILES}
    raw_form = request.form.get("submission")
    if raw_form:
        form = json.loads(raw_form)
        if not isinstance(form, dict):
            raise ValueError("submission must be a JSON object")
    elif not case:
        raise ValueError("Missing submission JSON")
    filenames = dict(FILES)
    for slot, filename in FILES.items():
        upload = request.files.get(slot) or request.files.get(filename)
        if upload is not None and upload.filename and not upload.filename.lower().endswith(".pdf"):
            raise ValueError(f"{slot} must be a PDF")
        if upload and upload.filename:
            files[slot] = upload.read()
            filenames[slot] = Path(upload.filename.replace("\\", "/")).name
    return form, files, case, request.form.get("parent_run_id"), filenames


def _names_for_screening(form: dict, documents: dict, nppes_checks: list[dict]) -> list[str]:
    names = [form.get("company_name"), form.get("dba_name"), form.get("authorized_official")]
    names.extend(form.get("owners_5_percent") or [])
    w9 = documents.get("w9", {}).get("extracted") or {}
    clia = documents.get("clia_certificate", {}).get("extracted") or {}
    names.extend([w9.get("signature_name"), clia.get("lab_director")])
    names.extend(item.get("actual") for item in nppes_checks
                 if item.get("check_name") == "NPPES authorized official")
    return list(dict.fromkeys(str(value).strip() for value in names if value and str(value).strip()))


def _redact_check(check_item: dict, form: dict) -> dict:
    sanitized = dict(check_item)
    for key in ("expected", "actual", "expected_value", "observed_value", "reasoning"):
        value = sanitized.get(key)
        if value is None:
            continue
        text = str(value)
        for secret in (form.get("bank_account_number"), form.get("tin_ein")):
            if secret:
                text = text.replace(str(secret), f"••••{str(secret)[-4:]}")
        sanitized[key] = text
    return sanitized


def _redact_payload(value, form: dict):
    if isinstance(value, dict):
        return {key: _redact_payload(item, form) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_payload(item, form) for item in value]
    if isinstance(value, str):
        for secret in (form.get("bank_account_number"), form.get("tin_ein")):
            if secret:
                value = value.replace(str(secret), f"••••{str(secret)[-4:]}")
    return value


def create_app(*, db_path: str | Path = DB_PATH) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
    app.config["DB_PATH"] = Path(db_path)
    app.config["CLASSIFIER"] = (None if os.getenv("ZAMP_CLASSIFIER", "rules").lower() == "openai"
                                else _local_classify)
    app.config["NPPES_CHECKER"] = verify_nppes
    app.config["OIG_PATH"] = OIG_CSV
    origins = ["http://127.0.0.1:5173", "http://localhost:5173"]
    if os.getenv("ZAMP_UI_ORIGIN"):
        origins.append(os.environ["ZAMP_UI_ORIGIN"])
    CORS(app, resources={r"/api/*": {"origins": origins}})

    @app.get("/api/cases")
    def cases():
        rows = []
        for key, label in CASES.items():
            folder = PACKETS / key
            form_path = folder / "submission.json"
            npi = None
            if form_path.is_file():
                try:
                    npi = json.loads(form_path.read_text(encoding="utf-8")).get("npi_number")
                except (OSError, ValueError):
                    pass
            rows.append({"id": key, "label": label, "available": folder.is_dir(),
                         "npi": npi})
        return jsonify(rows)

    @app.get("/api/cases/<case>/packet")
    def packet_preview(case: str):
        try:
            folder = _case_folder(case)
            form = json.loads((folder / "submission.json").read_text(encoding="utf-8"))
            documents = []
            for slot, filename in FILES.items():
                path = folder / filename
                details = _pdf_summary(path.read_bytes() if path.is_file() else None)
                documents.append({
                    "slot": slot, "filename": filename, "required_content": REQUIRED_CONTENT[slot],
                    "url": f"/api/cases/{case}/documents/{slot}", **details,
                })
            return jsonify({"case": case, "label": CASES[case],
                            "submission": _preview_form(form), "documents": documents})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.get("/api/cases/<case>/documents/<slot>")
    def case_document(case: str, slot: str):
        try:
            folder = _case_folder(case)
            if slot not in FILES:
                raise ValueError("Unknown document slot")
            path = folder / FILES[slot]
            if not path.is_file():
                raise FileNotFoundError("Document is missing")
            response = send_file(path, mimetype="application/pdf", as_attachment=False,
                                 download_name=FILES[slot])
            response.headers["Cache-Control"] = "private, no-store"
            return response
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.get("/api/cases/<case>/documents/<slot>/preview")
    def case_document_preview(case: str, slot: str):
        try:
            folder = _case_folder(case)
            if slot not in FILES:
                raise ValueError("Unknown document slot")
            path = folder / FILES[slot]
            if not path.is_file():
                raise FileNotFoundError("Document is missing")
            page_number = int(request.args.get("page", "0"))
            image, pages = _pdf_preview(path.read_bytes(), page_number)
            response = send_file(io.BytesIO(image), mimetype="image/png")
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-PDF-Pages"] = str(pages)
            return response
        except (ValueError, fitz.FileDataError) as exc:
            return jsonify({"error": str(exc)}), 400
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.post("/api/preview-document")
    def uploaded_document_preview():
        upload = request.files.get("pdf")
        if upload is None or not upload.filename:
            return jsonify({"error": "Supply a PDF file"}), 400
        try:
            page_number = int(request.args.get("page", "0"))
            image, pages = _pdf_preview(upload.read(), page_number)
            response = send_file(io.BytesIO(image), mimetype="image/png")
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-PDF-Pages"] = str(pages)
            return response
        except (ValueError, fitz.FileDataError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/registry/nppes/<npi>")
    def nppes_probe(npi: str):
        taxonomy = request.args.get("taxonomy", "")
        if not re.fullmatch(r"\d{10}", npi) or not re.fullmatch(r"[A-Za-z0-9]{10}", taxonomy):
            return jsonify({"error": "Supply a ten-digit NPI and ten-character taxonomy"}), 400
        checks = app.config["NPPES_CHECKER"](npi, taxonomy)
        required = {"NPPES active status", "NPPES entity type", "NPPES taxonomy"}
        return jsonify({"npi": npi, "claimed_taxonomy": taxonomy, "checks": checks,
                        "verified": all(any(item.get("check_name") == name and
                                            item.get("outcome") == "PASS" for item in checks)
                                        for name in required)})

    def prepare_run():
        form_payload, pdfs, case, parent_id, filenames = _read_request()
        form = VendorFormSubmission.model_validate(form_payload).model_dump(mode="json")
        if case == "case_5b" and not parent_id:
            parent = next((item for item in list_runs(500, db_path=app.config["DB_PATH"])
                           if item.get("case") in ("case_5a", "case_5")
                           and item.get("npi") == form["npi_number"]), None)
            if parent is None:
                raise ValueError("Run case_5a first or provide parent_run_id")
            parent_id = parent["run_id"]
        return form, pdfs, case, parent_id, filenames

    def execute_packet(form: dict, pdfs: dict[str, bytes | None],
                       case: str | None, parent_id: str | None,
                       filenames: dict[str, str]):
        documents: dict[str, dict] = {}
        checks: list[dict] = []

        yield {"type": "stage_started", "stage": "Stage 1"}
        for slot in FILES:
            yield {"type": "document_started", "stage": "Stage 1", "slot": slot}
            document = process_document(slot, pdfs.get(slot),
                                        classifier=app.config["CLASSIFIER"],
                                        include_forensics=False)
            documents[slot] = document
            yield {"type": "document_completed", "stage": "Stage 1", "slot": slot,
                   "document": _redact_payload({
                       "classification": document["classification"],
                       "classification_source": document["classification_source"],
                       "extracted": document["extracted"],
                       "forensics": document["forensics"],
                   }, form)}
            for item in document["checks"]:
                if item["stage"] == "Stage 1":
                    checks.append(item)
                    yield {"type": "check", "stage": "Stage 1", "slot": slot,
                           "check": _redact_payload(item, form)}
        yield {"type": "stage_completed", "stage": "Stage 1"}

        yield {"type": "stage_started", "stage": "Stage 2"}
        for slot, pdf_bytes in pdfs.items():
            if pdf_bytes is None:
                continue
            try:
                forensic = inspect_native_pdf(pdf_bytes)
                documents[slot]["forensics"].update({
                    key: value for key, value in forensic.items()
                    if key not in ("text", "checks")
                })
                yield {"type": "forensics_completed", "stage": "Stage 2", "slot": slot,
                       "forensics": _redact_payload(documents[slot]["forensics"], form)}
                for item in forensic["checks"]:
                    item = {**item, "evidence_source": slot}
                    checks.append(item)
                    yield {"type": "check", "stage": "Stage 2", "slot": slot,
                           "check": _redact_payload(item, form)}
            except Exception as exc:
                item = check("PDF forensics", "WARN_FIXABLE",
                             f"The PDF could not be inspected: {type(exc).__name__}: {exc}",
                             stage="Stage 2", source=slot)
                checks.append(item)
                yield {"type": "check", "stage": "Stage 2", "slot": slot,
                       "check": _redact_payload(item, form)}
        yield {"type": "stage_completed", "stage": "Stage 2"}

        yield {"type": "stage_started", "stage": "Stage 3"}
        for item in cross_document_checks(form, documents):
            checks.append(item)
            yield {"type": "check", "stage": "Stage 3",
                   "check": _redact_payload(item, form)}
        yield {"type": "stage_completed", "stage": "Stage 3"}

        yield {"type": "stage_started", "stage": "Stage 4"}
        yield {"type": "lookup_started", "stage": "Stage 4", "source": "CMS NPPES"}
        nppes_checks = app.config["NPPES_CHECKER"](
            form["npi_number"], form["claimed_taxonomy_code"])
        for item in nppes_checks:
            checks.append(item)
            yield {"type": "check", "stage": "Stage 4",
                   "check": _redact_payload(item, form)}
        registry_name = next((item.get("actual") for item in nppes_checks
                              if item.get("check_name") == "NPPES organization name"), None)
        parent_legal = next((item.get("actual") for item in nppes_checks
                             if item.get("check_name") == "NPPES parent legal name"), None)
        if registry_name:
            legal_matches = any(compare_names(form["company_name"], value) == "PASS"
                                for value in (registry_name, parent_legal) if value)
            item = check("NPPES legal name match", "PASS" if legal_matches else "WARN_FIXABLE",
                         "Submitted legal name compared with the NPI organization and its "
                         "reported parent legal business name.",
                         stage="Stage 4", expected=form["company_name"],
                         actual=" / ".join(value for value in (registry_name, parent_legal) if value),
                         source="CMS NPPES API")
            checks.append(item)
            yield {"type": "check", "stage": "Stage 4",
                   "check": _redact_payload(item, form)}
            if form.get("dba_name"):
                dba_matches = compare_names(form["dba_name"], registry_name) == "PASS"
                item = check("NPPES DBA match", "PASS" if dba_matches else "WARN_FIXABLE",
                             "Submitted DBA compared with the NPI organization name.",
                             stage="Stage 4", expected=form["dba_name"],
                             actual=registry_name, source="CMS NPPES API")
                checks.append(item)
                yield {"type": "check", "stage": "Stage 4",
                       "check": _redact_payload(item, form)}
        official = next((item.get("actual") for item in nppes_checks
                         if item.get("check_name") == "NPPES authorized official"), None)
        if official:
            official_matches = compare_names(form.get("authorized_official"), official) == "PASS"
            item = check("NPPES authorized official match",
                         "PASS" if official_matches else "WARN_FIXABLE",
                         "Application official compared with the person reported in NPPES.",
                         stage="Stage 4", expected=form.get("authorized_official"),
                         actual=official, source="CMS NPPES API")
            checks.append(item)
            yield {"type": "check", "stage": "Stage 4",
                   "check": _redact_payload(item, form)}
        yield {"type": "lookup_started", "stage": "Stage 4", "source": "OIG exclusions CSV"}
        for item in screen_oig(_names_for_screening(form, documents, nppes_checks),
                               str(app.config["OIG_PATH"])):
            checks.append(item)
            yield {"type": "check", "stage": "Stage 4",
                   "check": _redact_payload(item, form)}
        yield {"type": "stage_completed", "stage": "Stage 4"}

        yield {"type": "stage_started", "stage": "Stage 5"}
        status, message, alert = adjudicate(checks)
        message_source = "rules"
        if status != "APPROVED" and os.getenv("ZAMP_MESSAGE_MODEL", "1") == "1" and \
                not app.config.get("TESTING"):
            # The model sees only issue labels, never the private values or
            # confidential exclusion candidates. The rules still decide status.
            safe_issues = [item["check_name"] for item in checks
                           if item.get("outcome") in {"WARN_FIXABLE", "FAIL_ELIGIBILITY"}
                           and not item.get("requires_internal_review")
                           and not str(item.get("check_name", "")).startswith("OIG")]
            if status == "REJECTED: RISK":
                safe_issues = ["Securely reconfirm submitted payment or tax evidence"]
            try:
                message = draft_vendor_message(status, form["company_name"], message, safe_issues)
                message_source = "OpenAI draft with rule-based required actions"
            except Exception:
                message_source = "rules (OpenAI draft unavailable)"
        stored_form = {key: value for key, value in form.items()
                       if key not in ("bank_account_number", "tin_ein")}
        stored_form["bank_account_last4"] = form["bank_account_number"][-4:]
        stored_form["ein_last4"] = form["tin_ein"][-4:]
        record = {
            "run_id": str(uuid4()), "parent_run_id": parent_id,
            "pipeline_version": "2026-11-official-sources-v2",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "case": case, "vendor_name": form["company_name"], "npi": form["npi_number"],
            "input_form_sha256": hashlib.sha256(json.dumps(
                form, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
            "input_documents": {slot: {
                "filename": filenames[slot], "present": pdfs.get(slot) is not None,
                "sha256": hashlib.sha256(pdfs[slot]).hexdigest() if pdfs.get(slot) else None,
                "bytes": len(pdfs[slot]) if pdfs.get(slot) else 0,
            } for slot in FILES},
            "final_status": status, "vendor_message": message,
            "message_source": message_source,
            "internal_alert": _redact_payload(alert, form), "form": stored_form,
            "documents": _redact_payload({slot: {
                "classification": document["classification"],
                "classification_source": document["classification_source"],
                "forensics": document["forensics"],
                "extracted": document["extracted"],
            } for slot, document in documents.items()}, form),
            "checks": [_redact_payload(item, form) for item in checks],
        }
        saved = save_run(record, db_path=app.config["DB_PATH"])
        yield {"type": "stage_completed", "stage": "Stage 5"}
        yield {"type": "final", "run": saved}

    @app.post("/api/run")
    def run():
        try:
            final = next(event["run"] for event in execute_packet(*prepare_run())
                         if event["type"] == "final")
            return jsonify(final), 201
        except (ValueError, ValidationError, json.JSONDecodeError) as exc:
            return jsonify({"error": str(exc)}), 400
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.post("/api/run/stream")
    def run_stream():
        try:
            prepared = prepare_run()
        except (ValueError, ValidationError, json.JSONDecodeError) as exc:
            return jsonify({"error": str(exc)}), 400
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404

        @stream_with_context
        def events():
            try:
                for event in execute_packet(*prepared):
                    yield json.dumps(event, ensure_ascii=False, default=str) + "\n"
            except Exception as exc:
                app.logger.exception("Verification stream failed")
                yield json.dumps({"type": "error", "error": f"Verification failed: {exc}"}) + "\n"

        response = Response(events(), mimetype="application/x-ndjson")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    @app.get("/api/history")
    def history():
        try:
            limit = int(request.args.get("limit", "100"))
            offset = int(request.args.get("offset", "0"))
            return jsonify({"runs": list_runs(limit, offset, db_path=app.config["DB_PATH"]),
                            "limit": limit, "offset": offset})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/run/<run_id>")
    def run_detail(run_id: str):
        record = get_run(run_id, db_path=app.config["DB_PATH"])
        return (jsonify(record), 200) if record else (jsonify({"error": "Run not found"}), 404)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
