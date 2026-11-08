"""Releases only frozen unchanged, currently validated originals; no sends."""
import sys,json,sqlite3,datetime as dt,collections
sys.path.insert(0,'/app/data');sys.path.insert(0,'/app')
import sss_engine as E,legacy_permission as P,sss_spaced_send as S
m=json.load(sys.stdin);c=sqlite3.connect('/app/data/harvey.db',timeout=30);c.row_factory=sqlite3.Row
cap=min(20,E.daily_cap(c)[0]);used=P.sent_today(c)
reserved=c.execute("select count(*) from outbox where status='approved'").fetchone()[0]
slots=max(0,cap-used-reserved);approved=[];reasons=collections.Counter();seen=set()
# No template rewrites, recipient substitutions, or future IDs.
for oid,expected in sorted(m['items'].items()):
 if len(approved)>=slots:break
 r=c.execute('select * from outbox where id=?',(oid,)).fetchone()
 if not r or r['status']!='held':continue
 if P.digest(r,m['fields'])!=expected:reasons['changed']+=1;continue
 ok,why=P.permitted(c,oid)
 if not ok:reasons[why]+=1;continue
 em=r['to_email'].strip().lower()
 if em in seen or c.execute("select 1 from outbox where lower(to_email)=? and status='approved'",(em,)).fetchone():reasons['duplicate']+=1;continue
 from harvey.gate import pre_send_check
 if not pre_send_check(em,r['subject'],r['body'],prospect=None,kind='harvey_mode'):reasons['static_gate']+=1;continue
 if c.execute('select 1 from v6_suppression where lower(email)=? or prospect_id=?',(em,r['prospect_id'])).fetchone():reasons['suppressed']+=1;continue
 try:ok,checks=S.precheck(c,oid,r['prospect_id'],em,r['subject'],r['body'])
 except Exception:ok=False
 if not ok:reasons['live_precheck']+=1;continue
 # Take write lock only after network validation and recheck hash/status under lock.
 c.execute('begin immediate')
 fresh=c.execute('select * from outbox where id=?',(oid,)).fetchone()
 currentreserved=c.execute("select count(*) from outbox where status='approved'").fetchone()[0]
 if fresh['status']=='held' and P.digest(fresh,m['fields'])==expected and P.sent_today(c)+currentreserved<cap:
  c.execute("update outbox set status='approved',error=?,send_at=null where id=? and status='held'",('Scoped permission pt3726u; exact frozen SHA256 '+expected,oid));approved.append(oid);seen.add(em)
 c.commit()
print(json.dumps({'released':len(approved),'released_ids':approved,'cap':cap,'sent_today':used,'slots_before':slots,'rejections':dict(reasons),'permission':'pt3726u'}))
