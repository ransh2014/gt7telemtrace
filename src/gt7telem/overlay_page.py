"""overlay_page.py -- the transparent stream overlay served at /overlay by webui_server.

Add it to OBS as a Browser Source (http://<this PC>:8080/overlay, width 1920 height 1080, "Control audio via OBS" off;
the page background is fully transparent). Pick widgets with ?w=speed,gear,inputs,delta,lap,coach,fuel (default: all
but fuel) and a layout with ?pos=bl|br|tl|tr. Self-contained, no CDN, textContent only for server text.
"""

OVERLAY = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>TRACE overlay</title>
<style>
html,body{margin:0;background:transparent;overflow:hidden;font-family:Sora,"Segoe UI",system-ui,sans-serif;color:#f2f0ec}
#o{position:absolute;display:flex;gap:14px;align-items:flex-end;padding:24px}
.bl{left:0;bottom:0}.br{right:0;bottom:0}.tl{left:0;top:0}.tr{right:0;top:0}
.card{background:rgba(8,8,10,.72);border:1px solid rgba(255,255,255,.14);border-radius:8px;padding:10px 16px;min-width:96px;
text-shadow:0 1px 2px #000}
.big{font:700 52px/1 "JetBrains Mono",ui-monospace,monospace}.sm{font:600 12px/1 ui-monospace,monospace;color:#96949c;
letter-spacing:.12em;text-transform:uppercase;margin-top:4px}
.bar{width:14px;height:90px;background:rgba(255,255,255,.1);border-radius:3px;position:relative;overflow:hidden}
.bar i{position:absolute;left:0;right:0;bottom:0;height:0}#bt i{background:#3c9a5f}#bb i{background:#c8102e}
.pair{display:flex;gap:6px}.ahead{color:#3c9a5f}.behind{color:#e2455a}.msg{font:600 20px Sora,system-ui,sans-serif;
max-width:420px}.hide{display:none}
</style></head><body>
<div id="o" class="bl">
<div class="card w-speed"><div class="big" id="spd">0</div><div class="sm">km/h</div></div>
<div class="card w-gear"><div class="big" id="gear">N</div><div class="sm">gear</div></div>
<div class="card w-inputs pair"><div class="bar" id="bt"><i></i></div><div class="bar" id="bb"><i></i></div></div>
<div class="card w-delta"><div class="big" id="delta">--</div><div class="sm" id="dl">vs best</div></div>
<div class="card w-lap"><div class="big" id="lap" style="font-size:32px">--</div><div class="sm" id="lapl">lap</div></div>
<div class="card w-fuel"><div class="big" id="fuel" style="font-size:32px">--</div><div class="sm">laps of fuel</div></div>
<div class="card w-coach"><div class="msg" id="coach"></div><div class="sm">coach</div></div>
</div>
<script>
var q=new URLSearchParams(location.search),want=(q.get("w")||"speed,gear,inputs,delta,lap,coach").split(","),
pos=q.get("pos");if(["bl","br","tl","tr"].indexOf(pos)>=0)document.getElementById("o").className=pos;
document.querySelectorAll(".card").forEach(function(c){var k=c.className.match(/w-(\w+)/)[1];
if(want.indexOf(k)<0)c.classList.add("hide")});
function $(i){return document.getElementById(i)}
function ft(ms){if(!ms||ms<=0)return"--";var m=Math.floor(ms/60000),s=(ms%60000)/1000;return m+":"+(s<10?"0":"")+s.toFixed(3)}
var backoff=1000;
function connect(){var ws;try{ws=new WebSocket((location.protocol==="https:"?"wss://":"ws://")+location.host+"/ws")}
catch(e){setTimeout(connect,backoff);return}
ws.onopen=function(){backoff=1000};
ws.onmessage=function(ev){var m;try{m=JSON.parse(ev.data)}catch(e){return}
if(m.type!=="f")return;
$("spd").textContent=Math.round(m.spd);$("gear").textContent=m.gear<=0?(m.gear<0?"R":"N"):m.gear;
$("bt").firstChild.style.height=(m.thr*100)+"%";$("bb").firstChild.style.height=(m.brk*100)+"%";
var d=$("delta");if(m.delta==null){d.textContent="--";d.className="big"}else{d.textContent=(m.delta>0?"+":"")+m.delta.toFixed(2);
d.className="big "+(m.delta>0?"behind":"ahead")}
$("lapl").textContent="lap "+m.lap+(m.laps?"/"+m.laps:"");$("lap").textContent=ft(m.cur||m.last);
$("fuel").textContent=m.fl==null?"--":m.fl.toFixed(1);$("coach").textContent=m.coach||""};
ws.onclose=function(){setTimeout(connect,backoff);backoff=Math.min(backoff*2,8000)};ws.onerror=function(){ws.close()}}
connect();
</script></body></html>
"""
