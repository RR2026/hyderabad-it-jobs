"""Daily job fetcher: JSearch (Google Jobs data) + Adzuna -> scored jobs.json"""
import os, re, json, hashlib, datetime as dt, requests

RAPID_KEY = os.getenv("RAPIDAPI_KEY"); AZ_ID = os.getenv("ADZUNA_APP_ID"); AZ_KEY = os.getenv("ADZUNA_APP_KEY")
QUERIES = ["IT service desk analyst", "desktop support engineer", "technical support engineer",
           "IT helpdesk", "customer support engineer", "ServiceNow support", "fraud analyst"]
SKILLS = {"servicenow":"ServiceNow","active directory":"Active Directory","azure":"Azure","intune":"Intune",
  "office 365":"Microsoft 365","microsoft 365":"Microsoft 365","m365":"Microsoft 365","vpn":"VPN","mfa":"MFA",
  "salesforce":"Salesforce","hardware":"Hardware troubleshooting","laptop":"Hardware troubleshooting",
  "desktop":"Hardware troubleshooting","l1":"L1/L2 support","l2":"L1/L2 support","ticket":"L1/L2 support","sla":"L1/L2 support"}
TITLES = ["service desk","help desk","helpdesk","desktop support","technical support","it support","end user",
          "customer support","l1","l2","iam","fraud","servicenow","support engineer","support analyst"]
BONUS = ["fraud","fintech","bank","payments","risk"]
TXT = lambda *a: " ".join(str(x or "") for x in a).lower()

def get_degree(t):
    anyg = re.search(r"any (graduate|degree)|graduate in any|b\.?sc|bca|b\.?com", t)
    eng = re.search(r"b\.?\s?e\b|b\.?\s?tech|engineering degree", t)
    if eng and not anyg: return "check" if re.search(r"prefer|or related|equivalent", t) else "engineering"
    return "any" if anyg else "check" if re.search(r"degree|graduate", t) else "any"

def get_exp(t):
    m = re.search(r"(\d+)\s*(?:-|to)\s*(\d+)\s*(?:\+\s*)?years?", t) or re.search(r"(\d+)\s*\+?\s*years?", t)
    return int(m.group(1)) if m else None

def score(j):
    t = TXT(j["title"], j["desc"]); title = j["title"].lower()
    matched = sorted({v for k, v in SKILLS.items() if re.search(r"\b"+re.escape(k)+r"\b", t)})
    s = 30 if any(x in title for x in TITLES) else 12 if any(x in t for x in TITLES) else 0
    s += min(40, round(40 * len(matched) / 5))
    e = get_exp(t); s += 10 if e is None else 15 if e <= 3 else 8 if e <= 5 else 0
    deg = get_degree(t); s += {"any": 10, "check": 6, "engineering": 0}[deg]
    s += 5 if any(b in t for b in BONUS) or "hyderabad" in t else 0
    mode = "remote" if j["remote"] or "remote" in t else "hybrid" if "hybrid" in t else "onsite"
    voice = "nonvoice" if re.search(r"non[- ]?voice|chat|email support", t) else "voice" if re.search(r"voice|inbound|outbound|call", t) else ""
    return min(s, 100), matched, deg, mode, voice

def jsearch(q):
    if not RAPID_KEY: return []
    r = requests.get("https://jsearch.p.rapidapi.com/search", timeout=40,
        headers={"X-RapidAPI-Key": RAPID_KEY, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
        params={"query": f"{q} in Hyderabad, India", "country": "in", "date_posted": "week", "num_pages": 1})
    r.raise_for_status()
    return [dict(title=x.get("job_title"), company=x.get("employer_name"), loc=x.get("job_city") or "Hyderabad",
        url=x.get("job_apply_link"), desc=x.get("job_description"), posted=x.get("job_posted_at_datetime_utc"),
        source=x.get("job_publisher") or "Google Jobs", remote=bool(x.get("job_is_remote"))) for x in r.json().get("data", [])]

def adzuna(q):
    if not (AZ_ID and AZ_KEY): return []
    r = requests.get("https://api.adzuna.com/v1/api/jobs/in/search/1", timeout=40,
        params=dict(app_id=AZ_ID, app_key=AZ_KEY, what=q, where="Hyderabad", max_days_old=7, results_per_page=20))
    r.raise_for_status()
    return [dict(title=x.get("title"), company=(x.get("company") or {}).get("display_name"),
        loc=(x.get("location") or {}).get("display_name", "Hyderabad"), url=x.get("redirect_url"),
        desc=x.get("description"), posted=x.get("created"), source="Adzuna", remote=False) for x in r.json().get("results", [])]

def main():
    try: old = {j["id"]: j for j in json.load(open("jobs.json")).get("jobs", [])}
    except Exception: old = {}
    now = dt.datetime.utcnow(); seen, out = set(), []
    for q in QUERIES:
        for src in (jsearch, adzuna):
            try: res = src(q)
            except Exception as e: print("skip", src.__name__, q, e); continue
            for j in res:
                if not (j["title"] and j["url"]): continue
                key = re.sub(r"\W", "", (j["title"] + (j["company"] or "")).lower())
                if key in seen or not re.search(r"hyderabad|telangana|remote", TXT(j["loc"], j["desc"]) + ("remote" if j["remote"] else "")): continue
                seen.add(key); s, m, deg, mode, voice = score(j)
                if s < 30: continue
                jid = hashlib.md5(key.encode()).hexdigest()[:12]
                first = old.get(jid, {}).get("first_seen", now.isoformat() + "Z")
                out.append(dict(id=jid, title=j["title"], company=j["company"] or "", location=j["loc"], mode=mode,
                    voice=voice, degree=deg, source=j["source"], url=j["url"], posted=j["posted"] or first, score=s,
                    matched=m, first_seen=first, is_new=(now - dt.datetime.fromisoformat(first.rstrip("Z"))).days < 1))
    out = sorted(out, key=lambda x: -x["score"])[:150]
    json.dump(dict(updated=now.isoformat() + "Z", sample=False, jobs=out), open("jobs.json", "w"), indent=1)
    print(len(out), "jobs saved")

if __name__ == "__main__": main()
