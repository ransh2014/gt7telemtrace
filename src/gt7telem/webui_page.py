"""webui_page.py -- the single self-contained page served by webui_server.

Everything (HTML, CSS, JS) lives in this one string so it ships inside the
wheel and the PyInstaller binary with no package-data or spec changes. No CDN,
no external fonts or scripts: it works on a LAN with no internet.

All server-supplied text is inserted with textContent, never innerHTML.
"""

PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<meta name="color-scheme" content="dark light">
<title>TRACE Live</title>
<style>
/*@FONTS@*/
/* Same design system as the TRACE website (gt7trace.netlify.app): obsidian, graphite, one team red,
   petrol teal for data, JetBrains Mono / Sora / Anton / Manrope. The site loads those from Google Fonts;
   this page must work with no internet, so it names them first and falls back to system fonts. */
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#08080a;--bg-1:#0e0e11;--bg-2:#17171b;--bg-3:#202024;--cbg:#050506;
--line:rgba(255,255,255,.08);--line-2:rgba(255,255,255,.16);
--orange:#c8102e;--orange-dim:#8c0c22;--orange-glow:rgba(200,16,46,.22);--orange-2:#e2455a;
--blue:#1c5c58;--blue-2:#5fb3a8;--green:#3c9a5f;--gold:#c9a24b;
--txt:#f2f0ec;--mid:#96949c;--dim:#605e68;--radius:6px;--radius-sm:4px;--ease:cubic-bezier(.16,1,.3,1);
--shadow-sm:0 4px 16px rgba(0,0,0,.34);--shadow-md:0 20px 48px -16px rgba(0,0,0,.6);
--font-impact:Anton,Impact,"Arial Narrow Bold",Haettenschweiler,sans-serif;
--font-display:Sora,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
--font-body:Manrope,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
--font-mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Consolas,Menlo,monospace;
/* aliases used by the gauge code */
--fg:var(--txt);--panel:var(--bg-2);--line2:var(--line-2);--c:var(--blue-2);--p:var(--gold);--k:var(--orange-2);
--good:var(--green);--warn:var(--gold);--bad:var(--orange-2);--cold:var(--blue-2);--mono:var(--font-mono);--impact:var(--font-impact)}
:root[data-theme=light]{--bg:#f4f2ee;--bg-1:#ebe8e2;--bg-2:#fff;--bg-3:#e2dfd8;--cbg:#fff;--line:rgba(0,0,0,.1);--line-2:rgba(0,0,0,.2);
--txt:#16161a;--mid:#55535c;--dim:#7a7880;--blue-2:#1c5c58;--green:#23743f;--gold:#8a6a1c;--orange-2:#c8102e}
html{scroll-behavior:smooth;scrollbar-gutter:stable;scrollbar-width:thin;scrollbar-color:var(--dim) var(--bg-1);background:var(--bg)}
::-webkit-scrollbar{width:10px;height:10px}::-webkit-scrollbar-track{background:var(--bg-1)}
::-webkit-scrollbar-thumb{background:var(--bg-3);border-radius:6px;border:2px solid var(--bg-1)}::-webkit-scrollbar-thumb:hover{background:var(--dim)}
body{background:var(--bg);color:var(--txt);font-family:var(--font-body);line-height:1.65;overflow-x:hidden;position:relative;min-height:100vh;min-height:100dvh;
 padding-top:calc(62px + env(safe-area-inset-top));-webkit-text-size-adjust:100%}
body::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
 background-image:linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px);
 background-size:64px 64px;-webkit-mask-image:radial-gradient(ellipse 80% 60% at 50% 0%,#000 0%,transparent 75%);mask-image:radial-gradient(ellipse 80% 60% at 50% 0%,#000 0%,transparent 75%)}
:root[data-theme=light] body::before{background-image:linear-gradient(rgba(0,0,0,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(0,0,0,.04) 1px,transparent 1px)}
:focus-visible{outline:2px solid var(--orange);outline-offset:2px}::selection{background:var(--orange);color:#100b06}
nav,.page-hdr,main,footer{position:relative;z-index:1}
/* ── NAV (identical pattern to the site) ── */
nav.top{position:fixed;top:0;left:0;right:0;z-index:100;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px max(24px,env(safe-area-inset-right)) 12px max(24px,env(safe-area-inset-left));
 padding-top:max(12px,env(safe-area-inset-top));background:rgba(8,8,10,.9);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
:root[data-theme=light] nav.top{background:rgba(244,242,238,.92)}
.logo{font-family:var(--font-display);font-weight:600;font-size:1.1rem;letter-spacing:.12em;text-transform:uppercase;text-decoration:none;color:var(--txt);white-space:nowrap}
.logo span{color:var(--orange)}
.nav-links{display:flex;align-items:center;gap:2px;flex-wrap:nowrap}
.nav-link{background:none;border:0;cursor:pointer;color:var(--mid);font-family:var(--font-display);font-weight:700;font-size:.86rem;letter-spacing:.1em;text-transform:uppercase;padding:9px 12px;position:relative;transition:color .2s ease;white-space:nowrap;min-height:40px}
.nav-link::after{content:"";position:absolute;left:12px;right:12px;bottom:3px;height:2px;background:var(--txt);transform:scaleX(0);transform-origin:left;transition:transform .25s var(--ease)}
.nav-link:hover{color:var(--txt)}.nav-link:hover::after{transform:scaleX(1)}
.nav-link.on{color:var(--txt)}.nav-link.on::after{transform:scaleX(1);background:var(--orange)}
.nav-link[aria-pressed=true]{color:var(--blue-2)}
.nav-sep{width:1px;height:22px;background:var(--line-2);margin:0 8px}
.nav-btn{font-family:var(--font-display);font-weight:700;font-size:.86rem;letter-spacing:.1em;text-transform:uppercase;cursor:pointer;border:0;padding:9px 18px;margin-left:4px;white-space:nowrap;flex-shrink:0;min-height:40px;
 transition:transform .25s var(--ease),box-shadow .25s var(--ease),filter .2s ease}
.nav-btn.orange{background:linear-gradient(180deg,var(--orange-2) 0%,var(--orange) 55%,var(--orange-dim) 100%);color:#fbf3ef;border-radius:0;
 clip-path:polygon(0 0,100% 0,100% 100%,9px 100%,0 calc(100% - 9px));box-shadow:inset 0 1px 0 rgba(255,255,255,.22),inset 0 -2px 3px rgba(0,0,0,.28),0 1px 0 rgba(0,0,0,.4)}
.nav-btn:hover{transform:translateY(-2px);filter:brightness(1.08)}.nav-btn:active{transform:translateY(1px);filter:brightness(.95)}
/* ── PAGE HEADER ── */
.page-hdr{padding:34px max(24px,env(safe-area-inset-right)) 22px max(24px,env(safe-area-inset-left));max-width:1500px;margin:0 auto}
.eyebrow{display:flex;align-items:center;gap:9px;font-family:var(--font-mono);font-size:.68rem;letter-spacing:.28em;text-transform:uppercase;color:var(--blue-2)}
.eyebrow::before{content:"";width:15px;height:8px;flex-shrink:0;background:currentColor;clip-path:polygon(0 0,65% 0,100% 50%,65% 100%,0 100%,35% 50%)}
.page-hdr h1{font-family:var(--font-impact);font-weight:400;font-size:clamp(2.2rem,5vw,3.8rem);text-transform:uppercase;line-height:1.05;margin:14px 0 8px;letter-spacing:.002em}
.page-hdr h1 em{color:var(--orange);font-style:normal}.page-hdr h1 span{color:var(--blue-2)}
.stline{display:flex;align-items:center;gap:14px;flex-wrap:wrap;font-family:var(--font-mono);font-size:.74rem;letter-spacing:.14em;text-transform:uppercase;color:var(--mid)}
#status{display:inline-flex;align-items:center;gap:8px;color:var(--txt)}
#dot{width:9px;height:9px;border-radius:50%;background:var(--dim)}
#dot.live{background:var(--green);box-shadow:0 0 0 3px rgba(60,154,95,.22)}#dot.wait{background:var(--gold)}#dot.off{background:var(--orange-2)}
#ident{color:var(--mid);overflow-wrap:anywhere}
main{min-height:60vh}
.page{display:none;padding:6px max(24px,env(safe-area-inset-right)) 28px max(24px,env(safe-area-inset-left));gap:14px;max-width:1500px;margin:0 auto}
.page.on{display:grid}
.card{background:linear-gradient(180deg,rgba(255,255,255,.025),rgba(255,255,255,0)),var(--bg-2);border:1px solid var(--line);border-radius:var(--radius);padding:18px;min-width:0;display:flex;flex-direction:column;position:relative;overflow:hidden;box-shadow:var(--shadow-sm)}
.card h3{display:flex;align-items:center;gap:9px;font-family:var(--font-mono);font-weight:500;font-size:.66rem;letter-spacing:.28em;text-transform:uppercase;color:var(--blue-2);margin-bottom:12px}
.card h3::before{content:"";width:14px;height:8px;flex-shrink:0;background:currentColor;clip-path:polygon(0 0,65% 0,100% 50%,65% 100%,0 100%,35% 50%)}
.card svg{width:100%;height:auto;aspect-ratio:1;max-height:420px;display:block;margin:0 auto}
.card canvas{width:100%;display:block;height:260px}
.big{font:400 clamp(34px,7vw,72px)/1 var(--font-impact);font-variant-numeric:tabular-nums;letter-spacing:.02em}
.mid{font:700 clamp(18px,3.4vw,30px)/1.15 var(--font-mono);font-variant-numeric:tabular-nums}
.dim{color:var(--mid)}
.row{display:flex;justify-content:space-between;align-items:baseline;gap:8px;font:500 .82rem var(--font-mono);padding:5px 0;border-bottom:1px solid var(--line)}
.row:last-child{border-bottom:0}.row b{font-weight:700}
/* mobile / tablet bottom tab bar */
nav.tabs{display:none;position:fixed;left:0;right:0;bottom:0;z-index:100;background:rgba(8,8,10,.94);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);border-top:1px solid var(--line);padding-bottom:env(safe-area-inset-bottom)}
:root[data-theme=light] nav.tabs{background:rgba(244,242,238,.96)}
nav.tabs button{flex:1;background:none;border:0;color:var(--mid);padding:15px 2px;font-family:var(--font-display);font-weight:700;font-size:.74rem;letter-spacing:.08em;text-transform:uppercase;cursor:pointer;border-top:3px solid transparent;margin-top:-1px}
nav.tabs button.on{color:var(--txt);border-top-color:var(--orange)}
#ov{position:fixed;left:0;right:0;top:62px;bottom:0;display:none;align-items:center;justify-content:center;text-align:center;background:color-mix(in srgb,var(--bg) 82%,transparent);z-index:40;
 font:500 .9rem var(--font-mono);letter-spacing:.16em;text-transform:uppercase;padding:20px;color:var(--txt);pointer-events:none;white-space:pre-line}
#ov.on{display:flex}
footer{text-align:center;padding:32px 20px;font-family:var(--font-mono);font-size:.58rem;letter-spacing:.12em;text-transform:uppercase;color:var(--dim);border-top:1px solid var(--line);line-height:1.9}
footer a{color:var(--dim);text-decoration:none}footer a:hover{color:var(--mid)}
/* HUD */
#p0{grid-template-columns:1fr 1fr 1fr}
#p0 .mid3{align-items:center;justify-content:center;text-align:center}
#p0 .gearbig{font:400 clamp(110px,20vw,280px)/1 var(--font-impact);color:var(--txt);text-shadow:0 0 40px var(--orange-glow)}
#p0 .strip{grid-column:1/-1;display:flex;gap:14px;flex-direction:row;justify-content:space-around;align-items:center;flex-wrap:wrap}
#p1{grid-template-columns:2fr 1fr}#p1 .wide{grid-column:1/-1}#p1 .wide canvas,#p1 .gf canvas{height:300px}
#p2{grid-template-columns:1fr 1fr}
.tyres{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.tyre{border:2px solid var(--line);border-radius:var(--radius);padding:14px 8px;display:flex;flex-direction:column;justify-content:center;gap:3px;text-align:center;background:var(--bg-1)}
.tyre .t{font:400 clamp(30px,5vw,54px)/1 var(--font-impact);letter-spacing:.02em}.tyre .s{font:500 .72rem var(--font-mono);color:var(--mid)}.tyre .l{font:700 .72rem var(--font-mono);letter-spacing:.16em}
.bar{height:14px;border-radius:7px;background:var(--bg-3);overflow:hidden;position:relative;margin:6px 0}
.bar i{display:block;height:100%;width:0;background:var(--blue-2);border-radius:7px}
#p3{grid-template-columns:1fr 1fr;grid-template-rows:auto 1fr}#p3 .mapc{grid-row:1/3;grid-column:2}#p3 .mapc canvas{height:460px}
.dbar{height:24px;border-radius:12px;background:var(--bg-3);position:relative;overflow:hidden;margin:8px 0}
.dbar i{position:absolute;top:0;bottom:0;background:var(--green)}.dbar u{position:absolute;left:50%;top:0;bottom:0;width:2px;background:var(--txt);opacity:.6}
#p4{grid-template-columns:repeat(4,1fr)}
@media (max-width:1200px){.nav-link{padding:9px 8px;font-size:.8rem}.nav-btn{padding:9px 12px}#p4{grid-template-columns:repeat(2,1fr)}}
@media (max-width:980px){
 nav.top .nav-links .pg,nav.top .nav-sep{display:none}
 nav.tabs{display:flex}body{padding-bottom:calc(58px + env(safe-area-inset-bottom))}
 #ov{bottom:58px}
}
@media (max-width:820px),(orientation:portrait){
 nav.top{padding-left:max(14px,env(safe-area-inset-left));padding-right:max(14px,env(safe-area-inset-right))}
 .page-hdr{padding:22px max(14px,env(safe-area-inset-right)) 14px max(14px,env(safe-area-inset-left))}
 .page{gap:10px;padding:4px max(10px,env(safe-area-inset-right)) 22px max(10px,env(safe-area-inset-left))}
 #p0{grid-template-columns:1fr 1fr}#p0 .mid3{grid-column:1/-1;grid-row:1;padding:6px}#p0 .gearbig{font-size:clamp(100px,38vw,200px)}
 #p1,#p2,#p3{grid-template-columns:1fr}#p3 .mapc{grid-row:auto;grid-column:auto}#p3 .mapc canvas{height:300px}
 #p4{grid-template-columns:1fr}.tyre .t{font-size:30px}.card{padding:14px}
}
@media (max-width:560px){#p0{grid-template-columns:1fr}.nav-link{padding:8px 6px;font-size:.72rem}.nav-btn{padding:8px 10px;font-size:.74rem}.logo{font-size:.95rem}
 .card canvas,#p1 .wide canvas,#p1 .gf canvas{height:220px}nav.tabs button{font-size:.66rem;letter-spacing:.02em}}
@media (max-height:480px) and (orientation:landscape){#p0{grid-template-columns:1fr 1fr 1fr}#p0 .gearbig{font-size:110px}nav.tabs button{padding:9px 2px}}
@media (min-width:1700px){html{font-size:112%}}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important;scroll-behavior:auto!important}}
/* ===== SITE-PARITY LAYER: hairline tile grid, numbered cards, hero type, motion (mirrors index.html / styles.css) ===== */
body{animation:page-fade-in .4s ease forwards;opacity:0}
@keyframes page-fade-in{to{opacity:1}}
@keyframes line-up{to{opacity:1;transform:translateY(0)}}
@keyframes live-pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.35;transform:scale(.8)}}
@keyframes tile-in{from{opacity:0;transform:translateY(22px)}to{opacity:1;transform:translateY(0)}}
/* hero-style page header */
.page-hdr{position:relative;overflow:hidden;padding-top:40px;padding-bottom:26px}
.page-hdr::before{content:"";position:absolute;inset:0;z-index:-1;pointer-events:none;
 background:radial-gradient(ellipse 60% 50% at 50% 0%,rgba(255,255,255,.05) 0%,transparent 60%),radial-gradient(circle 520px at var(--mx,50%) var(--my,20%),rgba(255,255,255,.035),transparent 72%)}
.hero-num{position:absolute;top:-6%;right:-1%;z-index:-1;font-family:var(--font-impact);font-weight:400;font-size:clamp(9rem,26vw,22rem);line-height:1;color:transparent;
 -webkit-text-stroke:1px rgba(255,255,255,.06);pointer-events:none;user-select:none}
:root[data-theme=light] .hero-num{-webkit-text-stroke-color:rgba(0,0,0,.08)}
.page-hdr h1{font-size:clamp(2.6rem,6.4vw,5rem);line-height:1.02;margin:16px 0 14px -.03em;letter-spacing:.005em}
.page-hdr h1 .hl{display:inline-block;margin-right:.28em;color:var(--txt);opacity:0;transform:translateY(28px);animation:line-up .8s var(--ease) forwards}.page-hdr h1 .hl em{color:var(--orange)}
.page-hdr h1 .hl:nth-child(2){animation-delay:.12s}
.eyebrow#status{color:var(--green);font-size:.62rem;letter-spacing:.2em}
.eyebrow#status::before{display:none}
#dot{width:6px;height:6px}
#dot.live{animation:live-pulse 1.8s ease-in-out infinite;box-shadow:none}
.eyebrow#status #dot.wait~#stxt,.eyebrow#status #dot.off~#stxt{color:var(--mid)}
.stline{max-width:780px}
/* hairline tile grid: the site's .cards pattern */
.page{gap:1px;background:var(--line);border:1px solid var(--line);border-radius:10px;overflow:hidden;padding:0;
 margin:6px max(24px,env(safe-area-inset-left)) 0;max-width:calc(1500px - 48px)}
@media (min-width:1548px){.page{margin-left:auto;margin-right:auto}}
.page{counter-reset:tile}
.card{background:var(--bg);border:0;border-radius:0;box-shadow:none;padding:30px 28px;transition:background .3s var(--ease),transform .35s var(--ease);
 animation:tile-in .6s var(--ease) both}
.card:nth-child(2){animation-delay:.05s}.card:nth-child(3){animation-delay:.1s}.card:nth-child(4){animation-delay:.15s}
.card:nth-child(5){animation-delay:.2s}.card:nth-child(n+6){animation-delay:.25s}
.card::before{content:"";position:absolute;left:0;top:0;bottom:0;width:2px;background:var(--orange);transform:scaleY(0);transform-origin:top;transition:transform .4s var(--ease);z-index:2}
.card:hover{background:var(--bg-1)}.card:hover::before{transform:scaleY(1)}
.card h3{font-family:var(--font-display);font-weight:700;font-size:1.02rem;letter-spacing:.06em;text-transform:uppercase;color:var(--txt);margin-bottom:14px;align-items:baseline;gap:12px}
.card h3::before{content:counter(tile,decimal-leading-zero);counter-increment:tile;width:auto;height:auto;clip-path:none;background:none;
 font-family:var(--font-impact);font-weight:400;font-size:2.2rem;line-height:1;letter-spacing:-.01em;color:rgba(255,255,255,.12);transition:color .35s ease,transform .35s var(--ease)}
:root[data-theme=light] .card h3::before{color:rgba(0,0,0,.14)}
.card:hover h3::before{color:rgba(200,16,46,.45);transform:translateX(2px)}
.card.strip h3,.card:not(:has(h3))::after{display:none}
/* stat tiles: Anton numerals + mono micro-labels, like the site's live-stats strip */
.strip{background:var(--bg-1)}
.strip .mid{font-family:var(--font-impact);font-weight:400;letter-spacing:.02em;font-size:clamp(1.6rem,3.6vw,2.6rem)}
.strip .dim{font:500 .6rem var(--font-mono)!important;letter-spacing:.1em;text-transform:uppercase;color:var(--dim)}
.mid,.row b.mid,.big{font-family:var(--font-impact);font-weight:400;letter-spacing:.02em}
.mid{font-size:clamp(1.5rem,3vw,2.3rem)}
.row{font-size:.8rem;letter-spacing:.02em}.row .dim,.row span.dim{font-size:.66rem;letter-spacing:.1em;text-transform:uppercase}
.nav-link,.nav-btn{font-family:var(--font-display)}
/* back to top */
.back-to-top{position:fixed;right:24px;bottom:24px;z-index:90;width:46px;height:46px;border-radius:50%;background:rgba(13,16,23,.82);border:1px solid var(--line-2);
 backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);color:var(--txt);display:flex;align-items:center;justify-content:center;cursor:pointer;opacity:0;pointer-events:none;
 transform:translateY(10px);transition:opacity .25s ease,transform .25s ease,border-color .2s}
.back-to-top.show{opacity:1;pointer-events:auto;transform:none}.back-to-top:hover{border-color:var(--txt)}
.back-to-top svg{width:20px;height:20px}
@media (max-width:980px){.back-to-top{bottom:calc(70px + env(safe-area-inset-bottom));right:14px}}
@media (max-width:820px),(orientation:portrait){
 .page{margin:4px max(10px,env(safe-area-inset-left)) 0}
 .card{padding:22px 18px}.page-hdr h1{font-size:clamp(2.6rem,13vw,4.4rem)}.hero-num{font-size:9rem;top:0;right:-4%}
}
@media (prefers-reduced-motion:reduce){body{opacity:1;animation:none}.page-hdr h1 .hl{opacity:1;transform:none;animation:none}.card{animation:none}.back-to-top{transition:none}}
</style></head><body>
<nav class="top">
 <span class="logo">TRACE <span>GT7</span></span>
 <div class="nav-links">
  <span id="pgtop" style="display:contents"></span>
  <span class="nav-sep"></span>
  <button class="nav-link" id="bu" aria-pressed="false" title="Speed unit">KM/H</button>
  <button class="nav-link" id="bt" title="Theme">Theme</button>
  <button class="nav-link" id="bw" aria-pressed="false" title="Keep screen awake">Awake</button>
  <button class="nav-btn orange" id="bf" title="Fullscreen">Full</button>
 </div>
</nav>
<div class="page-hdr" id="main-content">
 <div class="eyebrow" id="status"><span id="dot"></span><span id="stxt">CONNECTING</span></div>
 <div class="hero-num" id="hnum" aria-hidden="true">N</div>
 <h1><span class="hl">Live</span><span class="hl"><em>Telemetry</em></span></h1>
 <div class="stline"><span id="ident"></span></div>
</div>
<main id="main">
 <section class="page" id="p0"></section><section class="page" id="p1"></section>
 <section class="page" id="p2"></section><section class="page" id="p3"></section>
 <section class="page" id="p4"></section>
 <div id="ov"></div>
</main>
<footer>
 TRACE &mdash; community tool, not affiliated with Polyphony Digital or Sony Interactive Entertainment<br>
 Read-only live view served by the TRACE Live Dashboard on your local network &middot; nothing leaves your LAN
</footer>
<nav class="tabs" id="nav"></nav>
<script>
"use strict";
var NS="http://www.w3.org/2000/svg";
var PAGES=["HUD","INPUTS","TYRES","TIMING","FULL"];
var S={ok:false,spd:0,rpm:0,maxrpm:8000,warnrpm:0,gear:0,sug:0,thr:0,brk:0,str:0,latg:0,long:0,tt:[0,0,0,0],ts:[1,1,1,1],
 fuel:0,cap:0,ev:false,lap:0,laps:0,cur:null,last:null,best:null,delta:null,top:null,fl:null,fpl:null,pit:null,spare:null,stop:null,
 stint:null,loss:null,trend:null,tyre:null,car:"",track:"",pos:0,x:0,z:0,hdg:0,tcs:false,asm:false,lim:false,hist:[]};
var V={spd:0,rpm:0,thr:0,brk:0,str:0,latg:0,long:0,x:0,z:0};   /* eased display values */
var TRACK=[],units="kmh",page=0,conn="connecting",lastFrameAt=0,peakSpd=0;
var reduce=window.matchMedia&&matchMedia("(prefers-reduced-motion: reduce)").matches;
function $(id){return document.getElementById(id)}
function lsGet(k,d){try{var v=localStorage.getItem(k);return v===null?d:v}catch(e){return d}}
function lsSet(k,v){try{localStorage.setItem(k,v)}catch(e){}}
function el(tag,attrs,parent,ns){var e=ns?document.createElementNS(NS,tag):document.createElement(tag);
 if(attrs)for(var k in attrs){if(k==="text")e.textContent=attrs[k];else e.setAttribute(k,attrs[k])}
 if(parent)parent.appendChild(e);return e}
function ft(ms){if(ms==null||ms<=0)return"--:--.---";var m=Math.floor(ms/60000),s=(ms%60000)/1000;return m+":"+(s<10?"0":"")+s.toFixed(3)}
function fs(v,n){return v==null?"--":(v>0?"+":"")+v.toFixed(n==null?2:n)+"s"}
function conv(k){return units==="mph"?k*0.621371:k}
function css(n){return getComputedStyle(document.documentElement).getPropertyValue(n).trim()}

/* ---------- widgets ---------- */
var WIDGETS=[];   /* {page:n, up:function(dt)} */
function reg(p,fn){WIDGETS.push({page:p,up:fn})}
function card(parent,title,cls){var c=el("div",{"class":"card "+(cls||"")},parent);if(title)el("h3",{text:title},c);return c}
function pt(cx,cy,a,r){return[cx+r*Math.cos(a),cy+r*Math.sin(a)]}
var A0=135*Math.PI/180,SW=270*Math.PI/180;
function arcPath(cx,cy,r,f0,f1){var a=pt(cx,cy,A0+SW*f0,r),b=pt(cx,cy,A0+SW*f1,r);
 return"M"+a[0].toFixed(1)+" "+a[1].toFixed(1)+"A"+r+" "+r+" 0 "+((f1-f0)*SW>Math.PI?1:0)+" 1 "+b[0].toFixed(1)+" "+b[1].toFixed(1)}

/* round gauge; returns {svg,draw(max,step,labelEvery,redFrom),set(frac,text,sub)} */
function gauge(parent,opt){
 var svg=el("svg",{viewBox:"0 0 200 200",preserveAspectRatio:"xMidYMid meet",role:"img","aria-label":opt.label},parent,1);
 var g=el("g",null,svg,1);
 el("path",{d:arcPath(100,100,86,0,1),fill:"none",stroke:"var(--line)","stroke-width":10,"stroke-linecap":"round"},svg,1);
 var val=el("path",{fill:"none",stroke:"var(--c)","stroke-width":10,"stroke-linecap":"round"},svg,1);
 var red=el("path",{fill:"none",stroke:"var(--bad)","stroke-width":10},svg,1);
 var needle=el("line",{x1:100,y1:100,x2:100,y2:30,stroke:"var(--k)","stroke-width":3,"stroke-linecap":"round"},svg,1);
 el("circle",{cx:100,cy:100,r:6,fill:"var(--k)"},svg,1);
 var big=el("text",{x:100,y:opt.bigY||132,"text-anchor":"middle","font-size":opt.bigSize||34,"font-weight":800,"font-family":"var(--mono)",fill:"var(--fg)"},svg,1);
 var sub=el("text",{x:100,y:152,"text-anchor":"middle","font-size":11,"font-family":"var(--mono)",fill:"var(--dim)"},svg,1);
 var cache="";
 function draw(max,step,every,redFrom){
  var key=[max,step,every,redFrom].join();if(key===cache)return;cache=key;g.textContent="";
  var n=Math.round(max/step);
  for(var i=0;i<=n*2;i++){var f=i/(n*2),major=i%2===0,a=A0+SW*f,p1=pt(100,100,a,major?70:76),p2=pt(100,100,a,84);
   el("line",{x1:p1[0],y1:p1[1],x2:p2[0],y2:p2[1],stroke:"var(--dim)","stroke-width":major?2:1},g,1);
   if(major&&((i/2)%every===0)){var tp=pt(100,100,a,58);
    el("text",{x:tp[0],y:tp[1]+3,"text-anchor":"middle","font-size":9,"font-family":"var(--mono)",fill:"var(--dim)",text:String(Math.round(i/2*step*(opt.labelMul||1)*10)/10)},g,1)}}
  red.setAttribute("d",redFrom!=null&&redFrom<1?arcPath(100,100,86,redFrom,1):"");
 }
 return{svg:svg,draw:draw,set:function(f,t,s){f=Math.max(0,Math.min(1,f));
  needle.setAttribute("transform","rotate("+((A0+SW*f)*180/Math.PI-270)+" 100 100)");
  val.setAttribute("d",f>0.002?arcPath(100,100,86,0,f):"");big.textContent=t;sub.textContent=s||""}};
}
function speedo(parent,pg){
 var c=card(parent,"SPEED","spd"),gg=gauge(c,{label:"Speedometer",bigSize:38});
 reg(pg,function(){var sp=conv(V.spd);if(sp>peakSpd)peakSpd=sp;
  var step=units==="mph"?20:20,max=Math.max(units==="mph"?160:260,Math.ceil(peakSpd*1.04/step)*step);
  gg.draw(max,step,units==="mph"?1:2);gg.set(sp/max,Math.round(sp),units==="mph"?"MPH":"KM/H")});
}
function tacho(parent,pg){
 var c=card(parent,"ENGINE","rpmc"),gg=gauge(c,{label:"Tachometer",bigSize:30});
 var leds=el("div",{style:"display:flex;gap:4px;justify-content:center;margin-top:6px"},c);
 var L=[];for(var i=0;i<10;i++)L.push(el("span",{style:"width:9%;max-width:26px;aspect-ratio:1;border-radius:50%;background:var(--line);display:block"},leds));
 var t0=0;
 reg(pg,function(){var mx=S.maxrpm>1000?S.maxrpm:8000,top=Math.ceil(mx/1000)*1000;
  gg.draw(top/1000,1,top>10000?2:1,Math.max(0,(S.warnrpm>1000?S.warnrpm:mx*0.93)/top));
  gg.set(V.rpm/top,Math.round(V.rpm),"RPM x1");
  var f=V.rpm/mx,on=S.lim||f>=0.985,blink=!reduce&&on&&(Math.floor(performance.now()/110)%2);
  for(var i=0;i<10;i++){var lit=f>=0.8+i*0.02||on;L[i].style.background=!lit||blink?"var(--line)":(i<4?"var(--good)":i<7?"var(--warn)":"var(--bad)")}});
}
function gearBox(parent,pg){
 var c=card(parent,"GEAR","mid3"),gb=el("div",{"class":"gearbig"},c),sg=el("div",{"class":"mid dim"},c),fl=el("div",{"class":"mid"},c);
 reg(pg,function(){gb.textContent=S.gear<0?"R":S.gear===0?"N":S.gear;
  sg.textContent=S.sug>0&&S.sug!==S.gear?"SHIFT TO "+S.sug:"";
  fl.textContent=(S.tcs?"TCS ":"")+(S.asm?"ASM ":"")+(S.lim?"LIMITER":"")});
}
function lapStrip(parent,pg){
 var c=card(parent,"","strip"),items={};
 [["LAP","lap"],["CURRENT","cur"],["DELTA","delta"],["LAST","last"],["BEST","best"]].forEach(function(a){
  var d=el("div",{style:"text-align:center"},c);el("div",{"class":"dim",style:"font:11px var(--mono)",text:a[0]},d);items[a[1]]=el("div",{"class":"mid"},d)});
 reg(pg,function(){items.lap.textContent=S.lap>0?S.lap+(S.laps>0?"/"+S.laps:""):"--";items.cur.textContent=ft(S.cur);
  items.delta.textContent=fs(S.delta);items.delta.style.color=S.delta==null?"":S.delta>0?"var(--bad)":"var(--good)";
  items.last.textContent=ft(S.last);items.best.textContent=ft(S.best)});
}
function bars(parent,pg){
 var c=card(parent,"INPUTS"),b={};
 [["THROTTLE","thr","var(--good)"],["BRAKE","brk","var(--bad)"],["STEERING","str","var(--c)"]].forEach(function(a){
  var r=el("div",{"class":"row"},c);el("span",{text:a[0]},r);b[a[1]+"v"]=el("b",{text:"0%"},r);
  var bar=el("div",{"class":"bar"},c);b[a[1]]=el("i",{style:"background:"+a[2]},bar);if(a[1]==="str")b.sbar=bar});
 reg(pg,function(){b.thr.style.width=V.thr*100+"%";b.thrv.textContent=Math.round(V.thr*100)+"%";
  b.brk.style.width=V.brk*100+"%";b.brkv.textContent=Math.round(V.brk*100)+"%";
  var s=Math.max(-1,Math.min(1,V.str));b.str.style.width=Math.abs(s)*50+"%";b.str.style.marginLeft=(s<0?50+s*50:50)+"%";
  b.strv.textContent=(s<0?"L ":s>0?"R ":"")+Math.round(Math.abs(s)*100)+"%"});
}
function canvasIn(parent){var cv=el("canvas",null,parent);
 function fit(){var d=window.devicePixelRatio||1,w=cv.clientWidth,h=cv.clientHeight;
  if(cv.width!==Math.round(w*d)||cv.height!==Math.round(h*d)){cv.width=Math.round(w*d);cv.height=Math.round(h*d)}return[cv.getContext("2d"),cv.width,cv.height,d]}
 return{cv:cv,fit:fit}}
var HIST=[];  /* input trace ring */
function trace(parent,pg,cls){
 var c=card(parent,"TRACE (last 20 s)",cls),cn=canvasIn(c);
 reg(pg,function(){var r=cn.fit(),x=r[0],w=r[1],h=r[2],d=r[3];x.clearRect(0,0,w,h);
  x.strokeStyle=css("--line");x.lineWidth=d;x.beginPath();for(var i=1;i<4;i++){x.moveTo(0,h*i/4);x.lineTo(w,h*i/4)}x.stroke();
  var now=performance.now();
  [["thr","--good",0],["brk","--bad",0],["str","--c",1]].forEach(function(s){x.strokeStyle=css(s[1]);x.lineWidth=2*d;x.beginPath();
   var first=true;for(var i=0;i<HIST.length;i++){var p=HIST[i],tx=w-(now-p.t)/20000*w;if(tx<0)continue;
    var v=s[2]?0.5-p[s[0]]/2:1-p[s[0]],ty=h*(0.06+0.88*v);if(first){x.moveTo(tx,ty);first=false}else x.lineTo(tx,ty)}x.stroke()})});
}
function gforce(parent,pg){
 var c=card(parent,"G-FORCE","gf"),cn=canvasIn(c),tr=[],rd=el("div",{"class":"row"},c),lt=el("span",null,rd),ln=el("span",null,rd);
 reg(pg,function(){var r=cn.fit(),x=r[0],w=r[1],h=r[2],d=r[3];x.clearRect(0,0,w,h);
  var cx=w/2,cy=h/2,R=Math.min(w,h)/2-6*d,sc=R/2.5;x.strokeStyle=css("--line");x.lineWidth=d;
  [1,2,2.5].forEach(function(g){x.beginPath();x.arc(cx,cy,g*sc,0,7);x.stroke()});
  x.beginPath();x.moveTo(cx-R,cy);x.lineTo(cx+R,cy);x.moveTo(cx,cy-R);x.lineTo(cx,cy+R);x.stroke();
  tr.push([V.latg,V.long]);if(tr.length>40)tr.shift();
  for(var i=0;i<tr.length;i++){x.fillStyle=css("--p");x.globalAlpha=i/tr.length*0.7;x.beginPath();x.arc(cx+tr[i][0]*sc,cy-tr[i][1]*sc,3*d,0,7);x.fill()}
  x.globalAlpha=1;x.fillStyle=css("--k");x.beginPath();x.arc(cx+V.latg*sc,cy-V.long*sc,7*d,0,7);x.fill();
  lt.textContent="LAT "+V.latg.toFixed(2)+" g";ln.textContent=(V.long>=0?"ACCEL ":"BRAKE ")+Math.abs(V.long).toFixed(2)+" g"});
}
function tyreState(t){return t<=0?["--","var(--dim)"]:t<60?["COLD","var(--cold)"]:t<80?["OK","var(--good)"]:t<100?["WARM","var(--warn)"]:["HOT","var(--bad)"]}
function tyres(parent,pg){
 var c=card(parent,"TYRES (temp / slip)"),gr=el("div",{"class":"tyres"},c),T=[];
 ["FL","FR","RL","RR"].forEach(function(n){var b=el("div",{"class":"tyre"},gr);
  el("div",{"class":"s",text:n},b);T.push({b:b,t:el("div",{"class":"t"},b),l:el("div",{"class":"l"},b),s:el("div",{"class":"s"},b)})});
 reg(pg,function(){for(var i=0;i<4;i++){var st=tyreState(S.tt[i]),sl=S.ts[i],bad=sl>0&&(sl<0.95||sl>=1.15);
  T[i].t.textContent=S.tt[i]>0?Math.round(S.tt[i])+"°C":"--";T[i].l.textContent=st[0];T[i].l.style.color=st[1];
  T[i].b.style.borderColor=st[1];T[i].s.textContent="slip "+(sl>0?sl.toFixed(2):"--")+(bad?"  ⚠ SLIDING":"")}});
}
function fuel(parent,pg){
 var c=card(parent,"FUEL & STRATEGY (estimates)"),r1=el("div",{"class":"row"},c),fa=el("b",null,r1),fb=el("span",{"class":"dim"},r1),
  bar=el("div",{"class":"bar"},c),fi=el("i",null,bar),rows={};
 [["Laps of fuel","fl"],["Burn / lap","fpl"],["Finish check","fin"],["Pit by","pit"],["Tyre stint","stint"],["Pace loss","loss"],["Trend","trend"],["Tyre state","tyre"]].forEach(function(a){
  var r=el("div",{"class":"row"},c);el("span",{"class":"dim",text:a[0]},r);rows[a[1]]=el("b",null,r)});
 reg(pg,function(){var pct=S.cap>0?S.fuel/S.cap:0;fi.style.width=Math.max(0,Math.min(1,pct))*100+"%";
  fi.style.background=S.fl!=null&&S.fl<2?"var(--bad)":S.fl!=null&&S.fl<4?"var(--warn)":"var(--c)";
  var u=S.cap===100||S.ev?"%":"";fa.textContent=S.fuel.toFixed(1)+u+(S.cap>0&&S.cap!==100?" / "+S.cap.toFixed(0):"");fb.textContent=Math.round(pct*100)+"%";
  rows.fl.textContent=S.fl==null?"--":S.fl.toFixed(1);rows.fpl.textContent=S.fpl==null?"--":S.fpl.toFixed(2)+u;
  rows.fin.textContent=S.spare==null?"--":S.spare<0?"SHORT "+Math.abs(S.spare).toFixed(1)+" laps":S.stop?"TIGHT":"+"+S.spare.toFixed(1)+" laps";
  rows.pit.textContent=S.pit!=null?"LAP "+S.pit:S.stop===false?"NO STOP":"--";
  rows.stint.textContent=S.stint==null?"--":S.stint+" laps";rows.loss.textContent=S.loss==null?"--":"+"+S.loss.toFixed(2)+"s";
  rows.trend.textContent=S.trend==null?"--":(S.trend>0?"+":"")+S.trend.toFixed(2)+"s/lap";rows.tyre.textContent=S.tyre||"--"});
}
function timing(parent,pg){
 var c=card(parent,"LAP TIMING"),R={};
 [["Current","cur"],["Last","last"],["Best","best"],["vs reference","delta"],["vs TOP lap","top"]].forEach(function(a){
  var r=el("div",{"class":"row"},c);el("span",{"class":"dim",text:a[0]},r);R[a[1]]=el("b",{"class":"mid"},r)});
 var bar=el("div",{"class":"dbar"},c),fill=el("i",null,bar);el("u",null,bar);var word=el("div",{"class":"row dim"},c);
 reg(pg,function(){R.cur.textContent=ft(S.cur);R.last.textContent=ft(S.last);R.best.textContent=ft(S.best);
  R.delta.textContent=fs(S.delta);R.top.textContent=fs(S.top);
  R.delta.style.color=S.delta==null?"":S.delta>0?"var(--bad)":"var(--good)";R.top.style.color=S.top==null?"":S.top>0?"var(--bad)":"var(--good)";
  var d=S.delta==null?0:Math.max(-2,Math.min(2,S.delta))/2;fill.style.left=d<0?50+d*50+"%":"50%";fill.style.width=Math.abs(d)*50+"%";
  fill.style.background=d>0?"var(--bad)":"var(--good)";
  word.textContent=S.delta==null?"no reference lap yet":S.delta>0?"BEHIND reference":"AHEAD of reference"});
}
function hist(parent,pg){
 var c=card(parent,"LAP HISTORY"),ls=el("div",null,c);
 reg(pg,function(){var key=JSON.stringify(S.hist)+S.best;if(ls._k===key)return;ls._k=key;ls.textContent="";
  var h=S.hist.slice().reverse();if(!h.length)el("div",{"class":"dim",text:"No completed laps yet"},ls);
  h.forEach(function(l){var r=el("div",{"class":"row"},ls);el("span",{text:"Lap "+l[0]},r);
   var b=S.best&&l[1]===S.best;el("b",{text:ft(l[1])+(b?"  ★ BEST":""),style:b?"color:var(--p)":""},r)})});
}
function tmap(parent,pg,cls){
 var c=card(parent,"TRACK MAP",cls),cn=canvasIn(c);
 reg(pg,function(){var r=cn.fit(),x=r[0],w=r[1],h=r[2],d=r[3];x.clearRect(0,0,w,h);
  var n=TRACK.length;if(n<2){x.fillStyle=css("--dim");x.font=12*d+"px sans-serif";x.textAlign="center";x.fillText("Drive a lap to draw the map",w/2,h/2);return}
  var x0=1e9,x1=-1e9,z0=1e9,z1=-1e9;TRACK.forEach(function(p){x0=Math.min(x0,p[0]);x1=Math.max(x1,p[0]);z0=Math.min(z0,p[1]);z1=Math.max(z1,p[1])});
  var m=14*d,sc=Math.min((w-2*m)/Math.max(1,x1-x0),(h-2*m)/Math.max(1,z1-z0)),ox=(w-(x1-x0)*sc)/2,oy=(h-(z1-z0)*sc)/2;
  function X(v){return ox+(v-x0)*sc}function Y(v){return h-oy-(v-z0)*sc}
  x.strokeStyle=css("--dim");x.lineWidth=3*d;x.lineJoin="round";x.beginPath();TRACK.forEach(function(p,i){i?x.lineTo(X(p[0]),Y(p[1])):x.moveTo(X(p[0]),Y(p[1]))});x.stroke();
  x.fillStyle=css("--k");x.strokeStyle=css("--bg");x.lineWidth=2*d;x.beginPath();x.arc(X(V.x),Y(V.z),7*d,0,7);x.fill();x.stroke()});
}
function mini(parent,pg){
 var c=card(parent,"CAR"),a=el("div",{"class":"big"},c),b=el("div",{"class":"row"},c);
 reg(pg,function(){a.textContent=S.gear<0?"R":S.gear===0?"N":S.gear;b.textContent=(S.tcs?"TCS ":"")+(S.asm?"ASM ":"")+(S.lim?"LIMITER":"")+(S.sug>0&&S.sug!==S.gear?"  →"+S.sug:"")});
}

function build(){
 var p0=$("p0"),p1=$("p1"),p2=$("p2"),p3=$("p3"),p4=$("p4");
 speedo(p0,0);gearBox(p0,0);tacho(p0,0);lapStrip(p0,0);
 bars(p1,1);gforce(p1,1);trace(p1,1,"wide");
 tyres(p2,2);fuel(p2,2);
 timing(p3,3);tmap(p3,3,"mapc");hist(p3,3);
 speedo(p4,4);tacho(p4,4);bars(p4,4);gforce(p4,4);tyres(p4,4);fuel(p4,4);timing(p4,4);tmap(p4,4);
 var nv=$("nav"),tp=$("pgtop");PAGES.forEach(function(n,i){var b=el("button",{text:n,"aria-label":n+" page"},nv);b.onclick=function(){show(i)};
  var t=el("button",{"class":"nav-link pg",text:n,"aria-label":n+" page"},tp);t.onclick=function(){show(i)}});
}
function show(i){page=(i+PAGES.length)%PAGES.length;peakSpd=Math.max(peakSpd,0);
 for(var k=0;k<PAGES.length;k++){$("p"+k).classList.toggle("on",k===page);$("nav").children[k].classList.toggle("on",k===page);$("pgtop").children[k].classList.toggle("on",k===page)}
 lsSet("trace.page",page);}

/* ---------- connection ---------- */
var ws=null,backoff=500;
function setConn(c,txt){conn=c;$("dot").className=c==="live"?"live":c==="wait"?"wait":c==="off"?"off":"";$("stxt").textContent=txt;
 var ov=$("ov");if(c==="live"){ov.className=""}else{ov.className="on";ov.textContent=c==="wait"?"Connected to TRACE.\nWaiting for telemetry from the console…":c==="off"?"Disconnected from TRACE.\nReconnecting…":"Connecting…";}}
function connect(){
 var u=(location.protocol==="https:"?"wss://":"ws://")+location.host+"/ws";
 try{ws=new WebSocket(u)}catch(e){setConn("off","DISCONNECTED");setTimeout(connect,backoff);return}
 ws.onopen=function(){backoff=500;setConn("wait","WAITING")};
 ws.onmessage=function(ev){var m;try{m=JSON.parse(ev.data)}catch(e){return}
  if(m.type==="f"){for(var k in m)S[k]=m[k];lastFrameAt=performance.now();
   HIST.push({t:performance.now(),thr:S.thr,brk:S.brk,str:Math.max(-1,Math.min(1,S.str))});while(HIST.length&&performance.now()-HIST[0].t>21000)HIST.shift();
   if(S.ok)setConn("live","LIVE"+(S.loss_pkt?" · loss "+S.loss_pkt+"%":""));else setConn("wait","WAITING");
   $("ident").textContent=[S.car,S.track].filter(Boolean).join(" · ")}
  else if(m.type==="track"){TRACK=m.pts||[]}
  else if(m.type==="idle"){setConn("wait","WAITING")}};
 ws.onclose=function(){ws=null;setConn("off","DISCONNECTED");
  /* logged out (LOG OUT ALL DEVICES, or the WEB UI was turned off and on): back to the code page */
  fetch("/session",{cache:"no-store",credentials:"same-origin"}).then(function(r){if(r.status===401)location.reload()}).catch(function(){});
  setTimeout(connect,backoff);backoff=Math.min(8000,backoff*1.6)};
 ws.onerror=function(){try{ws.close()}catch(e){}};
}

/* ---------- animation ---------- */
var tPrev=performance.now();
function frame(now){requestAnimationFrame(frame);if(document.hidden)return;
 var dt=Math.min(0.1,(now-tPrev)/1000);tPrev=now;var k=reduce?1:1-Math.exp(-dt*16);
 for(var n in V){var tgt=n==="latg"?S.latg:n==="long"?S.long:n==="str"?S.str:S[n];if(tgt==null)tgt=0;
  if(n==="x"||n==="z"){V[n]=tgt;continue}V[n]+=(tgt-V[n])*k}
 for(var i=0;i<WIDGETS.length;i++)if(WIDGETS[i].page===page)WIDGETS[i].up(dt)}

/* ---------- controls ---------- */
function applyTheme(t){document.documentElement.setAttribute("data-theme",t);lsSet("trace.theme",t)}
var wake=null;
function setWake(on){var b=$("bw");
 if(!on){if(wake){try{wake.release()}catch(e){}wake=null}b.setAttribute("aria-pressed","false");lsSet("trace.wake","0");return}
 if(!navigator.wakeLock){b.textContent="No wake";b.title="This browser can't keep the screen awake; change the device's sleep timeout instead";return}
 navigator.wakeLock.request("screen").then(function(w){wake=w;b.setAttribute("aria-pressed","true");lsSet("trace.wake","1");
  w.addEventListener("release",function(){if(wake===w)wake=null})}).catch(function(){b.textContent="No wake"})}
document.addEventListener("visibilitychange",function(){if(!document.hidden&&lsGet("trace.wake","0")==="1"&&!wake)setWake(true)});
$("bu").onclick=function(){units=units==="kmh"?"mph":"kmh";peakSpd=0;lsSet("trace.units",units);
 this.textContent=units==="kmh"?"KM/H":"MPH";this.setAttribute("aria-pressed",units==="mph")};
$("bt").onclick=function(){applyTheme(document.documentElement.getAttribute("data-theme")==="light"?"dark":"light")};
$("bw").onclick=function(){setWake(!wake)};
$("bf").onclick=function(){var d=document,e=d.documentElement;
 if(d.fullscreenElement||d.webkitFullscreenElement){(d.exitFullscreen||d.webkitExitFullscreen).call(d)}
 else if(e.requestFullscreen||e.webkitRequestFullscreen){(e.requestFullscreen||e.webkitRequestFullscreen).call(e)}
 else this.textContent="N/A"};
document.addEventListener("keydown",function(e){
 if(e.key>="1"&&e.key<="5")show(+e.key-1);else if(e.key==="ArrowRight")show(page+1);else if(e.key==="ArrowLeft")show(page-1);
 else if(e.key==="f"||e.key==="F")$("bf").click()});
var tx=null,ty=null;
document.addEventListener("touchstart",function(e){tx=e.touches[0].clientX;ty=e.touches[0].clientY},{passive:true});
document.addEventListener("touchend",function(e){if(tx===null)return;var dx=e.changedTouches[0].clientX-tx,dy=e.changedTouches[0].clientY-ty;tx=null;
 if(Math.abs(dx)>70&&Math.abs(dx)>Math.abs(dy)*1.6)show(page+(dx<0?1:-1))},{passive:true});

/* hero gear numeral, spotlight, back-to-top (same behaviours as the website) */
(function(){var hn=$("hnum"),hd=$("main-content"),last="";
 setInterval(function(){var g=S.gear<0?"R":S.gear===0?"N":String(S.gear);if(g!==last){last=g;hn.textContent=g}},120);
 hd.addEventListener("mousemove",function(e){var r=hd.getBoundingClientRect();hd.style.setProperty("--mx",(e.clientX-r.left)+"px");hd.style.setProperty("--my",(e.clientY-r.top)+"px")},{passive:true});
 var b=document.createElement("button");b.type="button";b.className="back-to-top";b.setAttribute("aria-label","Back to top");
 b.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"></polyline></svg>';
 document.body.appendChild(b);
 window.addEventListener("scroll",function(){b.classList.toggle("show",window.scrollY>400)},{passive:true});
 b.onclick=function(){window.scrollTo({top:0,behavior:reduce?"auto":"smooth"})};
})();

build();
units=lsGet("trace.units","kmh")==="mph"?"mph":"kmh";$("bu").textContent=units==="kmh"?"KM/H":"MPH";$("bu").setAttribute("aria-pressed",units==="mph");
applyTheme(lsGet("trace.theme",(window.matchMedia&&matchMedia("(prefers-color-scheme: light)").matches)?"light":"dark"));
show(parseInt(lsGet("trace.page","0"),10)||0);
if(lsGet("trace.wake","0")==="1")setWake(true);
setConn("connecting","CONNECTING");connect();requestAnimationFrame(frame);
</script></body></html>
"""

from .webui_fonts import FONT_CSS  # noqa: E402

PAGE = PAGE.replace("/*@FONTS@*/", FONT_CSS)
