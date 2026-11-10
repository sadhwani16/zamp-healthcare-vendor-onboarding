"""Exercise the Flask request path with controlled OCR and external responses."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from app import create_app
from core import list_runs
from decision_engine import check, cross_document_checks
from forensics import _local_classify, _revision_ends, inspect_native_pdf, process_document


ROOT = Path(__file__).resolve().parents[1]
PACKETS = ROOT / "data" / "packets_current"


def _nppes(npi: str, taxonomy: str) -> list[dict]:
    records = {
        "1780620526": ("QUEST DIAGNOSTICS NICHOLS INSTITUTE",
                        "QUEST DIAGNOSTICS INCORPORATED", "LEAH TIMMERMAN", {"291U00000X"}),
        "1134277494": ("BIOREFERENCE HEALTH, LLC", None, "MICHAEL SCHMID", {"291U00000X"}),
        "1548154826": ("ALLIED CLINICAL LABORATORY INC", None,
                        "GAYANE NIKOGHOSYAN", {"291U00000X"}),
        "1538273651": ("APRIA HEALTHCARE LLC", "APRIA HEALTHCARE GROUP LLC",
                        "PERRY BERNOCCHI", {"332B00000X"}),
        "1376587113": ("SUNRISE MEDICAL LABORATORIES, INC.", None,
                        "ALAN GREENBERG", {"291U00000X"}),
    }
    organization, parent, official, codes = records[npi]
    items = [
        check("NPPES active status", "PASS", "Active", stage="Stage 4", actual="A"),
        check("NPPES entity type", "PASS", "Organization", stage="Stage 4", actual="NPI-2"),
        check("NPPES taxonomy", "PASS" if taxonomy in codes else "FAIL_ELIGIBILITY",
              "Taxonomy compared", stage="Stage 4", expected=taxonomy,
              actual=", ".join(codes)),
        check("NPPES organization name", "PASS", "Registered name", stage="Stage 4",
              actual=organization),
        check("NPPES authorized official", "PASS", "Registered official",
              stage="Stage 4", actual=official),
    ]
    if parent:
        items.append(check("NPPES parent legal name", "PASS", "Parent legal name",
                           stage="Stage 4", actual=parent))
    return items


class ActivePipelineTests(unittest.TestCase):
    def test_all_packet_paths_are_decided_from_checks_and_saved(self):
        outcomes = {
            "case_1": "APPROVED", "case_2": "REJECTED: RISK",
            "case_3": "PENDING", "case_4": "REJECTED: NOT ELIGIBLE",
            "case_5a": "PENDING", "case_5b": "APPROVED",
        }
        laboratories = {
            "case_1": ("05D0643352", "CA", "Compliance", "QUEST DIAGNOSTICS INCORPORATED", "MORGAN VALE"),
            "case_2": ("31D0652945", "NJ", "Compliance", "BIOREFERENCE HEALTH LLC", "MORGAN VALE"),
            "case_3": ("45D0123456", "TX", "Compliance", "ALLIED CLINICAL LABORATORY INC", "JACOB LEE"),
            "case_4": ("22D0981035", "MA", "Waiver", "APRIA HEALTHCARE LLC", "MORGAN VALE"),
            "case_5a": ("33D0654120", "NY", "Compliance", "SUNRISE MEDICAL LABORATORIES, INC.", "MORGAN VALE"),
            "case_5b": ("33D0654120", "NY", "Compliance", "SUNRISE MEDICAL LABORATORIES, INC.", "MORGAN VALE"),
        }
        active = "case_1"

        def ocr(_pdf_bytes: bytes, _pages: list[int]) -> dict:
            clia_id, state, scope, facility, director = laboratories[active]
            text = (f"CLIA CERTIFICATE OF {scope.upper()}\n"
                    f"CLIA ID: {clia_id}\nFacility Name: {facility}\n"
                    f"Laboratory State: {state}\nLaboratory Director: {director}\n"
                    f"Certificate Type: {scope}\n"
                    f"Effective Date: {(date.today() - timedelta(days=45)):%m/%d/%Y}\n"
                    f"Expiration Date: {(date.today() + timedelta(days=365)):%m/%d/%Y}")
            return {"text": text, "sharpness": 20.0 if active == "case_5a" else 200.0,
                    "ocr_confidence": 0.55 if active == "case_5a" else 0.97,
                    "ocr_engine": "EasyOCR", "ocr_regions": []}

        def oig(names: list[str], _path: str) -> list[dict]:
            checks = [check("OIG LEIE source", "PASS", "Controlled source", stage="Stage 4")]
            for name in names:
                if active == "case_3" and name.upper().startswith("JACOB LEE"):
                    item = check(f"OIG LEIE candidate: {name}", "WARN_FIXABLE",
                                 "Name-only candidate; identity review required.", stage="Stage 4")
                    item["requires_internal_review"] = True
                else:
                    item = check(f"OIG LEIE name search: {name}", "PASS", "No candidate",
                                 stage="Stage 4")
                checks.append(item)
            return checks

        with tempfile.TemporaryDirectory() as directory, \
             patch("forensics.read_scanned_pages", side_effect=ocr), \
             patch("app.screen_oig", side_effect=oig):
            database = Path(directory) / "audit.db"
            application = create_app(db_path=database)
            application.config.update(TESTING=True, CLASSIFIER=_local_classify,
                                      NPPES_CHECKER=_nppes)
            client = application.test_client()
            saved = {}
            for case, status in outcomes.items():
                active = case
                response = client.post("/api/run", json={"case": case})
                self.assertEqual(response.status_code, 201, response.get_json())
                run = response.get_json()
                saved[case] = run
                self.assertEqual(run["final_status"], status,
                                 [(item["check_name"], item["outcome"])
                                  for item in run["checks"] if item["outcome"] != "PASS"])
                self.assertEqual(run["pipeline_version"], "2026-11-official-sources-v2")
                self.assertEqual(len(run["input_documents"]), 5)
                self.assertTrue(all(item["sha256"] for item in run["input_documents"].values()))
                self.assertTrue(all(stage in {item["stage"] for item in run["checks"]}
                                    for stage in ("Stage 1", "Stage 2", "Stage 3", "Stage 4")))
            self.assertTrue(any(item["check_name"] == "PDF revision text" and
                                item["outcome"] == "FAIL_RISK"
                                for item in saved["case_2"]["checks"]))
            self.assertIn("secure", saved["case_2"]["vendor_message"].lower())
            self.assertIn("identity review", saved["case_3"]["internal_alert"].lower())
            self.assertEqual(saved["case_5b"]["parent_run_id"], saved["case_5a"]["run_id"])
            self.assertEqual(len(list_runs(db_path=database)), len(outcomes))

    def test_cp575_suffix_alone_is_not_corporate_eligibility_proof(self):
        form = {"company_name": "ACME INC", "dba_name": None, "tin_ein": "12-3456789",
                "npi_number": "1780620526", "bank_routing_number": "021000021",
                "state": "CA", "requested_complexity": "High-Complexity Lab",
                "tax_classification": "C Corporation"}
        documents = {"cp575": {"extracted": {"notice_code": "CP 575 G",
                                            "ein": "12-3456789", "legal_name_line1": "ACME INC",
                                            "name_control": "ACME"}}}
        checks = cross_document_checks(form, documents)
        notice = next(item for item in checks if item["check_name"] == "CP-575 notice code")
        self.assertEqual(notice["outcome"], "PASS")

    def test_editor_metadata_alone_is_review_evidence(self):
        import fitz

        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), "Account Holder Name: ACME INC")
            metadata = pdf.metadata or {}
            metadata["producer"] = "iLovePDF"
            pdf.set_metadata(metadata)
            result = inspect_native_pdf(pdf.tobytes())
        metadata = next(item for item in result["checks"]
                        if item["check_name"] == "PDF editor metadata")
        self.assertEqual(metadata["outcome"], "WARN_FIXABLE")
        self.assertFalse(any(item["outcome"] == "FAIL_RISK" for item in result["checks"]))

    def test_stray_eof_marker_is_not_an_incremental_revision(self):
        import fitz

        with fitz.open() as pdf:
            pdf.new_page().insert_text((72, 72), "Unchanged content")
            content = pdf.tobytes()
        self.assertEqual(len(_revision_ends(content)), 1)
        self.assertEqual(len(_revision_ends(content + b"\n%%EOF\n")), 1)

    def test_invoice_title_overrides_model_distracted_by_w9_mention(self):
        invoice = (PACKETS / "case_5a" / "irs_cp575.pdf").read_bytes()
        with patch("forensics.analyze_document", return_value={
            "document_type": "w9", "fields": {}, "evidence": {},
        }):
            document = process_document("cp575", invoice, include_forensics=False)
        self.assertEqual(document["classification"], "wrong_document")
        self.assertIsNone(document["extracted"])
        self.assertTrue(any(item["check_name"] == "Wrong document in slot" and
                            item["outcome"] == "WARN_FIXABLE"
                            for item in document["checks"]))


if __name__ == "__main__":
    unittest.main()
