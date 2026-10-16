"""Daily job fetcher: JSearch (Google Jobs data) + Adzuna -> scored jobs.json"""
import os, re, json, hashlib, datetime as dt, requests

RAPID_KEY = os.getenv("RAPIDAPI_KEY"); AZ_ID = os.getenv("ADZUNA_APP_ID"); AZ_KEY = os.getenv("ADZUNA_APP_KEY")
QUERIES = ["IT service desk analyst", "desktop support engineer", "technical support engineer", "IT helpdesk",
           "customer support engineer", "ServiceNow support", "end user support", "fraud analyst"]
# JSearch free plan = 200 requests/month. 4 searches/day (~120/month) leaves room for manual test runs.
PER_DAY = int(os.getenv("QUERIES_PER_DAY", "4"))
SKILLS = {"servicenow":"ServiceNow","active directory":"Active Directory","azure":"Azure","intune":"Intune",
  "office 365":"Microsoft 365","microsoft 365":"Microsoft 365","m365":"Microsoft 365","vpn":"VPN","mfa":"MFA",
  "salesforce":"Salesforce","hardware":"Hardware troubleshooting","laptop":"Hardware troubleshooting",
  "desktop":"Hardware troubleshooting","l1":"L1/L2 support","l2":"L1/L2 support","ticket":"L1/L2 support","sla":"L1/L2 support"}
TITLES = ["service desk","help desk","helpdesk","desktop support","technical support","it support","end user",
          "customer support","l1","l2","iam","fraud","servicenow","support engineer","support analyst"]
BONUS = ["fraud","fintech","bank","payments","risk"]
TXT = lambda *a: " ".join(str(x or "") for x in a).lower()

WORDS = {"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10}
SENIOR = r"\b(senior|sr\.?|staff|principal|director|manager|head|lead|architect|vp)\b"
VOICE = r"voice (process|support|based|calls?)|international voice|inbound|outbound|calling process|call cent"

def get_degree(raw):
    t = raw.lower()
    anyg = re.search(r"any (graduate|degree)|graduate in any|\bb\.?sc\b|\bbca\b|\bb\.?com\b", t)
    eng = re.search(r"\bB\.\s?E\b|\bBE\b|\bB\.?\s?Tech\b", raw) or "engineering degree" in t or "bachelor of engineering" in t
    if eng and not anyg: return "check" if re.search(r"prefer|or related|equivalent", t) else "engineering"
    return "any" if anyg else "check" if re.search(r"\bdegree\b|\bgraduate\b", t) else "any"

def get_exp(t):
    t = re.sub(r"\b(" + "|".join(WORDS) + r")\b", lambda m: str(WORDS[m.group(1)]), t)
    n = [int(m.group(1)) for m in re.finditer(r"(\d+)\s*(?:-|to|\u2013)?\s*(?:\d+\s*)?\+?\s*(?:years?|yrs?)\b", t)]
    n = [x for x in n if x <= 20]
    return max(n) if n else None

def skip(j):
    t = TXT(j["title"], j["desc"])
    return ("dummy job data" in t or re.search(r"\bdemo\b", j["title"].lower()) or re.search(r"\$\s?\d{2,3},\d{3}", j["desc"] or "")
            or re.search(SENIOR, j["title"].lower()) or (get_exp(t) or 0) >= 6)

def score(j):
    raw = (j["title"] or "") + " " + (j["desc"] or ""); t = raw.lower(); title = j["title"].lower()
    matched = sorted({v for k, v in SKILLS.items() if re.search(r"\b"+re.escape(k)+r"\b", t)})
    s = 30 if any(x in title for x in TITLES) else 12 if any(x in t for x in TITLES) else 0
    s += min(40, round(40 * len(matched) / 5))
    e = get_exp(t); s += 10 if e is None else 15 if e <= 3 else 8 if e <= 5 else 0
    deg = get_degree(raw); s += {"any": 10, "check": 6, "engineering": 0}[deg]
    s += 5 if any(b in t for b in BONUS) or "hyderabad" in (j["loc"] or "").lower() else 0
    mode = "remote" if j["remote"] or re.search(r"work from home|\bwfh\b|fully remote|100% remote|remote (position|role|work)", t) else "hybrid" if "hybrid" in t else "onsite"
    voice = "nonvoice" if re.search(r"non[- ]?voice", t) else "voice" if re.search(VOICE, t) else "nonvoice" if re.search(r"\bchat\b|email support", t) else ""
    return min(s, 100), matched, deg, mode, voice

def jsearch(q):
    if not RAPID_KEY: return []
    r = requests.get("https://jsearch.p.rapidapi.com/search-v2", timeout=40,
        headers={"X-RapidAPI-Key": RAPID_KEY, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
        params={"query": f"{q} in Hyderabad, India", "country": "in", "date_posted": "week", "num_pages": 1})
    r.raise_for_status()
    d = r.json().get("data") or []
    items = d.get("jobs", []) if isinstance(d, dict) else d   # v5 returns {"jobs": [...]}
    if not items: print("JSearch returned no jobs. Response keys:", list(r.json().keys())[:6])
    return [dict(title=x.get("job_title"), company=x.get("employer_name"), loc=x.get("job_city") or "Hyderabad",
        url=x.get("job_apply_link"), desc=x.get("job_description"), posted=x.get("job_posted_at_datetime_utc"),
        source=x.get("job_publisher") or "Google Jobs", remote=bool(x.get("job_is_remote"))) for x in items]

def adzuna(q):
    if not (AZ_ID and AZ_KEY): return []
    r = requests.get("https://api.adzuna.com/v1/api/jobs/in/search/1", timeout=40,
        params=dict(app_id=AZ_ID, app_key=AZ_KEY, what=q, where="Hyderabad", max_days_old=7, results_per_page=20))
    r.raise_for_status()
    return [dict(title=x.get("title"), company=(x.get("company") or {}).get("display_name"),
        loc=(x.get("location") or {}).get("display_name", "Hyderabad"), url=x.get("redirect_url"),
        desc=x.get("description"), posted=x.get("created"), source="Adzuna", remote=False) for x in r.json().get("results", [])]

def main():
    try: prev = json.load(open("jobs.json"))
    except Exception: prev = {}
    old = {} if prev.get("sample") else {j["id"]: j for j in prev.get("jobs", [])}
    now = dt.datetime.utcnow(); seen, fresh = set(), {}
    n = len(QUERIES); start = (now.toordinal() * PER_DAY) % n
    todays = [QUERIES[(start + i) % n] for i in range(min(PER_DAY, n))]
    print("Searching today:", todays)
    for q in todays:
        for src in (jsearch, adzuna):
            try: res = src(q)
            except Exception as e: print("skip", src.__name__, q, e); continue
            print(src.__name__, q, "->", len(res), "raw results")
            for j in res:
                if not (j["title"] and j["url"]) or skip(j): continue
                key = re.sub(r"\W", "", (j["title"] + (j["company"] or "")).lower())
                if key in seen or not re.search(r"hyderabad|telangana|remote", TXT(j["loc"], j["desc"]) + ("remote" if j["remote"] else "")): continue
                seen.add(key); s, m, deg, mode, voice = score(j)
                if s < 30: continue
                jid = hashlib.md5(key.encode()).hexdigest()[:12]
                first = old.get(jid, {}).get("first_seen", now.isoformat() + "Z")
                fresh[jid] = dict(id=jid, title=j["title"], company=j["company"] or "", location=j["loc"], mode=mode,
                    voice=voice, degree=deg, source=j["source"], url=j["url"], posted=j["posted"] or first, score=s,
                    matched=m, first_seen=first)
    if not fresh:
        print("No jobs fetched, so the existing jobs.json is kept unchanged."); return
    for jid, j in old.items():  # keep jobs found on earlier days for 14 days
        if jid not in fresh and j.get("first_seen") and (now - dt.datetime.fromisoformat(j["first_seen"].rstrip("Z"))).days < 14:
            fresh[jid] = j
    out = list(fresh.values())
    for j in out: j["is_new"] = (now - dt.datetime.fromisoformat(j["first_seen"].rstrip("Z"))).days < 1
    out = sorted(out, key=lambda x: -x["score"])[:150]
    json.dump(dict(updated=now.isoformat() + "Z", sample=False, jobs=out), open("jobs.json", "w"), indent=1)
    print(len(out), "jobs saved")

if __name__ == "__main__": main()
