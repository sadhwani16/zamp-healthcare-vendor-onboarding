# Healthcare vendor onboarding, with evidence behind each decision

This is my submission for Zamp PS-2, **Vendor onboarding: from submission to approval**. I built a working review flow for a US healthcare vendor that submits an application and five supporting PDFs. The app reads the files, compares the claims across them, checks public healthcare sources, and returns a status with the reasons visible. The React interface shows each stage as it runs and keeps a history of decisions.

## Why I chose this problem

A vendor packet can look complete while its details disagree. A bank letter can name a different beneficiary. A laboratory can request high-complexity work while presenting a certificate of waiver. A clean company name can obscure a person who needs exclusion screening. When reviewers handle these files separately, they can miss the connections and spend time chasing corrections through email.

I checked the US sources before designing the workflow:

- [CMS measures both accuracy and timeliness in provider enrollment](https://www.cms.gov/medicare/medicare-contracting/medicare-administrative-contractors/qasp-provider-enrollment). Its [payment review manual](https://www.cms.gov/files/document/perm-manual.pdf-2) explicitly checks provider names, NPIs, licenses, CLIA certification, and federal sanctions. That gave me a concrete basis for the fields to compare, although payment review is a different workflow from this onboarding prototype.
- [HHS OIG explains that federal healthcare programs generally cannot pay for items or services furnished, ordered, or prescribed by an excluded person or entity](https://oig.hhs.gov/exclusions/online.asp). I therefore screen people named in the packet as well as the business. A name match still needs identity confirmation.
- [CMS says a CLIA certificate must match the highest complexity of testing a laboratory performs](https://www.cms.gov/medicare/health-safety-standards/clinical-laboratory-improvement-amendments-clia/getting-started-clia/apply-clia-certificate). I compare the requested service with the certificate type instead of accepting any CLIA PDF.
- Payment redirection has a large cost beyond healthcare. The [FBI recorded about $3 billion in reported business email compromise losses in 2025](https://www.ic3.gov/Outreach/Brochures/ic3-brochure.pdf) and [warns about changes to bank details in transfer requests](https://www.ic3.gov/PSA/2024/PSA240911). That figure covers business email compromise across industries; it is **not** an estimate of losses from healthcare onboarding.

The Zamp brief calls for a live process, a clear status, and visible reasoning. I used the research to choose checks with practical consequences, then made the packet scenarios exercise different paths through the same code.

## What I built

The input is one vendor application and five PDF slots. The included packets keep the application in submission.json; the API also accepts an uploaded submission and PDFs.

| Evidence | Fields I use |
| --- | --- |
| W-9 | Legal name, DBA, tax classification, EIN, signature, and date |
| IRS EIN evidence | CP-575 or 147C notice type, legal name, EIN, and name control when printed |
| Bank evidence | Bank, account holder, account type, routing number, and full account number |
| CLIA certificate | Facility, CLIA ID, state, director, certificate type, and expiration |
| Insurance COI | Insured name, specialty, policy dates, and coverage limits |

The bank slot can accept a bank letter, statement, voided check, or account confirmation when the needed details are readable. I keep the source files visible in the UI beside the extracted fields. The audit stores the checks, source-file hashes, status, and a link to the earlier run for a resubmission. Selecting a case supplies its documents; it does not select a prewritten decision.

## How the pipeline works

~~~
Application + five PDFs
    -> 1. Classify and extract
    -> 2. Inspect PDFs
    -> 3. Compare documents
    -> 4. Check external sources
    -> 5. Decide and save the audit
~~~

**1. Classify and extract.** PyMuPDF reads selectable text from every page of a digital PDF. For an image-only page, I render it and use EasyOCR locally. The app checks whether each document belongs in its upload slot and extracts the fields needed for the decision. It does not claim to capture every sentence or table cell. The W-9 EIN has a layout reader for its nine printed boxes, so unrelated page digits cannot be mistaken for the EIN. For ambiguous documents or missing fields in varied bank and IRS layouts, an optional OpenAI text model can suggest values. The bank/IRS fallback accepts a suggestion only when a verbatim source quote and the field schema support it. The current app sends extracted text, not page images, to the model; targeted vision review is not implemented. Missing or unreadable evidence remains open for review.

**2. Inspect PDFs.** I follow the PDF revision chain and compare earlier text with the final text. I also record Producer/Creator metadata and font differences around sensitive labels. A material change to payment or tax evidence can become a risk finding. Editor metadata and font differences alone are review signals, not proof of fraud. Scanned pages also have OCR confidence and sharpness checks.

**3. Compare documents.** I check the application against the W-9, IRS evidence, bank evidence, CLIA certificate, and COI. The rules cover names and DBA, EIN consistency and format, NPI check digit, routing checksum, bank account details, W-9 signature, CLIA state and test scope, insurance specialty and limits, and expiration dates. An unreadable field is recorded as a gap rather than an assumed match. The pipeline continues after a finding so the vendor can receive a complete correction list.

**4. Check external sources.** I query the live [CMS NPPES registry](https://npiregistry.cms.hhs.gov/api/) for NPI status, organization type, taxonomy, reported names, and authorized official. If the live service fails, the app can use a dated response fetched earlier from that API for up to 30 days. Without either source, verification remains pending. I also screen the company, owners, W-9 signer, CLIA director, and NPPES official against the full [HHS OIG List of Excluded Individuals/Entities](https://oig.hhs.gov/exclusions/download.asp). The bundled CSV has a source date and SHA-256 record and must be refreshed as OIG updates it. A name-only hit is a confidential identity-review candidate, not an automatic exclusion decision. [CMS also notes that an NPI does not establish licensure or credentialing](https://download.cms.gov/nppes/NPI_Files.html).

**5. Decide and audit.** I use a fixed order, independent of a risk score:

1. Any FAIL_RISK -> REJECTED: RISK. The vendor receives neutral wording; the internal alert contains the risk details.
2. Otherwise, any FAIL_ELIGIBILITY -> REJECTED: NOT ELIGIBLE, with the service or scope gap explained.
3. Otherwise, any WARN_FIXABLE -> PENDING, with a resubmission list or an internal verification task.
4. Otherwise -> APPROVED.

Flask streams document and check events to the React UI, then saves the run and its checks to SQLite. The dashboard shows history and linked resubmissions. Messages are **prepared** in the result; this prototype does not send email.

## Five packet scenarios

I included one clean path and four edge paths. Case 5 has an initial submission and a corrected resubmission.

| Case | What I put in the packet | What the pipeline surfaces |
| --- | --- | --- |
| **1. Quest Diagnostics — clean path** | A consistent application, W-9, IRS notice, bank evidence, scanned CLIA certificate, and lab COI. The current fixture uses organization NPI 1780620526. | Native text and scanned OCR both work; cross-document and external checks support APPROVED when current sources are available. |
| **2. BioReference — changed bank evidence** | The bank PDF has an incremental save that replaces the account-holder text with a personal beneficiary and records iLovePDF metadata. | Stage 2 shows the earlier and final text. Stage 3 finds the payment identity conflict. The decision is REJECTED: RISK, with a neutral vendor message and a detailed internal finding. |
| **3. Allied Clinical — exclusion name candidate** | A disclosed owner and the scanned CLIA director are named Jacob Lee. | The official LEIE download has a name candidate. The run remains PENDING for confidential identity review; the app does not label a person excluded from a name alone. |
| **4. Apria Healthcare — service scope conflict** | The application requests high-complexity lab work, but the CLIA PDF says waiver and has a different state prefix; the COI specialty and NPPES taxonomy also conflict with the request. | REJECTED: NOT ELIGIBLE, with the service and license gaps visible in the checks. |
| **5. Sunrise Medical — fix and resubmit** | In 5A, the IRS slot contains an invoice, the W-9 is unsigned, and the CLIA scan is blurred. In 5B, those document issues are corrected. | 5A is PENDING with a punch list. 5B links to 5A and is evaluated again. An unrelated LEIE name candidate in the current official data can keep 5B pending until a reviewer resolves identity; the controlled pipeline test clears that candidate and reaches APPROVED. |

I also test a W-9 swap that creates an EIN mismatch. The EIN comes from the PDF's printed boxes, not from the application JSON. This gives the tax consistency rule a separate negative test without misrepresenting one of the five packets.

The packet PDFs are generated **sample fixtures**, visibly marked as unsuitable for payment or credentialing. Their corporate styling and the use of real NPPES records do not mean a bank, insurer, the IRS, or CMS issued the documents.

## Run it locally

On Windows, from the repository root:

~~~powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
~~~

In a second terminal:

~~~powershell
npm ci
npm run dev
~~~

Open http://127.0.0.1:5173. Flask runs at http://127.0.0.1:5000. The included packets and reference-data snapshot let you start without regenerating files. EasyOCR may download its model on first use. The default classifier uses local content rules for clear files.

An OpenAI key is optional for ambiguous text classification, missing bank/IRS fields, and nonapproval message drafting. Set it in the **Flask terminal** before starting the server; never put it in the React app:

~~~powershell
$env:OPENAI_API_KEY = "your-key"
python app.py
~~~

The default text model is configured in openai_models.py; ZAMP_OPENAI_MODEL can override it. ZAMP_CLASSIFIER=openai asks the model to classify every document. ZAMP_MODEL_FIELDS=1 enables optional suggestions for other document types, while deterministic checks still decide the result.

Refresh the official reference data before a live presentation, especially if the bundled snapshot has aged:

~~~powershell
python refresh_reference_data.py --oig --packet-npis
~~~

The refresh needs network access. The OIG CSV must pass its source-date and hash checks; an unavailable or outdated source cannot silently count as a pass. The repository includes three blank PDF layouts under templates/ so you can regenerate the packet fixtures with python generate_test_packets.py --output-dir data/packets_current.

For a five-minute walkthrough, I open **Case 1**, preview the W-9 and scanned CLIA page, run the five stages, and show the approval. I then open **Case 2** and focus on the bank PDF revision difference, payment mismatch, and separate vendor/internal messages. The dashboard shows both saved runs. Case 5A/5B is useful if there is time to show the correction loop.

## Project map and checks

- app.py is the Flask API and single active orchestration path.
- forensics.py, ocr_engine.py, document_parsers.py, and w9_layout.py read and inspect the PDFs.
- decision_engine.py and credibility_and_decision.py compare evidence, query NPPES/LEIE, and decide status.
- core.py holds validators and the active SQLite audit store; models.py holds Pydantic schemas.
- Frontend.jsx renders the packet preview, live stage results, full check disclosure, and dashboard.
- generate_test_packets.py builds the sample packets; refresh_reference_data.py fetches official reference data.

The active audit store is core.py. [ARCHITECTURE.md](ARCHITECTURE.md) and [CODEBASE.md](CODEBASE.md) give the deeper implementation map.

~~~powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
npm run build
~~~

The scenario tests control OCR and external responses so the expected decisions are repeatable. Separate tests exercise printed EIN extraction and official reference-data/cache behavior. They do not measure field accuracy across arbitrary real-world documents.

## What remains for production

This is a runnable decision-support prototype, not an issuer-authentication or credentialing service. NPPES validates a reported registry record, not a bank account or CLIA license. LEIE name candidates need human identity confirmation. Real use would also require independent bank, IRS, CLIA, and insurer confirmation; protected document storage and access control; durable background processing; and a reviewed email-delivery workflow. I keep these boundaries explicit so a clear-looking PDF or a model-suggested field is never presented as independent proof.
