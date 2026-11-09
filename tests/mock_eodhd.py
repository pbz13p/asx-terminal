"""Fake EODHD responses in the documented shapes, for testing without a key."""
import json, math, random, re
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
CODES=['BHP','CBA','CSL','NAB','WBC','FMG','WDS','WES']+[f'T{i:02d}' for i in range(60)]
L=date(2026,11,6); P=date(2026,11,5)
def px(c,seed=0):
    random.seed(c+str(seed)); return round(random.uniform(.5,120),3)
def series(sym,n):
    random.seed(sym); p=random.uniform(5,200); o=[]; d=date.today()-timedelta(days=n)
    for i in range(n):
        p*=1+random.gauss(.0003,.014); o.append(dict(date=(d+timedelta(days=i)).isoformat(),open=round(p*.99,3),high=round(p*1.01,3),low=round(p*.98,3),close=round(p,3),volume=random.randint(1000,900000)))
    return o
class H(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def do_GET(self):
        u=urlparse(self.path);q={k:v[0] for k,v in parse_qs(u.query).items()};p=u.path.replace('/api/','',1)
        assert q.get('api_token')=='testkey'
        if p.startswith('exchange-symbol-list'):b=[{'Code':c,'Name':c+' Ltd','Type':'Common Stock'} for c in CODES]
        elif p.startswith('eod-bulk'):
            d=q.get('date',L.isoformat())
            if d not in (L.isoformat(),P.isoformat()):b=[]
            else:
                b=[]
                for c in CODES:
                    base=px(c);random.seed(c+'m');m=random.uniform(-.2,.2)
                    b.append(dict(code=c,exchange_short_name='AU',date=d,open=base,high=base,low=base,close=round(base*(1+m) if d==L.isoformat() else base,3),volume=random.randint(50000,3000000)))
        elif p.startswith('real-time/'):
            s=[p.split('/')[1]]+[x for x in q.get('s','').split(',') if x];b=[]
            for x in s:
                c=series(x,90)[-1]['close'];b.append(dict(code=x,timestamp=1794216000,gmtoffset=0,open=c,high=c*1.01,low=c*.99,close=c,volume=12345,previousClose=round(c/1.006,3),change=.1,change_p=.6))
            if len(b)==1:b=b[0]
        elif p.startswith('eod/'):b=series(p.split('/')[1],400)
        elif p.startswith('intraday/'):
            self.send_response(403);self.end_headers();return
        else:b=[]
        d=json.dumps(b).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(d)));self.end_headers();self.wfile.write(d)
HTTPServer(('127.0.0.1',9901),H).serve_forever()
