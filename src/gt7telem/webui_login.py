"""webui_login.py -- the small login page the WEB UI shows before the passcode is entered (ALLOW REMOTE on).

Same rules as webui_page.py: one self-contained string, the website's fonts inlined, no CDN, works offline. The
Android app serves this exact page too (tools/gen_android_webui.py copies it to assets/webui_login.html).

The code goes to the server in a request HEADER (GET /login with X-Trace-Code), never in the URL, so it can't end up
in browser history or a proxy log. The server answers 204 + an HttpOnly cookie, or 401 with one generic message.
"""

LOGIN_PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="dark">
<meta name="referrer" content="no-referrer">
<title>TRACE Live - Enter code</title>
<style>
/*@FONTS@*/
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#08080B;--bg-2:#17171b;--bg-3:#202024;--line:rgba(255,255,255,.08);--line-2:rgba(255,255,255,.16);
--red:#c8102e;--red-2:#e2455a;--red-dim:#8c0c22;--teal:#5fb3a8;--txt:#f2f0ec;--mid:#96949c;--dim:#605e68;
--impact:Anton,Impact,"Arial Narrow Bold",sans-serif;--display:Sora,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
--body:Manrope,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--mono:"JetBrains Mono",ui-monospace,Consolas,Menlo,monospace}
html,body{background:var(--bg);color:var(--txt);font-family:var(--body);min-height:100%}
body{min-height:100vh;min-height:100dvh;display:flex;align-items:center;justify-content:center;
 padding:max(24px,env(safe-area-inset-top)) max(16px,env(safe-area-inset-right)) max(24px,env(safe-area-inset-bottom)) max(16px,env(safe-area-inset-left));position:relative}
body::before{content:"";position:fixed;inset:0;pointer-events:none;
 background-image:linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px);
 background-size:64px 64px;-webkit-mask-image:radial-gradient(ellipse 80% 60% at 50% 0%,#000 0%,transparent 75%);mask-image:radial-gradient(ellipse 80% 60% at 50% 0%,#000 0%,transparent 75%)}
main{position:relative;width:100%;max-width:400px;background:linear-gradient(180deg,rgba(255,255,255,.025),rgba(255,255,255,0)),var(--bg-2);
 border:1px solid var(--line);border-radius:6px;padding:28px 24px 24px;box-shadow:0 20px 48px -16px rgba(0,0,0,.6)}
main::before{content:"";position:absolute;left:0;top:0;right:0;height:3px;background:var(--red);border-radius:6px 6px 0 0}
.logo{font-family:var(--display);font-weight:600;font-size:1rem;letter-spacing:.12em;text-transform:uppercase}
.logo span{color:var(--red)}
.eyebrow{display:flex;align-items:center;gap:9px;margin-top:18px;font-family:var(--mono);font-size:.68rem;letter-spacing:.28em;text-transform:uppercase;color:var(--teal)}
.eyebrow::before{content:"";width:15px;height:8px;background:currentColor;clip-path:polygon(0 0,65% 0,100% 50%,65% 100%,0 100%,35% 50%)}
h1{font-family:var(--impact);font-weight:400;font-size:2.6rem;line-height:1.05;text-transform:uppercase;margin:10px 0 8px}
h1 em{color:var(--red);font-style:normal}
p{color:var(--mid);font-size:.92rem;line-height:1.55}
label.code{display:block;margin-top:22px;font-family:var(--mono);font-size:.7rem;letter-spacing:.2em;text-transform:uppercase;color:var(--dim)}
input#code{display:block;width:100%;margin-top:8px;padding:12px 14px;background:var(--bg);color:var(--txt);border:1px solid var(--line-2);border-radius:4px;
 font-family:var(--mono);font-size:2rem;font-weight:700;letter-spacing:.45em;text-align:center}
input#code:focus{outline:2px solid var(--red);outline-offset:2px;border-color:transparent}
.rem{display:flex;align-items:center;gap:10px;margin-top:16px;font-size:.9rem;color:var(--txt);cursor:pointer;user-select:none}
.rem input{width:18px;height:18px;accent-color:var(--red)}
.rem small{display:block;color:var(--dim);font-size:.78rem}
button{width:100%;margin-top:20px;min-height:48px;border:0;cursor:pointer;color:#fbf3ef;font-family:var(--display);font-weight:700;font-size:.92rem;letter-spacing:.12em;text-transform:uppercase;
 background:linear-gradient(180deg,var(--red-2) 0%,var(--red) 55%,var(--red-dim) 100%);clip-path:polygon(0 0,100% 0,100% 100%,10px 100%,0 calc(100% - 10px));
 box-shadow:inset 0 1px 0 rgba(255,255,255,.22),inset 0 -2px 3px rgba(0,0,0,.28)}
button:disabled{filter:grayscale(.6) brightness(.8);cursor:wait}
button:focus-visible{outline:2px solid var(--txt);outline-offset:3px}
#err{min-height:1.4em;margin-top:14px;font-family:var(--mono);font-size:.78rem;color:var(--red-2)}
.foot{margin-top:18px;padding-top:14px;border-top:1px solid var(--line);font-family:var(--mono);font-size:.66rem;letter-spacing:.08em;color:var(--dim)}
</style></head><body>
<main>
 <div class="logo">GT7 <span>TRACE</span></div>
 <div class="eyebrow">WEB UI</div>
 <h1>Enter <em>code</em></h1>
 <p>Type the 6-digit code shown next to the WEB UI address in TRACE's Live Dashboard, or scan the QR code there.</p>
 <form id="f" autocomplete="off" novalidate>
  <label class="code" for="code">Code</label>
  <input id="code" name="code" inputmode="numeric" pattern="[0-9]*" maxlength="6" autocomplete="one-time-code" autofocus required>
  <label class="rem"><input type="checkbox" id="remember"><span>Remember this device<small>Skip the code here for 30 days</small></span></label>
  <button id="go" type="submit">Unlock</button>
  <div id="err" role="alert" aria-live="assertive"></div>
 </form>
 <div class="foot">Live telemetry from your own TRACE app. Nothing leaves your network.</div>
</main>
<script>
(function(){
 var f=document.getElementById("f"),c=document.getElementById("code"),r=document.getElementById("remember"),
     b=document.getElementById("go"),e=document.getElementById("err");
 c.addEventListener("input",function(){c.value=c.value.replace(/\D/g,"").slice(0,6);e.textContent=""});
 f.addEventListener("submit",function(ev){
  ev.preventDefault();
  if(c.value.length!==6){e.textContent="Enter all 6 digits.";c.focus();return}
  b.disabled=true;e.textContent="";
  fetch("/login",{method:"GET",cache:"no-store",credentials:"same-origin",
   headers:{"X-Trace-Code":c.value,"X-Trace-Remember":r.checked?"1":"0"}})
  .then(function(res){
   if(res.status===204){location.replace(location.pathname||"/");return}
   e.textContent="Wrong code, or too many tries. Wait a minute and try again.";c.value="";c.focus();b.disabled=false})
  .catch(function(){e.textContent="Can't reach TRACE. Is the WEB UI still on?";b.disabled=false});
 });
})();
</script>
</body></html>
"""

from .webui_fonts import FONT_CSS  # noqa: E402

LOGIN_PAGE = LOGIN_PAGE.replace("/*@FONTS@*/", FONT_CSS)
