"""Check printed EIN placement and extraction against every generated packet."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import fitz

from decision_engine import cross_document_checks
from document_parsers import parse_cp575
from forensics import _local_classify, process_document
from w9_layout import ein_cells, read_ein_from_cells


PACKETS = Path(__file__).resolve().parents[1] / "data" / "packets_current"
CASES = ("case_1", "case_2", "case_3", "case_4", "case_5", "case_5a", "case_5b")


class PrintedEinTests(unittest.TestCase):
    def test_all_packet_eins_are_centered_and_read_from_pdf(self):
        for case in CASES:
            with self.subTest(case=case):
                folder = PACKETS / case
                form = json.loads((folder / "submission.json").read_text(encoding="utf-8"))
                with fitz.open(folder / "w9_form.pdf") as pdf:
                    page = pdf[0]
                    ein, reason = read_ein_from_cells(page)
                    self.assertEqual(ein, form["tin_ein"], reason)
                    characters = [character for block in page.get_text("rawdict")["blocks"]
                                  if block.get("type") == 0
                                  for line in block.get("lines", [])
                                  for span in line.get("spans", [])
                                  for character in span.get("chars", [])]
                    for cell in ein_cells(page):
                        printed = [character for character in characters
                                   if character["c"].isascii() and character["c"].isdigit()
                                   and cell.contains(fitz.Point(
                                       (character["bbox"][0] + character["bbox"][2]) / 2,
                                       (character["bbox"][1] + character["bbox"][3]) / 2))]
                        self.assertEqual(len(printed), 1)
                        character = printed[0]
                        glyph_x = (character["bbox"][0] + character["bbox"][2]) / 2
                        box_x = (cell.x0 + cell.x1) / 2
                        self.assertLess(abs(glyph_x - box_x), 1.0)
                extracted = process_document(
                    "w9", (folder / "w9_form.pdf").read_bytes(),
                    classifier=_local_classify, include_forensics=False,
                )["extracted"]
                self.assertEqual(extracted["tin_ein"], form["tin_ein"])
                if case not in ("case_5", "case_5a"):
                    with fitz.open(folder / "irs_cp575.pdf") as notice:
                        self.assertEqual(parse_cp575(notice[0].get_text()).ein,
                                         form["tin_ein"])

    def test_swapped_w9_ein_is_not_taken_from_application(self):
        form = json.loads((PACKETS / "case_1" / "submission.json").read_text(encoding="utf-8"))
        other_w9 = (PACKETS / "case_2" / "w9_form.pdf").read_bytes()
        document = process_document("w9", other_w9,
                                    classifier=_local_classify, include_forensics=False)
        self.assertEqual(document["extracted"]["tin_ein"], "22-3123456")
        checks = cross_document_checks(form, {"w9": document})
        ein_check = next(item for item in checks if item["check_name"] == "W-9 EIN")
        self.assertEqual(ein_check["outcome"], "FAIL_RISK")


if __name__ == "__main__":
    unittest.main()
