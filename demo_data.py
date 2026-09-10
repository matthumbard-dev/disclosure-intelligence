from datetime import date, timedelta
from scoring import score_event, band


def demo_events():
    today = date.today()
    base = [
        dict(event_id="demo1", accession="demo1", form="4", filed_at=str(today), transaction_date=str(today-timedelta(days=1)), company="Acme Robotics", ticker="ACME", cik="0000000001", event_type="insider_transaction", actor="Jordan Lee", role="Chief Executive Officer", transaction_code="P", shares=50000, price=25.40, value=1270000, ownership_after=325000, source_url="https://www.sec.gov/edgar/search/", primary_document="", summary="CEO acquired 50,000 shares at $25.40"),
        dict(event_id="demo2", accession="demo2", form="SC 13D", filed_at=str(today), transaction_date=str(today), company="Northstar Systems", ticker="NSTR", cik="0000000002", event_type="filing", actor="Atlas Capital", role="", transaction_code="", shares=None, price=None, value=None, ownership_after=None, source_url="https://www.sec.gov/edgar/search/", primary_document="", summary="New Schedule 13D beneficial ownership filing"),
        dict(event_id="demo3", accession="demo3", form="144", filed_at=str(today), transaction_date=str(today), company="Vertex Bio", ticker="VBIO", cik="0000000003", event_type="filing", actor="", role="", transaction_code="", shares=None, price=None, value=None, ownership_after=None, source_url="https://www.sec.gov/edgar/search/", primary_document="", summary="Proposed affiliate sale filed"),
        dict(event_id="demo4", accession="demo4", form="4", filed_at=str(today-timedelta(days=1)), transaction_date=str(today-timedelta(days=2)), company="Cloud Forge", ticker="CLDF", cik="0000000004", event_type="insider_transaction", actor="Morgan Chen", role="Director", transaction_code="P", shares=12000, price=18.75, value=225000, ownership_after=72000, source_url="https://www.sec.gov/edgar/search/", primary_document="", summary="Director acquired 12,000 shares at $18.75"),
        dict(event_id="demo5", accession="demo5", form="8-K", filed_at=str(today), transaction_date=str(today), company="Harbor Energy", ticker="HBR", cik="0000000005", event_type="filing", actor="", role="", transaction_code="", shares=None, price=None, value=None, ownership_after=None, source_url="https://www.sec.gov/edgar/search/", primary_document="", summary="Material corporate event filed on Form 8-K"),
    ]
    out=[]
    for e in base:
        s,r=score_event(e); e["score"]=s; e["score_band"]=band(s); e["reasons"]="; ".join(r); out.append(e)
    return out
