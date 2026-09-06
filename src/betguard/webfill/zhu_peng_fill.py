"""ZhuPeng fill module — numbers via el.click(), bets via PengBet.Value inputs.

WARNING: This module drives a live browser page via Playwright page.evaluate().
All fill operations target window.frames[2] (the B03 frame inside the gts362
frameset).  The module is guarded:
  - Never auto-submit, confirm, or advance to the next item.
  - Every fill step is followed by a readback; mismatch → BLOCKED.
  - Safety words in element texts are rejected before any click.
  - Amounts are written through visible PengBet.Value knockout observables,
    not hidden tb_X inputs / OnChkBet / OnChkNO.
"""

from __future__ import annotations

import json
import time
from typing import Any

from betguard.webfill.safety import DANGER_WORDS

# ── B03 context helpers ────────────────────────────────────────────────

_FRAME_PREFIX = "window.frames[2]"


def _eval(page: Any, js: str) -> Any:
    """Evaluate JS against the main page (accesses frames[2] internally)."""
    return page.evaluate(js)


def _b03_js(inner: str) -> str:
    """Wrap inner JS fragment with B03 frame prefix."""
    return inner.replace("__B03__.", f"{_FRAME_PREFIX}.")


# ── Number selection ────────────────────────────────────────────────────

def click_number(page: Any, number: int) -> None:
    """Find the TD whose trimmed innerText equals *number* in B03 and click it.

    Skips already-selected numbers (idempotent — prevents toggle-deselect).
    """
    s = str(number).zfill(2)
    js = _b03_js("""
(function(){
    var t=__B03__.document.querySelectorAll('td');
    for(var i=0;i<t.length;i++){
        if((t[i].innerText||'').trim()==='""" + s + """'){
            var txt=(t[i].innerText||'').trim();
            if(""" + json.dumps(list(DANGER_WORDS)) + """.some(function(w){return txt.indexOf(w)>=0;})) return;
            // Idempotent: check if already selected via HasSeled
            var ctx=null;
            try{ctx=__B03__.ko.contextFor(t[i]);}catch(e){}
            if(ctx&&ctx.$data&&typeof ctx.$data.HasSeled==='function'&&ctx.$data.HasSeled()){
                return; // already selected — skip to avoid toggle
            }
            t[i].click();
            return;
        }
    }
})()
""")
    _eval(page, js)


def click_zhu_column(page: Any, index: int) -> None:
    """Click the column-header TD (1-indexed) whose data-bind contains OnClickZhu."""
    idx_str = str(index)
    js = _b03_js("""
(function(){
    var t=__B03__.document.querySelectorAll('td');
    for(var i=0;i<t.length;i++){
        var b=t[i].getAttribute('data-bind')||'';
        if(b.indexOf('OnClickZhu')>=0 && (t[i].innerText||'').trim()==='"""+idx_str+"""'){
            t[i].click();
            return;
        }
    }
})()
""")
    _eval(page, js)


def readback_zhu_data(page: Any) -> list[int]:
    """Return per-column Data lengths as list[int]."""
    raw = _eval(page, _b03_js(
        "JSON.stringify(__B03__.Mo.ZhuPengMgr.Zhus().map(function(z){return z.Data().length;}))"
    ))
    return json.loads(raw)


# ── PengBet amount fill ─────────────────────────────────────────────────

def set_pengbet_amounts(page: Any, amounts: dict[str, int]) -> None:
    """Fill visible PengBet.Value inputs.

    *amounts* maps star labels ('二星','三星','四星') → integer value.
    The visible PengBet inputs are indexed left-to-right: 0=二星, 1=三星, 2=四星.
    """
    star_map = {"二星": 0, "三星": 1, "四星": 2}

    amt_js_parts = []
    for star, amt in amounts.items():
        idx = star_map.get(star)
        if idx is None:
            raise ValueError(f"Unknown star: {star}")
        amt_js_parts.append(f"{idx}:{amt}")
    amt_obj = "{" + ",".join(amt_js_parts) + "}"

    js = _b03_js("""
(function(){
    var d=__B03__.document, k=__B03__.ko;
    var amts="""+amt_obj+""";
    var inps=d.querySelectorAll('input'); var pgs=[];
    for(var i=0;i<inps.length;i++){
        var b=inps[i].getAttribute('data-bind')||'';
        if(b.indexOf('PengBet.Value')>=0&&(inps[i].offsetWidth||inps[i].offsetHeight))
            pgs.push(inps[i]);
    }
    for(var idx in amts){
        var el=pgs[idx];
        if(!el) continue;
        var c=k.contextFor(el);
        if(!c||!c.$data||!c.$data.PengBet) continue;
        var bet=c.$data.PengBet;
        if(k.isObservable(bet.Enabled)) bet.Enabled(true);
        if(k.isObservable(bet.Value)) bet.Value(String(amts[idx]));
        el.value=String(amts[idx]);
        ['input','change','keyup','blur'].forEach(function(t){
            el.dispatchEvent(new Event(t,{bubbles:true,cancelable:true}));
        });
    }
})()
""")
    _eval(page, js)


def readback_pengbet_amounts(page: Any) -> list[dict[str, Any]]:
    """Return visible PengBet inputs: [{value, pengValue, disabled}, ...]."""
    raw = _eval(page, _b03_js("""
JSON.stringify((function(){
    var d=__B03__.document, k=__B03__.ko, o=[];
    d.querySelectorAll('input').forEach(function(el){
        var b=el.getAttribute('data-bind')||'';
        if(b.indexOf('PengBet.Value')>=0&&(el.offsetWidth||el.offsetHeight)){
            var c=k.contextFor(el);
            o.push({
                domValue:el.value,
                pengValue:c&&c.$data&&c.$data.PengBet?c.$data.PengBet.Value():'?',
                disabled:!!el.disabled
            });
        }
    });
    return o;
})())
"""))
    return json.loads(raw)


# ── Full fill plan ──────────────────────────────────────────────────────

def _detect_column_slot_count(page: Any) -> int:
    """Detect how many visible column header TDs exist in B03 (OnClickZhu)."""
    js = _b03_js("""
(function(){
    var hdrs=__B03__.document.querySelectorAll('td[data-bind*="OnClickZhu"]');
    var cnt=0;
    for(var i=0;i<hdrs.length;i++){
        if(hdrs[i].offsetParent!==null) cnt++;
    }
    return cnt;
})()
""")
    raw = _eval(page, js)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0  # Unknown capacity is not permission to fill.


def build_zhu_peng_plan(parsed_item: dict[str, Any]) -> dict[str, Any]:
    """Convert a parsed item into a ZhuPeng fill plan.

    Expected keys in *parsed_item*:
      - numbers: list of list of int (e.g. [[11],[22],[33],[13,23]])
      - amounts: dict star→int (e.g. {'二星':100, '三星':100, '四星':100})

    Returns a plan dict ready for *execute_zhu_peng_plan*.
    """
    numbers = parsed_item.get("numbers", [])
    amounts = parsed_item.get("amounts", {})
    if not numbers:
        raise ValueError("parsed_item must contain 'numbers'")
    return {"numbers": numbers, "amounts": amounts}


def execute_zhu_peng_plan(page: Any, plan: dict[str, Any]) -> dict[str, Any]:
    """Fill only allowed controls; verify exact membership and all star values."""
    from betguard.webfill.fill_readback import read_selected, sync_column
    numbers, amounts = plan["numbers"], plan.get("amounts", {})
    report = {"ok": False, "blocked": True, "numbers": numbers, "amounts": amounts,
              "numbers_verified": False, "amounts_verified": False,
              "steps": [], "filled_targets": [], "missing_targets": [],
              "auto_submit": False, "auto_confirm": False}
    try:
        slots = _detect_column_slot_count(page)
        report["site_column_slots"] = slots
        if not numbers or not amounts or slots < len(numbers) or slots > 20:
            raise ValueError("無法確認柱位或倍率欄位")
        # Empty unused columns too: prior bets must not leak into the new one.
        for i in range(slots):
            click_zhu_column(page, i + 1)
            sync_column(page, numbers[i] if i < len(numbers) else [])
        actual_groups = []
        for i in range(slots):
            click_zhu_column(page, i + 1)
            actual_groups.append(sorted(read_selected(page)))
        expected = [sorted(f"{int(n):02d}" for n in c) for c in numbers] + [[] for _ in range(slots-len(numbers))]
        report["readback_columns"] = actual_groups
        report["numbers_verified"] = actual_groups == expected
        if not report["numbers_verified"]:
            raise ValueError("柱群號碼讀回不一致；未繼續填倍率")
        full_amounts = {s: amounts.get(s, 0) for s in ("二星", "三星", "四星")}
        set_pengbet_amounts(page, full_amounts)
        values = readback_pengbet_amounts(page)
        report["readback_amounts"] = values
        report["amounts_verified"] = len(values) == 3 and all(
            str(v.get("domValue") or "0") == str(expected_amount)
            and str(v.get("pengValue") or "0") == str(expected_amount)
            and (not v.get("disabled") or expected_amount == 0)
            for v, expected_amount in zip(values, full_amounts.values())
        )
        if not report["amounts_verified"]:
            raise ValueError("柱碰倍率讀回不一致")
        # Amount updates can re-render/reset selection: verify numbers again.
        for i in range(slots):
            click_zhu_column(page, i+1)
            if sorted(read_selected(page)) != expected[i]:
                report["numbers_verified"] = False
                raise ValueError("倍率填入後柱群發生變動")
        report.update(ok=True, blocked=False,
                      filled_targets=[n for c in expected for n in c])
    except Exception as exc:
        report["error"] = str(exc)
    return report
