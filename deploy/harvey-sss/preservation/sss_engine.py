"""Site Safety Scan Harvey add-ons (2026-10-22): proof-only findings, decision log,
smart 30-day re-check, prospect brief, on-site decision-maker finder, reply learning.

Idea credit: evidence ledger + "never guess" brief rules adapted from trycompai CRM
(MIT, apps/agent/agent/skills/evidence.md, writing-a-brief.md). No code copied.

Runs inside the harvey-sss container:  python /app/data/sss_engine.py <cmd>
  dryrun [--db PATH]   build drafts/briefs/logs into a COPY of the DB, print samples, sends nothing
  audit-drafts         re-verify findings behind every pending draft; hold any without proof
  recheck [--dry]      30-day no-reply re-scan; queue at most ONE follow-up (pending_review only)
  replies              learn from inbound replies; write reply notices (brief + suggestion) for Jose
  queue LEADS.json     proof-gated drafts for new leads (pending_review, within ramp cap)
  brief HOST           print the brief for one host
This module never sends email. Everything it writes to outbox is status 'pending_review'.
"""
import os, re, sys, json, time, uuid, sqlite3, datetime as dt, urllib.request, urllib.parse, html as H
from collections import Counter
from zoneinfo import ZoneInfo
PT = ZoneInfo("America/Los_Angeles")
DATA = "/app/data"; DB = DATA + "/harvey.db"
SCAN_API = "https://sitesafetyscan.com/api/scan?url="
UA = "Mozilla/5.0 (compatible; SiteSafetyScanOutreach/1.0)"
FOLLOWUP_DAYS = 30
NICHES = {"dental": ["dentist", "dental", "orthodont", "chiro", "med spa", "medspa", "medical", "clinic"],
          "law": ["law", "attorney", "lawyer", "legal"], "accounting": ["account", "cpa", "tax", "bookkeep"]}

def now(): return dt.datetime.now(dt.timezone.utc)
def iso(t=None): return (t or now()).strftime("%Y-%m-%d %H:%M:%S")
def niche_of(ind):
    s = (ind or "").lower()
    for k, ws in NICHES.items():
        if any(w in s for w in ws): return k
    return "other"

# ---------------- schema ----------------
def migrate(c):
    c.executescript("""
    create table if not exists sss_decision_log(id integer primary key autoincrement, at text default current_timestamp,
      prospect_id text, host text, stage text, action text, reason text, detail text);
    create table if not exists sss_scans(id integer primary key autoincrement, host text, scanned_at text, source text, scan_json text);
    create table if not exists sss_evidence(id integer primary key autoincrement, host text, scan_id integer, check_name text,
      level text, kind text, proof_url text, proof text, verified_at text, usable integer);
    create table if not exists sss_briefs(prospect_id text primary key, host text, what text, what_src text, city text, city_src text,
      decision_maker text, dm_src text, dm_email text, finding text, finding_src text, brief text, updated_at text);
    create table if not exists sss_followups(prospect_id text primary key, outbox_id text, at text, reason text, old_findings text, new_findings text, result text);
    create table if not exists sss_reply_insights(id integer primary key autoincrement, conversation_id text, prospect_id text, niche text,
      kind text, category text, text text, at text default current_timestamp, unique(conversation_id, kind, text));
    create table if not exists sss_reply_notices(conversation_id text primary key, prospect_id text, brief text, reply_excerpt text,
      categories text, suggested_reply text, status text default 'for_jose', at text default current_timestamp);
    """)

def log(c, pid, host, stage, action, reason, detail=None):
    c.execute("insert into sss_decision_log(prospect_id,host,stage,action,reason,detail) values(?,?,?,?,?,?)",
              (pid, host, stage, action, reason, json.dumps(detail) if detail is not None else None))

# ---------------- fetch helpers ----------------
def fetch(url, method="GET", n=400000, redirect=True):
    class NoRed(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k): return None
    op = urllib.request.build_opener() if redirect else urllib.request.build_opener(NoRed)
    try:
        r = op.open(urllib.request.Request(url, method=method, headers={"User-Agent": UA}), timeout=12)
        return r.status, dict(r.headers), r.read(n).decode("utf-8", "replace"), r.geturl()
    except urllib.error.HTTPError as e:
        try: body = e.read(n).decode("utf-8", "replace")
        except Exception: body = ""
        return e.code, dict(e.headers or {}), body, url
    except Exception as e:
        return None, {}, "", str(e)[:120]

def txt(name):
    import dns.resolver
    try: return [b"".join(r.strings).decode("utf-8", "replace") for r in dns.resolver.resolve(name, "TXT", lifetime=8)]
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer): return []
    except Exception: return None   # lookup failed: unknown, not "missing"

def _outreach_key():
    try: return open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ssc_outreach_key")).read().strip()
    except OSError: return ""

def scan(host):
    h = {"User-Agent": "SSS-outreach/1.0", "X-SSC-Internal": "1"}
    k = _outreach_key()
    if k: h["X-SSC-Outreach-Key"] = k
    r = urllib.request.Request(SCAN_API + urllib.parse.quote(host), headers=h)
    return json.load(urllib.request.urlopen(r, timeout=45))

def store_scan(c, host, s, source):
    cur = c.execute("insert into sss_scans(host,scanned_at,source,scan_json) values(?,?,?,?)", (host, s.get("scannedAt") or iso(), source, json.dumps(s)))
    return cur.lastrowid

# ---------------- 1. proof-only findings ----------------
# Email-worthy checks, in priority order. Header-only nits are recorded but never emailed.
PRIORITY = ["PHP version", "readme.html", "xmlrpc.php", "SPF", "DMARC", "HTTP \u2192 HTTPS redirect"]
MINOR = {"Referrer-Policy", "Content-Security-Policy", "Clickjacking protection", "X-Content-Type-Options", "HSTS"}

def verify(host, chk):
    """Re-check a scan finding live. Returns (kind, proof_url, proof). kind: primary | contradiction | unverified."""
    n = chk["name"]
    if n in ("readme.html", "xmlrpc.php"):
        u = f"https://{host}/{n}"; st, hd, body, _ = fetch(u, n=60000)
        if n == "readme.html" and st == 200 and "wordpress" in body.lower(): return "primary", u, f"HTTP 200, page mentions WordPress"
        if n == "xmlrpc.php" and st in (200, 405) and "xml-rpc" in body.lower(): return "primary", u, f"HTTP {st}, XML-RPC endpoint answers"
        return ("contradiction" if st else "unverified"), u, f"live check returned {st}"
    if n in ("SPF", "DMARC"):
        name = host if n == "SPF" else "_dmarc." + host
        r = txt(name)
        if r is None: return "unverified", "dns:" + name, "DNS lookup failed"
        has = any(x.lower().startswith("v=spf1" if n == "SPF" else "v=dmarc1") for x in r)
        return ("contradiction", "dns:TXT " + name, "record now present") if has else ("primary", "dns:TXT " + name, f"no {n} record in public DNS")
    if n == "PHP version":
        st, hd, _, u = fetch(f"https://{host}/", n=2000)
        pw = next((v for k, v in hd.items() if k.lower() == "x-powered-by"), "")
        m = re.search(r"PHP/(\d+\.\d+\.\d+)", pw)
        want = re.search(r"PHP (\d+\.\d+\.\d+) is older", chk.get("detail", ""))
        if m and want and m.group(1) != want.group(1):
            return "contradiction", f"https://{host}/ (X-Powered-By header)", f"live header now shows PHP/{m.group(1)}, scan said {want.group(1)}"
        if m and not want: return "unverified", f"https://{host}/", "scan detail has no version to compare"
        if m: return "primary", f"https://{host}/ (X-Powered-By header)", "header shows PHP/" + m.group(1)
        return "unverified", f"https://{host}/", "PHP version not visible on live re-check"
    if n == "HTTP \u2192 HTTPS redirect":
        st, hd, _, _ = fetch(f"http://{host}/", n=2000, redirect=False)
        loc = next((v for k, v in hd.items() if k.lower() == "location"), "")
        if st == 200: return "primary", f"http://{host}/", "plain http:// page loads with no redirect to https"
        if st in (301, 302, 307, 308) and loc.startswith("https"): return "contradiction", f"http://{host}/", "now redirects to https"
        return "unverified", f"http://{host}/", f"live check returned {st}"
    return "unverified", "", "no live verifier for this check"

def proven_findings(c, host, s, scan_id=None, pid=None, stage="scan"):
    """Return usable findings (primary evidence only). Everything else is logged as skipped/held."""
    out = []
    if not s or s.get("error") or not s.get("scannedAt") or (s.get("host") or host).replace("www.", "") != host:
        log(c, pid, host, stage, "skip", "no valid scan result", {"error": (s or {}).get("error")}); return out
    for chk in s.get("checks", []):
        if chk.get("level") not in ("warn", "bad"): continue
        if chk["name"] in MINOR:
            c.execute("insert into sss_evidence(host,scan_id,check_name,level,kind,proof,verified_at,usable) values(?,?,?,?,?,?,?,0)",
                      (host, scan_id, chk["name"], chk["level"], "supporting", "minor header; recorded, never emailed", iso()))
            continue
        if chk["name"] not in PRIORITY:
            log(c, pid, host, stage, "skip-finding", "check has no live verifier", {"check": chk["name"]}); continue
        kind, url, proof = verify(host, chk)
        ok = kind == "primary"
        c.execute("insert into sss_evidence(host,scan_id,check_name,level,kind,proof_url,proof,verified_at,usable) values(?,?,?,?,?,?,?,?,?)",
                  (host, scan_id, chk["name"], chk["level"], kind, url, proof, iso(), int(ok)))
        if ok: out.append({"check": chk["name"], "level": chk["level"], "detail": chk.get("detail", ""), "proof_url": url, "proof": proof, "scanned_at": s["scannedAt"]})
        else: log(c, pid, host, stage, "hold-finding", f"{kind}: {proof}", {"check": chk["name"], "proof_url": url})
    out.sort(key=lambda f: PRIORITY.index(f["check"]))
    return out

# ---------------- 5. decision-maker finder (site pages only, free) ----------------
PAGES = ["/", "/about", "/about-us", "/team", "/our-team", "/meet-the-team", "/meet-the-doctor", "/meet-the-doctors", "/our-doctors",
         "/attorneys", "/our-attorneys", "/staff", "/contact", "/contact-us"]
TITLES = r"(Owner|Co-Owner|Founder|Co-Founder|Practice Manager|Office Manager|Managing Partner|Founding Attorney|Principal|President|Managing Attorney|Lead Dentist)"
NAME = r"([A-Z][a-z]{1,15}(?:\s[A-Z]\.)?\s[A-Z][a-zA-Z'\-]{1,20})"
CRED = r"(DDS|DMD|D\.D\.S\.|D\.M\.D\.|DC|D\.C\.|MD|M\.D\.|NP|CPA|EA|Esq\.?|J\.D\.)"
BADNAME = re.compile(r"\b(Our|The|Your|Meet|About|Contact|Dental|Family|Office|Team|Care|Services|Read|More|Call|Book|New|Patient|Law|Group|Firm|Center|Clinic|Spa|Tax|Home|Privacy|Policy|Street|Suite|Blvd|Ave)\b")
EM = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
GENERIC = re.compile(r"^(info|contact|office|hello|admin|frontdesk|front\.desk|reception|appointments?|team|support|mail|inquir\w*)@", re.I)

def strip(h):
    h = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", h)
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", h)))

def site_facts(host):
    pages, emails, people = {}, [], Counter(); src = {}
    for p in PAGES:
        st, _, body, final = fetch("https://" + host + p)
        if st != 200 or not body: continue
        if p != "/" and urllib.parse.urlparse(final).path in ("", "/"): continue   # redirected home: page doesn't exist
        pages[p] = body; t = strip(body)
        for m in re.findall(r'mailto:([^"\'?>\s]+)', body) + EM.findall(t) + EM.findall(body):
            m = m.lower().strip(".")
            if re.search(r"\.(png|jpe?g|gif|webp|svg)$|sentry|wixpress|example|domain\.com|godaddy|yourname|email\.com$", m): continue
            if "@" in m and m not in emails: emails.append(m)
        found = []
        for nm, cr in re.findall(NAME + r",?\s" + CRED + r"\b", t): found.append((nm, cr.replace(".", "")))
        for nm in re.findall(r"\bDr\.?\s" + NAME, t): found.append((nm, "Dr."))
        for nm, ti in re.findall(NAME + r"\s?[,\-\u2013|]\s?" + TITLES, t): found.append((nm, ti))
        for ti, nm in re.findall(TITLES + r"\s?[:,\-\u2013]?\s" + NAME, t): found.append((nm, ti))
        for nm, ti in found:
            if BADNAME.search(nm): continue
            people[(nm, ti)] += 1; src.setdefault((nm, ti), p)
    rank = {"Owner": 0, "Co-Owner": 0, "Founder": 0, "Co-Founder": 0, "Managing Partner": 0, "Founding Attorney": 0, "Principal": 0, "President": 0,
            "Practice Manager": 1, "Office Manager": 1, "Managing Attorney": 1}
    best = sorted(people.items(), key=lambda kv: (rank.get(kv[0][1], 2), -kv[1]))
    dm = None
    if best:
        (nm, ti), _ = best[0]
        dm = {"name": nm, "title": ti, "src": "https://" + host + src[(nm, ti)]}
    # email: only addresses published on the site. Personal one if it clearly matches the person, else generic.
    own = [e for e in emails if e.split("@")[1].endswith(host.split(".", 1)[-1] if host.count(".") > 1 else host)] or emails
    pick, why = "", "no email published on site"
    if dm:
        parts = [x.lower() for x in re.findall(r"[A-Za-z]+", dm["name"]) if len(x) > 1]
        for e in own:
            lp = e.split("@")[0]
            if any(pt in lp for pt in parts) and not GENERIC.match(e): pick, why = e, "published on site, matches " + dm["name"]; break
    if not pick:
        g = [e for e in own if GENERIC.match(e)]
        if g: pick, why = g[0], "generic contact address published on site"
        elif own: pick, why = own[0], "only address published on site"
    # what they do + city (only if the site states it)
    home = pages.get("/", "")
    md = re.search(r'(?is)<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{10,300})', home)
    ti = re.search(r"(?is)<title[^>]*>(.*?)</title>", home)
    what, what_src = "", ""
    if md: what, what_src = H.unescape(md.group(1)).strip()[:160], "homepage meta description"
    elif ti: what, what_src = H.unescape(strip(ti.group(1))).strip()[:120], "homepage title"
    city, city_src = "", ""
    for p in ["/contact", "/contact-us", "/"]:
        if p in pages:
            m = re.search(r"\b([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)?),\s?(CA|California)\s?(9\d{4})\b", strip(pages[p]))
            cty = re.sub(r"^(Rd|St|Ave|Blvd|Dr|Ste|Suite|Way|Pkwy|Ln|Ct|Hwy|Road|Street|Avenue|Drive|Parkway|Center)\s", "", m.group(1)) if m else ""
            if m: city, city_src = f"{cty}, CA {m.group(3)}", "address on https://" + host + p; break
    return {"pages": list(pages), "emails": emails, "dm": dm, "email": pick, "email_why": why, "what": what, "what_src": what_src, "city": city, "city_src": city_src}

# ---------------- 4. prospect brief ----------------
LABEL = {"PHP version": "older PHP version visible in server header", "readme.html": "/readme.html is public (shows WordPress details)",
         "xmlrpc.php": "/xmlrpc.php answers publicly", "SPF": "no SPF record (email deliverability)", "DMARC": "no DMARC record (email spoofing)",
         "HTTP \u2192 HTTPS redirect": "http:// version doesn't redirect to https"}
def make_brief(c, pid, host, name, facts, finding):
    dm = facts.get("dm")
    l1 = f"{name or host}" + (f": {facts['what']}" if facts.get("what") else "") + (f" ({facts['city']})" if facts.get("city") else "")
    l2 = "Decision-maker: " + ((f"Dr. {dm['name']}" if dm['title'] == "Dr." else f"{dm['name']}, {dm['title']}") + f" (named on {dm['src']})" if dm else "not named on the site")
    l3 = "Finding: " + (f"{LABEL.get(finding['check'], finding['check'])}. Proof: {finding['proof']} [{finding['proof_url']}]" if finding else "none with proof")
    b = "\n".join([l1, l2, l3])
    c.execute("insert or replace into sss_briefs values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (pid, host, facts.get("what", ""), facts.get("what_src", ""), facts.get("city", ""), facts.get("city_src", ""),
               f"{dm['name']} ({dm['title']})" if dm else "", dm["src"] if dm else "", facts.get("email", ""),
               finding["check"] if finding else "", finding["proof_url"] if finding else "", b, iso()))
    return b

# ---------------- 7. learn from replies ----------------
CATS = [("trust", r"\b(spam|scam|who (is|are) (this|you)|legit|phishing|how did you (get|find))"),
        ("has_web_person", r"\b(web ?(guy|person|developer|designer|company)|our (it|developer|host|agency)|someone (handles|manages)|we have (a|an) (it|web))"),
        ("price", r"\b(cost|price|how much|\$|charge|fee)"),
        ("not_interested", r"\b(not interested|no thanks|remove me|stop emailing|unsubscribe)\b"),
        ("timing", r"\b(busy|later|next (week|month)|not right now|after)\b"),
        ("how_to_fix", r"\b(how (do|can) (i|we) fix|what should (i|we) do|can you fix|help (us )?fix)")]
PREEMPT = {"trust": "No catch: it's a free tool and there's no call or signup needed.",
           "has_web_person": "It's easy to forward to whoever manages your site.",
           "price": "The scan itself is free.", "how_to_fix": "The free results page shows what we found."}
SUGGEST = {"trust": "Totally fair to ask. I'm Jose, I run Site Safety Scan, a small free tool. I found your site through a public search and ran the same free check anyone can run at {link}. No signup or call needed.",
           "has_web_person": "That's great, you're in good hands. If it helps, you can forward this to them: {finding}. The free results page at {link} shows the details.",
           "price": "The scan is free. If you'd like step-by-step fix instructions for your site, there's an optional $29 fix report, and an optional $9/month re-scan. No pressure either way.",
           "not_interested": "(No reply needed. Already suppressed: they asked not to be contacted.)",
           "timing": "No rush at all. The free scan at {link} will be there whenever you have a minute.",
           "how_to_fix": "Good question. For your site: {finding}. The free results page at {link} shows what we found. If you want step-by-step fix instructions for your site, there's an optional $29 fix report.",
           "other": "Thanks for getting back to me. Happy to help with any questions. The free results for your site are at {link}."}

def inbound_texts(thread_json):
    try: t = json.loads(thread_json or "[]")
    except Exception: return []
    out = []
    for m in t if isinstance(t, list) else []:
        d = (m.get("direction") or m.get("role") or m.get("from_type") or "").lower()
        if d in ("inbound", "in", "prospect", "user", "them", "received"):
            out.append(m.get("body") or m.get("text") or m.get("content") or "")
    return out

def learn_replies(c, dry=False):
    n = 0
    for cid, pid, tj in c.execute("select id,prospect_id,thread_json from conversations").fetchall():
        texts = [re.split(r"\n\s*(On .+wrote:|-----Original|From:)", x)[0] for x in inbound_texts(tj)]
        if not texts: continue
        p = c.execute("select industry,company,email from prospects where id=?", (pid,)).fetchone() or ("", "", "")
        nich = niche_of(p[0]); body = " ".join(texts).strip()
        cats = [k for k, rx in CATS if re.search(rx, body, re.I)] or ["other"]
        qs = [q.strip() + "?" for q in re.findall(r"([^.?!\n]{6,160})\?", body)][:5]
        phr = re.split(r"(?<=[.!?])\s", body)[0][:160]
        rows = [("category", k, k) for k in cats] + [("question", cats[0], q) for q in qs] + [("phrasing", cats[0], phr)]
        for kind, cat, t in rows:
            c.execute("insert or ignore into sss_reply_insights(conversation_id,prospect_id,niche,kind,category,text) values(?,?,?,?,?,?)", (cid, pid, nich, kind, cat, t))
        if c.execute("select 1 from sss_reply_notices where conversation_id=?", (cid,)).fetchone(): continue
        br = c.execute("select brief,host,finding from sss_briefs where prospect_id=?", (pid,)).fetchone()
        co = c.execute("select co.domain,co.name,co.notes from prospects p join companies co on co.id=p.company_id where p.id=?", (pid,)).fetchone()
        host = br[1] if br else (co[0] if co else "")
        if not br and co:   # older prospects: build a minimal factual brief from what's on file (no guessing)
            m = re.search(r"SSS finding: (.+?) \(", co[2] or "")
            br = (f"{co[1]} ({co[0]})\nDecision-maker: not on file\nFinding: {LABEL.get(m.group(1), m.group(1)) if m else 'none on file'}", co[0], m.group(1) if m else "")
        link = f"https://sitesafetyscan.com/?url={host}" if host else "https://sitesafetyscan.com"
        fnd = LABEL.get(br[2], br[2]) if br and br[2] else "the item from my first note"
        sug = " ".join(SUGGEST.get(k, SUGGEST["other"]).format(link=link, finding=fnd) for k in cats[:2])
        if "not_interested" in cats: sug = SUGGEST["not_interested"]
        c.execute("insert into sss_reply_notices(conversation_id,prospect_id,brief,reply_excerpt,categories,suggested_reply) values(?,?,?,?,?,?)",
                  (cid, pid, br[0] if br else "(no brief on file)", body[:600], ",".join(cats), sug))
        log(c, pid, host, "reply", "notice-for-jose", "reply received; suggestion drafted, not sent", {"categories": cats})
        n += 1
    return n

def learned_line(c, nich, words_left):
    rows = c.execute("select category,count(distinct conversation_id) from sss_reply_insights where kind='category' and (niche=? or ?='') group by 1 order by 2 desc", (nich, "")).fetchall()
    for cat, cnt in rows:
        if cnt >= 2 and cat in PREEMPT and len(PREEMPT[cat].split()) <= words_left: return PREEMPT[cat], cat
    return "", ""

# ---------------- email writer (50-80 words, proof-only) ----------------
AUD = {"dental": "patients", "law": "clients", "accounting": "clients", "other": "customers"}
def write_email(c, host, first, nich, f):
    who = AUD.get(nich, "customers"); hi = f"Hi {first}," if first else "Hi there,"
    link = f"https://sitesafetyscan.com/?url={host}"
    core = {
      "PHP version": f"I ran a free public check on {host} and saw the server header shows {f['proof'].split('header shows ')[-1]}, which isn't the latest patched release. The version number is visible to anyone from outside.",
      "readme.html": f"I ran a free public check on {host} and noticed the page /readme.html is publicly visible. It shows WordPress details your {who} don't need to see.",
      "xmlrpc.php": f"I ran a free public check on {host} and noticed /xmlrpc.php still answers publicly. Most sites don't use it anymore, so it's worth knowing it's on.",
      "SPF": f"I ran a free public check on {host} and noticed your domain doesn't have an SPF record yet. Without one, emails from your office to {who} are more likely to land in spam.",
      "DMARC": f"I ran a free public check on {host} and noticed the domain doesn't have a DMARC record. DMARC helps email providers tell your real messages to {who} apart from ones pretending to be you.",
      "HTTP \u2192 HTTPS redirect": f"I ran a free public check on {host} and noticed the plain http:// version of your site loads without switching to the secure https:// version. Visitors who type the plain address stay on the unencrypted version.",
    }[f["check"]]
    close = f"It takes about a minute and there's no signup. You can run the same free scan and see your results in plain English at {link}"
    base = f"{hi} {core}\n\n{close}"
    extra, cat = learned_line(c, nich, 80 - len(base.split()))
    body = f"{hi} {core}" + (f" {extra}" if extra else "") + f"\n\n{close}"
    return body, cat

def check_rules(body):
    w = len(body.split()); bad = []
    if not 50 <= w <= 80: bad.append(f"{w} words (need 50-80)")
    if re.search(r"full report", body, re.I): bad.append("mentions 'full report'")
    if re.search(r"\b(safe|hackproof|hack-proof|guarantee)\b", body, re.I): bad.append("forbidden claim word")
    if "sitesafetyscan.com/?url=" not in body: bad.append("missing free scan link")
    return w, bad

def finalize(body, pid):
    from harvey.state import CANSPAM_FOOTER
    import harvey.unsub as U
    return U.render(body + "\n\nJose, Site Safety Scan" + CANSPAM_FOOTER, pid)

# ---------------- caps ----------------
def daily_cap(c):
    try: rj = json.load(open(DATA + "/warmup_ramp.json"))
    except Exception: rj = {}
    if not rj.get("enabled") or not rj.get("start_date"): return 0, "warm-up ramp not enabled"
    d = dt.datetime.now(PT).date()
    day = (d - dt.date.fromisoformat(rj["start_date"])).days + 1  # 1-indexed sending day
    if day < 1: return 0, "before ramp start"
    if day <= 3: return int(rj.get("days_1_3", 5)), f"ramp day {day} (1-3)"
    if day <= 7: return int(rj.get("days_4_7", 10)), f"ramp day {day} (4-7)"
    if day <= 14: return int(rj.get("week2", 15)), f"ramp day {day} (week 2)"
    if day <= 21: return int(rj.get("week3", 20)), f"ramp day {day} (week 3)"
    return int(rj.get("after", 30)), f"ramp day {day} (after)"

def used_today(c):
    d = dt.datetime.now(PT).date().isoformat()
    rows = c.execute("select created_at,sent_at,status from outbox where kind in ('harvey_mode','sss_followup')").fetchall()
    def ptday(s):
        try: return dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).astimezone(PT).date().isoformat()
        except Exception: return ""
    return sum(1 for cr, se, st in rows if ptday(se or cr) == d and st not in ("held", "cancelled", "rejected"))

def blocked(c, pid, email):
    p = c.execute("select status from prospects where id=?", (pid,)).fetchone()
    if p and p[0] in ("opted_out", "bounced", "unsubscribed", "do_not_contact"): return "prospect status " + p[0]
    if c.execute("select 1 from v6_suppression where lower(email)=lower(?) or prospect_id=?", (email, pid)).fetchone(): return "suppressed (unsubscribe/bounce)"
    if c.execute("select 1 from outbox where prospect_id=? and status in ('bounced','failed')", (pid,)).fetchone(): return "previous bounce"
    if c.execute("select 1 from conversations where prospect_id=?", (pid,)).fetchone(): return "already replied"
    return ""

# ---------------- 3. smart 30-day re-check ----------------
def recheck(c, dry=False, force_days=None):
    days = FOLLOWUP_DAYS if force_days is None else force_days
    cut = iso(now() - dt.timedelta(days=days))
    cap, why = daily_cap(c); used = used_today(c); out = []
    rows = c.execute("""select o.prospect_id,o.to_email,o.sent_at,p.industry,p.first_name,co.domain from outbox o join prospects p on p.id=o.prospect_id
        left join companies co on co.id=p.company_id where o.kind='harvey_mode' and o.status='sent' and o.sent_at<=?
        and o.prospect_id not in (select prospect_id from sss_followups)""", (cut,)).fetchall()
    for pid, em, sent_at, ind, first, host in rows:
        host = host or em.split("@")[-1]
        b = blocked(c, pid, em)
        if b: log(c, pid, host, "recheck", "skip", b); c.execute("insert into sss_followups(prospect_id,at,reason,result) values(?,?,?,?)", (pid, iso(), b, "skipped")); continue
        old = {r[0] for r in c.execute("select check_name from sss_evidence where host=? and usable=1", (host,))}
        try: s = scan(host); sid = store_scan(c, host, s, "recheck")
        except Exception as e: log(c, pid, host, "recheck", "retry-later", "scan failed: " + str(e)[:80]); continue
        new = proven_findings(c, host, s, sid, pid, "recheck")
        fresh = [f for f in new if f["check"] not in old]
        if not fresh:
            c.execute("insert into sss_followups(prospect_id,at,reason,old_findings,new_findings,result) values(?,?,?,?,?,?)",
                      (pid, iso(), "no new verified issue", json.dumps(sorted(old)), json.dumps([f["check"] for f in new]), "no-followup"))
            log(c, pid, host, "recheck", "no-followup", "re-scan found nothing new with proof"); continue
        if not dry and used >= cap:
            log(c, pid, host, "recheck", "defer", f"daily cap reached ({used}/{cap}, {why})"); continue
        f = fresh[0]; nich = niche_of(ind)
        body = (f"Hi {first}," if first else "Hi there,") + f" quick follow-up on {host}. I re-ran the same free public check this month and one new thing showed up that wasn't there last time: {LABEL[f['check']]}. Proof: {f['proof']}. This is the only follow-up I'll send.\n\nThe free results are at https://sitesafetyscan.com/?url={host}"
        w, bad = check_rules(body)
        if bad: log(c, pid, host, "recheck", "hold", "follow-up failed rules: " + "; ".join(bad)); continue
        oid = uuid.uuid4().hex[:12]
        reason = f"new verified issue since first email: {f['check']} ({f['proof_url']})"
        if not dry:
            c.execute("insert into outbox(id,prospect_id,step,kind,to_email,subject,body,status) values(?,?,?,?,?,?,?,?)",
                      (oid, pid, 2, "sss_followup", em, "re: quick note on " + host, finalize(body, pid), "pending_review"))
            used += 1
        c.execute("insert into sss_followups values(?,?,?,?,?,?,?)", (pid, oid, iso(), reason, json.dumps(sorted(old)), json.dumps([x["check"] for x in fresh]), "dry-run" if dry else "queued"))
        log(c, pid, host, "recheck", "followup-queued" if not dry else "followup-dryrun", reason, {"words": w})
        out.append((host, body, reason))
    return out

# ---------------- build one prospect (dry run + future queueing) ----------------
def build(c, lead, scan_json, write_outbox=False, cap_state=None):
    host = lead["host"]; ind = lead.get("industry", ""); nich = niche_of(ind)
    pid = lead.get("pid") or uuid.uuid4().hex[:12]
    sid = store_scan(c, host, scan_json, "outreach")
    log(c, pid, host, "scan", "scanned", "public passive scan", {"score": scan_json.get("score"), "scannedAt": scan_json.get("scannedAt")})
    fnd = proven_findings(c, host, scan_json, sid, pid)
    facts = site_facts(host)
    log(c, pid, host, "contact", "dm-finder", facts["email_why"], {"pages": facts["pages"], "dm": facts["dm"], "email": facts["email"]})
    f = fnd[0] if fnd else None
    brief = make_brief(c, pid, host, lead.get("title", "").split("|")[0].strip(), facts, f)
    if not f: log(c, pid, host, "draft", "skip", "no finding with primary proof; nothing emailed"); return {"host": host, "brief": brief, "email": None, "pid": pid}
    if not facts["email"]: log(c, pid, host, "draft", "skip", "no email published on the site"); return {"host": host, "brief": brief, "email": None, "pid": pid}
    b = blocked(c, pid, facts["email"])
    if b: log(c, pid, host, "draft", "skip", b); return {"host": host, "brief": brief, "email": None, "pid": pid}
    # greet by name only when the published address clearly belongs to that person
    first = facts["dm"]["name"].split()[0] if facts["dm"] and facts["email_why"].startswith("published on site, matches") else ""
    if first and facts["dm"]["title"] in ("Dr.", "DDS", "DMD", "DC", "MD"): first = "Dr. " + facts["dm"]["name"].split()[-1]
    body, cat = write_email(c, host, first, nich, f)
    w, bad = check_rules(body)
    if bad: log(c, pid, host, "draft", "hold", "; ".join(bad)); return {"host": host, "brief": brief, "email": None, "pid": pid}
    log(c, pid, host, "draft", "drafted" if write_outbox else "drafted-dryrun", f"finding {f['check']} with proof {f['proof_url']}", {"words": w, "learned_line": cat or None, "to": facts["email"]})
    return {"host": host, "brief": brief, "email": body, "to": facts["email"], "words": w, "pid": pid, "finding": f}

def queue(c, leads):
    """Proof-gated drafting for the outreach routine. Writes companies/prospects/outbox (pending_review only), within the daily cap."""
    from harvey.state import CANSPAM_FOOTER  # noqa: F401 (fail early if image changes)
    cap, why = daily_cap(c); used = used_today(c); made = []
    for L in leads:
        if used >= cap: log(c, None, L.get("host"), "queue", "stop", f"daily cap {used}/{cap} ({why})"); break
        if c.execute("select 1 from companies where domain=?", (L["host"],)).fetchone(): continue
        try: s = L.get("scan") or scan(L["host"])
        except Exception as e: log(c, None, L["host"], "scan", "skip", "scan failed: " + str(e)[:80]); continue
        try: r = build(c, L, s, write_outbox=True)
        except Exception as e: log(c, None, L["host"], "build", "skip", "build error: " + str(e)[:80]); continue
        if not r["email"]: continue
        cid, pid = uuid.uuid4().hex[:12], r["pid"]; f = r["finding"]
        c.execute("insert into companies(id,name,domain,website,industry,location,source,notes) values(?,?,?,?,?,?,?,?)",
                  (cid, L.get("title", L["host"]).split("|")[0].strip()[:80], L["host"], "https://" + L["host"], L.get("industry", ""), "", "sss_engine",
                   f"SSS finding: {f['check']} (public scan {f['scanned_at'][:10]}; proof {f['proof_url']})"))
        c.execute("insert into prospects(id,company_id,email,title,source,status,company,industry,email_status) values(?,?,?,?,?,?,?,?,?)",
                  (pid, cid, r["to"], "", "sss_engine", "new", L.get("title", "")[:80], L.get("industry", ""), "published-on-site"))
        c.execute("insert into outbox(id,prospect_id,step,kind,to_email,subject,body,status) values(?,?,?,?,?,?,?,?)",
                  (uuid.uuid4().hex[:12], pid, 1, "harvey_mode", r["to"], "quick note on " + L["host"], finalize(r["email"], pid), "pending_review"))
        used += 1; made.append(L["host"])
    return made

def audit_drafts(c):
    res = []
    for oid, pid, em, subj, st in c.execute("select id,prospect_id,to_email,subject,status from outbox where kind='harvey_mode' and status in ('pending_review','approved','queued','scheduled')").fetchall():
        host = subj.replace("quick note on ", "").strip()
        notes = (c.execute("select notes from companies where domain=?", (host,)).fetchone() or [""])[0] or ""
        m = re.search(r"SSS finding: (.+?) \(", notes); chk = m.group(1) if m else ""
        body = (c.execute("select body from outbox where id=?", (oid,)).fetchone() or [""])[0]
        pv = re.search(r"PHP (\d+\.\d+\.\d+)", body)
        kind, url, proof = verify(host, {"name": chk, "detail": f"PHP {pv.group(1)} is older" if pv else ""}) if chk in PRIORITY else ("unverified", "", "no recorded finding")
        ok = kind == "primary"
        c.execute("insert into sss_evidence(host,check_name,level,kind,proof_url,proof,verified_at,usable) values(?,?,?,?,?,?,?,?)", (host, chk, "warn", kind, url, proof, iso(), int(ok)))
        if not ok: c.execute("update outbox set status='held',error=? where id=?", ("proof gate: " + proof, oid))
        log(c, pid, host, "audit", "proof-ok" if ok else "held", f"{chk}: {proof}", {"proof_url": url})
        res.append((host, chk, kind, proof))
    return res

def main():
    a = sys.argv[1:]; cmd = a[0] if a else "help"
    db = a[a.index("--db") + 1] if "--db" in a else DB
    if "--copy" in a:   # dry runs work on a snapshot copy of the live DB
        src = sqlite3.connect(DB); dst = sqlite3.connect(db); src.backup(dst); dst.close(); src.close()
    c = sqlite3.connect(db); migrate(c)
    if cmd == "dryrun":
        sys.path.insert(0, DATA)
        leads = []
        for fn in ("scanned2.json", "scanned.json"):
            try: leads += [x for x in json.load(open(DATA + "/" + fn)) if x.get("scan") and not x["scan"].get("error")]
            except Exception: pass
        lim = int(a[a.index("--n") + 1]) if "--n" in a else 6
        for L in leads[:lim]:
            r = build(c, L, L["scan"])
            print("=" * 70); print("HOST:", r["host"]); print("BRIEF:\n" + r["brief"])
            if r["email"]: print(f"EMAIL to {r['to']} ({r['words']} words):\n{r['email']}")
            else: print("EMAIL: none (see decision log)")
        print("=" * 70, "\nDECISION LOG (last 25):")
        for row in c.execute("select at,host,stage,action,reason from sss_decision_log order by id desc limit 25").fetchall()[::-1]: print(" ", row)
        print("\nRE-CHECK dry run (sent emails older than 30 days):", recheck(c, dry=True) or "none eligible yet")
    elif cmd == "queue": print("queued", queue(c, json.load(open(a[1]))))
    elif cmd == "audit-drafts": print(audit_drafts(c))
    elif cmd == "recheck": print(recheck(c, dry="--dry" in a))
    elif cmd == "replies": print("notices", learn_replies(c))
    elif cmd == "brief": print((c.execute("select brief from sss_briefs where host=?", (a[1],)).fetchone() or ["none"])[0])
    else: print(__doc__); return
    c.commit()

if __name__ == "__main__": main()
