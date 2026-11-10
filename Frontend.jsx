import React, { useEffect, useMemo, useState } from "react";

const API = window.__ZAMP_API_BASE_URL__ || "http://127.0.0.1:5000";
const STAGES = [
  ["Stage 1", "Classify & extract", "PDF type, OCR, document type, fields"],
  ["Stage 2", "PDF forensics", "Revisions, metadata, fonts"],
  ["Stage 3", "Cross-document", "Names, IDs, license, coverage"],
  ["Stage 4", "External sources", "CMS NPPES and OIG screening"],
  ["Stage 5", "Decision", "Strict precedence and messages"],
];
const SLOTS = {
  w9: "W-9 tax form", cp575: "IRS EIN evidence (CP-575 or 147C)", bank_letter: "Bank account evidence",
  clia_certificate: "CLIA certificate", coi_insurance: "Malpractice COI",
};
const KEY_FIELDS = {
  w9: ["legal_name_line1", "dba_name_line2", "tax_classification", "tin_ein", "is_signed"],
  cp575: ["notice_code", "legal_name_line1", "dba_name_line2", "ein", "name_control"],
  bank_letter: ["evidence_type", "bank_name", "account_holder_name", "account_type", "routing_number", "account_number"],
  clia_certificate: ["clia_id", "facility_name", "state", "certificate_type", "lab_director", "expiration_date"],
  coi_insurance: ["insured_name", "medical_specialty", "each_claim_limit", "aggregate_limit", "expiration_date"],
};
const FIELD_LABELS = {
  tin_ein: "EIN", ein: "EIN", clia_id: "CLIA ID", dba_name_line2: "DBA (Line 2)",
  legal_name_line1: "Legal name (Line 1)", is_signed: "Signed", each_claim_limit: "Each claim limit",
  aggregate_limit: "Aggregate limit", routing_number: "Routing number", account_number: "Account number",
};
const BADGE_LABELS = {
  PASS: "Verified", WARN_FIXABLE: "Review", FAIL_ELIGIBILITY: "Ineligible", FAIL_RISK: "Risk",
};
const FORM_SECTIONS = [
  ["Business identity", "company_name", "dba_name", "country", "physical_address", "city", "state", "zip_code"],
  ["Registration & service", "npi_number", "tin_ein", "vendor_category", "tax_classification", "claimed_taxonomy_code", "requested_complexity"],
  ["Banking & contact", "bank_routing_number", "bank_account_number", "contact_email"],
  ["People & disclosure", "authorized_official", "owners_5_percent", "prior_sanctions_disclosed"],
];
const CUSTOM_REQUIRED = ["company_name", "physical_address", "city", "state", "zip_code", "npi_number",
  "tin_ein", "vendor_category", "claimed_taxonomy_code", "requested_complexity",
  "bank_routing_number", "bank_account_number", "contact_email", "authorized_official"];
const EMPTY_CUSTOM_FORM = { country: "US", requested_complexity: "High-Complexity Lab",
  prior_sanctions_disclosed: false, owners_5_percent: "" };
const CUSTOM_DOCUMENTS = Object.keys(SLOTS).map((slot) => ({
  slot, filename: "No PDF selected", present: false, pages: 0,
}));
const RANK = { PASS: 0, WARN_FIXABLE: 1, FAIL_ELIGIBILITY: 2, FAIL_RISK: 3 };
const DEFAULT_CASES = [
  ["case_1", "Quest Diagnostics — Clean packet"], ["case_2", "BioReference — Bank revision"],
  ["case_3", "Allied Clinical — Exclusion match"], ["case_4", "Apria Healthcare — Scope conflict"],
  ["case_5a", "Sunrise Medical — Initial submission"], ["case_5b", "Sunrise Medical — Resubmission"],
].map(([id, label]) => ({ id, label, available: true }));

const url = (path) => API + path;
const human = (value) => String(value || "").replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
function shown(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "None";
  return String(value);
}
function palette(value) {
  if (value === "FAIL_RISK" || value === "REJECTED: RISK") return "border-errorrule bg-errorbg text-errorink";
  if (value === "FAIL_ELIGIBILITY" || value === "REJECTED: NOT ELIGIBLE") return "border-warningrule bg-warningbg text-warningink";
  if (value === "WARN_FIXABLE" || value === "PENDING") return "border-warningrule bg-warningbg text-warningink";
  if (value === "PASS" || value === "APPROVED") return "border-successrule bg-successbg text-successink";
  return "border-rule bg-paper text-ink2";
}
async function api(path, options) {
  const response = await fetch(url(path), options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || "Request failed (" + response.status + ")");
  return body;
}
function Badge({ value, children }) {
  return <span className={"hm-badge " + palette(value)}>{children || BADGE_LABELS[value] || human(value)}</span>;
}
function Panel({ title, note, children, action }) {
  return <section className="hm-panel">
    <div className="hm-panel__head">
      <div><h3>{title}</h3>{note && <p>{note}</p>}</div>{action}
    </div>{children}
  </section>;
}
function CheckList({ checks }) {
  if (!checks.length) return <p className="py-3 text-sm text-muted">No checks recorded yet.</p>;
  return <div className="hm-checklist">{checks.map((check, index) =>
    <details key={(check.check_name || "check") + index} className="group">
      <summary className="text-sm">
        <span className="min-w-0"><span className="font-semibold">{check.check_name}</span>
          <span className="ml-2 text-xs text-muted">{check.evidence_source || "Packet"}</span></span>
        <Badge value={check.outcome || check.status} />
      </summary>
      <div className="mt-3 text-sm leading-6 text-ink2">
        <p>{check.reasoning || "No additional explanation recorded."}</p>
        {(check.expected !== null && check.expected !== undefined || check.actual !== null && check.actual !== undefined) &&
          <dl className="mt-3 grid gap-3 border-t border-rule pt-3 text-xs sm:grid-cols-2">
            <div><dt className="font-bold text-muted">Expected</dt><dd className="mt-1 break-words">{shown(check.expected)}</dd></div>
            <div><dt className="font-bold text-muted">Observed</dt><dd className="mt-1 break-words">{shown(check.actual)}</dd></div>
          </dl>}
      </div>
    </details>)}</div>;
}
function AuditDisclosure({ checks, title = "View all checks" }) {
  return <details className="hm-disclosure">
    <summary><span>{title}</span><span className="text-xs text-muted">{checks.length} checks</span></summary>
    <div className="hm-disclosure__body"><CheckList checks={checks} /></div>
  </details>;
}
function Findings({ checks, empty = "No decision-driving findings in this stage.", ready = true }) {
  if (!ready) return <p className="rounded-xl border border-dashed border-rule bg-paper p-4 text-sm text-muted">Waiting for this evidence to be checked.</p>;
  const findings = checks.filter((item) => item.outcome && item.outcome !== "PASS");
  if (!findings.length) return <p className="rounded-xl border border-successrule bg-successbg p-4 text-sm text-successink">{empty}</p>;
  return <CheckList checks={findings} />;
}
function DocumentQueue({ selected, onSelect, documents, packet, run, checks, stage }) {
  return <Panel title="Document queue" note="Select one file to inspect its evidence">
    <div className="hm-queue">{Object.keys(SLOTS).map((slot, index) => {
      const doc = documents[slot];
      const file = packet?.documents?.find((item) => item.slot === slot);
      const missing = (run?.input_documents?.[slot]?.present ?? file?.present) === false;
      const related = checks.filter((item) => item.stage === stage && item.evidence_source === slot);
      const finding = related.find((item) => item.outcome !== "PASS");
      const ready = stage === "Stage 1" ? Boolean(doc?.classification || doc?.forensics || related.length) : doc?.forensics?.revision_count !== undefined;
      return <button key={slot} type="button" aria-current={selected === slot} onClick={() => onSelect(slot)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left">
        <span className="min-w-0"><span className="mr-2 text-xs font-bold text-muted">0{index + 1}</span>
          <strong className="text-sm">{SLOTS[slot]}</strong>
          <span className="mt-1 block truncate pl-6 text-xs text-muted">{run?.input_documents?.[slot]?.filename || file?.filename || slot}</span></span>
        <Badge value={finding?.outcome || (missing ? "WARN_FIXABLE" : ready ? "PASS" : null)}>
          {missing ? "Missing" : finding ? "Review" : ready ? "Read" : "Queued"}</Badge>
      </button>;
    })}</div>
  </Panel>;
}
function Fields({ fields, keys = Object.keys(fields || {}) }) {
  if (!fields) return <p className="px-5 py-5 text-sm text-muted">No structured fields available.</p>;
  return <dl className="grid px-5 sm:grid-cols-2">{keys.map((name) =>
    <div key={name} className="min-w-0 border-b border-rule py-3 pr-4">
      <dt className="text-xs font-semibold text-muted">{FIELD_LABELS[name] || human(name)}</dt>
      <dd className="mt-1 break-words text-sm font-medium">{name.endsWith("_limit") && typeof fields[name] === "number" ? "$" + fields[name].toLocaleString() : shown(fields[name])}</dd>
    </div>)}</dl>;
}
function StageStrip({ states, checks, run, selected, onSelect }) {
  return <nav className="stage-rail" aria-label="Verification stages">{STAGES.map(([id, title, note], index) => {
    const evidence = checks.filter((item) => item.stage === id);
    const worst = evidence.reduce((result, item) => RANK[item.outcome] > RANK[result] ? item.outcome : result, "PASS");
    const status = states[id] || "queued";
    const outcome = status === "complete" ? (id === "Stage 5" ? run?.final_status : evidence.length ? worst : "PASS") : null;
    return <button type="button" key={id} aria-current={selected === id ? "step" : undefined} onClick={() => onSelect(id)}>
      <span className="stage-rail__number">{String(index + 1).padStart(2, "0")}</span>
      <span className="stage-rail__label">{title}<span className="block pt-1 text-xs font-normal text-muted">{note}</span></span>
      <span className="stage-rail__state" data-outcome={outcome || status}>{outcome ? BADGE_LABELS[outcome] || outcome : status === "working" ? "In progress" : "Queued"}</span>
    </button>;
  })}</nav>;
}
function DocumentCard({ slot, document, forensic = false, checks = [] }) {
  const value = document?.forensics;
  const modelExtraction = checks.find((item) => item.check_name === "OpenAI evidence extraction" && item.evidence_source === slot);
  return <Panel title={SLOTS[slot]} note={forensic ? "Signals calculated from this PDF's bytes" : "Classification and fields used by verification"}
    action={document?.classification && !forensic ? <Badge value={document.classification === slot ? "PASS" : "WARN_FIXABLE"}>{SLOTS[document.classification] || human(document.classification)}</Badge> : null}>
    {forensic ? value?.revision_count !== undefined ? <>
      <div className="hm-signal-grid">
        <div><p>Saved revisions</p>
          <p className="mt-2 text-sm font-bold">{shown(value.revision_count)} version(s)</p>
          <p className="mt-1 text-xs text-ink2">{value.revision_diffs?.length || 0} changed text line(s)</p></div>
        <div><p>Producer / Creator</p>
          <p className="mt-2 break-words text-sm font-bold">{shown(value.producer)}</p>
          <p className="mt-1 break-words text-xs text-ink2">{shown(value.creator)}</p></div>
        <div><p>Mixed fonts</p>
          <p className="mt-2 text-sm font-bold">{value.font_anomalies?.length || 0} flagged line(s)</p>
          <p className="mt-1 text-xs text-ink2">Label/value span comparison</p></div>
      </div>
      {value.is_scanned_pdf && <p className="border-t border-rule px-5 py-3 text-xs text-ink2">Image-only scan. Text-layer revision and font checks do not assess edits inside the image. Sharpness: {shown(value.sharpness)}.</p>}
      {(value.revision_diffs?.length > 0 || value.font_anomalies?.length > 0) &&
        <div className="space-y-2 border-t border-rule p-4 font-figures text-xs text-errorink">
          {value.revision_diffs?.map((text, i) => <p key={"r" + i} className="rounded bg-errorbg p-2">{text}</p>)}
          {value.font_anomalies?.map((text, i) => <p key={"f" + i} className="rounded bg-warningbg p-2">{text}</p>)}
        </div>}
    </> : <p className="px-5 py-5 text-sm text-muted">Waiting for PDF inspection.</p> :
      <>
        <div className="flex flex-wrap gap-3 border-b border-rule px-5 py-3 text-xs text-ink2">
          <span><b>Text source:</b> {value ? human(value.text_layer || (value.is_scanned_pdf ? "scanned" : "native")) : "Waiting"}</span>
          <span><b>Classifier:</b> {document?.classification_source || "Waiting"}</span>
          {modelExtraction && <span><b>Model field fallback:</b> {modelExtraction.outcome === "PASS" ? "Quoted fields accepted" : "Needs review"}</span>}
          {value?.ocr_engine && <span><b>EasyOCR confidence:</b> {shown(value.ocr_confidence?.toFixed(2))}</span>}
        </div>
        <Fields fields={document?.extracted} keys={KEY_FIELDS[slot]} />
        {document?.extracted && <details className="border-t border-rule px-5 py-3"><summary className="cursor-pointer text-xs font-bold text-accent2">View every extracted field</summary><Fields fields={document.extracted} /></details>}
      </>}
  </Panel>;
}
function ProcessTrace({ activity, busy, checks, documents, states }) {
  const completed = STAGES.filter(([id]) => states[id] === "complete").length;
  const recentChecks = checks.slice(-3).reverse().map((item, index) => <div key={checks.length + "-" + index} className="flex items-center justify-between gap-3 text-xs">
    <span className="truncate"><b className="mr-2 text-muted">#{checks.length - index}</b>{item.check_name}</span><Badge value={item.outcome} /></div>);
  return <section aria-live="polite" className="process-trace">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h3 className="text-sm font-semibold">Live activity</h3>
        <p className="mt-1 text-sm text-ink2">{busy ? activity || "Reading packet" : completed ? "Verification recorded" : "Ready to verify"}</p></div>
      <div className="process-trace__metrics flex gap-4 text-xs text-ink2"><span>{completed}/5 stages</span><span>{Object.values(documents).filter((item) => item?.forensics || item?.classification).length}/5 files read</span><span>{checks.length} checks</span></div>
    </div>
    <div className="process-trace__bar mt-4"><span style={{ transform: "scaleX(" + (completed / 5) + ")" }} /></div>
    {checks.length > 0 && (busy ? <div className="mt-4 space-y-2 border-t border-rule pt-4">
      <p className="text-xs font-semibold text-muted">Latest recorded checks</p>{recentChecks}
    </div> : <details className="process-trace__recent mt-4 border-t border-rule pt-3">
      <summary className="cursor-pointer text-xs font-semibold text-accent2">Latest recorded checks</summary>
      <div className="mt-3 space-y-2">{recentChecks}</div>
    </details>)}
  </section>;
}
function FocusPreview({ slot, imageUrl, pdfUrl, pageCount, page, onPage, unavailableReason }) {
  return <Panel title="Source PDF" note={unavailableReason || "The page supplied in this packet"}
    action={pdfUrl && <a href={pdfUrl} target="_blank" rel="noreferrer" className="hm-link">Open PDF</a>}>
    {pageCount > 1 && <div className="flex items-center justify-end gap-2 border-b border-rule px-5 py-2 text-xs">
      <button type="button" disabled={!page} onClick={() => onPage(page - 1)} className="rounded border px-2 py-1 disabled:opacity-40">Previous</button>
      <span>Page {page + 1} of {pageCount}</span>
      <button type="button" disabled={page + 1 >= pageCount} onClick={() => onPage(page + 1)} className="rounded border px-2 py-1 disabled:opacity-40">Next</button>
    </div>}
    <div className="doc-viewport">
      {imageUrl ? <img src={imageUrl} alt={SLOTS[slot] + " page " + (page + 1)} /> :
        <p className="px-5 text-center text-sm text-muted">{unavailableReason || "No preview available for this slot."}</p>}
    </div>
  </Panel>;
}
function CrossGroup({ title, note, checks }) {
  const issues = checks.filter((item) => item.outcome !== "PASS");
  const passes = checks.filter((item) => item.outcome === "PASS");
  const worst = checks.reduce((status, item) => (RANK[item.outcome] || 0) > (RANK[status] || 0) ? item.outcome : status, "PASS");
  return <Panel title={title} note={note} action={<Badge value={checks.length ? worst : null}>{issues.length ? issues.length + " finding(s)" : passes.length ? passes.length + " passed" : "Waiting"}</Badge>}>
    <div className="p-5">
      {issues.length ? <CheckList checks={issues} /> : passes.length ? <div className="space-y-2">
        {passes.slice(0, 3).map((item, index) => <div key={index} className="flex items-center justify-between gap-3 text-sm"><span>{item.check_name}</span><Badge value="PASS" /></div>)}
        {passes.length > 3 && <p className="text-xs text-muted">{passes.length - 3} routine pass(es) in the full audit below.</p>}</div> :
        <p className="text-sm text-muted">Waiting for this group of checks.</p>}
    </div>
  </Panel>;
}
function crossGroup(name) {
  if (/bank|routing/i.test(name)) return "payment";
  if (/CLIA|COI|expiration/i.test(name)) return "scope";
  return "identity";
}

export default function Frontend() {
  const [tab, setTab] = useState("live");
  const [liveView, setLiveView] = useState(() => new URLSearchParams(window.location.search).get("view") === "verification" ? "verification" : "packet");
  const [historicalRun, setHistoricalRun] = useState(false);
  const [cases, setCases] = useState(DEFAULT_CASES);
  const [selectedCase, setSelectedCase] = useState("case_1");
  const [fixturePacket, setPacket] = useState(null);
  const [customForm, setCustomForm] = useState(EMPTY_CUSTOM_FORM);
  const [selectedSlot, setSelectedSlot] = useState("w9");
  const [overrides, setOverrides] = useState({});
  const [blobUrl, setBlobUrl] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [previewPage, setPreviewPage] = useState(0);
  const [overridePages, setOverridePages] = useState({});
  const [runs, setRuns] = useState([]);
  const [run, setRun] = useState(null);
  const [docs, setDocs] = useState({});
  const [checks, setChecks] = useState([]);
  const [states, setStates] = useState({});
  const [stage, setStage] = useState("Stage 1");
  const [activity, setActivity] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [registry, setRegistry] = useState(null);
  const [registryBusy, setRegistryBusy] = useState(false);
  const isCustom = selectedCase === "custom";
  const packet = isCustom ? { submission: customForm, documents: CUSTOM_DOCUMENTS.map((file) => ({
    ...file, present: Boolean(overrides[file.slot]), filename: overrides[file.slot]?.name || file.filename,
    bytes: overrides[file.slot]?.size || 0, pages: overridePages[file.slot] || 0,
  })) } : fixturePacket;
  const currentChecks = run?.checks || checks;
  const currentDocs = run?.documents || docs;
  const currentStageChecks = currentChecks.filter((item) => item.stage === stage);
  const selectedFile = packet?.documents?.find((item) => item.slot === selectedSlot);
  const historicalSourceUnavailable = historicalRun && run &&
    (!selectedFile?.sha256 || selectedFile.sha256 !== run.input_documents?.[selectedSlot]?.sha256);
  const pdfUrl = blobUrl || (!isCustom && selectedFile?.present ? url(selectedFile.url) : null);
  const pageCount = overridePages[selectedSlot] || selectedFile?.pages || 0;
  const imageUrl = overrides[selectedSlot] ? previewUrl :
    !isCustom && selectedFile?.present ? url(selectedFile.url + "/preview?page=" + previewPage) : null;
  const parent = useMemo(() => runs.find((item) => item.case === "case_5a" && item.final_status === "PENDING"), [runs]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api("/api/cases"), api("/api/history?limit=100")])
      .then(([list, history]) => { if (!cancelled) { setCases(list); setRuns(history.runs || []); } })
      .catch((cause) => { if (!cancelled) setError(cause.message); });
    return () => { cancelled = true; };
  }, []);
  useEffect(() => {
    let cancelled = false;
    setPacket(null);
    setRegistry(null);
    if (selectedCase === "custom") return () => { cancelled = true; };
    api("/api/cases/" + selectedCase + "/packet")
      .then((value) => { if (!cancelled) setPacket(value); })
      .catch((cause) => { if (!cancelled) setError(cause.message); });
    return () => { cancelled = true; };
  }, [selectedCase]);
  useEffect(() => {
    const file = overrides[selectedSlot];
    if (!file) { setBlobUrl(null); setPreviewUrl(null); return undefined; }
    const objectUrl = URL.createObjectURL(file);
    setBlobUrl(objectUrl);
    setPreviewUrl(null);
    const controller = new AbortController();
    let imageObjectUrl = null;
    const body = new FormData();
    body.append("pdf", file);
    fetch(url("/api/preview-document?page=" + previewPage), {
      method: "POST", body, signal: controller.signal,
    }).then(async (response) => {
      if (!response.ok) {
        const value = await response.json().catch(() => ({}));
        throw new Error(value.error || "Could not preview the replacement PDF");
      }
      setOverridePages((old) => ({ ...old, [selectedSlot]: Number(response.headers.get("X-PDF-Pages")) || 1 }));
      return response.blob();
    }).then((blob) => {
      if (!controller.signal.aborted) {
        imageObjectUrl = URL.createObjectURL(blob);
        setPreviewUrl(imageObjectUrl);
      }
    }).catch((cause) => {
      if (cause.name !== "AbortError") setError(cause.message);
    });
    return () => {
      controller.abort();
      URL.revokeObjectURL(objectUrl);
      if (imageObjectUrl) URL.revokeObjectURL(imageObjectUrl);
    };
  }, [overrides, selectedSlot, previewPage]);

  function changeCase(id) {
    setSelectedCase(id); setSelectedSlot("w9"); setOverrides({});
    setPreviewPage(0); setOverridePages({});
    setRun(null); setDocs({}); setChecks([]); setStates({}); setStage("Stage 1"); setLiveView("packet"); setHistoricalRun(false); setError("");
  }
  function handleEvent(event) {
    if (event.type === "stage_started") {
      setStates((old) => ({ ...old, [event.stage]: "working" }));
      setStage(event.stage);
      setActivity(STAGES.find((item) => item[0] === event.stage)?.[1] || event.stage);
    } else if (event.type === "stage_completed") {
      setStates((old) => ({ ...old, [event.stage]: "complete" }));
    } else if (event.type === "document_started") {
      setActivity("Processing " + SLOTS[event.slot]);
    } else if (event.type === "document_completed") {
      setDocs((old) => ({ ...old, [event.slot]: event.document }));
    } else if (event.type === "forensics_completed") {
      setDocs((old) => ({ ...old, [event.slot]: {
        ...old[event.slot], forensics: event.forensics,
      } }));
    } else if (event.type === "check") {
      setChecks((old) => [...old, event.check]);
      setActivity(event.check.check_name + " — " + human(event.check.outcome));
    } else if (event.type === "lookup_started") {
      setActivity("Checking " + event.source);
    } else if (event.type === "final") {
      setRun(event.run); setDocs(event.run.documents || {}); setChecks(event.run.checks || []);
      setActivity("Decision recorded");
    } else if (event.type === "error") {
      throw new Error(event.error || "Verification failed");
    }
  }
  async function refreshHistory() {
    const history = await api("/api/history?limit=100");
    setRuns(history.runs || []);
  }
  async function runSelected() {
    if (!packet || busy) return;
    if (isCustom) {
      const missing = CUSTOM_REQUIRED.filter((field) => !String(customForm[field] ?? "").trim());
      if (missing.length) { setError("Complete the required form fields: " + missing.map(human).join(", ")); return; }
      const missingFiles = Object.keys(SLOTS).filter((slot) => !overrides[slot]);
      if (missingFiles.length) { setError("Upload all five PDFs before verification: " + missingFiles.map((slot) => SLOTS[slot]).join(", ")); return; }
    }
    setBusy(true); setError(""); setRun(null); setDocs({}); setChecks([]);
    setHistoricalRun(false);
    setStates({}); setStage("Stage 1"); setLiveView("verification"); setActivity("Opening packet");
    try {
      let options;
      if (isCustom || Object.keys(overrides).length) {
        const body = new FormData();
        if (isCustom) {
          body.append("submission", JSON.stringify({ ...customForm,
            owners_5_percent: String(customForm.owners_5_percent || "").split(/[,\n]/).map((name) => name.trim()).filter(Boolean),
          }));
        } else body.append("case", selectedCase);
        if (selectedCase === "case_5b" && parent) body.append("parent_run_id", parent.run_id);
        Object.entries(overrides).forEach(([slot, file]) => body.append(slot, file));
        options = { method: "POST", body };
      } else {
        options = { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ case: selectedCase,
            parent_run_id: selectedCase === "case_5b" ? parent?.run_id : null }) };
      }
      const response = await fetch(url("/api/run/stream"), options);
      if (!response.ok) {
        const value = await response.json().catch(() => ({}));
        throw new Error(value.error || "Request failed (" + response.status + ")");
      }
      if (!response.body) throw new Error("Streaming responses are unavailable in this browser.");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let finished = false;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (!line.trim()) continue;
          const event = JSON.parse(line);
          handleEvent(event);
          if (event.type === "final") finished = true;
        }
      }
      if (buffer.trim()) {
        const event = JSON.parse(buffer);
        handleEvent(event);
        if (event.type === "final") finished = true;
      }
      if (!finished) throw new Error("Verification ended before the audit decision was saved.");
      await refreshHistory();
    } catch (cause) { setError(cause.message); }
    finally { setBusy(false); setActivity(""); }
  }
  async function probeNppes() {
    if (!packet || registryBusy) return;
    setRegistryBusy(true); setRegistry(null);
    try {
      const submission = historicalRun ? run?.form : packet.submission;
      setRegistry(await api("/api/registry/nppes/" + submission.npi_number +
        "?taxonomy=" + encodeURIComponent(submission.claimed_taxonomy_code)));
    } catch (cause) { setRegistry({ error: cause.message, checks: [] }); }
    finally { setRegistryBusy(false); }
  }
  async function openRun(item) {
    try {
      const detail = await api("/api/run/" + item.run_id);
      if (detail.case && cases.some((entry) => entry.id === detail.case)) setSelectedCase(detail.case);
      else {
        setSelectedCase("custom"); setOverrides({}); setOverridePages({});
        setCustomForm({ ...EMPTY_CUSTOM_FORM, ...detail.form, tin_ein: "", bank_account_number: "",
          owners_5_percent: (detail.form?.owners_5_percent || []).join(", ") });
      }
      setRun(detail); setDocs(detail.documents || {}); setChecks(detail.checks || []);
      setHistoricalRun(true);
      setStates(Object.fromEntries(STAGES.map(([id]) => [id, "complete"])));
      setStage("Stage 5"); setLiveView("verification"); setTab("live"); setError("");
    } catch (cause) { setError(cause.message); }
  }

  return <main className="app-shell">
    <header className="app-header">
      <div className="app-header__inner">
        <div className="app-brand"><strong className="app-brand__name">Zamp</strong>
          <h1 className="app-brand__context">Vendor Verification Workspace</h1></div>
        <nav className="app-nav" aria-label="Main navigation">
          {[["live", "Live run"], ["dashboard", "Audit dashboard"]].map(([id, label]) =>
            <button key={id} type="button" aria-current={tab === id ? "page" : undefined} onClick={() => setTab(id)}>{label}</button>)}
        </nav>
      </div>
    </header>
    <div className="app-content space-y-6">
      {error && <div role="alert" className="rounded-xl border border-errorrule bg-errorbg p-4 text-sm text-errorink">{error}</div>}
      {tab === "live" ? <>
        <div className="app-view-switch" role="tablist" aria-label="Live run views">
          <button type="button" role="tab" aria-selected={liveView === "packet"} onClick={() => setLiveView("packet")}
            >Packet and files</button>
          <button type="button" role="tab" aria-selected={liveView === "verification"} onClick={() => setLiveView("verification")}
            >Verification{run ? " — " + run.final_status : ""}</button>
        </div>
        {liveView === "packet" && <>
        {historicalRun && run && <div className="rounded-xl border border-warningrule bg-warningbg p-4 text-sm text-warningink">
          {isCustom ? "Saved custom runs keep findings and file hashes, but not the original PDFs. Upload the documents again to start a new run." :
            "This is the current packet fixture. The opened audit run keeps its recorded findings and input hashes; starting verification creates a new run from these files."}
        </div>}
        <div className="app-section-heading">
          <div><h2>{isCustom ? "Upload your own packet" : "Review the submission and source PDFs"}</h2>
            <p>{isCustom ? "Enter the vendor submission. Bank evidence may be a letter, statement, voided check, or confirmation; IRS EIN evidence may be CP-575 or 147C. A masked account number will need review." : "The file viewer below shows exactly what will be sent to verification."}</p></div>
          <label className="text-sm font-semibold text-ink2">Packet source
            <select value={selectedCase} disabled={busy} onChange={(event) => changeCase(event.target.value)}
              className="hm-input">
              <option value="custom">Upload my own packet</option>
              {cases.map((item) => <option key={item.id} value={item.id} disabled={!item.available}>{item.label}</option>)}
            </select></label>
        </div>
        {isCustom && <Panel title="Vendor submission" note="Required fields are marked *. The values entered here are compared with your PDFs.">
          <div className="space-y-6 p-5">{FORM_SECTIONS.map(([title, ...fields]) =>
            <fieldset key={title}><legend className="mb-3 text-sm font-semibold">{title}</legend>
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{fields.map((field) =>
                <label key={field} className="text-sm font-semibold text-ink2">{human(field)}{CUSTOM_REQUIRED.includes(field) ? " *" : ""}
                  {field === "requested_complexity" ? <select value={customForm[field]} disabled={busy}
                    onChange={(event) => setCustomForm((old) => ({ ...old, [field]: event.target.value }))}
                    className="hm-input mt-1 block w-full">
                    <option value="High-Complexity Lab">High-Complexity Lab</option><option value="Basic Waiver">Basic Waiver</option></select> :
                  field === "prior_sanctions_disclosed" ? <select value={String(customForm[field])} disabled={busy}
                    onChange={(event) => setCustomForm((old) => ({ ...old, [field]: event.target.value === "true" }))}
                    className="hm-input mt-1 block w-full">
                    <option value="false">No</option><option value="true">Yes</option></select> :
                  <input type={field === "contact_email" ? "email" : "text"} value={customForm[field] ?? ""} disabled={busy}
                    placeholder={field === "owners_5_percent" ? "Separate names with commas" : undefined}
                    onChange={(event) => setCustomForm((old) => ({ ...old, [field]: event.target.value }))}
                    className="hm-input mt-1 block w-full font-normal" />}
                </label>)}</div>
            </fieldset>)}</div>
        </Panel>}
        {packet ? <div className={"packet-workspace" + (isCustom ? " packet-workspace--custom" : "")}>
          <div className="packet-rail">
            <Panel title="Required documents" note="Select a PDF to inspect its content and requirements">
              <div className="hm-queue">{packet.documents.map((file) =>
                <button key={file.slot} type="button" aria-current={selectedSlot === file.slot} onClick={() => { setSelectedSlot(file.slot); setPreviewPage(0); }}
                  className="flex w-full items-center justify-between gap-3 px-5 py-4 text-left">
                  <span className="min-w-0"><strong className="block text-sm">{SLOTS[file.slot]}</strong>
                    <span className="mt-1 block truncate text-xs text-muted">{overrides[file.slot]?.name || file.filename}</span></span>
                  <Badge value={file.text_layer === "scanned" ? "WARN_FIXABLE" : null}>{overrides[file.slot] ? isCustom ? "Selected" : "Replaced" : isCustom ? "Missing" : file.text_layer}</Badge>
                </button>)}</div>
            </Panel>
          </div>
          <div className="packet-viewer"><Panel title={SLOTS[selectedSlot]} note={selectedFile?.required_content}
            action={pdfUrl && <a href={pdfUrl} target="_blank" rel="noreferrer" className="hm-link">Open PDF</a>}>
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-rule px-5 py-3 text-xs text-muted">
              <span>{pageCount} page(s), {Math.round((overrides[selectedSlot]?.size || selectedFile?.bytes || 0) / 1024)} KB</span>
              <span>{overrides[selectedSlot] ? "Local PDF selected" : isCustom ? "Upload a PDF to preview it" : "SHA-256 " + (selectedFile?.sha256?.slice(0, 16) || "—") + "…"}</span>
              {pageCount > 1 && <span className="flex items-center gap-2">
                <button type="button" disabled={previewPage === 0} onClick={() => setPreviewPage((n) => n - 1)}
                  className="hm-button hm-button--light">Previous</button>
                Page {previewPage + 1} of {pageCount}
                <button type="button" disabled={previewPage + 1 >= pageCount} onClick={() => setPreviewPage((n) => n + 1)}
                  className="hm-button hm-button--light">Next</button>
              </span>}
              <label className="hm-button hm-button--light cursor-pointer">{isCustom ? "Upload PDF" : "Replace this PDF"}
                <input type="file" accept="application/pdf,.pdf" className="sr-only" disabled={busy} onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) { setPreviewPage(0); setOverrides((old) => ({ ...old, [selectedSlot]: file })); event.target.value = ""; }
                }} /></label>
            </div>
            {imageUrl ? <div className="doc-viewport">
              <img key={imageUrl} src={imageUrl} alt={SLOTS[selectedSlot] + " page " + (previewPage + 1)}
                />
            </div> : <div className="doc-viewport text-sm text-muted">
              {overrides[selectedSlot] ? "Rendering selected PDF…" : "No PDF in this slot"}
            </div>}
          </Panel></div>
          {!isCustom && <div className="packet-claims"><Panel title="Vendor form answers" note="Key claims first; sensitive values are masked">
            <dl className="space-y-3 px-5 py-4 text-sm">{[["Legal name", "company_name"], ["DBA", "dba_name"], ["NPI", "npi_number"],
              ["EIN", "tin_ein"], ["Service", "requested_complexity"], ["Taxonomy", "claimed_taxonomy_code"]]
              .map(([label, key]) => <div key={key} className="grid gap-1"><dt className="text-xs text-muted">{label}</dt><dd className="break-words font-semibold">{shown(packet.submission[key])}</dd></div>)}</dl>
            <details className="border-t border-rule"><summary className="cursor-pointer px-5 py-3 text-sm font-semibold text-accent2">View complete application</summary>
              <div className="max-h-[510px] overflow-auto border-t border-rule">{FORM_SECTIONS.map(([title, ...fields]) =>
                <div key={title} className="border-b border-rule px-5 py-4"><h4 className="mb-3 text-sm font-semibold">{title}</h4>
                  <dl className="space-y-2">{fields.map((field) => <div key={field} className="grid gap-1 text-xs">
                    <dt className="text-muted">{human(field)}</dt><dd className="break-words font-semibold">{shown(packet.submission[field])}</dd>
                  </div>)}</dl></div>)}</div></details>
          </Panel></div>}
        </div> : <div className="rounded-2xl border border-rule bg-cotton p-10 text-sm text-muted">Loading packet files…</div>}
        {packet && <div className="packet-action">
          <div><h3 className="font-bold">Verify {packet.submission.company_name || (isCustom ? "your packet" : "packet")}</h3>
            <p className="mt-1 text-sm">NPI {packet.submission.npi_number || "—"}; {Object.keys(overrides).length} {isCustom ? "PDF(s) selected" : "file replacement(s)"}</p>
            {selectedCase === "case_5b" && !parent && <p className="mt-1 text-xs text-warningink">Run case 5A first to create the parent audit record.</p>}
          </div>
          <button type="button" disabled={busy || selectedCase === "case_5b" && !parent} onClick={runSelected}
            className="hm-button hm-button--light">
            {busy ? "Verifying packet…" : "Run verification"}</button>
        </div>}
        </>}
        {liveView === "verification" && <div className="space-y-5 pt-3">
          <div className="app-section-heading"><div><h2>Follow the checks step by step</h2>
            <p>Follow the live sequence, inspect one document at a time, and expand the complete audit when needed.</p></div></div>
          <ProcessTrace activity={activity} busy={busy} checks={currentChecks} documents={currentDocs} states={states} />
          <div className="verification-layout">
          <StageStrip states={states} checks={currentChecks} run={run} selected={stage} onSelect={setStage} />
          <div className="verification-layout__main">
          {stage === "Stage 1" && <div className="space-y-4">

            <div className="grid gap-4">
              <DocumentQueue selected={selectedSlot} onSelect={(slot) => { setSelectedSlot(slot); setPreviewPage(0); }} documents={currentDocs} packet={packet} run={run} checks={currentChecks} stage="Stage 1" />
              <div className="space-y-4">
                <div className="grid items-start gap-4 lg:grid-cols-2">
                  <DocumentCard slot={selectedSlot} document={currentDocs[selectedSlot]} checks={currentStageChecks} />
                  <FocusPreview slot={selectedSlot} imageUrl={historicalSourceUnavailable ? null : imageUrl}
                    pdfUrl={historicalSourceUnavailable ? null : pdfUrl} pageCount={historicalSourceUnavailable ? 0 : pageCount}
                    page={previewPage} onPage={setPreviewPage}
                    unavailableReason={historicalSourceUnavailable ? "The saved run retains the PDF hash, but this packet file differs. Its original PDF is not stored for preview." : null} />
                </div>
                <Panel title="Findings for this document" note="Only items that need attention"><div className="p-5"><Findings ready={Boolean(currentDocs[selectedSlot])} checks={currentStageChecks.filter((item) => item.evidence_source === selectedSlot)} /></div></Panel>
              </div>
            </div>
            <Panel title="Application claims" note="These are the form values that the five PDFs must support">
              <dl className="grid gap-3 p-5 text-sm sm:grid-cols-2 xl:grid-cols-4">
                {[["Legal name", "company_name"], ["DBA", "dba_name"], ["NPI", "npi_number"], ["EIN", "tin_ein"],
                  ["State", "state"], ["Taxonomy", "claimed_taxonomy_code"], ["Service", "requested_complexity"], ["Routing", "bank_routing_number"]]
                  .map(([label, key]) => {
                    const value = historicalRun ? key === "tin_ein" ? run?.form?.ein_last4 ? "••••" + run.form.ein_last4 : null : run?.form?.[key] : packet?.submission?.[key];
                    return <div key={key}><dt className="text-xs font-semibold text-muted">{label}</dt><dd className="mt-1 break-words font-semibold">{shown(value)}</dd></div>;
                  })}
              </dl></Panel>
            <AuditDisclosure checks={currentStageChecks} title="View all classify and extract checks" />
          </div>}
          {stage === "Stage 2" && <div className="space-y-4">
            <div className="grid gap-4">
              <DocumentQueue selected={selectedSlot} onSelect={(slot) => { setSelectedSlot(slot); setPreviewPage(0); }} documents={currentDocs} packet={packet} run={run} checks={currentChecks} stage="Stage 2" />
              <div className="space-y-4">
                <div className="grid items-start gap-4 lg:grid-cols-2">
                  <DocumentCard slot={selectedSlot} document={currentDocs[selectedSlot]} forensic />
                  <FocusPreview slot={selectedSlot} imageUrl={historicalSourceUnavailable ? null : imageUrl}
                    pdfUrl={historicalSourceUnavailable ? null : pdfUrl} pageCount={historicalSourceUnavailable ? 0 : pageCount}
                    page={previewPage} onPage={setPreviewPage}
                    unavailableReason={historicalSourceUnavailable ? "This saved run's source PDF differs from the current packet; only its recorded hash and findings remain." : null} />
                </div>
                <Panel title="Forensic findings for this PDF" note="A signal is evidence for review, not issuer authentication"><div className="p-5"><Findings ready={currentDocs[selectedSlot]?.forensics?.revision_count !== undefined} checks={currentStageChecks.filter((item) => item.evidence_source === selectedSlot)} /></div></Panel>
              </div>
            </div>
            <AuditDisclosure checks={currentStageChecks} title="View every PDF forensic check" />
          </div>}
          {stage === "Stage 3" && <div className="space-y-4">
            <div className="grid gap-4 xl:grid-cols-3">
              <CrossGroup title="Identity & tax" note="Legal name, DBA, W-9, IRS notice and IDs" checks={currentStageChecks.filter((item) => crossGroup(item.check_name) === "identity")} />
              <CrossGroup title="Payment" note="Bank beneficiary, account type and routing" checks={currentStageChecks.filter((item) => crossGroup(item.check_name) === "payment")} />
              <CrossGroup title="License & coverage" note="CLIA state/scope, specialty, limits and dates" checks={currentStageChecks.filter((item) => crossGroup(item.check_name) === "scope")} />
            </div>
            <AuditDisclosure checks={currentStageChecks} title="View every cross-document check" />
          </div>}
          {stage === "Stage 4" && <Panel title="CMS NPPES" note="NPI, entity type, taxonomy and registered names; unavailable registry is a verification gap"
            action={packet && <a target="_blank" rel="noreferrer" className="text-xs font-bold text-accent2 hover:underline"
              href={"https://npiregistry.cms.hhs.gov/provider-view/" + (historicalRun ? run?.npi : packet.submission.npi_number)}>Open CMS record</a>}>
            <div className="p-5"><Findings ready={states["Stage 4"] === "complete"} checks={currentStageChecks.filter((item) => item.check_name?.startsWith("NPPES"))} empty="No NPPES discrepancy recorded." />
              <p className="mt-3 text-xs text-muted">{currentStageChecks.filter((item) => item.check_name?.startsWith("NPPES") && item.outcome === "PASS").length} registry check(s) passed. Open the full audit for source values.</p>
            </div></Panel>}
          {stage === "Stage 4" && <Panel title="HHS OIG LEIE" note="Official exclusion data; name matches are candidates for identity review">
            <div className="p-5"><Findings ready={states["Stage 4"] === "complete"} checks={currentStageChecks.filter((item) => /OIG|exclusion/i.test(item.check_name || ""))} empty="No OIG candidate or source problem recorded." />
              <p className="mt-3 text-xs text-muted">{currentStageChecks.filter((item) => /OIG|exclusion/i.test(item.check_name || "") && item.outcome === "PASS").length} screening check(s) passed.</p>
            </div></Panel>}
          {stage === "Stage 4" && <AuditDisclosure checks={currentStageChecks} title="View every external-source check" />}
          {stage === "Stage 5" && (run ? <div className="space-y-4">
            <div className={"rounded-2xl border p-6 " + palette(run.final_status)}>
              <h2 className="text-2xl font-bold">{run.final_status}</h2>
              <p className="mt-2 text-sm">{run.vendor_name}. NPI {run.npi}. Run {run.run_id}</p>
              {!run.pipeline_version && <p className="mt-2 text-xs font-semibold">Earlier run: its reference sources were not verified by the current pipeline.</p>}</div>
            <Panel title="Why this decision" note="The findings that set the final precedence">
              <div className="p-5">{run.final_status === "APPROVED" ?
                <p className="rounded-xl border border-successrule bg-successbg p-4 text-sm text-successink">All required checks passed. The full audit is available below.</p> :
                <CheckList checks={currentChecks.filter((item) => item.outcome === (
                  run.final_status === "REJECTED: RISK" ? "FAIL_RISK" : run.final_status === "REJECTED: NOT ELIGIBLE" ? "FAIL_ELIGIBILITY" : "WARN_FIXABLE"))} />}</div>
            </Panel>
            <Panel title="Vendor communication" note={run.message_source || "Rule-based message"}><p className="whitespace-pre-wrap p-5 text-sm leading-7">{run.vendor_message}</p></Panel>
            {run.internal_alert && <details className="rounded-2xl border border-rule bg-cotton shadow-sm">
              <summary className="cursor-pointer px-5 py-4 text-sm font-bold">Internal reviewer alert</summary>
              <p className="whitespace-pre-wrap border-t border-rule p-5 text-sm leading-7">{run.internal_alert}</p></details>}
            <AuditDisclosure checks={currentChecks} title="View complete run audit" />
          </div> : <p className="rounded-xl border border-dashed border-rule bg-cotton p-8 text-sm text-muted">Decision appears after all stages finish.</p>)}
          {stage === "Stage 4" && packet && <Panel title="NPPES connection check" note="Probe the official API separately to diagnose registry access"
            action={<button type="button" disabled={registryBusy} onClick={probeNppes}
              className="hm-button hm-button--light">
              {registryBusy ? "Checking…" : "Verify NPI now"}</button>}>
            {registry && <div className="p-5">{registry.error ? <p className="text-sm text-errorink">{registry.error}</p> :
              <><p className="mb-3 text-xs font-semibold text-muted">{registry.verified ? "NPI, entity type, and taxonomy match" :
                registry.checks?.some((item) => item.check_name === "NPPES availability" || item.check_name === "NPPES response") ?
                  "CMS lookup unavailable or incomplete" : "Registry checked; review the findings below"}</p>
                <CheckList checks={registry.checks || []} /></>}</div>}
          </Panel>}
          </div>
          </div>
        </div>}
      </> : <Panel title="Audit history" note="Saved decisions and linked resubmissions"
        action={<button type="button" className="text-xs font-bold text-accent2" onClick={() => refreshHistory().catch((cause) => setError(cause.message))}>Refresh</button>}>
        {runs.length ? <div className="hm-history overflow-x-auto"><table className="min-w-full text-left text-sm">
          <thead className="bg-paper text-xs text-muted"><tr><th className="px-5 py-3">Vendor / NPI</th><th className="px-5 py-3">Case</th>
            <th className="px-5 py-3">Decision</th><th className="px-5 py-3">Recorded</th><th className="px-5 py-3">Link</th><th className="px-5 py-3">Evidence</th></tr></thead>
          <tbody className="divide-y divide-rule">{runs.map((item) => <tr key={item.run_id}>
            <td className="px-5 py-4"><strong className="block">{item.vendor_name}</strong><span className="text-xs text-muted">{item.npi}</span></td>
            <td className="px-5 py-4">{item.case || "Upload"}
              {cases.some((entry) => entry.id === item.case && entry.npi && entry.npi !== item.npi) &&
                <span className="mt-1 block text-xs font-semibold text-warningink">Earlier packet version</span>}
              {!item.pipeline_version &&
                <span className="mt-1 block text-xs font-semibold text-warningink">Earlier pipeline: reference data unverified</span>}
            </td><td className="px-5 py-4"><Badge value={item.final_status} /></td>
            <td className="px-5 py-4 text-xs">{new Date(item.created_at).toLocaleString()}</td>
            <td className="px-5 py-4 text-xs">{item.parent_run_id ? "Resubmission of " + item.parent_run_id.slice(0, 8) : "Initial"}</td>
            <td className="px-5 py-4"><button type="button" className="hm-link" onClick={() => openRun(item)}>Open checks</button></td>
          </tr>)}</tbody></table></div> : <p className="p-8 text-sm text-muted">No saved runs yet.</p>}
      </Panel>}
    </div>
  </main>;
}
