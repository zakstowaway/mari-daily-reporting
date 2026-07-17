import json, os, sys, urllib.request
from datetime import date, datetime, timezone, timedelta
TOKEN=os.environ["DEPUTY_TOKEN"]; OFF=10
XP=json.load(open('data/xero_pay_weekly.json')); EM=json.load(open('data/employee_map.json'))
def post(p,b):
    r=urllib.request.Request("https://831d4015123255.au.deputy.com"+p,data=json.dumps(b).encode(),
        headers={"Authorization":f"OAuth {TOKEN}","Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(r).read())
a,b=date(2026,3,2),date(2026,7,13)
t0=int(datetime(a.year,a.month,a.day,tzinfo=timezone(timedelta(hours=OFF))).timestamp())
t1=int(datetime(b.year,b.month,b.day,tzinfo=timezone(timedelta(hours=OFF))).timestamp())
ts=[];off=0
while True:
    r=post("/api/v1/resource/Timesheet/QUERY",{"search":{"s1":{"field":"StartTime","type":"ge","data":t0},
        "s2":{"field":"StartTime","type":"lt","data":t1},"s3":{"field":"Discarded","type":"eq","data":0}},"start":off,"max":500})
    ts+=r
    if len(r)<500: break
    off+=500
wk=lambda e:(datetime.fromtimestamp(e,tz=timezone(timedelta(hours=OFF))).date()+timedelta(days=6-datetime.fromtimestamp(e,tz=timezone(timedelta(hours=OFF))).date().weekday())).isoformat()
dw={}
for t in ts:
    dw.setdefault(str(t.get("Employee")),set()).add(wk(t["StartTime"]))
emps={str(e["Id"]):e["DisplayName"] for e in post("/api/v1/resource/Employee/QUERY",{"search":{},"max":500})}
cands={'69':'Samuel Hall'}
print("WHO IS id 69?  deputy name:", emps.get('69'))
print()
print(f"{'id':>4} {'deputy':16} {'xero':22} {'D wks':>6} {'X wks':>6} {'both':>5} {'D only':>7} {'X only':>7}  verdict")
for eid,xn in cands.items():
    d=dw.get(eid,set()); x={w for w,v in XP.get(xn,{}).items() if v}
    both=d&x; do=d-x; xo=x-d
    if not x: v="XERO HAS NO SUCH PAY"
    elif not d: v="no deputy hours"
    elif len(both)>=max(3,0.8*len(d)) and len(do)<=1: v="MATCH"
    elif both: v=f"partial ({len(both)}/{len(d)})"
    else: v="NO OVERLAP -> different people"
    print(f"{eid:>4} {emps.get(eid,'?')[:16]:16} {xn[:22]:22} {len(d):>6} {len(x):>6} {len(both):>5} {len(do):>7} {len(xo):>7}  {v}")
print("\nEvery unmapped Deputy account with hours since March, vs Samuel Hall's paid weeks:")
sh={w for w,v in XP.get('Samuel Hall',{}).items() if v}
for eid,ws in sorted(dw.items(), key=lambda k:-len(k[1]&sh)):
    if eid in EM: continue
    print(f"    id {eid:>4} {emps.get(eid,'?')[:22]:22} D wks {len(ws):>3}  overlap {len(ws&sh):>3}/{len(sh)}  D-only {len(ws-sh):>3}")
