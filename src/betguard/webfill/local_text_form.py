"""Loopback-only test form for the existing B03 text adapter (not a real site)."""
import html
import os
from urllib.parse import parse_qs, urlparse


def local_fill_url():
    value = os.environ.get("BETGUARD_LOCAL_FILL_URL", "")
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("本機填入測試只允許 loopback URL")
    return value.rstrip("/")


def render_test_form(path):
    query = parse_qs(urlparse(path).query)
    game = query.get("game", ["539"])[0]
    mode = query.get("mode", ["normal"])[0]
    if game not in {"539", "六合"} or mode not in {"normal", "column"}:
        raise ValueError("invalid test form configuration")
    if not urlparse(path).path.endswith("/Front/B/B03"):
        target = f"/__text_fill_test/Front/B/B03?game={game}&mode={mode}"
        return '<!doctype html><meta charset="utf-8"><title>Betguard 本機測試表單</title><iframe hidden></iframe><iframe hidden></iframe><iframe style="width:98vw;height:94vh" src="' + html.escape(target, quote=True) + '"></iframe>'
    maximum, game_id = (39, 13) if game == "539" else (49, 11)
    return ("""<!doctype html><meta charset="utf-8"><title>本機 B03 測試</title>
<style>td{border:1px solid #888;padding:8px;cursor:pointer}.selected{background:#acf}input{width:100px}</style>
<h2>本機測試表單（不是真站） GAME / MODE</h2>
<table id="columns"><tr></tr></table><table id="numbers"></table>
<div id="amounts"></div><p id="readback"></p>
<button onclick="window.submitCount++">送出（測試計數）</button>
<script>
window.submitCount=0;
window.$Global={GameID: GAME_ID};
const columnMode=IS_COLUMN;
let current=0;
const selected=Array.from({length:7},()=>new Set());
const values=['','',''];
function observable(read,write){ return function(v){if(arguments.length)write(v);return read();};}
window.ko={contextFor:el=>el._ctx,isObservable:v=>typeof v==='function'};
function refresh(){
 document.querySelectorAll('#numbers td').forEach(el=>el.classList.toggle('selected',selected[current].has(el.textContent)));
 document.getElementById('readback').textContent=JSON.stringify({columns:selected.map(s=>[...s]),amounts:values,current:current+1});
}
window.Mo={
 OnSwitchSel:function(data){data.toggle();},
 ZhuPengMgr:{Zhus:()=>selected.map(s=>({Data:()=>[...s]}))}
};
for(let c=0;c<7;c++){
 const el=document.createElement('td');el.textContent=String(c+1);
 el.setAttribute('data-bind','click: OnClickZhu');
 el.onclick=()=>{current=c;refresh();};
 document.querySelector('#columns tr').appendChild(el);
}
if(!columnMode)document.getElementById('columns').hidden=true;
for(let n=1;n<=MAX_NUMBER;n++){
 if((n-1)%10===0)document.getElementById('numbers').appendChild(document.createElement('tr'));
 const el=document.createElement('td'),s=String(n).padStart(2,'0');
 el.textContent=s;el.setAttribute('data-bind','click: OnSwitchSel');
 const data={HasSeled:()=>selected[current].has(s),toggle:()=>{selected[current].has(s)?selected[current].delete(s):selected[current].add(s);refresh();}};
 el._ctx={$data:data};el.onclick=()=>data.toggle();
 document.querySelector('#numbers tr:last-child').appendChild(el);
}
for(let i=0;i<3;i++){
 const label=document.createElement('label'),el=document.createElement('input');
 label.textContent=String(i+2)+'星';el.setAttribute('data-bind','value: PengBet.Value');
 const value=observable(()=>values[i],v=>{values[i]=String(v);el.value=String(v);refresh();});
 el._ctx={$data:{PengBet:{Value:value,Enabled:observable(()=>true,()=>{})}}};
 el.oninput=()=>{values[i]=el.value;refresh();};
 label.appendChild(el);document.getElementById('amounts').appendChild(label);
}
refresh();
</script>""".replace("GAME / MODE", html.escape(game+" / "+mode))
        .replace("GAME_ID", str(game_id)).replace("IS_COLUMN", str(mode=="column").lower())
        .replace("MAX_NUMBER", str(maximum)))
