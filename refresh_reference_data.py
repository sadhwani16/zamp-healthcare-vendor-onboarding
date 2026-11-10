"""Download only authentic, public HHS OIG and CMS NPPES reference data.

Run monthly (and before a production decision batch):
    python refresh_reference_data.py --oig --packet-npis
Failed downloads never replace a previously verified snapshot.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import requests


ROOT = Path(__file__).resolve().parent
OIG_URL = "https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv"
OIG_CSV = ROOT / "data" / "oig_exclusions.csv"
OIG_SOURCE = ROOT / "data" / "oig_exclusions.source.json"
NPPES_URL = "https://npiregistry.cms.hhs.gov/api/"
NPPES_CACHE = ROOT / "data" / "npi_cache.json"
REQUIRED_OIG_COLUMNS = {"LASTNAME", "FIRSTNAME", "BUSNAME", "NPI", "EXCLDATE", "REINDATE"}


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def refresh_oig(*, session: requests.Session | None = None) -> dict:
    """Replace the full LEIE snapshot only after checking origin, schema, and size."""
    client = session or requests.Session()
    response = client.get(OIG_URL, timeout=(10, 120), stream=True)
    response.raise_for_status()
    if urlparse(response.url).hostname != "oig.hhs.gov":
        raise ValueError("LEIE download redirected outside the official OIG host")
    if "csv" not in response.headers.get("Content-Type", "").lower():
        raise ValueError("The OIG endpoint did not return CSV")

    OIG_CSV.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=OIG_CSV.parent,
                                         prefix=".leie.", delete=False) as handle:
            temporary = Path(handle.name)
            digest = hashlib.sha256()
            byte_count = 0
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                byte_count += len(chunk)
                if byte_count > 100 * 1024 * 1024:
                    raise ValueError("LEIE response exceeded the expected size limit")
                digest.update(chunk)
                handle.write(chunk)
        if byte_count < 100_000:
            raise ValueError("LEIE response is too small to be the full dataset")
        with temporary.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = {str(name).strip().upper() for name in reader.fieldnames or []}
            if not REQUIRED_OIG_COLUMNS.issubset(columns):
                raise ValueError(f"LEIE columns missing: {sorted(REQUIRED_OIG_COLUMNS - columns)}")
            row_count = sum(1 for _ in reader)
        if row_count < 10_000:
            raise ValueError("LEIE response is too short to be the full exclusion list")
        modified = response.headers.get("Last-Modified")
        modified_date = parsedate_to_datetime(modified).date().isoformat() if modified else None
        source = {
            "source_url": OIG_URL,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "source_last_modified": modified,
            "source_date": modified_date,
            "sha256": digest.hexdigest(),
            "row_count": row_count,
            "bytes": byte_count,
        }
        os.replace(temporary, OIG_CSV)
        temporary = None
        _atomic_json(OIG_SOURCE, source)
        return source
    finally:
        response.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def fetch_nppes_record(npi: str, *, session: requests.Session | None = None) -> dict:
    if len(npi) != 10 or not npi.isdigit():
        raise ValueError(f"NPI must contain ten digits: {npi!r}")
    client = session or requests.Session()
    response = client.get(NPPES_URL, params={"number": npi, "version": "2.1"},
                          timeout=(5, 20))
    response.raise_for_status()
    if urlparse(response.url).hostname != "npiregistry.cms.hhs.gov":
        raise ValueError("NPPES lookup redirected outside the official CMS host")
    payload = response.json()
    if not isinstance(payload, dict) or "result_count" not in payload:
        raise ValueError("NPPES response has no result_count")
    if payload.get("result_count") and not any(
        isinstance(item, dict) and str(item.get("number")) == npi
        for item in payload.get("results") or []
    ):
        raise ValueError("NPPES response does not contain the requested NPI")
    return payload


def refresh_nppes(npis: list[str], *, session: requests.Session | None = None) -> dict:
    try:
        cache = json.loads(NPPES_CACHE.read_text(encoding="utf-8"))
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, ValueError):
        cache = {}
    fetched_at = datetime.now(timezone.utc).isoformat()
    for npi in dict.fromkeys(npis):
        payload = fetch_nppes_record(npi, session=session)
        cache[npi] = {
            "source": "CMS NPPES API",
            "source_url": f"{NPPES_URL}?number={npi}&version=2.1",
            "fetched_at": fetched_at,
            "response": payload,
        }
    _atomic_json(NPPES_CACHE, cache)
    return {npi: (cache[npi]["response"].get("result_count") or 0)
            for npi in dict.fromkeys(npis)}


def packet_npis() -> list[str]:
    result = []
    for path in sorted((ROOT / "data" / "packets_current").glob("case_*/submission.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("npi_number"):
            result.append(str(payload["npi_number"]))
    return list(dict.fromkeys(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refresh official OIG LEIE and NPPES data")
    parser.add_argument("--oig", action="store_true", help="Download the full official LEIE CSV")
    parser.add_argument("--packet-npis", action="store_true", help="Refresh NPIs in current packet fixtures")
    parser.add_argument("--npi", action="append", default=[], help="Additional NPI to refresh")
    options = parser.parse_args()
    if not (options.oig or options.packet_npis or options.npi):
        parser.error("Select --oig, --packet-npis, or --npi")
    if options.oig:
        print("OIG:", json.dumps(refresh_oig(), indent=2))
    npis = [*options.npi, *(packet_npis() if options.packet_npis else [])]
    if npis:
        print("NPPES:", json.dumps(refresh_nppes(npis), indent=2))
