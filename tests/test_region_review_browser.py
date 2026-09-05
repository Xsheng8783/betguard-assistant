"""Automated local synthetic interactions, NOT a human review time study."""
import json
from types import SimpleNamespace
from PIL import Image
from playwright.sync_api import sync_playwright, expect
from tests.test_image_text_assisted_fill import _running_app


def test_optional_panel_synthetic_browser(tmp_path,monkeypatch):
    from betguard.webui import app
    from betguard.vision import image_intake,service
    image=tmp_path/'synthetic.png';Image.new('RGB',(400,300),'white').save(image)
    monkeypatch.setattr(app,'RUNS_DIR',tmp_path/'runs')
    monkeypatch.setattr(image_intake,'get_metadata',lambda _:SimpleNamespace(storage_path=image,is_expired=lambda:False))
    monkeypatch.setattr(service,'get_image_preview',lambda _:(image.read_bytes(),'image/png',None))
    with _running_app() as port, sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1400,'height':1000})
        errors=[];forbidden=[];calls=[]
        page.on('pageerror',lambda err:errors.append(str(err)))
        def route(r):
            url=r.request.url
            if not url.startswith(f'http://127.0.0.1:{port}/'):
                forbidden.append(url);r.abort();return
            if r.request.method=='POST':calls.append(url)
            if any(x in url for x in ['/api/vision/v1/transcriptions','/assist-panel/create-batch','/execute','/submit']) and not url.endswith('/preflight'):
                forbidden.append(url);r.abort();return
            r.continue_()
        page.route('**/*',route)
        page.goto(f'http://127.0.0.1:{port}/assist-panel')
        page.evaluate("""() => {
          document.getElementById('vision-section').style.display='block';
          document.getElementById('assist-game').value='539';
          document.dispatchEvent(new CustomEvent('betguard:image-uploaded',{detail:{imageId:'synthetic'}}));
        }""")
        main=page.locator('#vision-transcription-text');main.fill('01 07 19 2×5\n01 07 19 3×0.5')
        expect(page.locator('#rr-panel')).to_be_hidden()
        page.locator('#rr-open').click();expect(page.locator('#rr-panel')).to_be_visible()
        expect(page.locator('#rr-text')).to_have_value('01 07 19 2×5\n01 07 19 3×0.5')
        expect(page.locator('#rr-assemble')).to_be_disabled()
        page.locator('[data-rr="confirm"]').click();expect(page.locator('#rr-state')).to_have_text('已確認')
        page.locator('#rr-text').fill('01 07 19 2×0.5')
        expect(page.locator('#rr-assemble')).to_be_disabled()
        page.locator('[data-rr="confirm"]').click();expect(page.locator('#rr-state')).to_have_text('已確認')
        page.locator('[data-rr="vertical"]').click()
        expect(page.locator('#rr-select option')).to_have_count(2)
        expect(page.locator('#rr-text')).to_have_value('')
        page.locator('#rr-text').fill('01 07 19 2×5')
        page.locator('[data-rr="confirm"]').click();expect(page.locator('#rr-state')).to_have_text('已確認')
        page.locator('[data-rr="next"]').click()
        page.locator('#rr-text').fill('03 × ?尾')
        page.locator('[data-rr="confirm"]').click()
        expect(page.locator('#rr-errors button').first).to_be_visible()
        expect(page.locator('#rr-assemble')).to_be_disabled()
        page.locator('#rr-text').fill('02 08 20 2×0.5')
        page.locator('[data-rr="confirm"]').click();expect(page.locator('#rr-state')).to_have_text('已確認')
        page.locator('#rr-assemble').click()
        expect(main).to_have_value('01 07 19 2×5\n\n02 08 20 2×0.5')
        import os
        if os.environ.get('BETGUARD_REGION_TEST_SCREENSHOT'):
            page.locator('#rr-panel').screenshot(path=os.environ['BETGUARD_REGION_TEST_SCREENSHOT'])
        page.locator('[data-rr="cancelled"]').click();expect(page.locator('#rr-state')).to_have_text('取消 · 待確認')
        expect(page.locator('#rr-assemble')).to_be_disabled()
        page.locator('[data-rr="confirm"]').click();expect(page.locator('#rr-state')).to_have_text('取消 · 已確認')
        page.locator('#rr-assemble').click();expect(main).to_have_value('01 07 19 2×5')
        # Drag at zoomed display: save must still use original-image pixels.
        page.locator('#rr-mode').select_option('add')
        page.locator('#rr-overlay').scroll_into_view_if_needed()
        rect=page.locator('#rr-overlay').bounding_box()
        page.mouse.move(rect['x']+rect['width']*.1,rect['y']+rect['height']*.1)
        page.mouse.down();page.mouse.move(rect['x']+rect['width']*.3,rect['y']+rect['height']*.3);page.mouse.up()
        expect(page.locator('#rr-select option')).to_have_count(3)
        page.locator('#rr-close').click();expect(page.locator('#rr-panel')).to_be_hidden()
        assert main.is_editable()
        snapshots=sorted((tmp_path/'runs/region-assisted-review-dataset-v1').glob('*/*/revision-*.json'))
        latest=json.loads(snapshots[-1].read_text(encoding='utf-8'))
        assert latest['regions'][-1]['bbox']==[40,30,80,60]
        assert not latest['regions'][-1]['region_confirmed']
        assert not list((tmp_path/'runs').rglob('batch_*.json'))
        assert not errors and not forbidden
        assert calls and all('region-review' in url or 'preflight' in url for url in calls)
        browser.close()
