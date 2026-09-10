from __future__ import annotations

FORM4_CODES={
 'P':('Open-market purchase','An insider voluntarily acquired shares in a market or private purchase.','high'),
 'S':('Open-market sale','An insider sold shares. The reason may be routine diversification, taxes, or a change in conviction, so context matters.','medium'),
 'A':('Equity award','The company granted equity as compensation. This is not the same as an insider choosing to buy stock.','low'),
 'M':('Option exercise / conversion','The insider exercised or converted a derivative security. This is often compensation-related and is not automatically bullish.','low'),
 'F':('Shares withheld for taxes','Shares were used or withheld to satisfy taxes or an exercise price. This is usually routine compensation activity.','low'),
 'G':('Gift / transfer','Shares were transferred as a gift. This usually says little about the insider\'s view of the stock.','low'),
 'D':('Disposition to issuer','Shares were disposed back to the company or otherwise surrendered. Context in the filing matters.','low'),
 'J':('Other transaction','The filing uses an "other" transaction code, so the footnotes or transaction description should be reviewed.','medium'),
}

ITEMS={
 '1.01':('Material agreement','The company entered into or amended a material definitive agreement.'),
 '1.02':('Material agreement ended','A material definitive agreement was terminated.'),
 '2.01':('Acquisition / disposition','The company completed a significant acquisition or sale of assets/business.'),
 '2.02':('Earnings / operating results','The company reported results or other operating information.'),
 '2.03':('New debt / obligation','The company created or became subject to a material financial obligation.'),
 '2.04':('Triggering event','An event occurred that may accelerate or increase a financial obligation.'),
 '3.01':('Listing / compliance event','The company disclosed a notice or failure involving exchange listing standards.'),
 '3.02':('Unregistered securities sale','The company sold equity securities outside a registered public offering.'),
 '4.01':('Auditor change','The company changed its independent accountant or disclosed a related accounting matter.'),
 '5.02':('Leadership change','A director or senior executive was appointed, departed, retired, terminated, or had compensation changed.'),
 '5.07':('Shareholder vote','The company reported the results of a shareholder vote.'),
 '7.01':('Regulation FD disclosure','The company furnished information under Regulation FD, often an investor presentation or public update.'),
 '8.01':('Other material event','The company disclosed another event it considered important enough to report promptly.'),
}

def _money(v):
 try:n=float(v or 0)
 except:n=0
 if not n:return ''
 if n>=1e9:return f'${n/1e9:.1f} billion'
 if n>=1e6:return f'${n/1e6:.1f} million'
 if n>=1e3:return f'${n/1e3:.0f} thousand'
 return f'${n:,.0f}'

def _shares(v):
 try:n=float(v or 0)
 except:n=0
 return f'{n:,.0f} shares' if n else ''

def _actor(e): return e.get('actor') or 'An insider'
def _ticker(e): return e.get('ticker') or e.get('company') or 'the company'

def build_story(e:dict)->dict:
 form=(e.get('form') or '').upper(); typ=e.get('event_type') or ''; code=(e.get('transaction_code') or '').upper()
 ticker=_ticker(e); company=e.get('company') or ticker; actor=_actor(e); role=e.get('role') or ''
 value=_money(e.get('value')); shares=_shares(e.get('shares')); price=e.get('price'); score=int(e.get('score') or 0)
 headline=''; happened=e.get('summary') or f'{form} filed'; meaning=''; caveat=''; reel=''; content=35

 if typ=='insider_transaction' or form=='4':
  label,meaning,kind=FORM4_CODES.get(code,('Insider transaction','An insider reported a change in ownership. The transaction code and footnotes determine what actually happened.','medium'))
  amount=value or shares or 'shares'
  if code=='P':
   headline=f'{actor} buys {value or shares or "stock"} of {ticker}'
   happened=f'{actor}{" ("+role+")" if role else ""} reported an open-market/private purchase'
   if shares:happened+=f' of {shares}'
   if price:happened+=f' at about ${float(price):,.2f} per share'
   if value:happened+=f', worth about {value}'
   caveat='This is different from an RSU grant, option exercise, gift, or tax-withholding transaction.'
   reel=f'{actor} just bought {value or shares or "shares"} of {ticker}. Here is why the filing stands out.'
   content=min(100,55+score//2)
  elif code=='S':
   headline=f'{actor} sells {value or shares or "stock"} of {ticker}'
   happened=f'{actor}{" ("+role+")" if role else ""} reported a sale'
   if shares:happened+=f' of {shares}'
   if price:happened+=f' at about ${float(price):,.2f} per share'
   if value:happened+=f', worth about {value}'
   caveat='Insider sales are not automatically bearish; compensation, diversification, taxes, and pre-arranged 10b5-1 plans can matter.'
   reel=f'An insider at {ticker} just reported a {value or shares or "stock"} sale. The context matters more than the headline.'
   content=min(92,40+score//2)
  else:
   headline=f'{ticker}: {label}'
   happened=e.get('summary') or f'{actor} reported {label.lower()}'
   caveat='This is usually less informative than an actual open-market purchase or sale.' if kind=='low' else 'The filing footnotes may be important for interpretation.'
   reel=f'{ticker} filed an insider transaction, but it is not a simple buy or sell. Here is what {label.lower()} actually means.'
   content=25 if kind=='low' else 45

 elif form=='144' or typ=='proposed_sale':
  headline=f'{ticker} insider files notice of a proposed stock sale'
  happened=f'{actor} filed Form 144 giving notice of a proposed affiliate sale'
  if shares:happened+=f' involving {shares}'
  if value:happened+=f' with an indicated value of about {value}'
  meaning='Form 144 is advance notice of certain proposed affiliate sales. It is not proof that the full proposed amount was ultimately sold.'
  caveat='The source of the shares, any 10b5-1 plan, and prior recent sales can materially change how informative the filing is.'
  reel=f'An insider at {ticker} just filed to sell {value or shares or "stock"} — but a Form 144 does not mean the sale already happened.'
  content=min(90,38+score//2+(8 if value else 0))

 elif '13D' in form:
  pct=e.get('ownership_after'); pcttxt=f'{float(pct):.1f}%' if pct else 'more than 5%'
  headline=f'Major investor discloses a {pcttxt} stake in {ticker}'
  happened=f'{actor or "A major investor"} filed {form.replace("SC ","Schedule ")} reporting beneficial ownership of {pcttxt} of {company}.'
  meaning='Schedule 13D is a significant-ownership filing and can be especially important when the investor may seek to influence management, strategy, capital allocation, board composition, or control.'
  caveat='The most important section is often the investor\'s stated purpose and plans; ownership alone does not prove an activist campaign.'
  reel=f'A major investor just disclosed a {pcttxt} stake in {ticker}. The most important part may be what they say they want next.'
  content=min(100,65+score//3)

 elif '13G' in form:
  pct=e.get('ownership_after'); pcttxt=f'{float(pct):.1f}%' if pct else 'more than 5%'
  headline=f'Large shareholder reports a {pcttxt} stake in {ticker}'
  happened=f'{actor or "A large shareholder"} filed {form.replace("SC ","Schedule ")} reporting beneficial ownership of {pcttxt}.'
  meaning='Schedule 13G generally reports significant beneficial ownership under a less activist-oriented reporting framework than Schedule 13D.'
  caveat='A large passive stake can still be important, but it does not by itself imply an attempt to influence the company.'
  reel=f'A shareholder just disclosed a {pcttxt} position in {ticker}. Here is what a 13G does — and does not — tell investors.'
  content=min(85,45+score//3)

 elif form.startswith('8-K') or typ=='material_event':
  raw=e.get('detail') or ''
  codes=[x.strip() for x in raw.replace(';',',').split(',') if x.strip()]
  known=[(c,ITEMS[c]) for c in codes if c in ITEMS]
  if known:
   top=known[0][1]
   headline=f'{ticker}: {top[0]}'
   happened=f'{company} filed an 8-K reporting '+', '.join(ITEMS[c][0].lower() for c,_ in known[:3])+'.'
   meaning=' '.join(ITEMS[c][1] for c,_ in known[:2])
   caveat='An 8-K can contain several unrelated items; the underlying filing should be checked before treating the event as positive or negative.'
   reel=f'{ticker} just filed an 8-K about {top[0].lower()}. Here is what the company disclosed and why it could matter.'
   content=min(92,40+score//2+(10 if codes and codes[0] in {'1.01','2.01','3.01','3.02','4.01','5.02'} else 0))
  else:
   headline=f'{ticker} files a material corporate update'
   happened=e.get('summary') or f'{company} filed an 8-K.'
   meaning='An 8-K is a prompt corporate-event filing. Its significance depends on the item disclosed.'
   caveat='The filing needs item-level context before it can be characterized as bullish, bearish, or routine.'
   reel=f'{ticker} just filed an 8-K. Here is the event investors should actually care about.'
   content=min(70,30+score//2)
 else:
  headline=f'{ticker}: {form} filed'
  meaning='This filing may contain a market-relevant ownership or corporate event.'
  caveat='Review the underlying SEC source for context.'
  reel=f'{ticker} just filed {form}. Here is the part investors need to know.'
  content=min(70,30+score//2)

 importance='High' if score>=80 else 'Interesting' if score>=60 else 'Notable' if score>=40 else 'Routine'
 recommendation='STRONG REEL' if content>=80 else 'POSSIBLE REEL' if content>=55 else 'SKIP / CONTEXT ONLY'
 return {
   'headline':headline,'what_happened':happened,'what_it_means':meaning,'context':caveat,
   'importance':importance,'content_score':int(content),'content_recommendation':recommendation,
   'reel_headline':reel,
 }
