"""Verify the real government snapshots and their decision behavior offline."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from credibility_and_decision import screen_oig_exclusions
from decision_engine import adjudicate, verify_nppes


ROOT = Path(__file__).resolve().parents[1]


class OfficialReferenceDataTests(unittest.TestCase):
    def test_authentic_nppes_cache_checks_actual_taxonomy(self):
        with patch("decision_engine.requests.get", side_effect=requests.ConnectionError("offline")):
            quest = verify_nppes("1780620526", "291U00000X")
            apria = verify_nppes("1538273651", "291U00000X")
        self.assertEqual(next(item["outcome"] for item in quest
                              if item["check_name"] == "NPPES taxonomy"), "PASS")
        self.assertEqual(next(item["outcome"] for item in apria
                              if item["check_name"] == "NPPES taxonomy"), "FAIL_ELIGIBILITY")
        self.assertTrue(all("cache" in item["evidence_source"] for item in quest))

    def test_mismatched_registry_response_does_not_claim_ineligibility(self):
        response = Mock()
        response.json.return_value = {"result_count": 1, "results": [{"number": "0000000000"}]}
        with patch("decision_engine.requests.get", return_value=response), \
             patch("decision_engine._cache_read", return_value=None), \
             patch("decision_engine._cache_write") as cache_write:
            checks = verify_nppes("1780620526", "291U00000X")
        self.assertEqual(checks[0]["outcome"], "WARN_FIXABLE")
        cache_write.assert_not_called()

    def test_official_leie_name_collisions_require_identity_review(self):
        checks = screen_oig_exclusions(
            ["JACOB LEE", "ALAN GREENBERG", "LEAH TIMMERMAN"],
            str(ROOT / "data" / "oig_exclusions.csv"),
        )
        self.assertEqual(checks[0]["outcome"], "PASS", checks[0])
        candidates = [item for item in checks if item.get("requires_internal_review")]
        self.assertEqual(len(candidates), 2, checks)
        self.assertTrue(all(item["outcome"] == "WARN_FIXABLE" for item in candidates))
        self.assertEqual(adjudicate(checks)[0], "PENDING")
        self.assertNotIn("Jacob", adjudicate(checks)[1])
        self.assertNotIn("Alan", adjudicate(checks)[1])


if __name__ == "__main__":
    unittest.main()
