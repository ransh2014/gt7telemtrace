"""3D lap replay: a single self-contained HTML file (no CDN, works offline) plus a GIF export.

The page draws the racing line from the lap's world position, coloured by speed, with a moving car, an optional ghost
car from a second lap, and a synced throttle / brake / speed trace under a scrubber. Drag to rotate, wheel to zoom,
space to play. A GIF (via Pillow, already a TRACE dependency) is a top-down loop of the same lap.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

MAX_POINTS = 1500


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _compact(samples, ref=False) -> list[list[float]]:
    """[x, y(up), z, speed, throttle, brake, t] per sample, thinned to MAX_POINTS."""
    rows = []
    for s in samples or []:
        if not isinstance(s, dict) or "world_x" not in s:
            continue
        rows.append([_num(s.get("world_x")), _num(s.get("world_y")), _num(s.get("world_z")),
                     _num(s.get("speed_kmh")), _num(s.get("throttle")), _num(s.get("brake")), _num(s.get("t"))])
    if len(rows) > MAX_POINTS:
        step = len(rows) / MAX_POINTS
        rows = [rows[int(i * step)] for i in range(MAX_POINTS)]
    if rows:
        t0 = rows[0][6]
        for r in rows:
            r[6] = round(r[6] - t0, 3)
            for i in range(6):
                r[i] = round(r[i], 2)
    return rows


def usable(samples) -> bool:
    return len(_compact(samples)) >= 20


def build_html(samples, title: str = "TRACE lap replay", ghost_samples=None) -> str:
    data = {"lap": _compact(samples), "ghost": _compact(ghost_samples) if ghost_samples else []}
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    safe_title = (title or "TRACE lap replay").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _PAGE.replace("/*@TITLE@*/", safe_title).replace("/*@DATA@*/", blob)


def write_html(path, samples, title="TRACE lap replay", ghost_samples=None) -> Path:
    p = Path(path)
    p.write_text(build_html(samples, title, ghost_samples), encoding="utf-8")
    return p


def export_gif(path, samples, size: int = 480, frames: int = 90, ms: int = 40) -> Path | None:
    """A top-down GIF of the lap, the line coloured by speed with a dot for the car. None if Pillow is missing or
    the lap has no positions."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    rows = _compact(samples)
    if len(rows) < 20:
        return None
    xs, zs = [r[0] for r in rows], [r[2] for r in rows]
    span = max(max(xs) - min(xs), max(zs) - min(zs), 1.0)
    pad = 24
    sc = (size - 2 * pad) / span
    cx, cz = (max(xs) + min(xs)) / 2, (max(zs) + min(zs)) / 2

    def pt(r):
        return (size / 2 + (r[0] - cx) * sc, size / 2 - (r[2] - cz) * sc)

    vmax = max(r[3] for r in rows) or 1.0

    def col(v):
        f = max(0.0, min(1.0, v / vmax))
        return (int(40 + 215 * (1 - f)), int(60 + 180 * f), 90)

    base = Image.new("RGB", (size, size), (8, 8, 10))
    d = ImageDraw.Draw(base)
    for a, b in zip(rows, rows[1:]):
        d.line([pt(a), pt(b)], fill=col((a[3] + b[3]) / 2), width=3)
    imgs = []
    for k in range(frames):
        i = min(len(rows) - 1, int(k * len(rows) / frames))
        im = base.copy()
        dd = ImageDraw.Draw(im)
        x, y = pt(rows[i])
        dd.ellipse([x - 6, y - 6, x + 6, y + 6], fill=(255, 255, 255), outline=(200, 16, 46), width=2)
        dd.text((10, 8), f"{rows[i][3]:.0f} km/h", fill=(242, 240, 236))
        imgs.append(im)
    p = Path(path)
    imgs[0].save(p, save_all=True, append_images=imgs[1:], duration=ms, loop=0, optimize=True)
    return p


_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>/*@TITLE@*/</title>
<style>
:root{--bg:#08080a;--panel:#0e0e11;--txt:#f2f0ec;--mid:#96949c;--red:#c8102e;--teal:#5fb3a8;--gold:#c9a24b}
@media (prefers-color-scheme:light){:root{--bg:#f4f2ee;--panel:#fff;--txt:#111;--mid:#666}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px system-ui,sans-serif;
display:flex;flex-direction:column;height:100vh}
header{padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
h1{font-size:16px;margin:0;flex:1}
button{background:var(--red);color:#fff;border:0;border-radius:4px;padding:6px 14px;font:inherit;cursor:pointer}
#view{flex:1;min-height:0;position:relative}canvas{width:100%;height:100%;display:block;touch-action:none}
#hud{position:absolute;left:12px;top:8px;font:13px ui-monospace,monospace;color:var(--mid)}
footer{padding:8px 16px;background:var(--panel)}#scrub{width:100%}
#trace{width:100%;height:84px;display:block}
</style></head><body>
<header><h1>/*@TITLE@*/</h1><button id="play">Play</button>
<label>Speed <select id="rate"><option>0.25</option><option>0.5</option><option selected>1</option><option>2</option><option>4</option></select></label></header>
<div id="view"><canvas id="c"></canvas><div id="hud"></div></div>
<footer><canvas id="trace"></canvas><input id="scrub" type="range" min="0" max="1000" value="0"></footer>
<script>
const D=/*@DATA@*/;
const L=D.lap,G=D.ghost,N=L.length;
if(N<2){document.body.innerHTML="<p style='padding:24px'>No position data in this lap.</p>"}
const cv=document.getElementById('c'),cx=cv.getContext('2d'),tr=document.getElementById('trace'),tx=tr.getContext('2d');
let yaw=-0.8,pitch=0.9,zoom=1,t=0,playing=false,last=0;
const T=L[N-1][6]||1;
let minx=1e9,maxx=-1e9,minz=1e9,maxz=-1e9,vmax=1;
for(const r of L){minx=Math.min(minx,r[0]);maxx=Math.max(maxx,r[0]);minz=Math.min(minz,r[2]);maxz=Math.max(maxz,r[2]);vmax=Math.max(vmax,r[3])}
const cxw=(minx+maxx)/2,czw=(minz+maxz)/2,span=Math.max(maxx-minx,maxz-minz,1);
function proj(x,y,z,w,h){x-=cxw;z-=czw;
 const a=Math.cos(yaw),b=Math.sin(yaw);const X=x*a-z*b,Z=x*b+z*a;
 const c=Math.cos(pitch),s=Math.sin(pitch);
 const k=Math.min(w,h)*0.9/span*zoom;return[w/2+X*k,h/2-(y*c+Z*s)*k]}
function col(v){const f=Math.max(0,Math.min(1,v/vmax));return`rgb(${Math.round(40+215*(1-f))},${Math.round(60+180*f)},90)`}
function idx(arr,tt){let lo=0,hi=arr.length-1;while(lo<hi){const m=(lo+hi)>>1;if(arr[m][6]<tt)lo=m+1;else hi=m}return lo}
function size(){const r=cv.getBoundingClientRect(),d=devicePixelRatio||1;cv.width=r.width*d;cv.height=r.height*d;
 const q=tr.getBoundingClientRect();tr.width=q.width*d;tr.height=q.height*d}
function draw(){const w=cv.width,h=cv.height;cx.clearRect(0,0,w,h);cx.lineWidth=3*(devicePixelRatio||1);cx.lineCap='round';
 let p=proj(L[0][0],L[0][1],L[0][2],w,h);
 for(let i=1;i<N;i++){const q=proj(L[i][0],L[i][1],L[i][2],w,h);cx.strokeStyle=col(L[i][3]);cx.beginPath();cx.moveTo(p[0],p[1]);cx.lineTo(q[0],q[1]);cx.stroke();p=q}
 if(G.length>1){cx.strokeStyle='rgba(150,148,156,.35)';cx.lineWidth=1.5;cx.beginPath();
  G.forEach((r,i)=>{const q=proj(r[0],r[1],r[2],w,h);i?cx.lineTo(q[0],q[1]):cx.moveTo(q[0],q[1])});cx.stroke();
  const g=G[idx(G,t)],q=proj(g[0],g[1],g[2],w,h);cx.fillStyle='#96949c';cx.beginPath();cx.arc(q[0],q[1],6*(devicePixelRatio||1),0,7);cx.fill()}
 const r=L[idx(L,t)],q=proj(r[0],r[1],r[2],w,h);cx.fillStyle='#fff';cx.strokeStyle='#c8102e';cx.lineWidth=3;
 cx.beginPath();cx.arc(q[0],q[1],7*(devicePixelRatio||1),0,7);cx.fill();cx.stroke();
 document.getElementById('hud').textContent=`${t.toFixed(2)} s   ${r[3].toFixed(0)} km/h   throttle ${(r[4]*100).toFixed(0)}%   brake ${(r[5]*100).toFixed(0)}%`;
 const tw=tr.width,th=tr.height;tx.clearRect(0,0,tw,th);
 const line=(f,c)=>{tx.strokeStyle=c;tx.lineWidth=2;tx.beginPath();L.forEach((r,i)=>{const X=r[6]/T*tw,Y=th-f(r)*(th-4)-2;i?tx.lineTo(X,Y):tx.moveTo(X,Y)});tx.stroke()};
 line(r=>r[3]/vmax,'#5fb3a8');line(r=>r[4],'#3c9a5f');line(r=>r[5],'#c8102e');
 tx.fillStyle='#fff';tx.fillRect(t/T*tw-1,0,2,th)}
function frame(ts){if(playing){t+=(ts-last)/1000*parseFloat(document.getElementById('rate').value);if(t>=T){t=0}
  document.getElementById('scrub').value=t/T*1000}last=ts;draw();requestAnimationFrame(frame)}
document.getElementById('play').onclick=function(){playing=!playing;this.textContent=playing?'Pause':'Play'};
document.getElementById('scrub').oninput=e=>{t=e.target.value/1000*T};
let drag=null;cv.onpointerdown=e=>{drag=[e.clientX,e.clientY];cv.setPointerCapture(e.pointerId)};
cv.onpointermove=e=>{if(drag){yaw+=(e.clientX-drag[0])*0.008;pitch=Math.max(0,Math.min(1.5,pitch+(e.clientY-drag[1])*0.006));drag=[e.clientX,e.clientY]}};
cv.onpointerup=()=>drag=null;cv.onwheel=e=>{e.preventDefault();zoom=Math.max(.3,Math.min(8,zoom*(e.deltaY<0?1.1:.9)))};
addEventListener('keydown',e=>{if(e.code==='Space'){e.preventDefault();document.getElementById('play').click()}});
addEventListener('resize',size);size();requestAnimationFrame(frame);
</script></body></html>
"""
