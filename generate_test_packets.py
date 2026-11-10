"""Generate polished, visibly marked onboarding fixtures from blank templates.

The supplied W-9 is blank. The supplied CP-575 and CLIA files are filled source
layouts; temporary masked copies are made before any values are overlaid.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Mapping

import cv2
import fitz
import numpy as np
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

from models import VendorFormSubmission
from w9_layout import centered_digit_origin, ein_cells


ROOT = Path(__file__).resolve().parent
TEMPLATES = ROOT / "templates"
DATA = ROOT / "data"
PACKETS = DATA / "packets_current"
TEMPLATE_ALTERNATIVES = (
    ("blank_w9.pdf", "blank_W9.pdf"),
    ("blank_cp575.pdf", "CP575A.pdf"),
    ("blank_clia.pdf", "CLIA-Certificate.pdf"),
)
@dataclass(frozen=True)
class OverlayText:
    x: float
    y: float
    text: str
    size: float = 9
    font: str = "helv"


@dataclass(frozen=True)
class Packet:
    number: int
    legal_name: str
    dba: str | None
    state: str
    city: str
    zip_code: str
    npi: str
    ein: str
    clia_id: str
    clia_state: str
    clia_type: str
    director: str
    owner: str
    nppes_official: str = ""
    street_address: str = "1200 COMMERCE PARKWAY"
    specialty: str = "Clinical Medical Laboratory"
    specialty_code: str = "291U00000X"
    cp_code: str = "CP 575 A"
    w9_signed: bool = True
    wrong_cp_document: bool = False
    bank_swap: bool = False
    blurry_clia: bool = False


def _packets() -> list[Packet]:
    return [
        Packet(1, "QUEST DIAGNOSTICS INCORPORATED", "QUEST DIAGNOSTICS NICHOLS INSTITUTE",
               "CA", "SAN JUAN CAPISTRANO", "92675", "1780620526", "16-1387862",
               "05D0643352", "CA", "Compliance", "MORGAN VALE", "MORGAN VALE",
               nppes_official="LEAH TIMMERMAN",
               street_address="33608 ORTEGA HWY"),
        Packet(2, "BIOREFERENCE HEALTH LLC", None, "NJ", "ELMWOOD PARK", "07407",
               "1134277494", "22-3123456", "31D0652945", "NJ", "Compliance",
               "MORGAN VALE", "MORGAN VALE", nppes_official="MICHAEL SCHMID",
               bank_swap=True),
        Packet(3, "ALLIED CLINICAL LABORATORY INC", None, "TX", "IRVING", "75038",
               "1548154826", "74-3256789", "45D0123456", "TX", "Compliance",
               "JACOB LEE Ph.D.", "JACOB LEE",
               nppes_official="GAYANE NIKOGHOSYAN",
               street_address="1329 W WALNUT HILL LN #117"),
        Packet(4, "APRIA HEALTHCARE LLC", None, "IN", "INDIANAPOLIS", "46237",
               "1538273651", "35-1234567", "22D0981035", "MA", "Waiver",
               "MORGAN VALE", "MORGAN VALE", specialty="Mental Health Counselor",
               nppes_official="PERRY BERNOCCHI",
               specialty_code="80723", cp_code="CP 575 G",
               street_address="7353 COMPANY DR"),
        Packet(5, "SUNRISE MEDICAL LABORATORIES, INC.", None, "NY", "HICKSVILLE", "11801",
               "1376587113", "74-4123456", "33D0654120", "NY", "Compliance",
               "MORGAN VALE", "MORGAN VALE", w9_signed=False,
               nppes_official="ALAN GREENBERG",
               wrong_cp_document=True, blurry_clia=True,
               street_address="250 MILLER PLACE"),
    ]


def _form(packet: Packet) -> VendorFormSubmission:
    return VendorFormSubmission(
        company_name=packet.legal_name,
        dba_name=packet.dba,
        country="US",
        physical_address=packet.street_address,
        city=packet.city,
        state=packet.state,
        zip_code=packet.zip_code,
        npi_number=packet.npi,
        tin_ein=packet.ein,
        vendor_category="Clinical Laboratory",
        tax_classification="C Corporation",
        claimed_taxonomy_code="291U00000X",
        requested_complexity="High-Complexity Lab",
        bank_routing_number="021000021",
        bank_account_number=f"6800{packet.number:02d}2391",
        contact_email=f"credentialing{packet.number}@mail.invalid",
        authorized_official=packet.nppes_official or packet.owner,
        owners_5_percent=[packet.owner],
        prior_sanctions_disclosed=False,
    )


def _mark_template(page: fitz.Page) -> None:
    width, height = page.rect.width, page.rect.height
    page.draw_line((40, height - 37), (width - 40, height - 37),
                   color=(0.7, 0.3, 0.3), width=0.8)
    page.insert_text((40, height - 25), "SAMPLE  |  NOT VALID FOR PAYMENT OR CREDENTIALING",
                     fontname="hebo", fontsize=9, color=(0.55, 0.12, 0.12))


def overlay_text_on_template(
    template_path: str | Path,
    output_path: str | Path,
    text_mapping: Mapping[str, OverlayText],
) -> None:
    """Overlay values on a blank PDF using upper-left-origin point coordinates."""
    template = Path(template_path)
    if not template.is_file():
        raise FileNotFoundError(f"Blank PDF template is missing: {template}")
    with fitz.open(template) as document:
        if len(document) != 1:
            raise ValueError(f"Expected one-page blank PDF template: {template}")
        page = document[0]
        if template.name.casefold() == "blank_w9.pdf":
            matches = page.search_for("example")
            for rect in matches:
                page.add_redact_annot(rect, fill=(1, 1, 1))
            if matches:
                page.apply_redactions()
                for rect in matches:
                    page.insert_text((rect.x0, rect.y1 - 1), "instance",
                                     fontname="helv", fontsize=6.8, color=(0, 0, 0))
        if template.name.casefold() == "blank_cp575.pdf" and "notice_code" in text_mapping:
            for label in ("CP 575 A", "CP 575 G"):
                for rect in page.search_for(label):
                    page.add_redact_annot(rect, fill=(1, 1, 1))
            page.apply_redactions()
        for field_name, placement in text_mapping.items():
            if not isinstance(placement, OverlayText):
                raise TypeError(f"{field_name} must be an OverlayText")
            if placement.text:
                page.insert_text((placement.x, placement.y), placement.text,
                                 fontname=placement.font, fontsize=placement.size,
                                 color=(0.12, 0.16, 0.22), overlay=True)
        _mark_template(page)
        Path(output_path).write_bytes(document.tobytes(garbage=4, deflate=True))


def _prepare_cp575(source_path: Path, output_path: Path) -> None:
    """Mask the previous addressee and identifiers in page 1 of a notice source."""
    with fitz.open(source_path) as source:
        source.select([0])
        page = source[0]
        old_text = page.get_text()
        for value in set(re.findall(r"\b\d{2}-\d{7}\b|\b\d{2}-\d{2}-\d{4}\b", old_text)):
            for rect in page.search_for(value):
                page.add_redact_annot(rect, fill=(1, 1, 1))
        page.add_redact_annot(fitz.Rect(377, 104, 565, 133), fill=(1, 1, 1))
        # The uploaded examples contain a different entity in this address block.
        address_bottom = 212 if "Notice" in source_path.name else 205
        page.add_redact_annot(fitz.Rect(106, 164, 330, address_bottom), fill=(1, 1, 1))
        page.add_redact_annot(fitz.Rect(66, 428, 582, 741), fill=(1, 1, 1))
        for rect in page.search_for("CORC"):
            page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()
        source.save(output_path, garbage=4, deflate=True)


def _prepare_clia(source_path: Path, output_path: Path) -> None:
    """Crop the supplied image certificate and mask its prior identity fields."""
    with fitz.open(source_path) as source, fitz.open() as blank:
        page = blank.new_page(width=612, height=410)
        page.show_pdf_page(page.rect, source, 0, clip=fitz.Rect(0, 0, 612, 410))
        for box in (
            (204, 88, 412, 108),   # certificate type
            (115, 132, 382, 165),  # laboratory name and location
            (412, 133, 503, 151),  # CLIA identifier
            (412, 169, 503, 189),  # effective date
            (115, 207, 363, 227),  # director
            (412, 205, 503, 225),  # expiration date
            (342, 286, 551, 361),  # source signature and named official
        ):
            page.draw_rect(fitz.Rect(box), color=None, fill=(1, 1, 1), overlay=True)
        blank.save(output_path, garbage=4, deflate=True)


# Positions are provisional until the supplied blank PDFs can be measured.
def _w9_mapping(packet: Packet, today: date,
                ein_boxes: list[fitz.Rect]) -> dict[str, OverlayText]:
    mapping = {
        "line1": OverlayText(80, 124, packet.legal_name, 9.2),
        "line2": OverlayText(80, 149, packet.dba or "", 9.2),
        "corporation_checkbox": OverlayText(181, 187, "X", 9, "hebo"),
        "address": OverlayText(80, 295, packet.street_address, 8.8),
        "city_state_zip": OverlayText(80, 320, f"{packet.city}, {packet.state} {packet.zip_code}", 8.8),
        "signature": OverlayText(150, 600, packet.nppes_official or packet.owner
                                 if packet.w9_signed else "", 10, "heit"),
        "signature_date": OverlayText(430, 600, today.strftime("%m/%d/%Y") if packet.w9_signed else "", 9),
    }
    digits = packet.ein.replace("-", "")
    if len(ein_boxes) != 9 or not re.fullmatch(r"[0-9]{9}", digits):
        raise ValueError("W-9 EIN needs nine template cells and nine digits")
    for index, (digit, cell) in enumerate(zip(digits, ein_boxes, strict=True)):
        x, y = centered_digit_origin(cell, digit)
        mapping[f"tin_digit_{index}"] = OverlayText(x, y, digit, 10.5)
    return mapping


def _cp575_mapping(packet: Packet, today: date) -> dict[str, OverlayText]:
    control = "".join(c for c in packet.legal_name if c.isalpha())[:4]
    mapping = {
        "notice_date": OverlayText(503, 96, today.strftime("%m/%d/%Y"), 9, "cour"),
        "ein_label": OverlayText(379, 113, "Employer Identification Number:", 9, "cour"),
        "ein_header": OverlayText(379, 126, packet.ein, 9, "cour"),
        "legal_name": OverlayText(109, 177, packet.legal_name, 9, "cour"),
        "dba": OverlayText(109, 187, packet.dba or "", 9, "cour"),
        "address": OverlayText(109, 197 if packet.dba else 187, packet.street_address, 9, "cour"),
        "city_state_zip": OverlayText(109, 207 if packet.dba else 197,
                                      f"{packet.city}, {packet.state} {packet.zip_code}", 9, "cour"),
        "ein_body": OverlayText(92, 336, packet.ein, 9, "cour"),
        "name_control": OverlayText(82, 688, f"Name Control: {control}", 9, "cour"),
    }
    paragraphs = (
        "Please use the EIN and legal name shown above on all federal tax filings.",
        "The four-character name control associated with this EIN appears below.",
        "Keep this notice with your permanent business records.",
        "",
        "IMPORTANT REMINDERS",
        "  * Use the EIN and legal name exactly as shown on this notice.",
        "  * Include a copy of this notice when writing to the IRS about your EIN.",
        "  * Notify the IRS if your business address or responsible party changes.",
        "",
        "Thank you for your cooperation.",
    )
    for index, line in enumerate(paragraphs):
        mapping[f"body_line_{index}"] = OverlayText(82, 458 + index * 18, line, 9, "cour")
    return mapping


def _clia_mapping(packet: Packet, today: date) -> dict[str, OverlayText]:
    return {
        "certificate_type": OverlayText(232, 101, f"CERTIFICATE OF {packet.clia_type.upper()}", 11.5, "hebo"),
        "facility": OverlayText(118, 145, packet.legal_name, 10),
        "location": OverlayText(118, 157, f"{packet.city}, {packet.clia_state} {packet.zip_code}", 9.5),
        "clia_id": OverlayText(414, 145, packet.clia_id, 11.5, "hebo"),
        "director": OverlayText(118, 220, packet.director, 10),
        "effective": OverlayText(414, 181, (today - timedelta(days=45)).strftime("%m/%d/%Y"), 10),
        "expiration": OverlayText(414, 218, (today + timedelta(days=365)).strftime("%m/%d/%Y"), 10),
        "signature": OverlayText(355, 326, "Avery Collins", 15, "heit"),
        "signature_title": OverlayText(355, 343, "Authorized Reviewer", 7),
    }


def _save_scanned_clia(overlay_pdf: Path, output_pdf: Path, *, blurry: bool) -> None:
    with fitz.open(overlay_pdf) as source:
        page = source[0]
        pixmap = page.get_pixmap(dpi=220, alpha=False)
        pixels = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width, pixmap.n
        )[:, :, :3]
        if blurry:
            pixels = cv2.GaussianBlur(pixels, (75, 75), 25)
            cv2.putText(pixels, "SAMPLE - NOT VALID", (120, pixels.shape[0] - 70),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.85, (150, 35, 35), 2, cv2.LINE_AA)
        image = Image.fromarray(pixels)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        page_size = page.rect
    with fitz.open() as output:
        page = output.new_page(width=page_size.width, height=page_size.height)
        page.insert_image(page.rect, stream=stream.getvalue())
        output_pdf.write_bytes(output.tobytes(garbage=4, deflate=True))


def _mark_reportlab(c: canvas.Canvas) -> None:
    c.saveState()
    c.setStrokeColor(colors.HexColor("#B47A7A"))
    c.line(48, 42, 564, 42)
    c.setFillColor(colors.HexColor("#A93C3C"))
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(306, 29, "SAMPLE  |  NOT VALID FOR PAYMENT OR CREDENTIALING")
    c.restoreState()


def _save_bank_letter(packet: Packet, path: Path, today: date) -> None:
    c = canvas.Canvas(str(path), pagesize=letter, pageCompression=1)
    c.setTitle("Bank account verification")
    c.setFillColor(colors.HexColor("#152B45"))
    c.setFont("Times-Bold", 18)
    c.drawString(56, 733, "JPMorgan Chase Commercial Banking")
    c.setStrokeColor(colors.HexColor("#152B45"))
    c.setLineWidth(2)
    c.line(56, 717, 556, 717)
    c.setFont("Times-Roman", 10)
    c.drawString(56, 689, "Treasury Services  |  Account Verification")
    c.drawRightString(556, 689, today.strftime("%B %d, %Y"))
    c.setFillColor(colors.HexColor("#222222"))
    c.setFont("Times-Roman", 11)
    c.drawString(56, 643, "To Whom It May Concern:")
    for index, line in enumerate((
        "Please accept this letter as formal verification that the entity listed below",
        "maintains an active commercial checking account with JPMorgan Chase Bank, N.A.",
        "This information is provided at the account holder's request for EFT enrollment.",
    )):
        c.drawString(56, 607 - index * 17, line)
    c.setFont("Times-Bold", 11)
    c.drawString(56, 518, "Account Details")
    c.setStrokeColor(colors.HexColor("#778A9D"))
    c.setLineWidth(0.7)
    c.line(56, 507, 556, 507)
    details = (
        ("Bank Name", "JPMorgan Chase Bank, N.A."),
        ("Account Type", "Commercial Checking"),
        ("Routing Number", "021000021"),
        ("Account Number", _form(packet).bank_account_number),
        ("Account Holder Name", packet.legal_name),
    )
    for index, (label, value) in enumerate(details):
        c.setFont("Times-Roman", 10.5)
        c.drawString(75, 482 - index * 29, f"{label}: {value}")
    c.setFont("Times-Roman", 11)
    c.drawString(56, 278, "Please contact Commercial Treasury Services if additional verification is required.")
    c.drawString(56, 245, "Sincerely,")
    c.setFont("Times-Italic", 19)
    c.drawString(56, 202, "Morgan Vale")
    c.setFont("Times-Roman", 10.5)
    c.drawString(56, 177, "Morgan Vale")
    c.drawString(56, 161, "Vice President, Commercial Treasury Services")
    _mark_reportlab(c)
    c.showPage()
    c.save()

    if packet.bank_swap:
        with fitz.open(path) as changed:
            page = changed[0]
            original = f"Account Holder Name: {packet.legal_name}"
            matches = page.search_for(original)
            if len(matches) != 1:
                raise RuntimeError(f"Cannot find original account holder in {path}")
            row = matches[0]
            page.add_redact_annot(row, fill=(1, 1, 1))
            page.apply_redactions()
            page.insert_text((row.x0, row.y1 - 2),
                             "Account Holder Name: ANDRE MAURICE TERRELL PERSONAL CHECKING",
                             fontname="cour", fontsize=9.6)
            page.insert_text((row.x0, row.y1 + 16), "Beneficiary:",
                             fontname="tiro", fontsize=10)
            page.insert_text((row.x0 + 69, row.y1 + 16), "ANDRE MAURICE TERRELL",
                             fontname="cour", fontsize=9.6)
            metadata = changed.metadata or {}
            metadata["producer"] = "iLovePDF"
            metadata["creator"] = "iLovePDF PDF Editor"
            changed.set_metadata(metadata)
            changed.saveIncr()


def _draw_table(
    c: canvas.Canvas, rows: list[list[str]], widths: list[float],
    top: float, row_heights: list[float], extra_style: list[tuple] | None = None,
) -> float:
    table = Table(rows, colWidths=widths, rowHeights=row_heights)
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#343434")),
        ("INNERGRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#555555")),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        *(extra_style or []),
    ]))
    _, height = table.wrapOn(c, sum(widths), sum(row_heights))
    table.drawOn(c, 48, top - height)
    return top - height


def _save_coi(packet: Packet, path: Path, today: date) -> None:
    c = canvas.Canvas(str(path), pagesize=letter, pageCompression=1)
    c.setTitle("Certificate of liability insurance")
    c.setFillColor(colors.HexColor("#1D2732"))
    c.setFont("Helvetica-Bold", 15)
    c.drawString(48, 744, "CERTIFICATE OF LIABILITY INSURANCE")
    c.setFont("Helvetica", 9)
    c.drawRightString(564, 744, today.strftime("%m/%d/%Y"))
    c.setLineWidth(1.3)
    c.line(48, 734, 564, 734)
    c.setFont("Helvetica", 7.7)
    c.drawString(48, 710, "This certificate is issued as a matter of information only and confers no rights upon the holder.")
    producer = "PRODUCER\nHarborview Risk Partners\n1800 Market Street, Suite 450\nPhiladelphia, PA 19103"
    insured = f"INSURED\n{packet.legal_name}\n{packet.street_address}\n{packet.city}, {packet.state} {packet.zip_code}"
    _draw_table(c, [[producer, insured]], [258, 258], 692, [90], [
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F4F6F8")),
    ])
    c.setFont("Helvetica-Bold", 8)
    c.drawString(48, 579, "COVERAGES")
    c.setFont("Helvetica", 7.4)
    c.drawString(48, 565, "The policies listed below have been issued to the insured named above.")
    effective = (today - timedelta(days=45)).strftime("%m/%d/%Y")
    expiry = (today + timedelta(days=365)).strftime("%m/%d/%Y")
    _draw_table(c, [
        ["TYPE OF INSURANCE", "POLICY NUMBER", "EFF DATE", "EXP DATE", "LIMITS"],
        ["Professional Liability", f"PL-{packet.number:04d}-88421", effective, expiry,
         "Each Claim  $1,000,000\nAggregate   $3,000,000"],
        ["Medical Specialty", packet.specialty, "", "", f"Code  {packet.specialty_code}"],
        ["General Liability", f"GL-{packet.number:04d}-52017", effective, expiry,
         "Each Occurrence  $1,000,000\nGeneral Aggregate  $3,000,000"],
    ], [150, 112, 66, 66, 122], 548, [31, 65, 46, 65], [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDE5EC")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7.2),
    ])
    c.setFont("Helvetica-Bold", 8)
    c.drawString(48, 321, "CERTIFICATE HOLDER")
    _draw_table(c, [["Healthcare Vendor Credentialing Department",
                     "CANCELLATION\nNotice will be delivered in accordance with policy provisions."]],
                [258, 258], 310, [69], [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F4F6F8")),
                ])
    c.setFont("Times-Italic", 17)
    c.drawString(316, 178, "Avery Collins")
    c.setFont("Helvetica", 8)
    c.drawString(316, 160, "AUTHORIZED REPRESENTATIVE")
    c.setFont("Helvetica", 7)
    c.drawString(48, 80, "Policy terms, exclusions, and conditions govern coverage shown above.")
    _mark_reportlab(c)
    c.showPage()
    c.save()


def _save_invoice(path: Path, packet: Packet, today: date) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(50, 733, "INVOICE")
    c.setFont("Helvetica", 10)
    c.drawString(50, 701, "North Atlantic Laboratory Supply")
    c.drawRightString(560, 701, f"INV-{today.year}-2047")
    c.drawRightString(560, 683, today.strftime("%B %d, %Y"))
    c.drawString(50, 634, f"Bill To: {packet.legal_name}")
    _draw_table(c, [["DESCRIPTION", "QTY", "AMOUNT"],
                    ["Laboratory equipment maintenance", "1", "$425.00"]],
                [360, 50, 106], 591, [33, 43], [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDE5EC")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ])
    c.drawString(50, 480, "Please attach your W-9 to the remittance paperwork.")
    _mark_reportlab(c)
    c.showPage()
    c.save()


def _require_templates() -> None:
    missing = [" or ".join(str(TEMPLATES / name) for name in options)
               for options in TEMPLATE_ALTERNATIVES
               if not any((TEMPLATES / name).is_file() for name in options)]
    if not (TEMPLATES / "blank_cp575.pdf").is_file() and not (TEMPLATES / "CP575 Notice.pdf").is_file():
        missing.append(str(TEMPLATES / "CP575 Notice.pdf"))
    if missing:
        raise FileNotFoundError(
            "Supply the three blank PDF templates before generation:\n" +
            "\n".join(missing)
        )


def generate_test_packets(output_dir: Path = PACKETS) -> None:
    """Generate the five scenarios, including a linked 5A/5B resubmission pair."""
    _require_templates()  # Fail before changing any existing packet.
    today = date.today()
    with tempfile.TemporaryDirectory() as scratch:
        temp = Path(scratch)
        cp_a = temp / "blank_cp575_a.pdf"
        cp_g = temp / "blank_cp575_g.pdf"
        clia = temp / "blank_clia.pdf"
        w9_template = TEMPLATES / "blank_w9.pdf"
        if not w9_template.is_file():
            w9_template = TEMPLATES / "blank_W9.pdf"
        with fitz.open(w9_template) as w9_document:
            ein_boxes = ein_cells(w9_document[0])
        blank_cp = TEMPLATES / "blank_cp575.pdf"
        if blank_cp.is_file():
            cp_a = cp_g = blank_cp
        else:
            _prepare_cp575(TEMPLATES / "CP575A.pdf", cp_a)
            _prepare_cp575(TEMPLATES / "CP575 Notice.pdf", cp_g)
        blank_clia = TEMPLATES / "blank_clia.pdf"
        if blank_clia.is_file():
            clia = blank_clia
        else:
            _prepare_clia(TEMPLATES / "CLIA-Certificate.pdf", clia)
        packet_rows = [(packet, f"case_{packet.number}") for packet in _packets()]
        pending = packet_rows[-1][0]
        packet_rows.append((pending, "case_5a"))
        packet_rows.append((replace(pending, w9_signed=True, wrong_cp_document=False,
                                    blurry_clia=False), "case_5b"))
        for packet, folder_name in packet_rows:
            folder = output_dir / folder_name
            folder.mkdir(parents=True, exist_ok=True)
            submission = _form(packet).model_dump(mode="json")
            if folder_name == "case_5b":
                submission["resubmission_of"] = "case_5a"
            (folder / "submission.json").write_text(json.dumps(submission, indent=2) + "\n",
                                                      encoding="utf-8")
            overlay_text_on_template(w9_template, folder / "w9_form.pdf",
                                     _w9_mapping(packet, today, ein_boxes))
            if packet.wrong_cp_document:
                _save_invoice(folder / "irs_cp575.pdf", packet, today)
            else:
                cp_mapping = _cp575_mapping(packet, today)
                if blank_cp.is_file():
                    cp_mapping["notice_code"] = OverlayText(514, 168, packet.cp_code, 9, "cour")
                overlay_text_on_template(cp_g if packet.cp_code.endswith("G") else cp_a,
                                         folder / "irs_cp575.pdf", cp_mapping)
            _save_bank_letter(packet, folder / "bank_eft_letter.pdf", today)
            overlaid = temp / f"clia_overlay_{packet.number}.pdf"
            overlay_text_on_template(clia, overlaid, _clia_mapping(packet, today))
            _save_scanned_clia(overlaid, folder / "clia_certificate.pdf",
                               blurry=packet.blurry_clia)
            _save_coi(packet, folder / "malpractice_coi.pdf", today)
    print(f"Generated five scenarios, including 5A and 5B, in {output_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate onboarding sample packets")
    parser.add_argument("--output-dir", type=Path, default=PACKETS)
    arguments = parser.parse_args()
    generate_test_packets(arguments.output_dir)
