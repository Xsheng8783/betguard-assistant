"""Synthetic cases based on existing verified rules, not parser-generated truth."""
import io
import json
from unittest.mock import Mock

import pytest

from betguard.webfill.batch_mock_queue import build_batch_mock_queue
from betguard.webfill.manual_reparse import reparse_text
from betguard.webui import app


def invoke(method, data):
    handler = app.build_workbench_handler(project_version='test', git_commit='test').__new__(
        app.build_workbench_handler(project_version='test', git_commit='test'))
    handler._read_json_body = lambda: data
    replies=[]
    handler._send_json=lambda value, **kwargs: replies.append(value)
    handler.server=Mock(server_address=('127.0.0.1',8766))
    getattr(handler, method)()
    return replies[-1]


@pytest.mark.parametrize('text,columns',[
    ('01 × 12 × 23 33 2×3 3×1',[[1],[12],[23,33]]),
    ('01 × 12 × 23 2×0.5',[[1],[12],[23]]),
    ('01X12X23 2×0.5',[[1],[12],[23]]),
])
def test_edit_preserves_nested_groups_and_rules(text,columns):
    result=reparse_text(text,game='539')
    assert result['ok'],result
    assert result['type']=='column'
    assert result['columns']==columns
    assert result['game']=='539'
    assert result['amounts']==({'2':300,'3':100} if '3×1' in text else {'2':50})


def test_same_second_batches_never_overwrite(tmp_path,monkeypatch):
    import datetime
    class FrozenClock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026,9,7,12,0,0,tzinfo=tz)
    monkeypatch.setattr(datetime,'datetime',FrozenClock)
    monkeypatch.setattr(app,'RUNS_DIR',tmp_path)
    a=invoke('_handle_assist_panel_create_batch',{'text':'01 12 23 2×1','game':'539'})
    b=invoke('_handle_assist_panel_create_batch',{'text':'02 13 24 2×5','game':'539'})
    assert a['ok'] and b['ok']
    assert a['queue_path'] != b['queue_path']
    files=list((tmp_path/'assist-panel-batches').glob('*.json'))
    assert len(files)==2
    assert {json.loads(p.read_text(encoding='utf-8'))['source_text'] for p in files}=={
        '01 12 23 2×1','02 13 24 2×5'}


def test_preview_has_complete_groups_amounts_and_explicit_game(tmp_path,monkeypatch):
    monkeypatch.setattr(app,'RUNS_DIR',tmp_path)
    result=invoke('_handle_assist_panel_create_batch',{'text':'01 × 12 × 23 33 2×3 3×1','game':'六合'})
    candidate=result['valid_candidates'][0]
    assert candidate['columns']==[[1],[12],[23,33]]
    assert candidate['amounts']=={'2':300,'3':100}
    assert candidate['game']=='六合'


def test_car_preview_honestly_reports_unmapped_fill(tmp_path,monkeypatch):
    monkeypatch.setattr(app,'RUNS_DIR',tmp_path)
    result=invoke('_handle_assist_panel_create_batch',{'text':'01 12 各半車','game':'539'})
    assert len(result['valid_candidates'])==2
    for c in result['valid_candidates']:
        assert c['fill_supported'] is False
        assert c['fill_unsupported_reason']


def test_same_scope_continuation_not_car_and_keeps_source():
    text='01 12 23 2×3\n3×1'
    queue=build_batch_mock_queue(text,game='539')
    candidates=queue['preprocessing']['valid_candidates']
    assert len(candidates)==1
    assert candidates[0]['result']['bets']=={'2':{'unit':3,'money':300},'3':{'unit':1,'money':100}}
    assert candidates[0]['original_lines']==text.splitlines()


def test_intentional_duplicates_and_standalone_car_remain_legal():
    queue=build_batch_mock_queue('01 12 23 2×1\n01 12 23 2×1\n\n3×5',game='539')
    candidates=queue['preprocessing']['valid_candidates']
    assert len(candidates)==3
    assert candidates[2]['result']['type']=='car'


def test_column_readback_cannot_accept_equal_lengths_with_wrong_numbers(monkeypatch):
    from betguard.webfill import zhu_peng_fill as z
    monkeypatch.setattr(z,'_detect_column_slot_count',lambda p:7)
    monkeypatch.setattr(z,'click_number',lambda *a:None)
    monkeypatch.setattr(z,'click_zhu_column',lambda *a:None)
    monkeypatch.setattr(z,'readback_zhu_data',lambda p:[1,1])
    monkeypatch.setattr(z,'set_pengbet_amounts',lambda *a:None)
    monkeypatch.setattr(z,'readback_pengbet_amounts',lambda p:[{'pengValue':'100','domValue':'100','disabled':False}])
    page=Mock()
    page.evaluate.return_value='["09"]'
    result=z.execute_zhu_peng_plan(page,{'numbers':[[1],[12]],'amounts':{'二星':100}})
    assert result['ok'] is False


def test_normal_extra_selected_number_is_not_success(monkeypatch):
    from betguard.webfill import web_assist_session as w
    worker=w._AssistWorker()
    worker._page=Mock()
    worker.state=w.READY_CHECKED
    worker.numbers=[1,12,23]
    worker.stars=[2]
    worker.amounts={'2':100}
    worker.game='539'
    monkeypatch.setattr('betguard.webfill.fill_readback.verify_game',lambda *a:True)
    worker._collect_diagnostics=lambda:{}
    monkeypatch.setattr(w,'_fast_select_numbers_knockout',lambda *a:None)
    monkeypatch.setattr(w,'_fill_amounts_on_b03',lambda *a:[{'star':2,'executed':True,'verified':True,'actual_amount':'100'}])
    monkeypatch.setattr(w,'_count_selected_numbers',lambda *a:3)
    monkeypatch.setattr(w,'_get_selected_numbers',lambda *a:{'01','12','23','39'})
    outcome=w._CommandResult()
    worker._handle_execute_fill(outcome)
    assert outcome.result['ok'] is False


@pytest.mark.parametrize('raw',[
    '01 12 23 2×5\n2×0.5',
    '01 12 23 2×0',
    '01 12 23 二星',
    '01 × 12 × ?尾 2×1',
    '01 12 各車',
    '01 12 23\n04 15 26\n全部同上',
])
def test_unclear_scope_or_value_keeps_raw_for_review(raw):
    queue=build_batch_mock_queue(raw,game='539')
    assert queue['preprocessing']['original_text']==raw
    assert queue['preprocessing']['invalid_fragments']


@pytest.mark.parametrize('game,number,ok',[('539',39,True),('539',40,False),('539',49,False),('六合',39,True),('六合',40,True),('六合',49,True)])
def test_game_boundaries_reparse(game,number,ok):
    result=reparse_text(f'01 12 {number} 2×1',game=game)
    assert result['ok'] is ok
    if ok:
        assert result['game']==game


def test_operation_guard_explicit_refill_and_busy(tmp_path):
    from betguard.webfill.fill_operation import FillOperation
    data={'queue_path':'synthetic.json','item_index':1}
    with FillOperation(data,tmp_path):
        with pytest.raises(ValueError,match='正在填入'):
            with FillOperation({**data,'item_index':2},tmp_path): pass
    with pytest.raises(ValueError,match='未重複'):
        with FillOperation(data,tmp_path): pass
    refill={**data,'refill':True,'operation_id':'1db7d922-48bd-4c62-9915-3269713d0ab7'}
    with FillOperation(refill,tmp_path): pass
    with pytest.raises(ValueError):
        with FillOperation(refill,tmp_path): pass
    # A deliberately identical bet at another physical item is not deduped.
    with FillOperation({**data,'item_index':2},tmp_path): pass


def test_timeout_cannot_enqueue_an_automatic_retry():
    from betguard.webfill.web_assist_session import _AssistWorker
    import threading
    worker=_AssistWorker()
    waiting=threading.Event()
    started=threading.Event()
    calls=[]
    def slow(cmd,payload,result):
        calls.append(cmd); started.set(); waiting.wait(2); result.set({'ok':True})
    worker._process=slow
    worker.start()
    try:
        first=worker.dispatch('synthetic',{},timeout=0.02)
        assert started.wait(1) and first['ok'] is False
        second=worker.dispatch('synthetic',{},timeout=0.02)
        assert second['ok'] is False and len(calls)==1
    finally:
        waiting.set();worker._running=False;worker.join(timeout=3)


def test_bad_previous_fragment_does_not_abort_entire_batch():
    raw="01 × ? 2×?\n3×1\n\n02 13 24 2×5"
    queue=build_batch_mock_queue(raw,game="539")
    assert queue["preprocessing"]["invalid_fragments"]
    assert any(c["result"].get("numbers")==[2,13,24] for c in queue["preprocessing"]["valid_candidates"])
    assert queue["preprocessing"]["original_text"]==raw


def test_failed_write_stops_but_allows_later_explicit_operation(monkeypatch):
    from betguard.webfill import web_assist_session as w
    worker=w._AssistWorker()
    worker._page=Mock()
    worker.state=w.READY_CHECKED
    worker.numbers=[1,12,23]
    worker.game="539"
    worker.amounts={"2":100}
    worker._collect_diagnostics=lambda:{}
    monkeypatch.setattr("betguard.webfill.fill_readback.verify_game",lambda *a:True)
    monkeypatch.setattr(w,"_fast_select_numbers_knockout",lambda *a:None)
    calls=[]
    def failed(*args):
        calls.append(True)
        raise ConnectionError("synthetic disconnect after number selection")
    monkeypatch.setattr(w,"_fill_amounts_on_b03",failed)
    result=w._CommandResult()
    worker._handle_execute_fill(result)
    assert result.result["ok"] is False
    assert len(calls)==1
    assert worker.state==w.BROWSER_IDLE
