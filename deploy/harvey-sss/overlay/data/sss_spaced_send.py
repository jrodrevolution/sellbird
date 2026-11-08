#!/usr/bin/env python3
import os, re, sqlite3, asyncio, datetime as dt
from email.message import EmailMessage
from email.utils import make_msgid, formataddr
import aiosmtplib
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,"/app")
sys.path.insert(0,"/app/data")
from harvey.gate import pre_send_check
import harvey.unsub as U


def in_send_window(now=None):
    from zoneinfo import ZoneInfo
    t = now or dt.datetime.now(ZoneInfo("America/Los_Angeles"))
    return 9 <= t.hour < 18

# ---------------- pre-send double-check gate (added 2026-10-23) ----------------
FREEMAIL = {"gmail.com","yahoo.com","hotmail.com","outlook.com","aol.com","icloud.com","me.com","live.com","msn.com","proton.me","protonmail.com","att.net","sbcglobal.net","comcast.net"}
BANNED = r"\b(safe|hackproof|hack-proof|guarantee[sd]?|hacked|urgent)\b|full report"
def _norm(h): return (h or "").lower().strip().strip("/").removeprefix("https://").removeprefix("http://").removeprefix("www.").split("/")[0]

def precheck(c, oid, pid, em, subj, body):
    import urllib.request, sss_engine as E
    res = []
    def chk(name, ok, detail=""): res.append((name, bool(ok), detail))
    m = re.match(r"(?:re: )?quick note on (\S+)$", subj or "")
    sd = _norm(m.group(1)) if m else ""
    main = (body or "").split("\n\n--")[0]
    links = [_norm(x) for x in re.findall(r"https://sitesafetyscan\.com/\?url=([^\s]+)", main)]
    mentioned = {_norm(x) for x in re.findall(r"check on (\S+?) and", main)}
    rd = _norm(em.split("@")[-1])
    row = c.execute("select co.domain from prospects p left join companies co on co.id=p.company_id where p.id=?", (pid,)).fetchone()
    lead = _norm(row[0]) if row and row[0] else ""
    if not sd and lead and re.search(r"(?i)(?<![a-z0-9.-])" + re.escape(lead) + r"(?![a-z0-9.-])", subj or ""):
        sd = lead
    if sd and not mentioned and re.search(r"(?i)(?<![a-z0-9.-])" + re.escape(sd) + r"(?![a-z0-9.-])", main):
        mentioned = {sd}
    dom_ok = bool(sd) and links and all(l == sd for l in links) and mentioned == {sd}
    if rd in FREEMAIL: tgt_ok = (lead == sd); why = f"freemail recipient; lead site {lead}"
    else: tgt_ok = (rd == sd); why = f"recipient domain {rd}"
    chk("a_domains_match", dom_ok and tgt_ok, f"subject={sd} links={links} body={sorted(mentioned)} {why}")
    try: st = urllib.request.urlopen(urllib.request.Request(f"https://sitesafetyscan.com/?url={sd}", headers={"User-Agent": "SSS-precheck/1.0"}), timeout=15).status
    except Exception as e: st = getattr(e, "code", str(e)[:60])
    chk("b_scan_link_200", st == 200, f"status {st}")
    st2 = E.fetch(f"https://{sd}/", n=2000)[0] if sd else None
    if not st2 or st2 >= 500: st2 = E.fetch(f"http://{sd}/", n=2000)[0] if sd else None
    chk("c_site_reachable", st2 is not None and st2 < 500, f"status {st2}")
    low = main.lower()
    fname = ("readme.html" if "readme.html" in low else "xmlrpc.php" if "xmlrpc" in low else "DMARC" if "dmarc" in low
             else "SPF" if "spf" in low else "HTTP \u2192 HTTPS redirect" if "http://" in low else "PHP version" if "php" in low else None)
    if fname == "PHP version":
        ev = c.execute("select proof from sss_evidence where host=? and check_name='PHP version' order by id desc limit 1", (sd,)).fetchone()
        detail = ""
        mm = re.search(r"PHP (\d+\.\d+\.\d+)", main)
        if mm: detail = f"PHP {mm.group(1)} is older"
        v = E.verify(sd, {"name": fname, "detail": detail})
    else:
        v = E.verify(sd, {"name": fname}) if fname else ("unverified", "", "no recognizable finding in body")
    chk("d_live_proof", v[0] == "primary", f"{fname}: {v[0]} {v[2]}")
    sup = c.execute("select 1 from v6_suppression where lower(email)=lower(?) or prospect_id=?", (em, pid)).fetchone()
    prior = c.execute("select 1 from outbox where lower(to_email)=lower(?) and status='sent' and id<>? and kind='harvey_mode'", (em, oid)).fetchone()
    pst = (c.execute("select status from prospects where id=?", (pid,)).fetchone() or [""])[0]
    chk("e_not_unsub_or_contacted", not sup and not (prior and not subj.startswith("re: ")) and (pst != "contacted" or subj.startswith("re: ")), f"suppressed={bool(sup)} prior_sent={bool(prior)} prospect={pst}")
    w = len(main.split())
    chk("f_words_50_120", 50 <= w <= 120, f"{w} words")
    chk("f_unsub_link", bool(re.search(r"https://sitesafetyscan\.com/u/[A-Za-z0-9._%-]+", body or "")), "")
    chk("f_footer", "San Ysidro" in (body or ""), "")
    bw = re.findall(BANNED, main, re.I)
    chk("f_no_banned_words", not bw, str(bw))
    ph = re.findall(r"\{\{|\}\}|\bNone\b|\{[a-z_]+\}", (subj or "") + "\n" + (body or ""))
    chk("g_no_placeholders", not ph, str(ph))
    return all(ok for _, ok, _ in res), res

def log_precheck(oid, em, ok, res):
    print(f"precheck {oid} {em}: {'PASS' if ok else 'FAIL'}")
    for n, k, d in res: print(f"   {'pass' if k else 'FAIL'} {n} {d}")

async def go():
    assert U.base() == "https://sitesafetyscan.com", U.base()
    c=sqlite3.connect("/app/data/harvey.db")
    paused=(c.execute("select value from settings where key='sending_paused'").fetchone() or [""])[0]
    if paused:
        print("paused:", paused); return
    now=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).replace(microsecond=0).isoformat()
    rows=c.execute(
        "select id,prospect_id,to_email,subject,body,send_at from outbox where status='approved' and (send_at is null or send_at<=?) order by send_at limit 5",
        (now,),
    ).fetchall()
    if not rows:
        print("nothing due"); return
    smtp=dict(host=os.environ["SMTP_HOST"], port=int(os.environ.get("SMTP_PORT","465")),
              user=os.environ["SMTP_USERNAME"], password=os.environ["SMTP_PASSWORD"])
    for oid,pid,em,subj,body,sa in rows:
        if "harvey.dmtoclose.com" in (body or ""):
            c.execute("update outbox set status='held', error=? where id=?", ("wrong unsub host", oid)); c.commit(); continue
        if U.PLACEHOLDER in body:
            c.execute("update outbox set status='failed', error=? where id=?", ("unsub placeholder", oid)); c.commit(); continue
        # Scoped pt3726u originals only: exact content hash + current daily cap + replies/dedupe.
        import json
        import legacy_permission as LP
        legacy_ok, legacy_reason = LP.permitted(c, oid)
        if not legacy_ok:
            c.execute("update outbox set status='held', error=? where id=?", (legacy_reason, oid)); c.commit(); continue
        in_legacy_manifest = False
        try:
            m = json.load(open(LP.PATH))
            in_legacy_manifest = oid in (m.get("items") or {})
        except Exception:
            in_legacy_manifest = False
        try:
            from sss_glm.bind import allow_send as glm_allow_send
            glm_ok, glm_reason = glm_allow_send(c, oid, in_legacy_manifest=in_legacy_manifest)
        except Exception:
            from sss_glm.bind import allow_send_if_glm_import_failed
            try:
                glm_ok, glm_reason = allow_send_if_glm_import_failed(in_legacy_manifest=in_legacy_manifest)
            except Exception:
                glm_ok, glm_reason = (True, "") if in_legacy_manifest else (False, "glm binding guard unavailable; hold")
        if not glm_ok:
            c.execute("update outbox set status='held', error=? where id=?", (glm_reason or "unverifiable non-legacy; hold", oid)); c.commit(); continue
        g=pre_send_check(em,subj,body,prospect=None,kind="harvey_mode")
        if not g:
            c.execute("update outbox set status='failed', error=? where id=?", ("gate: "+", ".join(g.reasons), oid)); c.commit(); continue
        if c.execute("select 1 from v6_suppression where lower(email)=lower(?) or prospect_id=?", (em,pid)).fetchone():
            c.execute("update outbox set status='cancelled', error=? where id=?", ("suppressed", oid)); c.commit(); continue
        try: ok, res = precheck(c, oid, pid, em, subj, body)
        except Exception as e: ok, res = False, [("precheck_error", False, str(e)[:200])]
        log_precheck(oid, em, ok, res)
        if not ok:
            c.execute("update outbox set status='held', error=? where id=?", ("precheck: " + "; ".join(f"{n} ({d})" for n, k, d in res if not k)[:300], oid)); c.commit(); continue
        msg=EmailMessage()
        msg["To"]=em
        msg["From"]=formataddr(("Jose", "jose@getsitesafetyscan.com"))
        msg["Subject"]=subj
        mid=make_msgid(domain="getsitesafetyscan.com"); msg["Message-ID"]=mid
        m=re.search(r"https://sitesafetyscan.com/u/[A-Za-z0-9._%-]+", body)
        if m:
            msg["List-Unsubscribe"]=f"<{m.group(0)}>"; msg["List-Unsubscribe-Post"]="List-Unsubscribe=One-Click"
        msg.set_content(body)
        try:
            await aiosmtplib.send(msg, hostname=smtp["host"], port=smtp["port"], username=smtp["user"],
                password=smtp["password"], start_tls=(smtp["port"]==587), use_tls=(smtp["port"]==465), timeout=30)
            c.execute("update outbox set status='sent', sent_at=?, message_id=?, thread_ref=? where id=?", (now,mid,mid,oid))
            c.execute("update prospects set status='contacted' where id=?", (pid,))
            c.commit(); print("sent", em, "subj", subj)
        except Exception as e:
            c.execute("update outbox set status='failed', error=? where id=?", (str(e)[:300], oid)); c.commit(); print("fail", e)

if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        c = sqlite3.connect("file:/app/data/harvey.db?mode=ro", uri=True)
        for oid, pid, em, subj, body in c.execute("select id,prospect_id,to_email,subject,body from outbox where status='approved' order by send_at").fetchall():
            ok, res = precheck(c, oid, pid, em, subj, body); log_precheck(oid, em, ok, res)
        sys.exit(0)
    if not in_send_window():
        print("outside send window (daily 9:00-18:00 America/Los_Angeles), skip"); sys.exit(0)
    asyncio.run(go())
