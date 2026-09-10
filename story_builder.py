from __future__ import annotations
import json,re

FORM4_CODES={
 'P':('Open-market/private purchase','An insider chose to acquire shares in a market or private purchase.','high'),
 'S':('Sale','An insider sold shares. The reason may be routine diversification, taxes, or a change in conviction, so context matters.','medium'),
 'A':('Equity award','The company granted equity as compensation. This is not the same as an insider choosing to buy stock.','low'),
 'M':('Option exercise / conversion','The insider exercised or converted a derivative security. This is often compensation-related.','low'),
 'F':('Shares withheld for taxes','Shares were surrendered or withheld to satisfy taxes or an exercise price. This is usually routine compensation activity.','low'),
 'G':('Gift / transfer','Shares were transferred as a gift. This usually says little about the insider\'s market view.','low'),
 'D':('Disposition to issuer','Shares were disposed back to the company or surrendered. Context matters.','low'),
 'J':('Other transaction','The filing uses an “other” code; the filing explanation matters.','medium'),
}

def _money(v):
 try:n=float(v or 0)
 except:n=0
 if not n:return ''
 if n>=1e9:return f'${n/1e9:.1f}B'
 if n>=1e6:return f'${n/1e6:.1f}M'
 if n>=1e3:return f'${n/1e3:.0f}K'
 return f'${n:,.0f}'
def _shares(v):
 try:n=float(v or 0)
 except:n=0
 return f'{n:,.0f} shares' if n else ''
def _pct(v):
 try:return f'{float(v):.1f}%'
 except:return ''
def _details(e):
 d=e.get('details')
 if isinstance(d,dict):return d
 try:return json.loads(e.get('details_json') or '{}')
 except:return {}
def _clip(s,n=420):
 s=' '.join(str(s or '').split())
 return s if len(s)<=n else s[:n-1].rstrip()+'…'
def _join_bits(bits): return '; '.join(x for x in bits if x)

def build_story(e:dict)->dict:
 form=(e.get('form') or '').upper(); typ=e.get('event_type') or ''; code=(e.get('transaction_code') or '').upper(); d=_details(e)
 ticker=e.get('ticker') or ''; company=e.get('company') or ticker; actor=e.get('actor') or 'An insider'; role=e.get('role') or ''
 value=_money(e.get('value')); shares=_shares(e.get('shares')); price=e.get('price'); score=int(e.get('score') or 0)
 headline=e.get('summary') or f'{form} filed'; happened=headline; meaning=''; context=''; reel=''; content=20; facts=[]

 if typ=='insider_transaction' or form=='4':
  label,meaning,kind=FORM4_CODES.get(code,('Insider ownership change','An insider reported a change in ownership.','medium'))
  security=d.get('security') or 'shares'; holdings=_shares(e.get('ownership_after')); change=_pct(d.get('holdings_change_pct')); foot=_clip(d.get('footnotes'),300)
  who=actor+(f' ({role})' if role else '')
  facts=[f'Transaction: {label}',f'Person: {who}',f'Security: {security}',f'Amount: {shares}' if shares else '',f'Price: ${float(price):,.2f}/share' if price else '',f'Approx. value: {value}' if value else '',f'Holdings after: {holdings}' if holdings else '',f'Position changed by about {change}' if change else '',f'Ownership: {d.get("direct_or_indirect")}' if d.get('direct_or_indirect') else '']
  if code=='P':
   headline=f'{who} buys {value or shares or "stock"} of {ticker}'
   happened=f'{who} reported an actual purchase'
   if shares:happened+=f' of {shares}'
   if price:happened+=f' at about ${float(price):,.2f} per share'
   if value:happened+=f' (about {value})'
   if holdings:happened+=f'. Holdings after the transaction: {holdings}'
   meaning='This is materially different from receiving an RSU grant or exercising an option: the filing codes this as a purchase.'
   context=('The purchase increased the reported position by about '+change+'. ' if change else '')+('Filing note: '+foot if foot else 'Check whether multiple insiders are buying and whether price/volume confirm the signal.')
   reel=f'{who} just bought {value or shares or "stock"} of {ticker}. Here is what the SEC filing shows.'
   content=min(98,60+score//3+(8 if value else 0)+(6 if change else 0))
  elif code=='S':
   headline=f'{who} sells {value or shares or "stock"} of {ticker}'
   happened=f'{who} reported a stock sale'
   if shares:happened+=f' of {shares}'
   if price:happened+=f' at about ${float(price):,.2f} per share'
   if value:happened+=f' (about {value})'
   if holdings:happened+=f'. Reported holdings after: {holdings}'
   meaning='This is a completed reported disposition, but insider sales are not automatically bearish.'
   context=('The sale represented about '+change+' of the prior reported position. ' if change else '')+('Filing note: '+foot if foot else 'A 10b5-1 plan, taxes, option exercise, or diversification can materially change the interpretation.')
   reel=f'{who} just reported selling {value or shares or "stock"} of {ticker}. Here is how large the sale actually was.'
   content=min(90,45+score//3+(10 if value else 0)+(8 if change else 0))
  else:
   headline=f'{ticker}: {label}'
   happened=f'{who} reported {label.lower()}'
   if shares:happened+=f' involving {shares}'
   if holdings:happened+=f'. Holdings after: {holdings}'
   context=('Filing note: '+foot) if foot else ('This is usually lower-signal compensation/ownership activity rather than an open-market buy or sell.' if kind=='low' else 'The transaction explanation should be considered before drawing a conclusion.')
   reel=f'{ticker} reported {label.lower()}. Here is what that means — and why it is not the same as a normal buy or sale.'
   content=18 if kind=='low' else 42

 elif form=='144' or typ=='proposed_sale':
  acqs=d.get('acquisition_history') or []; prior=d.get('prior_3_month_sales') or []; no_prior=d.get('prior_3_month_sales_none'); plan=d.get('tenb5_1_plan'); plan_date=d.get('plan_adoption_date')
  natures=[]; total_acq=0
  for a in acqs:
   if a.get('nature') and a.get('nature') not in natures:natures.append(a.get('nature'))
   try:total_acq+=float(a.get('shares') or 0)
   except:pass
  acquisition_phrase=', '.join(natures[:3])
  who=actor+(f' ({role})' if role and role!='Affiliate' else '')
  headline=f'{ticker} insider proposes selling {value or shares or "stock"}'
  happened=f'{who} filed Form 144 giving advance notice of a proposed sale'
  if shares:happened+=f' involving {shares}'
  if value:happened+=f' with an indicated market value of about {value}'
  if e.get('transaction_date'):happened+=f'. Approximate sale date: {e.get("transaction_date")}'
  meaning='Form 144 is a notice of an intended affiliate sale. It does not prove the shares have already been sold or that the full proposed amount will be sold.'
  ctx=[]
  if acquisition_phrase:ctx.append('The shares trace to '+acquisition_phrase.lower())
  if acqs:
   dates=[a.get('date') for a in acqs if a.get('date')];
   if dates:ctx.append(f'acquisition history spans {min(dates)} to {max(dates)}')
  if no_prior:ctx.append('the filer reported no sales during the prior 3 months')
  elif prior:ctx.append(f'{len(prior)} prior-sale entr'+('y' if len(prior)==1 else 'ies')+' reported for the prior 3 months')
  if plan:ctx.append('a Rule 10b5-1 trading plan is indicated'+(f' (adopted {plan_date})' if plan_date else ''))
  if d.get('broker'):ctx.append('broker: '+str(d.get('broker')))
  context=_join_bits(ctx) or 'The filing did not expose enough structured context to determine share origin, prior sales, or a 10b5-1 plan.'
  facts=[f'Person: {who}',f'Proposed amount: {shares}' if shares else '',f'Indicated value: {value}' if value else '',f'Approx. sale date: {e.get("transaction_date")}' if e.get('transaction_date') else '',f'Share origin: {acquisition_phrase}' if acquisition_phrase else '',f'Prior 3-month sales: {"None reported" if no_prior else len(prior) if prior else "Not resolved"}',f'10b5-1 plan: {"Yes" if plan else "Not identified"}',f'Broker: {d.get("broker")}' if d.get('broker') else '',f'Exchange: {d.get("exchange")}' if d.get('exchange') else '']
  reel=f'{who} filed to sell {value or shares or "stock"} of {ticker}'
  if acquisition_phrase:reel+=f' — and the shares came from {acquisition_phrase.lower()}'
  reel+='.'
  completeness=sum(bool(x) for x in [shares,value,actor,acquisition_phrase,no_prior or prior,plan or plan_date])
  content=min(94,30+score//3+completeness*7)

 elif '13D' in form or '13G' in form:
  pct=_pct(e.get('ownership_after')) or 'more than 5%'; owned=_shares(d.get('shares_beneficially_owned')); purpose=_clip(d.get('purpose'),500); is13d='13D' in form
  who=actor if actor and actor!='An insider' else 'A large shareholder'
  headline=(f'Major investor reports a {pct} stake in {ticker}' if is13d else f'Large shareholder reports a {pct} stake in {ticker}')
  happened=f'{who} filed {form.replace("SC ","Schedule ")} reporting beneficial ownership of {pct} of {company}'
  if owned:happened+=f' ({owned})'
  meaning=('Schedule 13D is a >5% ownership disclosure commonly used when the holder may not qualify for the more passive 13G route. The stated purpose can be highly important.' if is13d else 'Schedule 13G reports significant beneficial ownership under a generally less activist-oriented framework than Schedule 13D.')
  context=('Purpose/plan language: '+purpose) if purpose else ('No clear purpose language was extracted from this filing.' if is13d else 'A large passive stake can matter, but it does not by itself imply an activist campaign.')
  facts=[f'Holder: {who}',f'Ownership: {pct}',f'Shares beneficially owned: {owned}' if owned else '',f'Sole voting power: {_shares(d.get("sole_voting_power"))}' if d.get('sole_voting_power') else '',f'Shared voting power: {_shares(d.get("shared_voting_power"))}' if d.get('shared_voting_power') else '']
  reel=(f'A major investor just disclosed a {pct} stake in {ticker}. '+('Here is what the filing says they may want next.' if purpose else 'Here is what that ownership filing means.'))
  content=min(98,(62 if is13d else 45)+score//3+(12 if purpose else 0)+(8 if e.get('ownership_after') else 0))

 elif form.startswith('8-K') or typ=='material_event':
  codes=d.get('item_codes') or []; labels=d.get('items') or []; sections=d.get('sections') or []
  top=labels[0] if labels else 'Material corporate event'; headline=f'{ticker}: {top}'
  happened=f'{company} filed an 8-K reporting '+(', '.join(labels[:3]).lower() if labels else 'a corporate event')
  meaning='An 8-K is an event report. The item number tells you what actually happened; different 8-K items can represent very different events.'
  summaries=[_clip(s.get('summary_text'),260) for s in sections if s.get('summary_text')]
  context=('Filing detail: '+summaries[0]) if summaries else ('Items reported: '+', '.join(codes) if codes else 'The filing item could not be resolved from the document.')
  facts=['Items: '+', '.join(f'{c} — {labels[i] if i<len(labels) else ""}' for i,c in enumerate(codes[:5]))] if codes else []
  reel=f'{ticker} just reported {top.lower()} in an 8-K. Here is what the filing actually says.'
  high={'Acquisition or disposition','Exit or disposal plan','Material impairment','Exchange listing/compliance event','Auditor/accountant change','Financial statements should no longer be relied upon','Change in control','Director/executive change or compensation event'}
  content=min(94,38+score//3+(15 if top in high else 0)+(10 if summaries else 0))

 else:
  meaning='The filing was collected, but the parser did not resolve a supported transaction/event type.';context='This should generally be skipped until the underlying event is parsed.';reel=f'{ticker} filed {form}.';content=5

 # If the parser lacks core facts, do not oversell it as reel-ready.
 factual_fields=sum(bool(x) for x in [e.get('actor'),e.get('shares'),e.get('value'),e.get('ownership_after'),d.get('purpose'),d.get('item_codes'),d.get('acquisition_history')])
 if factual_fields==0 and form not in {'4'}: content=min(content,35)
 recommendation='STRONG REEL' if content>=80 else 'POSSIBLE REEL' if content>=55 else 'SKIP / LOW INFO'
 facts=[x for x in facts if x]
 return {'headline':headline,'what_happened':happened,'what_it_means':meaning,'context':context,'facts':facts,'reel_headline':reel,'content_score':content,'content_recommendation':recommendation}
