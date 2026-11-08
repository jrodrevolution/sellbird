import sys,json,hashlib,sqlite3,collections
sys.path.insert(0,'/app');sys.path.insert(0,'/app/data')
import sss_spaced_send as S
from harvey.gate import pre_send_check
m=json.load(sys.stdin);c=sqlite3.connect('file:/app/data/harvey.db?mode=ro',uri=True);c.row_factory=sqlite3.Row
reasons=collections.Counter();eligible=[];static_pass=[]
for oid,digest in m['items'].items():
 r=c.execute('select * from outbox where id=?',(oid,)).fetchone()
 if r is None:reasons['missing']+=1;continue
 frozen={k:r[k] for k in m['fields']}
 if hashlib.sha256(json.dumps(frozen,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=digest:reasons['content_changed']+=1;continue
 if r['status']!='held':reasons['not_held']+=1;continue
 pid=r['prospect_id'];em=r['to_email'];body=r['body'];subj=r['subject']
 if c.execute('select 1 from v6_suppression where lower(email)=lower(?) or prospect_id=?',(em,pid)).fetchone():reasons['suppressed']+=1;continue
 if c.execute('select 1 from conversations where prospect_id=?',(pid,)).fetchone():reasons['already_replied']+=1;continue
 if c.execute("select 1 from outbox where id<>? and lower(to_email)=lower(?) and status='sent'",(oid,em)).fetchone():reasons['prior_sent']+=1;continue
 gate=pre_send_check(em,subj,body,prospect=None,kind='harvey_mode')
 if not gate:
  for why in gate.reasons:
   category=why.split(':')[0].split('(')[0].strip();reasons['gate '+category]+=1
  continue
 static_pass.append(oid)
if '--live' in sys.argv:
 import concurrent.futures
 def check(oid):
  conn=sqlite3.connect('file:/app/data/harvey.db?mode=ro',uri=True);conn.row_factory=sqlite3.Row
  r=conn.execute('select * from outbox where id=?',(oid,)).fetchone()
  try:
   ok,checks=S.precheck(conn,oid,r['prospect_id'],r['to_email'],r['subject'],r['body'])
   return oid,ok,[name for name,passed,detail in checks if not passed]
  except Exception:return oid,False,['live_validation_unavailable']
  finally:conn.close()
 with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
  for oid,ok,failed in pool.map(check,static_pass):
   if ok:eligible.append(oid)
   else:
    for reason in failed:reasons[reason]+=1
print(json.dumps({'eligible':eligible,'frozen':len(m['items']),'static_pass':len(static_pass),'rejection_reasons':dict(reasons),'static_pass_ids':static_pass}))
