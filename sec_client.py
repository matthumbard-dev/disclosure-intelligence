from __future__ import annotations
import hashlib
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable
import requests
from scoring import score_event, band

SEC_DATA = "https://data.sec.gov"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
TRACKED_FORMS = {"3", "3/A", "4", "4/A", "5", "5/A", "144", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A", "8-K", "8-K/A"}


@dataclass
class SecClient:
    user_agent: str
    min_interval: float = 0.12

    def __post_init__(self):
        if not self.user_agent or "@" not in self.user_agent:
            raise ValueError("SEC User-Agent should identify you and include an email, e.g. 'DisclosureDashboard you@example.com'.")
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Host": "data.sec.gov",
        })
        self._last = 0.0

    def _get(self, url: str, data_host: bool = True):
        elapsed = time.time() - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
        r = requests.get(url, headers=headers, timeout=25)
        self._last = time.time()
        r.raise_for_status()
        return r

    def ticker_map(self) -> dict[str, dict]:
        data = self._get(SEC_TICKERS, data_host=False).json()
        return {v["ticker"].upper(): {"cik": str(v["cik_str"]).zfill(10), "title": v["title"]} for v in data.values()}

    def submissions(self, cik: str) -> dict:
        return self._get(f"{SEC_DATA}/submissions/CIK{str(cik).zfill(10)}.json").json()

    def filing_document(self, cik: str, accession: str, primary_document: str) -> bytes:
        cik_num = str(int(cik))
        acc_nodash = accession.replace("-", "")
        url = f"{SEC_ARCHIVES}/{cik_num}/{acc_nodash}/{primary_document}"
        return self._get(url, data_host=False).content


def _text(node, path: str, default=""):
    x = node.find(path)
    return (x.text or "").strip() if x is not None else default


def _value(node, path: str, default=""):
    x = node.find(path)
    if x is None:
        return default
    v = x.find("value")
    return ((v.text if v is not None else x.text) or "").strip()


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_form4(xml_bytes: bytes, filing: dict, ticker: str, cik: str) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    issuer = _text(root, "issuer/issuerName") or filing.get("company", "")
    owner = _text(root, "reportingOwner/reportingOwnerId/rptOwnerName")
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    roles = []
    if rel is not None:
        if _text(rel, "isDirector") == "1": roles.append("Director")
        if _text(rel, "isOfficer") == "1": roles.append(_text(rel, "officerTitle") or "Officer")
        if _text(rel, "isTenPercentOwner") == "1": roles.append(">10% Owner")
        if _text(rel, "isOther") == "1": roles.append(_text(rel, "otherText") or "Other")
    role = ", ".join(roles)

    events = []
    tx_nodes = list(root.findall("nonDerivativeTable/nonDerivativeTransaction"))
    tx_nodes += list(root.findall("derivativeTable/derivativeTransaction"))

    for i, tx in enumerate(tx_nodes):
        code = _text(tx, "transactionCoding/transactionCode")
        tx_date = _value(tx, "transactionDate")
        shares = _float(_value(tx, "transactionAmounts/transactionShares"))
        price = _float(_value(tx, "transactionAmounts/transactionPricePerShare"))
        acquired = _value(tx, "transactionAmounts/transactionAcquiredDisposedCode")
        after = _float(_value(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction"))
        value = (shares * price) if shares is not None and price is not None else None
        event = {
            **filing,
            "event_id": f"{filing['accession']}:{i}:{code}:{tx_date}",
            "event_type": "insider_transaction",
            "company": issuer,
            "ticker": ticker,
            "cik": cik,
            "actor": owner,
            "role": role,
            "transaction_code": code,
            "transaction_date": tx_date,
            "shares": shares,
            "price": price,
            "value": value,
            "ownership_after": after,
        }
        direction = "acquired" if acquired == "A" else "disposed" if acquired == "D" else "transacted"
        event["summary"] = f"{owner or 'Insider'} {direction} {shares:,.0f} shares" if shares is not None else f"{owner or 'Insider'} reported a transaction"
        if price is not None:
            event["summary"] += f" at ${price:,.2f}"
        score, reasons = score_event(event)
        event["score"], event["score_band"], event["reasons"] = score, band(score), "; ".join(reasons)
        events.append(event)
    return events


def _generic_event(filing: dict, ticker: str, cik: str) -> dict:
    form = filing["form"]
    labels = {
        "144": "Proposed affiliate sale filed",
        "SC 13D": "New Schedule 13D beneficial ownership filing",
        "SC 13D/A": "Schedule 13D amendment filed",
        "SC 13G": "New Schedule 13G beneficial ownership filing",
        "SC 13G/A": "Schedule 13G amendment filed",
        "8-K": "Material corporate event filed on Form 8-K",
        "8-K/A": "Form 8-K amendment filed",
        "3": "Initial insider ownership statement filed",
        "3/A": "Initial insider ownership amendment filed",
        "5": "Annual insider ownership statement filed",
        "5/A": "Annual insider ownership amendment filed",
    }
    event = {
        **filing,
        "event_id": filing["accession"],
        "event_type": "filing",
        "ticker": ticker,
        "cik": cik,
        "actor": "",
        "role": "",
        "transaction_code": "",
        "transaction_date": filing.get("report_date") or "",
        "shares": None,
        "price": None,
        "value": None,
        "ownership_after": None,
        "summary": labels.get(form, f"{form} filed"),
    }
    score, reasons = score_event(event)
    event["score"], event["score_band"], event["reasons"] = score, band(score), "; ".join(reasons)
    return event


def ingest_ticker(client: SecClient, ticker: str, cik: str, company: str, max_filings: int = 80) -> list[dict]:
    sub = client.submissions(cik)
    recent = sub.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    events: list[dict] = []

    n = min(len(forms), max_filings)
    for i in range(n):
        form = forms[i]
        if form not in TRACKED_FORMS:
            continue
        accession = recent["accessionNumber"][i]
        primary = recent["primaryDocument"][i]
        filed_at = recent["filingDate"][i]
        report_date = recent.get("reportDate", [""] * len(forms))[i]
        source_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{accession}-index.htm"
        filing = {
            "accession": accession,
            "form": form,
            "filed_at": filed_at,
            "company": company or sub.get("name", ticker),
            "primary_document": primary,
            "source_url": source_url,
            "report_date": report_date,
        }

        if form in {"4", "4/A"} and primary.lower().endswith((".xml", ".html", ".htm")):
            try:
                raw = client.filing_document(cik, accession, primary)
                # Ownership primary docs are XML even when rendered via HTML endpoints in many filings.
                if raw.lstrip().startswith(b"<?xml") or b"<ownershipDocument" in raw[:1000]:
                    events.extend(parse_form4(raw, filing, ticker, cik))
                    continue
            except Exception:
                pass
        events.append(_generic_event(filing, ticker, cik))
    return events
