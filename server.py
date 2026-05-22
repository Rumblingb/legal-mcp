#!/usr/bin/env python3
"""
Legal MCP — Comprehensive legal toolkit for AI agents.

Consolidates 4 tools into one server:
  - Patent research (Google Patents + USPTO)
  - Court records (CourtListener 5M+ opinions)
  - Legal contract generation (NDA, SaaS, service agreement, DPA)
  - Contract analysis (parties, terms, risk, obligations)

Usage:
  python3 server.py                    # Free tier (50 calls/instance)
  python3 server.py --pro-key PROL_XXX  # Pro tier (unlimited)
"""

import json
import re
import socket
import sys
from datetime import datetime
from typing import Any

import httpx
from mcp.server.lowlevel import Server, stdio_server
from mcp.types import Tool, TextContent

# ── Rate Limiting ──────────────────────────────────────────────────────────
FREE_LIMIT = 50
PRO_KEYS = {"PROL_AGENTPAY_DEMO": "demo"}
STRIPE_LINK = "https://buy.stripe.com/28E3cxflRabW1jqgvx1oI0s"

PRO_KEY = None
for i, arg in enumerate(sys.argv):
    if arg == "--pro-key" and i + 1 < len(sys.argv):
        PRO_KEY = sys.argv[i + 1]
        break

IS_PRO = PRO_KEY in PRO_KEYS
call_counter = 0

def check_rate_limit():
    global call_counter
    if IS_PRO:
        return None
    call_counter += 1
    if call_counter > FREE_LIMIT:
        return {
            "error": f"Free limit reached ({FREE_LIMIT} calls). Upgrade at {STRIPE_LINK}",
            "isError": True,
            "calls_used": call_counter,
            "limit": FREE_LIMIT
        }
    return None

# ── HTTP Client ────────────────────────────────────────────────────────────
_http = httpx.Client(
    timeout=30.0,
    headers={
        "User-Agent": "Mozilla/5.0 (compatible; LegalMCP/1.0; +https://rumblingb.github.io/legal-mcp/)",
        "Accept": "application/json",
    }
)

# ══════════════════════════════════════════════════════════════════════════
# PATENT SEARCH  (Google Patents + USPTO)
# ══════════════════════════════════════════════════════════════════════════

PATENTS_API = "https://patents.google.com/api/patents"

def _safe(obj, keys, default=""):
    for k in keys:
        if isinstance(obj, dict):
            obj = obj.get(k, default)
        else:
            return default
    return obj if obj is not None else default

def _parse_patent(entry):
    patent_id = _safe(entry, ["patent_id"]) or _safe(entry, ["id"]) or _safe(entry, ["publication_number"])
    assignee = _safe(entry, ["assignee"]) or _safe(entry, ["assignee_original"])
    if isinstance(assignee, list):
        assignee = "; ".join(str(a) for a in assignee)
    inventors = _safe(entry, ["inventor"]) or _safe(entry, ["inventor_name"])
    if isinstance(inventors, list):
        inventors = "; ".join(str(i) for i in inventors)
    cpc = _safe(entry, ["cpc_classification"])
    if isinstance(cpc, list):
        cpc = "; ".join(str(c) for c in cpc)
    return {
        "patent_id": str(patent_id),
        "title": str(_safe(entry, ["title"])),
        "abstract": str(_safe(entry, ["abstract"]))[:500],
        "assignee": str(assignee),
        "inventors": str(inventors),
        "filing_date": str(_safe(entry, ["filing_date"])),
        "publication_date": str(_safe(entry, ["publication_date"])),
        "cpc_classifications": str(cpc),
        "url": f"https://patents.google.com/patent/{patent_id}/en",
    }

def _patent_search(query: str, limit: int = 10) -> list:
    try:
        resp = _http.get(PATENTS_API, params={"q": query, "num": min(limit, 50), "format": "json"})
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("results") or data.get("patents") or data.get("items") or []
        return [_parse_patent(e) for e in raw[:limit] if _safe(e, ["patent_id"]) or _safe(e, ["id"])]
    except Exception as e:
        return [{"error": str(e)}]

# ══════════════════════════════════════════════════════════════════════════
# COURT RECORDS  (CourtListener v4 API)
# ══════════════════════════════════════════════════════════════════════════

CL_BASE = "https://www.courtlistener.com/api/rest/v4"

def _cl_get(path: str, params: dict) -> dict:
    try:
        resp = _http.get(f"{CL_BASE}{path}", params=params)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def _format_opinion(result: dict) -> dict:
    return {
        "cluster_id": result.get("cluster_id") or result.get("id"),
        "case_name": result.get("case_name", ""),
        "court": result.get("court_id", ""),
        "date_filed": result.get("dateFiled", result.get("date_filed", "")),
        "citation": result.get("citation", [result.get("cluster_id", "")]),
        "score": result.get("score"),
        "url": f"https://www.courtlistener.com{result.get('absolute_url', '')}",
        "snippet": result.get("snippet", "")[:300],
    }

# ══════════════════════════════════════════════════════════════════════════
# LEGAL CONTRACT GENERATION
# ══════════════════════════════════════════════════════════════════════════

_CONTRACT_TEMPLATES = {
    "nda": {
        "name": "Non-Disclosure Agreement",
        "template": """NON-DISCLOSURE AGREEMENT

This Non-Disclosure Agreement ("Agreement") is entered into as of {date} between {party_a} ("Disclosing Party") and {party_b} ("Receiving Party").

1. CONFIDENTIAL INFORMATION
"Confidential Information" means any non-public information disclosed by Disclosing Party to Receiving Party, including but not limited to technical data, trade secrets, business plans, and AI model specifications.

2. OBLIGATIONS
Receiving Party agrees to: (a) hold Confidential Information in strict confidence; (b) not disclose to third parties without prior written consent; (c) use only for evaluation purposes described in this Agreement.

3. EXCLUSIONS
Obligations do not apply to information that: (a) is or becomes publicly known through no breach; (b) was rightfully known before disclosure; (c) is independently developed; (d) is required to be disclosed by law.

4. TERM
This Agreement is effective for {term} years from the date above.

5. RETURN OF INFORMATION
Upon request, Receiving Party shall return or destroy all Confidential Information.

6. REMEDIES
Breach may cause irreparable harm. Disclosing Party is entitled to seek injunctive relief in addition to other remedies.

7. GOVERNING LAW
This Agreement is governed by the laws of {jurisdiction}.

IN WITNESS WHEREOF, the parties have executed this Agreement as of the date first written above.

{party_a}: _________________________  Date: ____________
{party_b}: _________________________  Date: ____________"""
    },
    "service_agreement": {
        "name": "AI Agent Service Agreement",
        "template": """AI AGENT SERVICE AGREEMENT

This Service Agreement ("Agreement") is entered into as of {date} between {party_a} ("Service Provider") and {party_b} ("Client").

1. SERVICES
Service Provider agrees to provide the following services: {scope}. AI agents may be used to perform tasks within the agreed scope.

2. PAYMENT
Client agrees to pay {price} per {billing_period}. Payment is due within 30 days of invoice.

3. AGENT GOVERNANCE
All AI agents acting on behalf of Service Provider are governed by AgentPay payment rails. No agent may exceed pre-approved spending limits without explicit human approval.

4. INTELLECTUAL PROPERTY
All deliverables created under this Agreement are work-for-hire and become property of Client upon full payment.

5. LIABILITY
Service Provider liability is limited to the amount paid in the prior 3 months. Neither party is liable for indirect, incidental, or consequential damages.

6. TERMINATION
Either party may terminate with {notice} days written notice. Client pays for services rendered through termination date.

7. CONFIDENTIALITY
Each party agrees to keep the other party's Confidential Information private for 2 years post-termination.

8. GOVERNING LAW
This Agreement is governed by {jurisdiction} law.

{party_a}: _________________________  Date: ____________
{party_b}: _________________________  Date: ____________"""
    },
    "data_processing": {
        "name": "Data Processing Agreement (GDPR/CCPA)",
        "template": """DATA PROCESSING AGREEMENT

This Data Processing Agreement ("DPA") supplements the Service Agreement between {party_a} ("Controller") and {party_b} ("Processor") as of {date}.

1. SCOPE AND PURPOSE
Processor will process personal data on behalf of Controller solely for the purposes described in the Service Agreement.

2. DATA SUBJECT CATEGORIES
Categories of data subjects: {data_subjects}
Categories of personal data: {data_categories}

3. PROCESSOR OBLIGATIONS
Processor shall: (a) process data only on documented Controller instructions; (b) implement appropriate technical and organisational security measures; (c) not engage sub-processors without prior written consent; (d) assist Controller with data subject requests within 5 business days.

4. SECURITY
Processor shall implement measures including: encryption at rest and in transit, access controls, audit logging, and incident response procedures.

5. AI AGENT CONTROLS
Any AI agents processing personal data must be registered with AgentPay and subject to spending and data access limits set by Controller.

6. DATA TRANSFERS
No transfer of personal data outside {jurisdiction} without Controller's prior written consent and appropriate safeguards.

7. AUDIT RIGHTS
Controller may audit Processor's compliance once per year with 30 days notice.

8. BREACH NOTIFICATION
Processor shall notify Controller within 72 hours of becoming aware of a personal data breach.

Signed:
{party_a} (Controller): _________________________ Date: ____________
{party_b} (Processor):  _________________________ Date: ____________"""
    },
    "saas_terms": {
        "name": "SaaS Subscription Terms",
        "template": """SAAS SUBSCRIPTION TERMS OF SERVICE

Effective: {date}
Provider: {party_a}
Customer: {party_b}

1. SUBSCRIPTION
Provider grants Customer a non-exclusive, non-transferable licence to access and use the Service during the Subscription Term.

2. FEES AND PAYMENT
Subscription Fee: {price} per {billing_period}
Payment Method: Credit card or bank transfer
Late payments incur 1.5% monthly interest.

3. UPTIME SLA
Provider targets 99.5% monthly uptime. Credits apply for downtime exceeding threshold (5% credit per hour beyond SLA, capped at one month's fee).

4. ACCEPTABLE USE
Customer must not: (a) reverse engineer the Service; (b) resell access without written consent; (c) use Service for illegal purposes; (d) exceed usage limits in the subscription tier.

5. DATA AND PRIVACY
Customer data remains Customer's property. Provider may use aggregated anonymised data for service improvement. See Privacy Policy at [URL].

6. AI AGENT INTEGRATIONS
Third-party AI agents accessing the Service must be pre-approved. All agent transactions are governed by AgentPay payment rails.

7. TERM AND RENEWAL
Initial term: {term}. Auto-renews unless cancelled 30 days before renewal date.

8. TERMINATION
Either party may terminate for material breach with 30 days notice if breach is not cured. Provider may suspend immediately for non-payment.

9. LIMITATION OF LIABILITY
Provider's liability is capped at 12 months of fees paid. No liability for indirect damages.

10. GOVERNING LAW
These Terms are governed by {jurisdiction} law.

{party_a}: _________________________  Date: ____________
{party_b}: _________________________  Date: ____________"""
    },
}

def _generate_contract(contract_type: str, params: dict) -> str:
    template_data = _CONTRACT_TEMPLATES.get(contract_type)
    if not template_data:
        return json.dumps({"error": f"Unknown contract type: {contract_type}. Valid: {list(_CONTRACT_TEMPLATES.keys())}"})

    template = template_data["template"]
    defaults = {
        "date": datetime.now().strftime("%B %d, %Y"),
        "party_a": "Party A",
        "party_b": "Party B",
        "term": "2",
        "jurisdiction": "England and Wales",
        "price": "[PRICE]",
        "billing_period": "month",
        "notice": "30",
        "scope": "[DESCRIBE SERVICES]",
        "data_subjects": "Customers and employees",
        "data_categories": "Names, email addresses, usage data",
    }
    merged = {**defaults, **params}

    try:
        filled = template.format(**merged)
    except KeyError as e:
        filled = template

    return json.dumps({
        "contract_type": contract_type,
        "name": template_data["name"],
        "generated_at": datetime.now().isoformat(),
        "contract_text": filled,
        "note": "This is a template for informational purposes. Have a qualified solicitor review before signing."
    }, indent=2)

# ══════════════════════════════════════════════════════════════════════════
# CONTRACT ANALYSIS  (pattern-based, no external APIs)
# ══════════════════════════════════════════════════════════════════════════

PARTY_RE = re.compile(r'(?:between|by and between)\s+([A-Z][A-Za-z0-9\s,.&]+?)(?:\s+and\s+)([A-Z][A-Za-z0-9\s,.&]+?)(?:\s*[,.(])', re.DOTALL)
DATE_RE = re.compile(r'(?:effective|dated|as of|entered into)[:\s]+([A-Z][a-z]+ \d{1,2},?\s*\d{4})', re.IGNORECASE)
MONEY_RE = re.compile(r'\$\s*([\d,]+(?:\.\d{2})?)\s*(?:per\s+(?:month|year|annum)|monthly|annually)?', re.IGNORECASE)
NOTICE_RE = re.compile(r'(\d+)\s*(?:calendar\s+)?(?:business\s+)?days?\s+(?:prior\s+|written\s+)?notice', re.IGNORECASE)

RISK_HIGH = ["indemnify", "unlimited liability", "irrevocable", "waive", "non-refundable",
             "liquidated damages", "sole discretion", "binding arbitration", "non-compete",
             "joint and several", "forfeiture"]
RISK_MED = ["best efforts", "reasonable efforts", "material adverse", "time is of the essence",
            "automatic renewal", "liquidated", "exclusivity"]

def _analyze_contract(text: str) -> dict:
    text_lower = text.lower()

    parties = PARTY_RE.findall(text)
    parties_clean = [p.strip() for pair in parties for p in pair if len(p.strip()) > 2][:4]

    dates = DATE_RE.findall(text)
    amounts = MONEY_RE.findall(text)
    notice_days = NOTICE_RE.findall(text)

    high_risks = [t for t in RISK_HIGH if t in text_lower]
    med_risks = [t for t in RISK_MED if t in text_lower]

    risk_level = "HIGH" if high_risks else ("MEDIUM" if med_risks else "LOW")

    obligations = [s.strip()[:150] for s in re.findall(r'[^.]*\b(?:shall|must|agrees? to|covenants? to)\b[^.]*\.', text, re.IGNORECASE)][:8]

    termination = {}
    if re.search(r'terminat\w+\s+for\s+cause|material breach', text, re.IGNORECASE):
        termination["for_cause"] = True
    if re.search(r'terminat\w+\s+without cause|for\s+convenience|at will', text, re.IGNORECASE):
        termination["without_cause"] = True
    if notice_days:
        termination["notice_days"] = notice_days[0]
    if re.search(r'auto(?:matic|matically)\s+renew', text, re.IGNORECASE):
        termination["auto_renewal"] = True

    return {
        "parties_identified": parties_clean,
        "effective_dates": dates[:3],
        "monetary_amounts": [f"${a}" for a in amounts[:5]],
        "termination": termination,
        "notice_periods_days": notice_days[:3],
        "risk_level": risk_level,
        "high_risk_terms": high_risks,
        "medium_risk_terms": med_risks,
        "key_obligations": obligations,
        "word_count": len(text.split()),
        "analysis_note": "Pattern-based analysis. Not a substitute for legal advice."
    }

def _compare_contracts(text_a: str, text_b: str) -> dict:
    a, b = _analyze_contract(text_a), _analyze_contract(text_b)
    return {
        "contract_a": {"parties": a["parties_identified"], "risk": a["risk_level"],
                       "amounts": a["monetary_amounts"], "obligations_count": len(a["key_obligations"])},
        "contract_b": {"parties": b["parties_identified"], "risk": b["risk_level"],
                       "amounts": b["monetary_amounts"], "obligations_count": len(b["key_obligations"])},
        "risk_comparison": {"a_higher": a["risk_level"] == "HIGH" and b["risk_level"] != "HIGH",
                            "b_higher": b["risk_level"] == "HIGH" and a["risk_level"] != "HIGH",
                            "same": a["risk_level"] == b["risk_level"]},
        "unique_high_risks_in_a": [t for t in a["high_risk_terms"] if t not in b["high_risk_terms"]],
        "unique_high_risks_in_b": [t for t in b["high_risk_terms"] if t not in a["high_risk_terms"]],
        "note": "Pattern-based comparison. Consult a solicitor for legal advice."
    }

# ══════════════════════════════════════════════════════════════════════════
# MCP SERVER
# ══════════════════════════════════════════════════════════════════════════

server = Server("legal-mcp")

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        # ── Patent Research ──
        Tool(name="search_patents",
             description="Search patents by keyword using Google Patents. Returns patent ID, title, abstract, assignee, dates, CPC classifications.",
             inputSchema={"type":"object","properties":{"query":{"type":"string"},"limit":{"type":"integer","default":10,"maximum":50}},"required":["query"]}),
        Tool(name="get_patent_details",
             description="Get full details for a patent by ID (e.g. US10529241B2): abstract, claims, assignee, inventors, classifications.",
             inputSchema={"type":"object","properties":{"patent_id":{"type":"string"}},"required":["patent_id"]}),
        Tool(name="search_patents_by_assignee",
             description="Search patents filed by a specific company or inventor.",
             inputSchema={"type":"object","properties":{"assignee":{"type":"string"},"limit":{"type":"integer","default":10}},"required":["assignee"]}),
        Tool(name="search_patents_by_class",
             description="Search patents by CPC classification code (e.g. G06N for neural networks, H04L for network protocols).",
             inputSchema={"type":"object","properties":{"class_code":{"type":"string"},"limit":{"type":"integer","default":10}},"required":["class_code"]}),
        # ── Court Records ──
        Tool(name="search_court_cases",
             description="Search 5M+ US court opinions via CourtListener. Returns case name, court, date, citation and snippet.",
             inputSchema={"type":"object","properties":{"query":{"type":"string"},"court":{"type":"string","description":"Court ID e.g. scotus, ca1, cand"},"limit":{"type":"integer","default":10,"maximum":50}},"required":["query"]}),
        Tool(name="get_court_opinion",
             description="Retrieve a full court opinion by CourtListener cluster ID.",
             inputSchema={"type":"object","properties":{"cluster_id":{"type":"integer"}},"required":["cluster_id"]}),
        Tool(name="list_courts",
             description="List available court IDs for use in search_court_cases.",
             inputSchema={"type":"object","properties":{}}),
        Tool(name="get_recent_opinions",
             description="Get recent court opinions, optionally filtered by court.",
             inputSchema={"type":"object","properties":{"court":{"type":"string"},"limit":{"type":"integer","default":10}},"required":[]}),
        # ── Contract Generation ──
        Tool(name="generate_legal_contract",
             description="Generate a legal contract template. Types: nda, service_agreement, data_processing, saas_terms.",
             inputSchema={"type":"object","properties":{
                 "contract_type":{"type":"string","enum":["nda","service_agreement","data_processing","saas_terms"]},
                 "party_a":{"type":"string","description":"First party name"},
                 "party_b":{"type":"string","description":"Second party name"},
                 "jurisdiction":{"type":"string","description":"Governing jurisdiction","default":"England and Wales"},
                 "extra_params":{"type":"object","description":"Additional template variables (price, term, scope, etc.)"}
             },"required":["contract_type"]}),
        # ── Contract Analysis ──
        Tool(name="analyze_contract",
             description="Analyze a contract for parties, key terms, monetary amounts, termination clauses, and risk factors.",
             inputSchema={"type":"object","properties":{"contract_text":{"type":"string"}},"required":["contract_text"]}),
        Tool(name="compare_contracts",
             description="Compare two contracts side-by-side: parties, risk levels, amounts, obligations, and unique high-risk terms.",
             inputSchema={"type":"object","properties":{"contract_a":{"type":"string"},"contract_b":{"type":"string"}},"required":["contract_a","contract_b"]}),
        Tool(name="check_contract_compliance",
             description="Check a contract for common compliance issues: GDPR data processing terms, force majeure, limitation of liability.",
             inputSchema={"type":"object","properties":{"contract_text":{"type":"string"},"standard":{"type":"string","enum":["gdpr","ccpa","general"],"default":"general"}},"required":["contract_text"]}),
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    limit_check = check_rate_limit()
    if limit_check:
        return [TextContent(type="text", text=json.dumps(limit_check, indent=2))]

    try:
        # ── Patents ──
        if name == "search_patents":
            results = _patent_search(arguments.get("query",""), int(arguments.get("limit",10)))
            return [TextContent(type="text", text=json.dumps(results, indent=2))]

        elif name == "get_patent_details":
            pid = arguments.get("patent_id","")
            results = _patent_search(pid, 1)
            if results and "error" not in results[0]:
                return [TextContent(type="text", text=json.dumps(results[0], indent=2))]
            return [TextContent(type="text", text=json.dumps({"patent_id": pid, "error": "Not found"}, indent=2))]

        elif name == "search_patents_by_assignee":
            q = f'assignee:"{arguments.get("assignee","")}"'
            results = _patent_search(q, int(arguments.get("limit",10)))
            return [TextContent(type="text", text=json.dumps(results, indent=2))]

        elif name == "search_patents_by_class":
            q = f'cpc:"{arguments.get("class_code","")}"'
            results = _patent_search(q, int(arguments.get("limit",10)))
            return [TextContent(type="text", text=json.dumps(results, indent=2))]

        # ── Court Records ──
        elif name == "search_court_cases":
            params = {"q": arguments.get("query",""), "format": "json",
                      "page_size": min(int(arguments.get("limit",10)),50)}
            if arguments.get("court"):
                params["court"] = arguments["court"]
            data = _cl_get("/search/", params)
            results = [_format_opinion(r) for r in data.get("results", [])[:params["page_size"]]]
            return [TextContent(type="text", text=json.dumps(results, indent=2))]

        elif name == "get_court_opinion":
            cid = arguments.get("cluster_id")
            data = _cl_get(f"/clusters/{cid}/", {})
            return [TextContent(type="text", text=json.dumps(data, indent=2))]

        elif name == "list_courts":
            courts = {"supreme_court":"scotus","1st_circuit":"ca1","2nd_circuit":"ca2",
                      "3rd_circuit":"ca3","4th_circuit":"ca4","5th_circuit":"ca5",
                      "6th_circuit":"ca6","7th_circuit":"ca7","8th_circuit":"ca8",
                      "9th_circuit":"ca9","10th_circuit":"ca10","11th_circuit":"ca11",
                      "dc_circuit":"cadc","federal_circuit":"cafc",
                      "nd_california":"cand","sd_new_york":"nysd"}
            return [TextContent(type="text", text=json.dumps(courts, indent=2))]

        elif name == "get_recent_opinions":
            params = {"format":"json","order_by":"-date_filed",
                      "page_size": min(int(arguments.get("limit",10)),50)}
            if arguments.get("court"):
                params["court"] = arguments["court"]
            data = _cl_get("/clusters/", params)
            results = [_format_opinion(r) for r in data.get("results", [])]
            return [TextContent(type="text", text=json.dumps(results, indent=2))]

        # ── Contract Generation ──
        elif name == "generate_legal_contract":
            params = {
                "party_a": arguments.get("party_a", "Party A"),
                "party_b": arguments.get("party_b", "Party B"),
                "jurisdiction": arguments.get("jurisdiction", "England and Wales"),
                **(arguments.get("extra_params") or {})
            }
            result = _generate_contract(arguments.get("contract_type","nda"), params)
            return [TextContent(type="text", text=result)]

        # ── Contract Analysis ──
        elif name == "analyze_contract":
            result = _analyze_contract(arguments.get("contract_text",""))
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "compare_contracts":
            result = _compare_contracts(arguments.get("contract_a",""), arguments.get("contract_b",""))
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "check_contract_compliance":
            text = arguments.get("contract_text","").lower()
            standard = arguments.get("standard","general")
            issues = []
            ok = []
            checks = {
                "gdpr": [
                    ("data_processing_purpose", "data.*purpose|purpose.*processing", "No explicit data processing purpose clause"),
                    ("breach_notification", "breach.*notif|72 hour|72-hour", "Missing 72-hour breach notification requirement"),
                    ("data_subject_rights", "data subject|right to erasure|right of access", "No data subject rights clause"),
                    ("dpa_required", "data processing agreement|data processor|data controller", "Missing DPA/DPO references"),
                ],
                "ccpa": [
                    ("right_to_opt_out", "opt.?out|right to opt", "No opt-out right for California residents"),
                    ("do_not_sell", "do not sell|sale of personal information", "No 'Do Not Sell' provision"),
                    ("privacy_notice", "privacy notice|privacy policy", "Missing privacy notice reference"),
                ],
                "general": [
                    ("limitation_liability", "limitation of liability|liability.*limited|limit.*liability", "No limitation of liability clause"),
                    ("force_majeure", "force majeure|act of god|circumstances beyond", "No force majeure clause"),
                    ("dispute_resolution", "arbitrat|mediati|dispute resolution", "No dispute resolution mechanism"),
                    ("governing_law", "governing law|jurisdiction|choice of law", "No governing law clause"),
                    ("termination", "terminat", "No termination clause"),
                ],
            }
            for check_id, pattern, warning in checks.get(standard, checks["general"]):
                if re.search(pattern, text):
                    ok.append(check_id)
                else:
                    issues.append({"check": check_id, "warning": warning})

            return [TextContent(type="text", text=json.dumps({
                "standard": standard,
                "compliant": len(issues) == 0,
                "issues_found": len(issues),
                "passed": ok,
                "issues": issues,
                "note": "Pattern-based check only. Legal review required."
            }, indent=2))]

        else:
            return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}, indent=2))]

    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e), "tool": name}, indent=2))]

def main():
    import anyio
    async def run():
        async with stdio_server() as (r, w):
            await server.run(r, w, server.create_initialization_options())
    anyio.run(run)

if __name__ == "__main__":
    main()
