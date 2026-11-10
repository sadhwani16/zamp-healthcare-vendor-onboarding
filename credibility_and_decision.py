"""External credibility checks and strict vendor onboarding adjudication.

Every check is a plain dictionary with an ``outcome`` key so it can be combined
with checks from earlier stages. A failed lookup produces ``WARN_FIXABLE``;
unavailable evidence must never be treated as a passing verification.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from core import normalize_business_name


def _check(
    check_name: str,
    outcome: str,
    reasoning: str,
    evidence_source: str,
    expected_value: str | None = None,
    observed_value: str | None = None,
) -> dict[str, str | None]:
    return {
        "stage_name": "Stage 4",
        "check_name": check_name,
        "outcome": outcome,
        "expected_value": expected_value,
        "observed_value": observed_value,
        "evidence_source": evidence_source,
        "reasoning": reasoning,
    }


def _normalize_name(value: Any) -> str:
    value = str(value or "").upper()
    value = re.sub(r"\b(?:PH\s*\.?\s*D|M\s*\.?\s*D|D\s*\.?\s*O)\.?\b", " ", value)
    tokens = re.findall(r"[A-Z0-9]+", value)
    return " ".join(token for token in tokens
                    if token not in {"DR", "MR", "MS", "MRS", "PHD", "MD", "DO", "JR", "SR"})


@lru_cache(maxsize=3)
def _oig_table(path_string: str, modified_ns: int, size: int) -> pd.DataFrame:
    """Cache the validated official table until the file is refreshed."""
    data = pd.read_csv(path_string, dtype=str, keep_default_na=False,
                       encoding="utf-8-sig")
    data.columns = [str(column).strip().upper() for column in data.columns]
    required = {"LASTNAME", "FIRSTNAME", "BUSNAME", "EXCLDATE", "REINDATE", "NPI"}
    if not required.issubset(data.columns) or len(data) < 10_000:
        raise ValueError("The LEIE file is incomplete or is not the full official dataset")
    data["_BUS_KEY"] = data["BUSNAME"].map(normalize_business_name)
    data["_FIRST_KEY"] = data["FIRSTNAME"].map(_normalize_name)
    data["_LAST_KEY"] = data["LASTNAME"].map(_normalize_name)
    return data


def _oig_source_check(path: Path) -> tuple[dict | None, dict]:
    metadata_path = path.with_suffix(".source.json")
    try:
        source = json.loads(metadata_path.read_text(encoding="utf-8"))
        if source.get("source_url") != "https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv":
            raise ValueError("The LEIE source is not the official OIG download")
        source_date = date.fromisoformat(source["source_date"])
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != source.get("sha256"):
            raise ValueError("The LEIE file hash differs from its download record")
        if source_date > date.today() or date.today() - source_date > timedelta(days=45):
            raise ValueError(f"The LEIE snapshot dated {source_date} is too old for a new decision")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return None, _check("OIG LEIE source", "WARN_FIXABLE",
                            f"Official exclusion data needs refresh or verification: {exc}",
                            str(metadata_path), "current authenticated OIG full CSV", "unverified")
    return source, _check("OIG LEIE source", "PASS",
                          f"Official full LEIE snapshot dated {source_date}; "
                          f"{source.get('row_count')} records; SHA-256 {source['sha256']}.",
                          source["source_url"], "current OIG snapshot", str(source_date))


def screen_oig_exclusions(names_to_check: list[str], oig_csv_path: str) -> list[dict]:
    """Find LEIE name candidates; only OIG identity verification can confirm one."""
    checks: list[dict] = []
    path = Path(oig_csv_path)
    if not names_to_check:
        return [_check("OIG exclusions screening", "WARN_FIXABLE",
                       "No company or person names were supplied for screening.",
                       str(path), "at least one name", "none")]
    if not path.is_file():
        return [_check("OIG LEIE source", "WARN_FIXABLE",
                       "Download the current full LEIE CSV from HHS OIG before screening.",
                       str(path), "official LEIE CSV", "missing")]
    source, source_check = _oig_source_check(path)
    checks.append(source_check)
    if source is None:
        return checks
    try:
        stat = path.stat()
        data = _oig_table(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        return [*checks, _check("OIG LEIE data", "WARN_FIXABLE",
                                f"Official LEIE CSV cannot be read: {type(exc).__name__}: {exc}",
                                str(path), "complete LEIE CSV", "unavailable")]

    seen_names: set[str] = set()
    for raw_name in names_to_check:
        normalized = _normalize_name(raw_name)
        if not normalized:
            checks.append(_check("OIG exclusion: blank name", "WARN_FIXABLE",
                                 "A name supplied for screening is blank.", str(path),
                                 "nonempty name", str(raw_name)))
            continue
        if normalized in seen_names:
            continue
        seen_names.add(normalized)
        business_hits = data.index[data["_BUS_KEY"] == normalize_business_name(raw_name)]
        tokens = normalized.split()
        if len(tokens) >= 2:
            person_hits = data.index[
                ((data["_FIRST_KEY"] == tokens[0]) & (data["_LAST_KEY"] == tokens[-1])) |
                ((data["_FIRST_KEY"] == tokens[-1]) & (data["_LAST_KEY"] == tokens[0]))
            ]
        else:
            person_hits = data.index[:0]
        hit_indices = list(dict.fromkeys([*business_hits.tolist(), *person_hits.tolist()]))
        if hit_indices:
            row = data.loc[hit_indices[0]]
            matched = row["BUSNAME"] or " ".join(part for part in
                (row["FIRSTNAME"], row["LASTNAME"]) if part)
            candidate = _check(
                f"OIG LEIE candidate: {raw_name}", "WARN_FIXABLE",
                "Name-only match in the official LEIE. A reviewer must confirm identity "
                "with the OIG online verification service before an adverse decision.",
                source["source_url"], "no name candidate",
                f"{matched}; state {row.get('STATE', '')}; exclusion date {row.get('EXCLDATE', '')}",
            )
            candidate["requires_internal_review"] = True
            candidate["candidate_count"] = len(hit_indices)
            checks.append(candidate)
        else:
            checks.append(_check(
                f"OIG LEIE name search: {raw_name}", "PASS",
                "No matching name in the current official LEIE snapshot.",
                source["source_url"], "no name match", "none",
            ))
    return checks


def _finding(check: dict) -> str:
    name = str(check.get("check_name") or check.get("name") or "Unnamed check")
    reason = str(check.get("reasoning") or check.get("message") or "Review required")
    return f"{name}: {reason}"


def adjudicate_submission(all_checks: list[dict]) -> tuple[str, str, str]:
    """Return (final status, vendor message, internal alert) by hard precedence."""
    if not all(isinstance(check, dict) for check in all_checks):
        raise TypeError("all_checks must contain dictionaries")
    def outcome(check: dict) -> str | None:
        return check.get("outcome", check.get("status"))

    risk = [check for check in all_checks if outcome(check) == "FAIL_RISK"]
    eligibility = [check for check in all_checks if outcome(check) == "FAIL_ELIGIBILITY"]
    fixable = [check for check in all_checks if outcome(check) == "WARN_FIXABLE"]
    fixable.extend({
        "check_name": check.get("check_name") or "Unrecognized check",
        "reasoning": "The check has no recognized outcome and needs review.",
    } for check in all_checks if outcome(check) not in {
        "PASS", "WARN_FIXABLE", "FAIL_ELIGIBILITY", "FAIL_RISK"})
    if risk:
        internal_alert = "Risk checks requiring internal review:\n" + "\n".join(
            f"- {_finding(check)} | Evidence: {check.get('evidence_source') or 'not supplied'}"
            f" | Observed: {check.get('observed_value') or check.get('actual') or 'not supplied'}"
            for check in risk
        )
        bank_issue = any("bank" in str(item.get("check_name", "")).lower() or
                         "account" in str(item.get("check_name", "")).lower()
                         for item in risk)
        tax_issue = any("ein" in str(item.get("check_name", "")).lower() or
                        "tax" in str(item.get("check_name", "")).lower()
                        for item in risk)
        next_steps = []
        if bank_issue:
            next_steps.append("Please ask your procurement contact for a secure way to confirm "
                              "the account holder and payment instructions with your bank.")
        if tax_issue:
            next_steps.append("Please provide corrected tax information or current IRS EIN evidence "
                              "through the submission portal.")
        if not next_steps:
            next_steps.append("Please contact your procurement representative to review the "
                              "documents needed to resolve this decision.")
        return (
            "REJECTED: RISK",
            "Thank you for submitting your application. After reviewing your submission, "
            "we are unable to proceed with your onboarding at this time.\n" +
            "\n".join(next_steps),
            internal_alert,
        )
    if eligibility:
        details = "\n".join(f"- {_finding(check)}" for check in eligibility)
        review = [check for check in all_checks if check.get("requires_internal_review")]
        return (
            "REJECTED: NOT ELIGIBLE",
            "We cannot approve the services requested because the submitted license scope, "
            "entity type, or NPI taxonomy does not match the application:\n" + details,
            "LEIE candidate identity review also remains open:\n" +
            "\n".join(f"- {_finding(check)}" for check in review) if review else "",
        )
    if fixable:
        external = [check for check in fixable if str(check.get("check_name") or "").startswith(
            ("NPPES availability", "NPPES response", "OpenAI classifier availability",
             "OpenAI evidence extraction", "OIG LEIE", "CLIA registry verification")) or check.get("requires_internal_review")]
        vendor_items = [check for check in fixable if check not in external]
        items = "\n".join(f"- {_finding(check)}" for check in vendor_items)
        vendor_message = (
            "Please correct the following items and resubmit your application:\n" + items
            if vendor_items else "Your application is pending verification."
        )
        if external:
            vendor_message += ("\nWe are completing external verification checks; "
                               "no action is needed from you for those checks.")
        return (
            "PENDING",
            vendor_message,
            "Confidential identity review required:\n" +
            "\n".join(f"- {_finding(check)}" for check in fixable
                      if check.get("requires_internal_review"))
            if any(check.get("requires_internal_review") for check in fixable) else "",
        )
    return "APPROVED", "Congratulations, your onboarding is approved.", ""
