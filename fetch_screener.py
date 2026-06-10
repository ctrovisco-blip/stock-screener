import json, time, os, sys, urllib.request
import yfinance as yf
from datetime import datetime, timezone

COUNTRY_FLAG = {
    "United States": "🇺🇸", "United Kingdom": "🇬🇧", "Germany": "🇩🇪",
    "France": "🇫🇷", "Portugal": "🇵🇹", "Netherlands": "🇳🇱", "Canada": "🇨🇦",
    "Japan": "🇯🇵", "China": "🇨🇳", "Australia": "🇦🇺", "Switzerland": "🇨🇭",
    "Sweden": "🇸🇪", "Denmark": "🇩🇰", "Norway": "🇳🇴", "Spain": "🇪🇸",
    "Italy": "🇮🇹", "Belgium": "🇧🇪", "Ireland": "🇮🇪", "Taiwan": "🇹🇼",
    "South Korea": "🇰🇷", "India": "🇮🇳", "Brazil": "🇧🇷",
}


def fmt_large(v):
    if v is None: return None
    v = float(v)
    if abs(v) >= 1e12: return f"{v/1e12:.2f}T"
    if abs(v) >= 1e9:  return f"{v/1e9:.2f}B"
    if abs(v) >= 1e6:  return f"{v/1e6:.1f}M"
    return str(round(v, 2))


def sr(v, n=2):
    if v is None: return None
    try: return round(float(v), n)
    except: return None


def pct_dec(v, n=2):
    if v is None: return None
    try: return round(float(v) * 100, n)
    except: return None


def div_cagr_5y(t):
    try:
        divs = t.dividends
        if divs is None or len(divs) < 4: return None
        annual = {}
        for dt, amt in divs.items():
            annual.setdefault(dt.year, 0)
            annual[dt.year] += float(amt)
        now = datetime.now().year
        recent = {y: v for y, v in annual.items() if y >= now - 5}
        if len(recent) < 2: return None
        yrs = sorted(recent.keys())
        n = yrs[-1] - yrs[0]
        if n <= 0 or recent[yrs[0]] <= 0: return None
        return round(((recent[yrs[-1]] / recent[yrs[0]]) ** (1 / n) - 1) * 100, 2)
    except: return None


def buyback_yield(t, mkt_cap):
    try:
        if not mkt_cap or mkt_cap <= 0: return None
        cf = t.cashflow
        if cf is None or cf.empty: return None
        for rname in ["Repurchase Of Capital Stock", "Common Stock Payments"]:
            if rname in cf.index:
                amounts = [abs(float(cf.loc[rname, c])) for c in list(cf.columns)[:2]
                           if str(cf.loc[rname, c]) != "nan"]
                if amounts:
                    return round(sum(amounts) / len(amounts) / mkt_cap * 100, 2)
        return None
    except: return None


def rev_growth(t):
    try:
        fin = t.financials
        if fin is None or fin.empty: return None
        rev_rows = [r for r in fin.index if "Revenue" in str(r) or "revenue" in str(r)]
        if not rev_rows: return None
        row = fin.loc[rev_rows[0]]
        cols = [c for c in row.index if row[c] and str(row[c]) != "nan"]
        if len(cols) < 2: return None
        r1, r0 = float(row[cols[0]]), float(row[cols[1]])
        if r0 and r0 > 0:
            return round((r1 / r0 - 1) * 100, 2)
    except: return None


def safe_float(df, row, col):
    try:
        v = float(df.loc[row, col])
        return None if str(v) == "nan" else v
    except: return None


def find_row(df, *keywords):
    for r in df.index:
        rs = str(r).lower()
        if all(kw.lower() in rs for kw in keywords):
            return r
    return None


FMP_API_KEY    = os.environ.get("FMP_API_KEY", "")
FISCAL_API_KEY = os.environ.get("FISCAL_API_KEY", "")

_YF_TO_FISCAL = {
    "NMS": "NASDAQ", "NMQ": "NASDAQ", "NGM": "NASDAQ", "NIM": "NASDAQ",
    "NYQ": "NYSE",   "NYS": "NYSE",
    "PCX": "NYSE",   "ASE": "NYSE",
    "LSE": "LSE",    "FRA": "XETRA",  "XETRA": "XETRA",
    "PAR": "EURONEXT", "AMS": "EURONEXT",
    "TSX": "TSX",    "CVE": "TSX",
}

def _fiscal_get(path):
    if not FISCAL_API_KEY:
        return None
    sep = "&" if "?" in path else "?"
    url = f"https://api.fiscal.ai/{path}{sep}apiKey={FISCAL_API_KEY}"
    try:
        with urllib.request.urlopen(url, timeout=12) as r:
            data = json.loads(r.read().decode())
            return data if data else None
    except Exception as e:
        print(f"  Fiscal.ai error ({path[:50]}): {e}")
        return None

def _fiscal_company_key(ticker, yf_exchange):
    exchange = _YF_TO_FISCAL.get(yf_exchange or "", "")
    candidates = []
    if exchange:
        candidates.append(f"{exchange}_{ticker}")
    for ex in ("NASDAQ", "NYSE"):
        key = f"{ex}_{ticker}"
        if key not in candidates:
            candidates.append(key)
    return candidates

def _fiscal_find(items, *keywords):
    for row in items:
        label = str(row.get("label", "") or row.get("name", "")).lower()
        if all(kw.lower() in label for kw in keywords):
            return row
    return None

def _fiscal_latest_val(row):
    if not row:
        return None
    for key in ("annualValues", "values", "data"):
        vals = row.get(key)
        if vals and isinstance(vals, list):
            for v in vals:
                val = v.get("value") if isinstance(v, dict) else v
                try:
                    f = float(val)
                    if f == f:
                        return f
                except (TypeError, ValueError):
                    continue
    return None

def _fiscal_unwrap(resp):
    if not resp:
        return []
    rows = resp if isinstance(resp, list) else resp.get("data", resp.get("items", []))
    if rows and isinstance(rows[0], dict) and "items" in rows[0]:
        rows = rows[0].get("items", [])
    return rows

def fetch_fiscal_ai(ticker, yf_exchange, entry):
    """Fetch missing fields from Fiscal.ai as fallback (only fills None values)."""
    if not FISCAL_API_KEY:
        return {}

    missing = {k for k, v in entry.items() if v is None}
    needs_income  = missing & {"grossMargin", "operatingMargin", "netMargin", "revenueGrowth"}
    needs_balance = missing & {"debtToEquity", "currentRatio"}
    needs_cf      = missing & {"fcfYield"}

    if not (needs_income or needs_balance or needs_cf):
        return {}

    company_key = None
    inc_resp = None
    for ck in _fiscal_company_key(ticker, yf_exchange):
        resp = _fiscal_get(f"v1/company/financials/income-statement/standardized?companyKey={ck}&periodType=annual&limit=3")
        if resp:
            company_key = ck
            inc_resp = resp
            break

    if not company_key:
        print(f"  Fiscal.ai: no data for {ticker}")
        return {}

    print(f"  Fiscal.ai: companyKey={company_key}")
    out = {}

    if needs_income and inc_resp:
        items = _fiscal_unwrap(inc_resp)
        rev_row = _fiscal_find(items, "revenue")
        ni_row  = _fiscal_find(items, "net", "income")
        gp_row  = _fiscal_find(items, "gross", "profit")
        oi_row  = _fiscal_find(items, "operating", "income")
        rev = _fiscal_latest_val(rev_row)
        ni  = _fiscal_latest_val(ni_row)
        gp  = _fiscal_latest_val(gp_row)
        oi  = _fiscal_latest_val(oi_row)
        if rev and rev > 0:
            if entry.get("netMargin") is None and ni is not None:
                out["netMargin"] = sr(ni / rev * 100, 2)
            if entry.get("grossMargin") is None and gp is not None:
                out["grossMargin"] = sr(gp / rev * 100, 2)
            if entry.get("operatingMargin") is None and oi is not None:
                out["operatingMargin"] = sr(oi / rev * 100, 2)
        if entry.get("revenueGrowth") is None and rev_row:
            rev_vals = []
            for key in ("annualValues", "values", "data"):
                vals = rev_row.get(key)
                if vals and isinstance(vals, list):
                    for v in vals[:2]:
                        try: rev_vals.append(float(v.get("value") if isinstance(v, dict) else v))
                        except: pass
                    break
            if len(rev_vals) >= 2 and rev_vals[1] > 0:
                out["revenueGrowth"] = sr((rev_vals[0] / rev_vals[1] - 1) * 100, 2)

    if needs_balance:
        bs_resp = _fiscal_get(f"v1/company/financials/balance-sheet/standardized?companyKey={company_key}&periodType=annual&limit=1")
        if bs_resp:
            items = _fiscal_unwrap(bs_resp)
            td_row = _fiscal_find(items, "total", "debt") or _fiscal_find(items, "long", "term", "debt")
            eq_row = _fiscal_find(items, "total", "equity") or _fiscal_find(items, "stockholder")
            ca_row = _fiscal_find(items, "current", "assets")
            cl_row = _fiscal_find(items, "current", "liabilities")
            td = _fiscal_latest_val(td_row)
            eq = _fiscal_latest_val(eq_row)
            ca = _fiscal_latest_val(ca_row)
            cl = _fiscal_latest_val(cl_row)
            if entry.get("debtToEquity") is None and td is not None and eq and eq > 0:
                out["debtToEquity"] = sr(td / eq * 100, 2)
            if entry.get("currentRatio") is None and ca is not None and cl and cl > 0:
                out["currentRatio"] = sr(ca / cl, 2)

    if needs_cf:
        cf_resp = _fiscal_get(f"v1/company/financials/cash-flow/standardized?companyKey={company_key}&periodType=annual&limit=1")
        if cf_resp:
            items = _fiscal_unwrap(cf_resp)
            opcf_row  = _fiscal_find(items, "operating")
            capex_row = _fiscal_find(items, "capital", "expenditure") or _fiscal_find(items, "capex")
            opcf  = _fiscal_latest_val(opcf_row)
            capex = _fiscal_latest_val(capex_row)
            mc_str = str(entry.get("marketCap") or "")
            if entry.get("fcfYield") is None and opcf is not None and capex is not None and mc_str:
                try:
                    mult = {"T": 1e12, "B": 1e9, "M": 1e6}.get(mc_str[-1], 1)
                    mc = float(mc_str[:-1]) * mult if mc_str[-1] in "TBM" else float(mc_str)
                    if mc > 0:
                        out["fcfYield"] = sr((opcf + capex) / mc * 100, 2)
                except: pass

    if out:
        print(f"  Fiscal.ai filled: {list(out.keys())}")
    return out


def fmp_get(path):
    if not FMP_API_KEY:
        return None
    url = f"https://financialmodelingprep.com/api/v3/{path}&apikey={FMP_API_KEY}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode())
            return data if data else None
    except Exception as e:
        print(f"  FMP error ({path[:40]}): {e}")
        return None


def fetch_fmp(ticker):
    out = {}

    km = fmp_get(f"key-metrics-ttm/{ticker}?limit=1")
    if km and isinstance(km, list) and km:
        m = km[0]
        ev_ebitda = m.get("enterpriseValueOverEBITDATTM")
        if ev_ebitda and float(ev_ebitda) > 0:
            out["evEbitda"] = sr(ev_ebitda, 1)
        roic = m.get("roicTTM")
        if roic is not None:
            out["roic"] = sr(float(roic) * 100 if abs(float(roic)) <= 1 else float(roic), 2)

    profile = fmp_get(f"profile/{ticker}?")
    if profile and isinstance(profile, list) and profile:
        p = profile[0]
        insider = p.get("insidersOwnership")
        inst    = p.get("institutionalOwnership")
        if insider is not None:
            out["insiderOwnership"] = sr(float(insider) * 100 if float(insider) <= 1 else float(insider), 2)
        if inst is not None:
            out["institutionalOwnership"] = sr(float(inst) * 100 if float(inst) <= 1 else float(inst), 2)

    si = fmp_get(f"historical/shares_float?symbol={ticker}&")
    if si and isinstance(si, list) and si:
        short_pct = si[0].get("shortPercentOfFloat")
        if short_pct is not None:
            out["shortInterest"] = sr(float(short_pct) * 100 if float(short_pct) <= 1 else float(short_pct), 2)

    rtg = fmp_get(f"analyst-stock-recommendations/{ticker}?limit=1")
    if rtg and isinstance(rtg, list) and rtg:
        r = rtg[0]
        buy  = (r.get("analystRatingsbuy")  or 0) + (r.get("analystRatingsStrongBuy") or 0)
        hold = r.get("analystRatingsHold")  or 0
        sell = (r.get("analystRatingsSell") or 0) + (r.get("analystRatingsStrongSell") or 0)
        total = buy + hold + sell
        if total > 0:
            out["analystBuy"]  = int(buy)
            out["analystHold"] = int(hold)
            out["analystSell"] = int(sell)

    return out


def generate_summary(e):
    parts = []
    sym = {"EUR": "€", "GBP": "£", "USD": "$"}.get(e.get("currency", "USD"), "$")

    val = []
    pe = e.get("pe")
    if pe is not None:
        if pe < 0:    val.append("empresa ainda sem lucros (P/E negativo)")
        elif pe < 15: val.append(f"P/E de {pe:.0f}x (atrativo)")
        elif pe < 25: val.append(f"P/E de {pe:.0f}x (razoável)")
        elif pe < 40: val.append(f"P/E de {pe:.0f}x (premium)")
        else:         val.append(f"P/E de {pe:.0f}x (muito elevado)")
    ps = e.get("priceToSales")
    if ps is not None:
        val.append(f"P/S {ps:.1f}x")
    pb = e.get("priceToBook")
    if pb is not None and pb > 0:
        val.append(f"P/B {pb:.1f}x")
    if val:
        parts.append(f"Negoceia a {', '.join(val)}.")

    gm = []
    rg = e.get("revenueGrowth")
    if rg is not None:
        if rg > 30:   gm.append(f"crescimento de receita forte (+{rg:.0f}%)")
        elif rg > 10: gm.append(f"crescimento de receita de +{rg:.0f}%")
        elif rg > 0:  gm.append(f"crescimento de receita moderado (+{rg:.0f}%)")
        else:         gm.append(f"receita em queda ({rg:.0f}%)")
    nm = e.get("netMargin")
    if nm is not None:
        if nm > 25:   gm.append(f"margens excelentes (net {nm:.0f}%)")
        elif nm > 10: gm.append(f"margens sólidas (net {nm:.0f}%)")
        elif nm > 0:  gm.append(f"margens reduzidas (net {nm:.0f}%)")
        else:         gm.append(f"margem líquida negativa ({nm:.0f}%)")
    roe = e.get("roe")
    if roe is not None and roe > 20:
        gm.append(f"ROE de {roe:.0f}%")
    if gm:
        parts.append(f"{', '.join(gm[:2]).capitalize()}.")

    tp = e.get("targetPrice")
    cp = e.get("curPrice")
    upside = None
    if tp and cp:
        upside = (tp / cp - 1) * 100
        direction = f"+{upside:.0f}% upside" if upside >= 0 else f"{upside:.0f}% downside"
        parts.append(f"Analistas têm preço-alvo de {sym}{tp:.2f} ({direction}).")

    score = 0
    if pe is not None:
        if pe < 0: score -= 1
        elif pe < 20: score += 2
        elif pe < 35: score += 1
        else: score -= 1
    if rg is not None:
        if rg > 20: score += 2
        elif rg > 5: score += 1
        elif rg < 0: score -= 1
    if nm is not None:
        if nm > 20: score += 2
        elif nm > 5: score += 1
        elif nm < 0: score -= 2
    de = e.get("debtToEquity")
    if de is not None:
        if de < 50: score += 1
        elif de > 200: score -= 1
    if upside is not None:
        if upside > 20: score += 2
        elif upside > 0: score += 1
        elif upside < -10: score -= 1
    fcfy = e.get("fcfYield")
    if fcfy is not None:
        if fcfy > 5: score += 1
        elif fcfy < 0: score -= 1

    if pe is not None and pe < 0 and (nm is None or nm < 0):
        verdict, vcolor = "Especulativo", "#F0883E"
    elif score >= 6:
        verdict, vcolor = "Muito Favorável", "#089981"
    elif score >= 3:
        verdict, vcolor = "Favorável", "#089981"
    elif score >= 0:
        verdict, vcolor = "Neutro", "#c8ccd4"
    elif score >= -2:
        verdict, vcolor = "Cauteloso", "#F0883E"
    else:
        verdict, vcolor = "Desfavorável", "#ef5350"

    return {"text": " ".join(parts), "verdict": verdict, "verdictColor": vcolor}


def get_price_history(t):
    out = {}
    import math
    periods = [
        ("ph1D",  "1d",   "5m"),
        ("ph6M",  "6mo",  "1d"),
        ("ph5Y",  "5y",   "1wk"),
        ("phAll", "max",  "1mo"),
    ]
    for key, period, interval in periods:
        try:
            hist = t.history(period=period, interval=interval)
            if not hist.empty:
                closes = [round(float(v), 4) for v in hist["Close"].tolist()
                          if not math.isnan(float(v))]
                if len(closes) >= 2:
                    out[key] = closes
        except Exception as e:
            print(f"  Price history {key} error: {e}")
    return out


def get_history(t):
    out = {}
    try:
        fin = t.financials
        cf  = t.cashflow

        if fin is not None and not fin.empty:
            cols = list(fin.columns)[:5]

            rev_row = find_row(fin, "total", "revenue") or find_row(fin, "revenue")
            ni_row  = find_row(fin, "net income")
            gp_row  = find_row(fin, "gross profit")

            if rev_row:
                vals = [safe_float(fin, rev_row, c) for c in cols]
                vals = [round(v / 1e9, 2) for v in vals if v is not None]
                if len(vals) >= 2:
                    out["revenueHistory"] = vals[::-1]

            if ni_row and rev_row:
                margins = []
                for c in cols:
                    ni = safe_float(fin, ni_row, c)
                    rv = safe_float(fin, rev_row, c)
                    if ni is not None and rv and rv > 0:
                        margins.append(round(ni / rv * 100, 1))
                if len(margins) >= 2:
                    out["netMarginHistory"] = margins[::-1]

            if gp_row and rev_row:
                margins = []
                for c in cols:
                    gp = safe_float(fin, gp_row, c)
                    rv = safe_float(fin, rev_row, c)
                    if gp is not None and rv and rv > 0:
                        margins.append(round(gp / rv * 100, 1))
                if len(margins) >= 2:
                    out["grossMarginHistory"] = margins[::-1]

        if cf is not None and not cf.empty:
            cf_cols = list(cf.columns)[:5]
            opcf_row  = find_row(cf, "operating", "cash") or find_row(cf, "operating activities")
            capex_row = find_row(cf, "capital", "expenditure") or find_row(cf, "capital expenditures")

            if opcf_row and capex_row:
                fcfs = []
                for c in cf_cols:
                    opcf  = safe_float(cf, opcf_row, c)
                    capex = safe_float(cf, capex_row, c)
                    if opcf is not None and capex is not None:
                        fcfs.append(round((opcf + capex) / 1e9, 2))
                if len(fcfs) >= 2:
                    out["fcfHistory"] = fcfs[::-1]

    except Exception as e:
        print(f"  History error: {e}")

    return out


tickers_env = os.environ.get("TICKERS", "").strip()
mode = os.environ.get("MODE", "add").strip().lower()

if not tickers_env:
    print("No TICKERS env var — nothing to do.")
    sys.exit(0)

tickers = [t.strip().upper() for t in tickers_env.split(",") if t.strip()]
print(f"Mode: {mode} | Tickers: {tickers}")

os.makedirs("data", exist_ok=True)
existing = {}
if mode == "add" and os.path.exists("data/screener.json"):
    with open("data/screener.json", encoding="utf-8") as f:
        existing = json.load(f)
    print(f"  Loaded {len(existing)} existing tickers")

results = dict(existing)
now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

for ticker in tickers:
    print(f"Fetching {ticker}...")
    try:
        t = yf.Ticker(ticker)
        info = t.info
        name = info.get("longName") or info.get("shortName")
        if not name:
            print(f"  SKIP: no data found for {ticker}")
            continue

        mkt_cap = info.get("marketCap")
        dy = info.get("dividendYield")
        if dy is None:
            div_yield = pct_dec(info.get("trailingAnnualDividendYield"))
        elif dy > 0.2:
            div_yield = sr(dy)
        else:
            div_yield = pct_dec(dy)
        if div_yield == 0.0:
            div_yield = None

        fcf = info.get("freeCashflow")
        bb = buyback_yield(t, mkt_cap)
        sh = sr((div_yield or 0) + (bb or 0)) if (div_yield or bb) else None
        price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")

        entry = {
            "name":             name,
            "sector":           info.get("sector") or "—",
            "flag":             COUNTRY_FLAG.get(info.get("country", ""), "🌍"),
            "exchange":         info.get("exchange") or "—",
            "currency":         info.get("currency") or "USD",
            "curPrice":         sr(price, 2),
            "marketCap":        fmt_large(mkt_cap),
            "totalDebt":        fmt_large(info.get("totalDebt")),
            "pe":               sr(info.get("trailingPE") or info.get("forwardPE"), 1),
            "priceToBook":      sr(info.get("priceToBook"), 1),
            "priceToSales":     sr(info.get("priceToSalesTrailing12Months"), 1),
            "beta":             sr(info.get("beta"), 2),
            "evEbitda":         sr(info.get("enterpriseToEbitda"), 1),
            "week52Low":        sr(info.get("fiftyTwoWeekLow"), 2),
            "week52High":       sr(info.get("fiftyTwoWeekHigh"), 2),
            "targetPrice":      sr(info.get("targetMeanPrice"), 2),
            "operatingMargin":  pct_dec(info.get("operatingMargins")),
            "grossMargin":      pct_dec(info.get("grossMargins")),
            "netMargin":        pct_dec(info.get("profitMargins")),
            "roe":              pct_dec(info.get("returnOnEquity")),
            "roa":              pct_dec(info.get("returnOnAssets") or None),
            "fcfYield":         sr(fcf / mkt_cap * 100) if fcf and mkt_cap and mkt_cap > 0 else None,
            "revenueGrowth":    rev_growth(t),
            "debtToEquity":     sr(info.get("debtToEquity"), 2),
            "currentRatio":     sr(info.get("currentRatio"), 2),
            "sharesOutstanding": fmt_large(info.get("sharesOutstanding") or info.get("impliedSharesOutstanding") or None),
            "eps":              sr(info.get("trailingEps"), 2),
            "epsGrowth":        pct_dec(info.get("earningsGrowth")),
            "divYield":         div_yield,
            "buybackYield":     bb,
            "shareholderYield": sh,
            "payoutRatio":      pct_dec(info.get("payoutRatio")),
            "divCagr5y":        div_cagr_5y(t),
            "fetchedAt":        now_str,
        }

        entry.update(get_history(t))
        entry.update(get_price_history(t))
        fmp_data = fetch_fmp(ticker)
        if fmp_data:
            entry.update(fmp_data)
            print(f"  FMP: {list(fmp_data.keys())}")
        fiscal_data = fetch_fiscal_ai(ticker, info.get("exchange", ""), entry)
        if fiscal_data:
            entry.update(fiscal_data)
        entry["summary"] = generate_summary(entry)

        results[ticker] = entry
        print(f"  OK: {name} @ {price} {info.get('currency', '')}")
    except Exception as e:
        print(f"  ERROR: {e}")
    time.sleep(0.3)

with open("data/screener.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False)
print(f"\nDone! Saved {len(results)} tickers to data/screener.json")
