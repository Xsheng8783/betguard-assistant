"""Read visible selected values and explicit game configuration, never infer it."""
import json
from betguard.game_rules import GAME_RULES


def read_selected(page):
    raw = page.evaluate("""() => {
      const f=window.frames[2], selected=[];
      for(const el of f.document.querySelectorAll('td')) {
        const s=(el.textContent||'').trim(), c=f.ko.contextFor(el);
        if(/^\\d{2}$/.test(s) && el.offsetParent!==null &&
           c && c.$data && typeof c.$data.HasSeled==='function' && c.$data.HasSeled()) selected.push(s);
      }
      return JSON.stringify(selected);
    }""")
    value = json.loads(raw)
    if not isinstance(value, list) or any(not isinstance(n, str) or not n.isdigit() for n in value):
        raise ValueError("無法讀回完整號碼")
    return set(value)


def verify_game(page, game):
    rule = GAME_RULES.get(game)
    if not rule or game not in {"539", "六合"}:
        return False
    try:
        value = page.evaluate("""() => {
          const f=window.frames[2];
          return f && f.$Global ? f.$Global.GameID : null;
        }""")
        return str(value) == str(rule.game_id)
    except Exception:
        return False


def sync_column(page, wanted):
    # Only visible number controls, never submit/confirm/clear-all actions.
    page.evaluate("""wanted => {
      const f=window.frames[2];
      for(const el of f.document.querySelectorAll('td')) {
        const s=(el.textContent||'').trim(), c=f.ko.contextFor(el);
        if(/^\\d{2}$/.test(s) && el.offsetParent!==null &&
           c && c.$data && typeof c.$data.HasSeled==='function' &&
           Boolean(c.$data.HasSeled()) !== wanted.includes(s)) el.click();
      }
    }""", [f"{int(n):02d}" for n in wanted])
