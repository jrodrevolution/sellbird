"""Exact frozen legacy drafts only. Does not authorize new drafts or send anything."""
import json,hashlib,datetime as dt
from zoneinfo import ZoneInfo
PATH='/app/data/legacy_permission_manifest.json'
def digest(row,fields):return hashlib.sha256(json.dumps({k:row[k] for k in fields},sort_keys=True,separators=(',',':')).encode()).hexdigest()
def sent_today(c):
 day=dt.datetime.now(ZoneInfo('America/Tijuana')).date();n=0
 for (s,) in c.execute("select sent_at from outbox where status='sent'"):
  try:
   t=dt.datetime.fromisoformat(s.replace('Z','+00:00'))
   if t.tzinfo is None:t=t.replace(tzinfo=dt.timezone.utc)
   n+=t.astimezone(ZoneInfo('America/Tijuana')).date()==day
  except Exception:raise ValueError('unknown sent timestamp; hold')
 return n

def permitted(c,oid):
 import sss_engine as E
 try:
  m=json.load(open(PATH))
  if oid not in m['items']:return True,'' # Human-approved future rows follow their existing path; no auto-approval here.
  fields=m['fields'];cur=c.execute('select '+','.join(fields)+' from outbox where id=?',(oid,));r=dict(zip(fields,cur.fetchone()))
  if digest(r,fields)!=m['items'][oid]:return False,'legacy content changed; fresh approval required'
  cap=min(20,E.daily_cap(c)[0])
  if sent_today(c)>=cap:return False,'legacy daily warmup/cap reached'
  pid=r['prospect_id'];em=r['to_email']
  if c.execute('select 1 from conversations where prospect_id=?',(pid,)).fetchone():return False,'legacy recipient has replied'
  if c.execute("select 1 from outbox where id<>? and lower(to_email)=lower(?) and status='sent'",(oid,em)).fetchone():return False,'legacy recipient already sent'
  return True,''
 except Exception:return False,'legacy permission guard unavailable; hold'
