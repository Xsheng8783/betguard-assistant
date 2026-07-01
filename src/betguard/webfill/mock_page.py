from __future__ import annotations

from pathlib import Path


MOCK_PAGE_FILENAME = "mock_bet_page.html"
STAR_FIELDS = ["二星", "三星", "四星"]
DANGER_BUTTONS = ["送出注單", "確認"]


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_mock_page_path() -> Path:
    return project_root() / MOCK_PAGE_FILENAME


def build_mock_page_html() -> str:
    number_buttons = "\n".join(
        f'      <button type="button" data-number="{number:02d}">{number:02d}</button>'
        for number in range(1, 40)
    )
    amount_fields = "\n".join(
        [
            f'      <label>{star}</label>\n'
            f'      <input data-amount-field="{star}" inputmode="numeric" />'
            for star in STAR_FIELDS
        ]
    )
    danger_buttons = "\n".join(
        f'      <button type="button" data-danger="true">{label}</button>'
        for label in DANGER_BUTTONS
    )
    return f"""<!doctype html>
<html lang="zh-Hant">
  <head>
    <meta charset="utf-8" />
    <title>539 二三四星 連碰 Mock Page</title>
    <style>
      body {{ font-family: system-ui, sans-serif; margin: 24px; }}
      .numbers {{ display: grid; grid-template-columns: repeat(10, minmax(44px, 1fr)); gap: 8px; max-width: 560px; }}
      button {{ padding: 8px 10px; }}
      button.selected {{ outline: 3px solid #0f766e; background: #ccfbf1; }}
      .amounts {{ display: grid; grid-template-columns: 80px 160px; gap: 8px; max-width: 260px; margin-top: 24px; }}
      .danger {{ margin-top: 24px; }}
    </style>
  </head>
  <body>
    <h1>539 二三四星 連碰 Mock Page</h1>
    <section class="numbers">
{number_buttons}
    </section>
    <section class="amounts">
{amount_fields}
    </section>
    <section class="danger">
{danger_buttons}
    </section>
    <script>
      document.querySelectorAll("[data-number]").forEach((button) => {{
        button.addEventListener("click", () => {{
          button.classList.add("selected");
          button.dataset.selected = "true";
        }});
      }});
      document.querySelectorAll("[data-danger]").forEach((button) => {{
        button.addEventListener("click", () => {{
          document.body.dataset.dangerClicked = "true";
        }});
      }});
    </script>
  </body>
</html>
"""


def ensure_mock_page(path: str | Path | None = None) -> Path:
    target = Path(path) if path is not None else default_mock_page_path()
    target.write_text(build_mock_page_html(), encoding="utf-8")
    return target


def mock_page_url(path: str | Path | None = None) -> str:
    return ensure_mock_page(path).resolve().as_uri()
