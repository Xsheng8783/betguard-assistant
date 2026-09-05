"""Synthetic contract tests; no image/model or human dataset mutation."""
from copy import deepcopy
import pytest


def record(id='A', groups=None, rules=None, **extra):
    return dict(line_id=id, review_action='confirmed', number_groups=groups or [['01','07','19']],
                multiplier_rules=rules if rules is not None else [{'categories':['2'],'value':'5'}, {'categories':['3'],'value':'0.5'}], **extra)


def truth(*records, **extra):
    return dict(review_status='reviewed', lines=list(records), cancelled_bets=[], **extra)


def render(t, game='539'):
    from betguard.vision.lossless_representation import render_lossless_human_truth
    return render_lossless_human_truth(t, game=game)


def proof(t, r, game='539'):
    from betguard.vision.lossless_representation import prove_lossless_round_trip
    return prove_lossless_round_trip(t, r, game=game)


def test_normal_multi_rule_scopes_and_truth_immutable():
    t=truth(record()); saved=deepcopy(t); r=render(t)
    assert r['ok']
    assert r['human_verified_betguard_text'].splitlines()==['01 07 19 2 × 5','01 07 19 3 × 0.5']
    p=proof(t,r)
    assert p['exact'] and p['SEMANTIC_ROUND_TRIP_EXACT']
    assert p['normalized_semantics']==r['source_semantic_result']
    assert p['normalized_semantics']['records'][0]['multiplier_rule_map']=={'2':'5','3':'0.5'}
    assert t==saved


def test_column_multi_rule_never_flattens_unequal_heights():
    t=truth(record(groups=[['01'],['07','17'],['09','19','29']],layout_hint='column_bet'))
    r=render(t); assert r['ok'] and proof(t,r)['exact']
    assert all(line.startswith('01 × 07 17 × 09 19 29 ') for line in r['human_verified_betguard_text'].splitlines())
    assert r['source_semantic_result']['physical_record_count']==1
    assert r['source_semantic_result']['expanded_text_line_count']==2


def test_shared_rule_explicit_scope():
    t=truth(record('A',rules=[]),record('B',groups=[['02','08','20']],rules=[]),
            shared_multiplier_rules=[{'applies_to_line_ids':['A','B'],'categories':['2','3'],'value':'0.1','scope':'explicit'}])
    r=render(t); assert r['ok'] and proof(t,r)['exact']
    assert len(r['human_verified_betguard_text'].splitlines())==2
    assert r['source_semantic_result']['records'][0]['rule_scopes'][0]['applies_to_line_ids']==['A','B']


@pytest.mark.parametrize('shared',[
    {'categories':['2'],'value':'1'},
    {'applies_to_line_ids':[],'categories':['2'],'value':'1'},
    {'applies_to_line_ids':['MISSING'],'categories':['2'],'value':'1'},
    {'applies_to_line_ids':['A'],'categories':['2'],'value':'1','boundary_uncertain':True},
])
def test_unclear_shared_scope_fail_closed(shared):
    assert not render(truth(record(),shared_multiplier_rules=[shared]))['ok']


@pytest.mark.parametrize('special,groups', [('7尾',[['03'],['16']]), ('0尾',[['01'],['02']])])
def test_tail_preserves_digit_separate_from_normal_numbers(special,groups):
    t=truth(record(groups=groups,rules=[{'categories':['2','3'],'value':'0.5'}],special_play=special))
    r=render(t); assert r['ok'] and proof(t,r)['exact']
    s=r['source_semantic_result']['records'][0]
    assert s['number_groups']==groups
    assert s['special_play']['digit']==special[0]


@pytest.mark.parametrize('play,groups', [('半車',[['11']]),('各半車',[['11','33']]),('各0.5車',[['11','33']]),('1車',[['11']])])
def test_car_each_half_car_lossless(play,groups):
    t=truth(record(groups=groups,rules=[],play_type='car_bet',play_text=play))
    r=render(t); assert r['ok'] and proof(t,r)['exact']
    assert r['source_semantic_result']['records'][0]['special_play']['kind']=='car'


def test_continuation_proof_has_explicit_source_and_scope():
    t=truth(record('A'),record('B',groups=[['02','08','20']],continuation=True,
            continuation_from='A',continuation_scope=['2','3']))
    r=render(t); assert r['ok'] and proof(t,r)['exact']
    assert r['source_semantic_result']['records'][1]['continuation']=={'source_line_id':'A','categories':['2','3']}
    assert all('01 07 19' in l or '02 08 20' in l for l in r['human_verified_betguard_text'].splitlines())


def test_missing_continuation_scope_and_orphan_rejected():
    assert not render(truth(record(continuation=True)))['ok']
    t=truth(record()); r=render(t)
    r['human_verified_betguard_text']='3×5'
    assert not proof(t,r)['exact']
    orphan=record(); orphan['number_groups']=[]
    assert not render(truth(orphan))['ok']
    # This was historically legal single-number car shorthand. Do not break it
    # to enforce a stronger, explicitly-scoped human-truth representation.
    from betguard.vision.image_text_acceptance import validate_with_existing_parser
    assert validate_with_existing_parser('3×5',game='539')['parser_normalized_result']['bets'][0]['result']['type']=='car'


def test_cancelled_audit_preserved_and_tamper_detected():
    cancelled={'line_id':'C','review_action':'confirmed','cancelled':True,'raw_text':'09 29 2×5','provenance':{'human':'yes'}}
    t=truth(record(),cancelled); r=render(t)
    assert r['ok'] and proof(t,r)['exact']
    assert '09 29' not in r['human_verified_betguard_text']
    assert r['source_semantic_result']['cancelled_records']==[cancelled]
    r['source_semantic_result']['cancelled_records']=[]
    assert not proof(t,r)['exact']


@pytest.mark.parametrize('mutation',['text','game','id','scope','continuation'])
def test_contract_tamper_cannot_pass(mutation):
    t=truth(record()); r=render(t)
    if mutation=='text': r['human_verified_betguard_text']=r['human_verified_betguard_text'].replace('0.5','5')
    elif mutation=='game': r['game']='六合'
    elif mutation=='id': r['bindings'][0]['source_line_id']='B'
    elif mutation=='scope': r['bindings'][0]['categories']=['4']
    else: r['source_semantic_result']['records'][0]['continuation']={'source_line_id':'Z','categories':['2']}
    assert not proof(t,r)['exact']


@pytest.mark.parametrize('game,n,ok',[('539','39',True),('539','40',False),('539','49',False),('六合','39',True),('六合','40',True),('六合','49',True)])
def test_game_boundaries(game,n,ok):
    t=truth(record(groups=[['01',n]],rules=[{'categories':['2'],'value':'0.1'}]))
    r=render(t,game); assert r['ok'] is ok
    if ok: assert proof(t,r,game)['exact']


@pytest.mark.parametrize('rules',[[],[{'categories':['2'],'value':'0'}],[{'categories':['2','9'],'value':'1'}],
    [{'categories':['2'],'value':'1','parse_status':'unresolved'}]])
def test_missing_zero_or_unresolved_rule_rejected(rules):
    assert not render(truth(record(rules=rules)))['ok']


def test_unsupported_special_and_conflicting_truth_stay_rejected():
    assert not render(truth(record(special_play='特尾917')))['ok']
    assert not render(truth(record(uncertain=True,uncertain_reason='multiplier_conflict')))['ok']
    assert not render(truth(record(),game='六合'),'539')['ok']


def test_explicit_human_raw_car_without_conflicting_fields():
    t=truth(record(groups=[['34']],rules=[],raw_text='34 x 0 . 5 車'))
    r=render(t); assert r['ok'] and proof(t,r)['exact']
    assert r['human_verified_betguard_text']=='34車0.5支'
    t['lines'][0]['multiplier_text']='4x0'
    assert not render(t)['ok']


def test_promoted_complete_literal_is_not_a_guessed_default():
    t=truth(record(rules=[{'parse_status':'unresolved_human_literal','rule_text':'23X0.5','categories':None,'value':None}]))
    r=render(t); assert r['ok'] and proof(t,r)['exact']
    t['lines'][0]['multiplier_rules'][0]['rule_text']='23X?'
    assert not render(t)['ok']


def test_liuhe_tail_existing_parser_gap_does_not_pass_as_exact():
    t=truth(record(groups=[['03'],['16']],rules=[{'categories':['2','3'],'value':'0.5'}],special_play='7尾'))
    r=render(t,'六合'); assert r['ok']
    # Existing parser expands tails to 39 even in 六合. Preserve behavior until
    # its game-specific special rules are approved; never call that lossless.
    assert not proof(t,r,'六合')['exact']


def test_shared_local_category_conflict_rejected():
    t=truth(record(),shared_multiplier_rules=[{'applies_to_line_ids':['A'],'categories':['2'],'value':'0.5'}])
    assert not render(t)['ok']


def test_proof_cannot_use_altered_truth_or_drop_special_metadata():
    t=truth(record(groups=[['11']],rules=[],play_type='car_bet',play_text='半車'))
    r=render(t); p=proof(t,r); assert p['exact'] and p['bindings']==r['bindings']
    r['source_semantic_result']['records'][0]['special_play']['half_car']=False
    assert not proof(t,r)['exact']


def test_existing_embedded_human_answer_proves_local_continuation_without_neighbor_guess():
    t=truth(record(continuation=True,human_answer={'confirmed':True,'continuation':True,'scope':'normal','multiplier':'2X5 3X0.5'},
        human_truth_provenance={'source_human_bet_id':'physical-alpha','projection':'verbatim_human_answer_with_deterministic_field_mapping'}))
    r=render(t); assert r['ok'] and proof(t,r)['exact']
    assert r['source_semantic_result']['records'][0]['continuation']['source_line_id']=='A'
    t['lines'][0]['human_answer']['multiplier']='2X0.5 3X5'
    assert not render(t)['ok']


def test_continuation_cannot_claim_different_parent_multiplier():
    t=truth(record('A',rules=[{'categories':['2'],'value':'1'}]),
            record('B',rules=[{'categories':['2'],'value':'5'}],continuation=True,continuation_from='A',continuation_scope=['2']))
    assert render(t)['reason']=='CONTINUATION_RULE_MAP_CONFLICT'


def test_backfill_stores_explicit_game_bindings_and_revision_without_mutating_truth(tmp_path,monkeypatch):
    import hashlib,json
    from betguard.vision import image_text_truth_backfill as migration
    t=truth(record()); original=deepcopy(t)
    image=tmp_path/'synthetic.bin'; image.write_bytes(b'synthetic-test-image')
    digest=hashlib.sha256(image.read_bytes()).hexdigest()
    source={'sample_id':'sample-901','kind':'structured_truth','truth':t,'image_path':image,
            'image_sha256':digest,'provenance':{'authority_kind':'persisted_complete_human_review'}}
    monkeypatch.setattr(migration,'discover_human_confirmed_sources',lambda _: {'eligible':[source],'rejected_unverified':[]})
    monkeypatch.setattr(migration,'_manifest',lambda _: {'sample-901':{'gt_schema_version':'539-semantic-gt-v1'}})
    destination=tmp_path/'acceptance'
    first=migration.backfill_existing_human_truth(tmp_path,acceptance_dataset_root=destination)
    second=migration.backfill_existing_human_truth(tmp_path,acceptance_dataset_root=destination)
    assert first['round_trip_exact_samples']==1 and second['dataset_unique_count_after']==1
    saved=json.loads((destination/'samples'/digest/'latest.json').read_text(encoding='utf-8'))
    assert saved['game']=='539' and saved['revision_number']==2
    assert t==original
    assert saved['source_semantic_result']['physical_record_count']==1
    assert saved['source_semantic_result']['expanded_text_line_count']==2


def test_cancelled_truth_contradiction_not_promoted():
    t=truth(record());t['cancelled_bets']=[{'human_bet_id':'C','cancelled':True,'human_answer':{'cancelled':False}}]
    assert not render(t)['ok']


@pytest.mark.parametrize('field',['numbers','amount','whole_document'])
def test_proof_detects_actual_parser_semantic_drift(monkeypatch,field):
    from betguard.vision import lossless_representation as contract
    t=truth(record());r=render(t)
    original=contract.validate_with_existing_parser
    def drift(text,*,game):
        result=deepcopy(original(text,game=game))
        if result['ok'] and (field!='whole_document' or '\n' in text):
            bet=result['parser_normalized_result']['bets'][0]['result']
            if field in {'numbers','whole_document'}: bet['numbers']=[1,8,19]
            else:
                bet['unit']=99
                for amount in bet.get('bets',{}).values(): amount['unit']=99
        return result
    monkeypatch.setattr(contract,'validate_with_existing_parser',drift)
    assert not proof(t,r)['exact']
