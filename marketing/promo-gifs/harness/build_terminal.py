#!/usr/bin/env python3
"""Regenerate terminal.html from fresh CLI captures.

Capture real output first (from a machine that can reach the worker API):
    COLUMNS=120 poindexter costs budget      > costs.out
    COLUMNS=120 poindexter posts list --limit 5 > posts.out
    COLUMNS=120 poindexter doctor            > doctor.out   # exit 1 on FAILs is fine
Then:  python3 build_terminal.py
The TEMPLATE below is the shipped page verbatim (flat bg, clear-per-command,
sized so the full doctor report fits) with two placeholders: __SCRIPT__ and
__PROBES__ (spinner label, computed from doctor.out's FAIL/OK counts).
"""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))

def read(name: str) -> str:
    with open(os.path.join(HERE, name)) as f:
        return f.read().rstrip("\n")

outs = {n: read(f"{n}.out") for n in ("costs", "posts", "doctor")}

probes = sum(int(n) for n in re.findall(r"^(?:FAIL|OK) \((\d+)\)", outs["doctor"], re.M))
if not probes:
    raise SystemExit("doctor.out has no FAIL (n) / OK (n) headers - wrong capture?")

script = [
    {"cmd": "poindexter costs budget", "out": outs["costs"], "pause": 900},
    {"cmd": "poindexter posts list --limit 5", "out": outs["posts"], "pause": 900},
    {"cmd": "poindexter doctor", "out": outs["doctor"], "pause": 1600, "spinner": 1100},
]

TEMPLATE = r"""<!doctype html>
<meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  html,body{margin:0;height:100%}
  body{background:#06090d;
       display:flex;align-items:center;justify-content:center;font-family:'JetBrains Mono',monospace}
  .win{width:940px;height:616px;background:#0a0f14;border:1px solid #1c2833;border-radius:10px;
       box-shadow:0 24px 70px rgba(0,0,0,.65);display:flex;flex-direction:column;overflow:hidden}
  .bar{height:34px;background:#0e151c;border-bottom:1px solid #1c2833;display:flex;align-items:center;padding:0 14px;flex:none}
  .dot{width:11px;height:11px;border-radius:50%;margin-right:7px}
  .title{margin-left:12px;color:#5c7285;font-size:12px;letter-spacing:.12em}
  .scr{flex:1;overflow-y:hidden;padding:10px 18px;font-size:12.5px;line-height:1.28;color:#c7d5e0;white-space:pre-wrap;word-break:break-all}
  .p{color:#22d3ee;font-weight:700}
  .cmd{color:#f2f7fa;font-weight:500}
  .dim{color:#5c7285}.ok{color:#34d399}.bad{color:#f87171}.warn{color:#fbbf24}
  .hd{color:#93c5fd;font-weight:700}.money{color:#fbbf24}
  .cur{display:inline-block;width:8px;height:15px;background:#22d3ee;vertical-align:-2px;animation:bl 1s steps(1) infinite}
  @keyframes bl{50%{opacity:0}}
</style>
<div class="win">
  <div class="bar">
    <span class="dot" style="background:#ff5f57"></span>
    <span class="dot" style="background:#febc2e"></span>
    <span class="dot" style="background:#28c840"></span>
    <span class="title">poindexter · operator cli</span>
  </div>
  <div class="scr" id="scr"></div>
</div>
<script>
const SCRIPT = __SCRIPT__;
const scr = document.getElementById('scr');
const sleep = ms => new Promise(r => setTimeout(r, ms));
function scrollBottom(){ scr.scrollTop = scr.scrollHeight; }

function styleLine(l){
  const esc = l.replace(/&/g,'&amp;').replace(/</g,'&lt;');
  if (/^Health score:/.test(l)) return '<span class="hd">'+esc+'</span>';
  if (/^FAIL/.test(l)) return '<span class="bad">'+esc+'</span>';
  if (/^OK \(/.test(l)) return '<span class="ok">'+esc+'</span>';
  if (/^Status: healthy/.test(l)) return '<span class="ok">'+esc+'</span>';
  if (/^Posts:/.test(l)) return '<span class="hd">'+esc+'</span>';
  let s = esc.replace(/\bpublished\b/g,'<span class="ok">published</span>');
  s = s.replace(/\$[0-9][0-9.,]*/g, m => '<span class="money">'+m+'</span>');
  if (/^    \//.test(l)) s = '<span class="dim">'+s+'</span>';
  return s;
}

async function typeCmd(cmd){
  const line = document.createElement('div');
  line.innerHTML = '<span class="p">➜  ~ </span><span class="cmd"></span><span class="cur"></span>';
  scr.appendChild(line); scrollBottom();
  const span = line.querySelector('.cmd');
  for (const ch of cmd){
    span.textContent += ch;
    await sleep(26 + Math.random()*30);
  }
  await sleep(320);
  line.querySelector('.cur').remove();
}

async function printOut(text){
  for (const l of text.split('\n')){
    const d = document.createElement('div');
    d.innerHTML = styleLine(l) || '&nbsp;';
    scr.appendChild(d); scrollBottom();
    await sleep(24 + Math.random()*22);
  }
}

(async () => {
  await sleep(700);
  let first = true;
  for (const step of SCRIPT){
    if (!first) { scr.innerHTML = ''; await sleep(260); }
    first = false;
    await typeCmd(step.cmd);
    if (step.spinner){
      const d = document.createElement('div');
      d.className = 'dim'; scr.appendChild(d);
      const frames = ['⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏'];
      const t0 = Date.now(); let i = 0;
      while (Date.now() - t0 < step.spinner){
        d.textContent = frames[i++ % frames.length] + ' running __PROBES__ health probes…';
        scrollBottom(); await sleep(70);
      }
      d.remove();
    }
    await printOut(step.out);
    await sleep(step.pause);
  }
  const tail = document.createElement('div');
  tail.innerHTML = '<span class="p">➜  ~ </span><span class="cur"></span>';
  scr.appendChild(tail); scrollBottom();
  await sleep(3200);
  window.__done = true;
})();
</script>
"""

html = TEMPLATE.replace("__SCRIPT__", json.dumps(script)).replace("__PROBES__", str(probes))
with open(os.path.join(HERE, "terminal.html"), "w") as f:
    f.write(html)
print(f"terminal.html written ({len(html)} bytes, {probes} probes)")
