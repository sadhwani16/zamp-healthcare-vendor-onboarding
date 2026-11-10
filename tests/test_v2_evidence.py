"""Check alternative EIN/bank evidence and conservative OCR normalization."""

from __future__ import annotations

import unittest
import io
from pathlib import Path
from unittest.mock import patch

import fitz
from flask import Flask

from decision_engine import cross_document_checks
from document_parsers import parse_bank_letter, parse_cp575
from forensics import _local_classify, process_document
from openai_models import draft_vendor_message, extract_evidence_fields
from app import _read_request


PACKETS = Path(__file__).resolve().parents[1] / "data" / "packets_current"


def pdf_with_text(text: str) -> bytes:
    with fitz.open() as pdf:
        page = pdf.new_page()
        y = 72
        for line in text.splitlines():
            page.insert_text((72, y), line)
            y += 16
        return pdf.tobytes()


class V2EvidenceTests(unittest.TestCase):
    def test_uploaded_evidence_keeps_original_filename_in_audit_input(self):
        app = Flask(__name__)
        with app.test_request_context(
            "/api/run", method="POST",
            data={"submission": "{}", "cp575": (io.BytesIO(b"%PDF-1.7"), "147C-letter.pdf")},
        ):
            _form, _pdfs, _case, _parent, filenames = _read_request()
        self.assertEqual(filenames["cp575"], "147C-letter.pdf")

    def test_irs_147c_is_ein_evidence_without_name_control(self):
        text = ("INTERNAL REVENUE SERVICE\nLetter 147C EIN verification\n"
                "Employer Identification Number: 12-3456789\n"
                "Legal Name: ACME LAB INC\nNotice Date: 11/01/2026")
        self.assertEqual(_local_classify(text), "cp575")
        document = process_document("cp575", pdf_with_text(text),
                                    classifier=_local_classify, include_forensics=False)
        self.assertEqual(document["extracted"]["notice_code"], "147C")
        self.assertFalse(any(item["check_name"] == "Required field extraction" and
                             item["outcome"] != "PASS" for item in document["checks"]))
        checks = cross_document_checks(
            {"company_name": "ACME LAB INC", "tin_ein": "12-3456789",
             "npi_number": "1780620526", "bank_routing_number": "021000021",
             "state": "CA", "requested_complexity": "High-Complexity Lab"},
            {"cp575": document},
        )
        self.assertEqual(next(item for item in checks if item["check_name"] ==
                              "IRS EIN evidence type")["outcome"], "PASS")
        self.assertFalse(any(item["check_name"] == "CP-575 name control" for item in checks))

    def test_bank_statement_and_masked_account(self):
        text = ("BANK STATEMENT\nBank Name: Example Bank\nAccount Owner: ACME LAB INC\n"
                "Account Type: Business Checking\nRouting Number: 021000021\n"
                "Account Number: 1234567890")
        self.assertEqual(_local_classify(text), "bank_letter")
        bank = parse_bank_letter(text)
        self.assertEqual(bank.evidence_type, "bank_statement")
        self.assertEqual(bank.account_type, "Commercial Checking")
        self.assertEqual(bank.account_number, "1234567890")
        document = process_document("bank_letter", pdf_with_text(text),
                                    classifier=_local_classify, include_forensics=False)
        self.assertEqual(document["extracted"]["account_number"], "1234567890")
        self.assertTrue(all(item["outcome"] == "PASS" for item in document["checks"]))
        masked = parse_bank_letter(text.replace("1234567890", "****7890"))
        self.assertIsNone(masked.account_number)
        self.assertEqual(parse_bank_letter(text.replace("Bank Name: Example Bank", "Example Bank")).bank_name,
                         "Example Bank")

    def test_llm_field_requires_verbatim_quote(self):
        text = ("BANK STATEMENT\nAccount Owner: ACME LAB INC\n"
                "Account Type: Business Checking\nRouting Number: 021000021\n"
                "Account Number: 1234567890\nCHASE")
        response = {"fields": {"bank_name": "CHASE"},
                    "evidence": {"bank_name": "CHASE"}}
        with patch("forensics.extract_evidence_fields", return_value=response):
            document = process_document("bank_letter", pdf_with_text(text),
                                        classifier=_local_classify, include_forensics=False)
        self.assertEqual(document["extracted"]["bank_name"], "CHASE")
        response["evidence"]["bank_name"] = "Invented bank name"
        with patch("forensics.extract_evidence_fields", return_value=response):
            document = process_document("bank_letter", pdf_with_text(text),
                                        classifier=_local_classify, include_forensics=False)
        self.assertIsNone(document["extracted"]["bank_name"])

    def test_ocr_threshold_and_fixed_clia_character(self):
        pdf = (PACKETS / "case_1" / "clia_certificate.pdf").read_bytes()
        text = ("CLIA CERTIFICATE OF COMPLIANCE\n"
                "LABORATORY NAME AND ADDRESS                 CLIA ID NUMBER\n"
                "QUEST DIAGNOSTICS INCORPORATED                 0500643352\n"
                "SAN JUAN CAPISTRANO, CA 92675\n"
                "LABORATORY DIRECTOR\nMORGAN VALE\n"
                "EFFECTIVE DATE\n09/21/2026\n"
                "EXPIRATION DATE\n11/05/2027")
        for confidence, expected in ((0.79, "PASS"), (0.65, "WARN_FIXABLE")):
            with self.subTest(confidence=confidence), patch(
                "forensics.read_scanned_pages",
                return_value={"text": text, "sharpness": 200.0,
                              "ocr_confidence": confidence, "ocr_engine": "EasyOCR",
                              "ocr_regions": []},
            ):
                document = process_document("clia_certificate", pdf,
                                            classifier=_local_classify, include_forensics=False)
                check = next(item for item in document["checks"] if item["check_name"] ==
                             "CLIA ID OCR ambiguity")
                self.assertEqual(check["outcome"], expected)

    def test_message_draft_keeps_rule_actions_and_rejects_fraud_claim(self):
        baseline = "Please submit a clearer scan of the CLIA certificate."
        with patch("openai_models._request_json", return_value={
            "message": "Your application is pending. Please share a clearer CLIA certificate."
        }):
            message = draft_vendor_message("PENDING", "ACME LAB", baseline,
                                           ["OCR confidence"])
        self.assertIn(baseline, message)
        with patch("openai_models._request_json", return_value={
            "message": "Your application is fraudulent and will never be approved."
        }):
            with self.assertRaises(ValueError):
                draft_vendor_message("PENDING", "ACME LAB", baseline,
                                     ["OCR confidence"])

    def test_openai_request_uses_structured_output_without_storage(self):
        from unittest.mock import Mock

        reply = Mock()
        reply.json.return_value = {"status": "completed", "output": [
            {"content": [{"type": "output_text", "text":
                          '{"fields":{"bank_name":"Example Bank"},'
                          '"evidence":{"bank_name":"Example Bank"}}'}]}
        ]}
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), \
             patch("openai_models.requests.post", return_value=reply) as post:
            result = extract_evidence_fields("Example Bank", "bank_letter", ["bank_name"])
        self.assertEqual(result["fields"]["bank_name"], "Example Bank")
        body = post.call_args.kwargs["json"]
        self.assertIs(body["store"], False)
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertEqual(body["text"]["format"]["strict"], True)
        self.assertNotIn("test-key", str(body))


if __name__ == "__main__":
    unittest.main()
