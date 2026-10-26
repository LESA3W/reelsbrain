#!/usr/bin/env python3
"""Generate a self-contained visual dashboard of the ReelsBrain vault.

Scans notes/*.md, parses frontmatter + takeaways + transcript, and writes
ReelsBrain.html (data embedded, works via file:// — no server). Searchable card
grid. Regenerated automatically after each save (called by save_note.py).
"""
import json
import os
import re
import sys


def vault_path():
    return os.environ.get("REELS_VAULT") or os.path.join(
        os.path.expanduser("~"), "Obsidian", "ReelsBrain")


def parse_note(path):
    with open(path, encoding="utf-8") as f:
        c = f.read()
    fm = {}
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", c, re.S)
    body = c
    if m:
        block, body = m.group(1), m.group(2)
        for line in block.splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k, v = k.strip(), v.strip()
            if k == "topics":
                try:
                    fm["topics"] = json.loads(v)
                except Exception:
                    fm["topics"] = [t.strip(' "') for t in v.strip("[]").split(",") if t.strip()]
            else:
                fm[k] = v.strip().strip('"')
    take, transcript = [], ""
    ms = re.split(r"^##\s+Takeaway\s*$", body, flags=re.M)
    if len(ms) > 1:
        after = re.split(r"^##\s+", ms[1], flags=re.M)[0]
        take = [l.strip()[2:].strip() for l in after.splitlines() if l.strip().startswith("- ")]
    mt = re.split(r"^##\s+Transcript\s*$", body, flags=re.M)
    if len(mt) > 1:
        transcript = mt[1].strip()
    title = ""
    mh = re.search(r"^#\s+(.+)$", body, flags=re.M)
    if mh:
        title = mh.group(1).strip()
    return {
        "id": os.path.basename(path)[:-3],
        "title": title or fm.get("title", "Sans titre"),
        "url": fm.get("source_url", ""),
        "platform": fm.get("platform", ""),
        "author": fm.get("author", ""),
        "date": fm.get("date_added", ""),
        "duration": fm.get("duration", ""),
        "topics": fm.get("topics", []),
        "takeaway": take,
        "transcript": transcript,
    }


HTML = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ReelsBrain</title>
<style>
:root{--bg:#0e0f13;--card:#181a21;--mut:#8b90a0;--fg:#e7e9ee;--acc:#7c5cff;--chip:#262a36;--ok:#3ddc97}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
header{position:sticky;top:0;background:rgba(14,15,19,.92);backdrop-filter:blur(8px);
padding:18px 22px;border-bottom:1px solid #23262f;z-index:5}
.row{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
h1{font-size:20px;margin:0}.count{color:var(--mut);font-size:13px}
#q{flex:1;min-width:200px;background:var(--card);border:1px solid #2a2e3a;color:var(--fg);
padding:10px 14px;border-radius:10px;font-size:14px;outline:none}
#q:focus{border-color:var(--acc)}
main{padding:22px;display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:16px;max-width:1400px;margin:auto}
.card{background:var(--card);border:1px solid #23262f;border-radius:14px;padding:16px 18px;display:flex;flex-direction:column;gap:10px}
.card:hover{border-color:#34384a}
.badge{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);display:flex;gap:8px;align-items:center}
.dot{width:7px;height:7px;border-radius:50%;background:var(--acc)}
.ig{background:#e1306c}.tt{background:#25f4ee}.yt{background:#ff0033}
.title{font-size:16px;font-weight:650;margin:0;line-height:1.3}
.tk{margin:0;padding-left:18px;color:#cdd1db}.tk li{margin:3px 0}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{background:var(--chip);color:#aeb3c2;font-size:11.5px;padding:3px 9px;border-radius:20px}
.meta{color:var(--mut);font-size:12px;display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:2px}
.meta a{color:var(--acc);text-decoration:none}.meta a:hover{text-decoration:underline}
details{font-size:13px;color:#aeb3c2}summary{cursor:pointer;color:var(--mut);font-size:12px}
details pre{white-space:pre-wrap;background:#10121a;padding:10px;border-radius:8px;max-height:240px;overflow:auto;margin-top:8px}
.empty{grid-column:1/-1;text-align:center;color:var(--mut);padding:80px 20px}
.empty b{color:var(--fg)}
</style></head><body>
<header><div class="row"><h1>🧠 ReelsBrain</h1><span class="count" id="count"></span>
<input id="q" placeholder="Rechercher un sujet, un mot-clé, un conseil…"></div></header>
<main id="grid"></main>
<script>
const DATA = __DATA__;
const PB = {instagram:'ig',tiktok:'tt',youtube:'yt'};
const esc = s => (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function card(r){
  const cls = PB[r.platform]||'';
  const chips = (r.topics||[]).map(t=>`<span class="chip">${esc(t)}</span>`).join('');
  const tk = (r.takeaway||[]).map(b=>`<li>${esc(b)}</li>`).join('');
  const src = r.url?`<a href="${esc(r.url)}" target="_blank" rel="noopener">↗ voir le reel</a>`:'';
  const tr = r.transcript?`<details><summary>Transcript</summary><pre>${esc(r.transcript)}</pre></details>`:'';
  return `<div class="card">
    <div class="badge"><span class="dot ${cls}"></span>${esc(r.platform||'reel')}</div>
    <p class="title">${esc(r.title)}</p>
    ${tk?`<ul class="tk">${tk}</ul>`:''}
    <div class="chips">${chips}</div>
    <div class="meta">${r.author?'@'+esc(r.author):''} ${r.duration?'· '+esc(r.duration):''} ${r.date?'· '+esc(r.date):''} ${src}</div>
    ${tr}
  </div>`;
}
const grid=document.getElementById('grid'),cnt=document.getElementById('count'),q=document.getElementById('q');
function render(list){
  cnt.textContent=`${list.length} reel${list.length>1?'s':''}`;
  grid.innerHTML = list.length? list.map(card).join('')
    : `<div class="empty"><b>Aucun reel pour l'instant.</b><br>Envoie un lien de reel à ton bot Telegram — il apparaîtra ici.</div>`;
}
function filt(){
  const s=q.value.toLowerCase().trim();
  render(!s?DATA:DATA.filter(r=>(r.title+' '+(r.topics||[]).join(' ')+' '+(r.takeaway||[]).join(' ')+' '+r.author).toLowerCase().includes(s)));
}
q.addEventListener('input',filt);
DATA.sort((a,b)=>(b.date||'').localeCompare(a.date||''));
render(DATA);
</script></body></html>"""


def build(vault=None):
    vault = vault or vault_path()
    notes_dir = os.path.join(vault, "notes")
    reels = []
    if os.path.isdir(notes_dir):
        for fn in sorted(os.listdir(notes_dir)):
            if fn.endswith(".md"):
                try:
                    reels.append(parse_note(os.path.join(notes_dir, fn)))
                except Exception:
                    pass
    html = HTML.replace("__DATA__", json.dumps(reels, ensure_ascii=False))
    out = os.path.join(vault, "ReelsBrain.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return out, len(reels)


if __name__ == "__main__":
    path, n = build()
    sys.stdout.buffer.write(f"OK viewer : {path} ({n} reels)\n".encode("utf-8"))
