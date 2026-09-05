"""Synthetic UI service tests; no models, no customer truth, no real timing."""
import json
from pathlib import Path
import pytest
from PIL import Image
from betguard.vision.region_review import RegionReviewStore


@pytest.fixture
def store(tmp_path):
    path = tmp_path/'image.png'
    Image.new('RGB', (200, 100), 'white').save(path)
    return RegionReviewStore(tmp_path/'runs/region-assisted-review-dataset-v1', path)


def action(store, state, op, **payload):
    return store.update(state['session_id'], state['revision'], op, **payload)


def start(store):
    return store.start('539', initial_text='01 07 19 2×5\n01 07 19 3×0.5')


def test_initial_region_not_authority(store):
    s=start(store); r=s['regions'][0]
    assert r['bbox']==[0,0,200,100]
    assert r['initial_text']==r['raw_transcription']=='01 07 19 2×5\n01 07 19 3×0.5'
    assert not r['region_confirmed']
    assert not store.assemble(s)['ok']


@pytest.mark.parametrize('axis', ['horizontal','vertical'])
def test_split_blank_children_and_audited_original(store,axis):
    s=start(store); old=s['regions'][0]['id']
    s=action(store,s,'split',region_id=old,axis=axis)
    assert len(s['regions'])==2
    assert all(not r['raw_transcription'] and not r['region_confirmed'] for r in s['regions'])
    assert s['retired_regions'][0]['initial_text']
    boxes=[r['bbox'] for r in s['regions']]
    assert boxes==([[0,0,200,50],[0,50,200,50]] if axis=='horizontal' else [[0,0,100,100],[100,0,100,100]])


def test_add_move_resize_delete_revisions(store):
    s=start(store); sid=s['session_id']
    s=action(store,s,'add',bbox=[10,10,20,30]); rid=s['regions'][-1]['id']
    s=action(store,s,'bbox',region_id=rid,bbox=[20,20,40,50])
    assert s['regions'][-1]['bbox']==[20,20,40,50]
    s=action(store,s,'delete',region_id=rid)
    assert len(s['regions'])==1 and len(s['retired_regions'])==1
    assert len(list((store.root/store.image_sha/sid).glob('revision-*.json')))==4
    assert s['metrics']['add']==s['metrics']['delete']==s['metrics']['bbox_adjustments']==1


def test_adjacent_merge_and_nonadjacent_rejection(store):
    s=start(store);s=action(store,s,'split',region_id=s['regions'][0]['id'],axis='vertical')
    ids=[r['id'] for r in s['regions']]
    s=action(store,s,'merge',region_ids=ids)
    assert len(s['regions'])==1 and s['regions'][0]['bbox']==[0,0,200,100]
    s=action(store,s,'bbox',region_id=s['regions'][0]['id'],bbox=[0,0,20,20])
    s=action(store,s,'add',bbox=[100,50,20,20])
    with pytest.raises(ValueError,match='相鄰'):
        action(store,s,'merge',region_ids=[r['id'] for r in s['regions']])


@pytest.mark.parametrize('op,payload',[('text',{'text':'01 07 19 2×0.5'}),('bbox',{'bbox':[1,1,180,90]}),('cancelled',{'cancelled':True})])
def test_edits_reset_confirmation(store,op,payload):
    s=start(store);rid=s['regions'][0]['id']
    s=action(store,s,'confirm',region_id=rid);assert s['regions'][0]['region_confirmed']
    s=action(store,s,op,region_id=rid,**payload)
    assert not s['regions'][0]['region_confirmed']
    assert s['regions'][0]['verified_at'] is None


def test_reorder_resets_all_and_order_assembly(store):
    s=start(store);s=action(store,s,'add',bbox=[0,0,20,20]);ids=[r['id'] for r in s['regions']]
    s=action(store,s,'text',region_id=ids[1],text='02 08 20 2×1')
    for rid in ids:s=action(store,s,'confirm',region_id=rid)
    s=action(store,s,'reorder',region_ids=ids[::-1])
    assert not any(r['region_confirmed'] for r in s['regions'])
    assert not store.assemble(s)['ok']
    for rid in ids:s=action(store,s,'confirm',region_id=rid)
    result=store.assemble(s)
    assert result['ok'] and result['text']=='02 08 20 2×1\n\n01 07 19 2×5\n01 07 19 3×0.5'
    assert result['line_regions'][0]['region_id']==ids[1]


def test_cancelled_preserved_excluded_restore_unconfirmed(store):
    s=start(store);s=action(store,s,'add',bbox=[0,0,20,20]);a,b=[r['id'] for r in s['regions']]
    s=action(store,s,'text',region_id=b,text='? crossed out')
    s=action(store,s,'cancelled',region_id=b,cancelled=True)
    for rid in [a,b]:s=action(store,s,'confirm',region_id=rid)
    assert store.assemble(s)['text']=='01 07 19 2×5\n01 07 19 3×0.5'
    saved=json.loads(sorted((store.root/store.image_sha/s['session_id']).glob('revision-*.json'))[-1].read_text(encoding='utf-8'))
    assert saved['regions'][1]['human_verified_region_text']=='? crossed out'
    assert saved['regions'][1]['cancelled'] and saved['regions'][1]['human_verified']
    s=action(store,s,'cancelled',region_id=b,cancelled=False)
    assert not store.assemble(s)['ok']


def test_parser_failure_maps_roi_and_never_partial_output(store):
    s=start(store);s=action(store,s,'add',bbox=[0,0,20,20]);a,b=[r['id'] for r in s['regions']]
    s=action(store,s,'confirm',region_id=a)
    s=action(store,s,'text',region_id=b,text='03 × ?尾')
    s=action(store,s,'confirm',region_id=b)
    assert not s['regions'][1]['region_confirmed']
    assert s['issues'][0]['region_id']==b
    result=store.assemble(s)
    assert not result['ok'] and 'text' not in result


def test_stale_revision_and_bbox_fail_closed(store):
    s=start(store);rid=s['regions'][0]['id'];action(store,s,'text',region_id=rid,text='01 02 2×1')
    with pytest.raises(ValueError,match='更新'):action(store,s,'confirm',region_id=rid)
    for box in [[-1,0,2,2],[0,0,201,100],[0,0,0,1],[0,0,1.2,5]]:
        current=store.load(s['session_id'])
        with pytest.raises(ValueError):action(store,current,'add',bbox=box)


def test_game_change_resets_and_revalidates(store):
    s=store.start('六合',initial_text='01 49 2×1');rid=s['regions'][0]['id']
    s=action(store,s,'confirm',region_id=rid)
    s=action(store,s,'game',game='539')
    s=action(store,s,'confirm',region_id=rid)
    assert not s['regions'][0]['region_confirmed']
    assert s['game']=='539' and s['issues']


def test_dataset_ignored_and_no_unsafe_dependencies():
    root=Path(__file__).resolve().parents[1]
    import subprocess
    result=subprocess.run(['git','check-ignore','runs/region-assisted-review-dataset-v1/image/crop.png'],cwd=root,capture_output=True)
    assert result.returncode==0
    code=(root/'src/betguard/vision/region_review.py').read_text(encoding='utf-8')
    assert all(x not in code for x in ['requests.', 'transcribe_with_', 'create_batch', 'execute_fill'])


def test_http_region_edit_is_not_batch_creation(tmp_path,monkeypatch):
    import http.client
    from types import SimpleNamespace
    from tests.test_image_text_assisted_fill import _running_app
    from betguard.webui import app
    from betguard.vision import image_intake
    p=tmp_path/'synthetic.png';Image.new('RGB',(200,100),'white').save(p)
    monkeypatch.setattr(app,'RUNS_DIR',tmp_path/'runs')
    monkeypatch.setattr(image_intake,'get_metadata',lambda _:SimpleNamespace(storage_path=p,is_expired=lambda:False))
    with _running_app() as port:
        def post(body):
            conn=http.client.HTTPConnection('127.0.0.1',port)
            try:
                conn.request('POST','/api/vision/v1/region-review',json.dumps(body),{'Content-Type':'application/json'})
                response=conn.getresponse();return response.status,json.loads(response.read())
            finally:conn.close()
        code,data=post({'image_id':'test','operation':'start','game':'539','initial_text':'01 02 2×5'})
        assert code==200;s=data['state']
        assert 'source_image_reference' not in s and not data['auto_submit']
        code,data=post({'image_id':'test','operation':'confirm','session_id':s['session_id'],'revision':s['revision'],
                        'payload':{'region_id':s['regions'][0]['id']}})
        assert code==200 and data['state']['regions'][0]['human_verified']
        assert not list((tmp_path/'runs').rglob('batch_*.json'))
        assert post({'image_id':'test','operation':'load','session_id':'../../outside'})[0]==400


def test_private_source_survives_upload_expiry(store):
    s=start(store)
    private=Path(s['source_image_reference'])
    assert private!=store.image_path and private.read_bytes()==store.image_path.read_bytes()
    old=private.read_bytes();s=action(store,s,'bbox',region_id=s['regions'][0]['id'],bbox=[5,5,30,30])
    assert private.read_bytes()==old and store.image_path.read_bytes()==old


def test_invalid_legacy_inline_multiplier_not_silently_rewritten(store):
    s=store.start('539',initial_text='01 07 19 2×5 3×0.5')
    s=action(store,s,'confirm',region_id=s['regions'][0]['id'])
    assert not s['regions'][0]['region_confirmed']
    assert s['regions'][0]['raw_transcription']=='01 07 19 2×5 3×0.5'


def test_failed_revision_publication_keeps_previous_complete_state(store,monkeypatch):
    import betguard.vision.region_review as module
    s=start(store);sid=s['session_id']
    def fail(*args):raise OSError('simulated publication failure')
    monkeypatch.setattr(module.os,'link',fail)
    with pytest.raises(OSError):action(store,s,'text',region_id=s['regions'][0]['id'],text='01 02 2×1')
    assert store.load(sid)==s
    assert not list((store.root/store.image_sha/sid).glob('.pending-*'))


def test_crop_hash_changes_only_with_pixels_or_dimensions(store):
    s=start(store);rid=s['regions'][0]['id'];initial=s['regions'][0]['crop_sha256']
    s=action(store,s,'text',region_id=rid,text='01 07 19 2×1')
    assert s['regions'][0]['crop_sha256']==initial
    s=action(store,s,'bbox',region_id=rid,bbox=[0,0,100,100])
    assert s['regions'][0]['crop_sha256']!=initial
