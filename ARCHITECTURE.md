# Zamp PS-2: healthcare vendor onboarding

## Problem in plain language

A vendor submits one application and five PDFs: a W-9, IRS EIN notice, bank
letter, CLIA certificate, and malpractice certificate. The system reads what
each document actually says, compares the claims across the packet, checks
public government sources, and records every finding. It prepares a vendor
message and an internal reviewer summary. It must never approve a packet when
a required source cannot be verified.

The final decision uses this order: confirmed risk, then service eligibility,
then fixable or unresolved review items, then approval. A name-only match or a
PDF editing tool is a lead for review, not proof of fraud.

## One active request path

```text
React intake and reviewer UI
    -> Flask app.py (validate form, collect exactly five document slots)
    -> Stage 1: forensics.py + ocr_engine.py + openai_models.py + document_parsers.py
       native PDF text or locally rendered EasyOCR scan; classify and extract
    -> Stage 2: forensics.py
       PDF revision chain, metadata, and font evidence; flag corroborated edits
    -> Stage 3: decision_engine.py
       field presence, names, identifiers, entity type, CLIA scope, COI, dates
    -> Stage 4: decision_engine.py + credibility_and_decision.py
       live CMS NPPES with dated authentic cache; official HHS OIG LEIE CSV
       with candidate matches sent to identity review
    -> Stage 5: decision_engine.py
       strict precedence, vendor message, internal findings
    -> core.py (one SQLite audit schema and immutable input evidence hashes)
```

EasyOCR is the only OCR engine and runs locally. This project classifies clear
documents from their titles and structure, then asks OpenAI when the content is
ambiguous and marks that classification for review. `ZAMP_CLASSIFIER=openai`
asks OpenAI to classify every document. OpenAI suggests missing fields in bank
or IRS evidence; `ZAMP_MODEL_FIELDS=1` enables optional suggestions for other
documents. Deterministic parsers remain the default field path. Model values
must have an exact source quote and pass the field schema. OCR/native text
needed for the task is sent through the OpenAI Responses API with `store: false`;
PDF bytes are not sent. Missing model output is visible as an unresolved item.

For native PDFs, PyMuPDF reads each page's entire selectable text layer.
The document parsers then keep only the fields needed for the onboarding
decision; they do not claim to extract every sentence or table cell. Scanned
pages are rendered and read with EasyOCR. The W-9 EIN is a deliberate layout
exception: the parser reads one digit from each of the nine printed Part I
boxes, located from the form label and cell borders, so unrelated page digits
cannot be mistaken for the EIN.

## Source meaning

- NPPES proves that an NPI record exists and exposes its reported taxonomy,
  organization name, status, and authorized official. It does not prove CLIA
  licensure or ownership of a bank account.
- The HHS OIG LEIE full CSV is downloaded from the official source, validated,
  dated, and refreshed monthly. A matching name is a *candidate* until identity
  is confirmed through OIG's online verification process. The CSV does not
  contain SSNs or EINs.
- A PDF's contents are claims and evidence, not independent issuer
  authentication. CLIA, insurance, tax, and bank verification need their own
  authoritative confirmation before a real production approval.

## Five scenario acceptance paths

1. Quest: clean packet; all verified checks pass before approval.
2. BioReference: material bank revision and beneficiary mismatch cause risk
   rejection, with neutral vendor wording.
3. Allied: LEIE name candidate enters confidential identity review; only a
   confirmed exclusion causes risk rejection.
4. Apria: NPPES taxonomy, CLIA waiver, state, and COI scope conflict with the
   requested high-complexity lab service, causing eligibility rejection.
5. Sunrise: wrong IRS notice, unsigned W-9, and blurred scan produce a complete
   resubmission list; corrected 5B links to 5A and is reconsidered from scratch.

Quest's generated CLIA scan has median OCR confidence around 0.80. The current
0.75 gate, sharpness check, required-field check, and audited normalization of
the fixed `D` in its CLIA ID allow a live happy-path approval. Poor scans remain
pending. Sunrise also has a real LEIE namesake requiring confidential identity
review, regardless of any OCR improvement.

The generated packets are fixtures. Their PDF appearance does not establish
that a real bank, insurer, IRS office, or CMS issued them. Automated tests must
drive the Flask request path with controlled external responses; optional live
smoke checks separately report current network availability.

## File ownership

- `app.py`: HTTP boundary and one orchestration path.
- `forensics.py`: PDF routing and forensic evidence.
- `ocr_engine.py`: EasyOCR rendering, layout reconstruction, and OCR coordinates.
- `openai_models.py`: OpenAI classification, quoted field suggestions, and message drafts.
- `document_parsers.py`: deterministic field parsers for the five PDF types.
- `w9_layout.py`: locates the printed W-9 EIN boxes and reads their digits.
- `decision_engine.py`: cross-document rules and NPPES lookup.
- `credibility_and_decision.py`: LEIE lookup and final adjudication.
- `core.py`: validators and active SQLite audit persistence.
- `models.py`: input and extracted-field schemas.
- `generate_test_packets.py`: fixtures only; never updates the official LEIE.
- The historical `db.py` is excluded from the public submission; `core.py` is
  the active SQLite adapter.
- `tests/test_app_pipeline.py`: tests the active Flask path.
- `tests/test_ein_documents.py`: checks every printed EIN and swapped-document detection.

## Remaining production gates

Role-based access, protected source-document storage, reviewer confirmation
for LEIE candidates, independent CLIA/insurance/bank confirmation, durable
background processing, and delivery of vendor messages are separate gates.
The app must expose these as unresolved work rather than imply they were done.
