"""Versioned, deterministic human-truth text + provenance contract.

No new parser dialect. Physical identity, shared scope, continuation links and
cancellation live in a bound sidecar, not guessed from a flattened text string.
The proof requires the independent original truth, not merely a self-issued hash.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import re
from typing import Any

from betguard.game_rules import GAME_RULES
from betguard.expander import expand_tail
from betguard.vision.image_text_acceptance import validate_with_existing_parser
from betguard.vision.image_text_truth_backfill import (
    _actual_rule_map, _all_lines_human_confirmed, _decimal_text, _json_sha256,
    _number, _rules, _rules_from_text, _canonical_rule_map,
)

VERSION = 'betguard-lossless-representation-v1'


def _fail(reason: str, *, source_line_id=None) -> dict[str, Any]:
    return {'ok': False, 'reason': reason, 'source_line_id': source_line_id}


def _positive(value: Any) -> str:
    result = _decimal_text(value)
    # Existing validator.py rejects unit <= 0; this is not a new product rule.
    if Decimal(result) <= 0:
        raise ValueError('MULTIPLIER_MUST_BE_POSITIVE')
    return result


def _strict_rules(line: dict[str, Any]) -> list[dict[str, Any]]:
    given = line.get('multiplier_rules')
    if given is not None and (not isinstance(given, list) or any(not isinstance(r,dict) for r in given)):
        raise ValueError('MALFORMED_MULTIPLIER_RULES')
    for r in given or []:
        status=str(r.get('parse_status',''))
        # A promoted, complete human literal is deterministically parsed below;
        # this legacy marker says machine fields are absent, not that a value
        # should be guessed. Other unresolved records remain rejected.
        literal_only=(status=='unresolved_human_literal' and r.get('categories') is None
                      and r.get('value') is None and bool(str(r.get('rule_text') or '').strip()))
        if r.get('uncertain') or (status.startswith('unresolved') and not literal_only):
            raise ValueError('UNRESOLVED_MULTIPLIER_RULE')
        categories=r.get('categories')
        if categories is not None:
            if isinstance(categories,list):
                valid=all(str(c) in {'2','3','4','二','三','四'} for c in categories)
            else:
                valid=bool(re.fullmatch(r'[234二三四/,、\s]+',str(categories)))
            if not valid:
                raise ValueError('INVALID_MULTIPLIER_CATEGORIES')
    result=_rules(line)
    for r in result:
        r['value']=_positive(r['value'])
    _canonical_rule_map(result)
    # Two explicit structured/text statements must not disagree. Raw OCR fields
    # are never used to repair a human-confirmed multiplier.
    if given and str(line.get('multiplier_text') or '').strip():
        other=_rules({'multiplier_text':line['multiplier_text']})
        if _canonical_rule_map(other)!=_canonical_rule_map(result):
            raise ValueError('HUMAN_MULTIPLIER_CONFLICT')
    return result


def _car(line: dict[str, Any], groups: list[list[str]]) -> dict[str, Any] | None:
    play=str(line.get('special_play') or line.get('play_text') or '').strip()
    raw=str(line.get('human_raw_text') or line.get('raw_text') or '').strip()
    is_car=line.get('play_type')=='car_bet' or '車' in play or '車' in raw
    if not is_car:
        return None
    if len(groups)!=1:
        raise ValueError('CAR_REQUIRES_FLAT_NUMBER_GROUP')
    if line.get('multiplier_rules') or str(line.get('multiplier_text') or '').strip() not in {'','車'}:
        raise ValueError('CAR_AND_STAR_RULE_CONFLICT')
    # Only anchored, already-defined car literals. No implicit full-car default.
    literal=play or raw
    compact=re.sub(r'\s+','',literal)
    numbers=''.join(groups[0])
    if compact.startswith(numbers):
        compact=compact[len(numbers):]
        if len(groups[0])==1 and compact.startswith(('x','X','×')):
            compact=compact[1:]
    elif literal==raw and raw:
        m=re.fullmatch(r'(\d{1,2})\s*[xX×]\s*(\d+(?:\s*\.\s*\d+)?)\s*車',raw)
        if not m or groups!=[[_number(m[1])]]:
            raise ValueError('CAR_LITERAL_NUMBER_MISMATCH_OR_UNSUPPORTED')
        compact=m[2].replace(' ','')+'車'
    each=compact.startswith('各')
    suffix=compact[1:] if each else compact
    if suffix=='半車':
        units='0.5'; half=True
    else:
        m=re.fullmatch(r'(\d+(?:\.\d+)?)車',suffix)
        if not m:
            raise ValueError('UNSUPPORTED_CAR_LITERAL')
        units=_positive(m[1]); half=False
    if len(groups[0])>1 and not each:
        raise ValueError('MULTI_CAR_EACH_SCOPE_REQUIRED')
    return {'kind':'car','units':units,'each':each,'half_car':half,'literal':literal}


def render_lossless_human_truth(truth: dict[str, Any], *, game: str) -> dict[str, Any]:
    """Compile verified semantic records. This never changes ``truth``."""
    line_id=None
    try:
        if game not in {'539','六合'}:
            raise ValueError('EXPLICIT_GAME_REQUIRED')
        if truth.get('game') not in {None,'',game}:
            raise ValueError('HUMAN_GAME_CONFLICT')
        if truth.get('review_status')!='reviewed' or not _all_lines_human_confirmed(truth):
            raise ValueError('HUMAN_CONFIRMATION_INCOMPLETE')
        lines=deepcopy(truth['lines'])
        if any(l.get('cancelled') is not None and not isinstance(l.get('cancelled'),bool) for l in lines):
            raise ValueError('CANCELLED_STATE_UNRESOLVED')
        ids=[l.get('line_id') for l in lines]
        if any(not isinstance(i,str) or not i.strip() for i in ids) or len(ids)!=len(set(ids)):
            raise ValueError('UNIQUE_SOURCE_LINE_ID_REQUIRED')
        active={l['line_id']:l for l in lines if l.get('cancelled') is not True}
        physical_ids={i:str(l.get('physical_record_id') or l.get('human_truth_provenance',{}).get('source_human_bet_id') or i)
                      for i,l in active.items()}
        if len(set(physical_ids.values()))!=len(physical_ids):
            raise ValueError('DUPLICATE_PHYSICAL_RECORD_ID')
        shared=truth.get('shared_multiplier_rules') or []
        if isinstance(shared,dict): shared=[shared]
        shared_rules={i:[] for i in active}
        for index,item in enumerate(shared):
            targets=item.get('applies_to_line_ids')
            if (item.get('uncertain') or item.get('boundary_uncertain') or not isinstance(targets,list)
                or not targets or len(set(targets))!=len(targets) or any(i not in active for i in targets)):
                raise ValueError('SHARED_MULTIPLIER_SCOPE_UNRESOLVED')
            rr=_strict_rules({'multiplier_rules':item.get('multiplier_rules') or [item]})
            if not rr: raise ValueError('SHARED_MULTIPLIER_RULE_MISSING')
            for target in targets:
                for rule in rr:
                    shared_rules[target].append((rule,{'origin':f'shared-{index+1}','applies_to_line_ids':targets}))
        cancelled=[deepcopy(l) for l in lines if l.get('cancelled') is True]
        for c in truth.get('cancelled_bets',[]):
            if not isinstance(c,dict) or c.get('cancelled') is not True:
                raise ValueError('CANCELLED_METADATA_NOT_EXPLICIT')
            if isinstance(c.get('human_answer'),dict) and c['human_answer'].get('cancelled') is False:
                raise ValueError('CANCELLED_HUMAN_ANSWER_CONFLICT')
            cancelled.append(deepcopy(c))
        cancelled_ids=[c.get('physical_record_id') or c.get('human_bet_id') or c.get('line_id') for c in cancelled]
        if any(not i for i in cancelled_ids) or len(set(cancelled_ids))!=len(cancelled_ids) or set(cancelled_ids)&set(physical_ids.values()):
            raise ValueError('CANCELLED_RECORD_ID_CONFLICT')
        text_lines=[]; bindings=[]; records=[]
        for line_id,line in active.items():
            reason=str(line.get('uncertain_reason') or '').lower()
            if line.get('uncertain') and any(x in reason for x in ('conflict','unresolved','unclear_multiplier','unsupported_play')):
                raise ValueError('HUMAN_TRUTH_UNRESOLVED_OR_CONFLICTING')
            original_groups=line.get('number_groups')
            if not isinstance(original_groups,list) or not original_groups or any(not isinstance(g,list) or not g for g in original_groups):
                raise ValueError('EMPTY_OR_MALFORMED_NUMBER_GROUPS')
            groups=[[_number(n) for n in g] for g in original_groups]
            if any(int(n)>GAME_RULES[game].number_max for g in groups for n in g):
                raise ValueError('NUMBER_OUT_OF_GAME_RANGE')
            car=_car(line,groups)
            rules=[] if car else _strict_rules(line)
            scopes=[{'origin':line_id,'applies_to_line_ids':[line_id],**deepcopy(r)} for r in rules]
            for r,s in shared_rules[line_id]:
                rules.append(deepcopy(r)); scopes.append({**deepcopy(s),**deepcopy(r)})
            if car and scopes: raise ValueError('CAR_AND_STAR_RULE_CONFLICT')
            if not car and not rules: raise ValueError('MISSING_MULTIPLIER')
            rule_map=_canonical_rule_map(rules)
            # Repeated categories cannot silently collapse multiple executions.
            categories=[str(c) for r in rules for c in r['categories']]
            if len(categories)!=len(set(categories)):
                raise ValueError('DUPLICATE_MULTIPLIER_CATEGORY_SCOPE')
            continuation=None
            if line.get('continuation') or line.get('continuation_from'):
                parent=line.get('continuation_from'); scope=line.get('continuation_scope')
                answer=line.get('human_answer') or {}
                provenance=line.get('human_truth_provenance') or {}
                # Existing promoted Human Answer embeds its continuation and
                # complete multiplier in ONE physical answer, scope="normal".
                # This proves same-record continuation, NOT inheritance from a
                # neighboring bet. A naked continuation=True proves neither.
                local=(parent is None and answer.get('confirmed') is True
                       and answer.get('continuation') is True and answer.get('scope')=='normal'
                       and provenance.get('source_human_bet_id')
                       and provenance.get('projection')=='verbatim_human_answer_with_deterministic_field_mapping')
                if local:
                    if _canonical_rule_map(_rules_from_text(str(answer.get('multiplier') or '')))!=rule_map:
                        raise ValueError('CONTINUATION_MULTIPLIER_SCOPE_CONFLICT')
                    continuation={'source_line_id':line_id,'physical_record_id':physical_ids[line_id],
                                  'categories':list(rule_map),'kind':'within_confirmed_physical_record'}
                else:
                    if parent not in active or parent==line_id or ids.index(parent)>=ids.index(line_id):
                        raise ValueError('CONTINUATION_SOURCE_REQUIRED')
                    if not isinstance(scope,list) or set(map(str,scope))!=set(rule_map) or len(scope)!=len(rule_map):
                        raise ValueError('CONTINUATION_MULTIPLIER_SCOPE_REQUIRED')
                    source_record=next(r for r in records if r['source_line_id']==parent)
                    if any(source_record['multiplier_rule_map'].get(str(c))!=rule_map[str(c)] for c in scope):
                        raise ValueError('CONTINUATION_RULE_MAP_CONFLICT')
                    continuation={'source_line_id':parent,'categories':list(map(str,scope))}
            layout=str(line.get('layout_hint') or '')
            if layout in {'normal','normal_row'} and len(groups)>1:
                raise ValueError('HUMAN_LAYOUT_CONFLICT')
            kind='car' if car else ('column' if len(groups)>1 or layout in {'column','column_bet'} else 'normal')
            special=car or {'kind':'none'}
            special_raw=str(line.get('special_play') or line.get('play_text') or '').strip()
            body=' × '.join(' '.join(g) for g in groups) if kind=='column' else ' '.join(groups[0])
            if not car and special_raw:
                m=re.fullmatch(r'(\d)尾',special_raw)
                if not m: raise ValueError('UNSUPPORTED_SPECIAL_LITERAL:'+special_raw)
                special={'kind':'tail','digit':m[1],'literal':special_raw}
                kind='column';body=' × '.join([' '.join(g) for g in groups]+[special_raw])
            record={'physical_record_id':physical_ids[line_id],'source_line_id':line_id,'status':'active','game':game,
                    'type':kind,'number_groups':groups,'multiplier_rule_map':rule_map,'rule_scopes':scopes,
                    'special_play':special,'continuation':continuation}
            records.append(record)
            outputs=[]
            if car:
                outputs=[(' '.join(groups[0])+f" 各 {car['units']}車",[])] if car['each'] else [(groups[0][0]+f"車{car['units']}支",[])]
            else:
                outputs=[(body+' '+','.join(str(c) for c in r['categories'])+' × '+r['value'],[str(c) for c in r['categories']]) for r in rules]
            for text,cats in outputs:
                text_lines.append(text)
                bindings.append({'text_line':len(text_lines),'source_line_id':line_id,'physical_record_id':physical_ids[line_id],
                                 'categories':cats,'text_sha256':_json_sha256(text)})
        counts=truth.get('truth_counts') or {}
        if counts and (counts.get('human_records'),counts.get('active_bets'),counts.get('cancelled_bets'))!=(len(records)+len(cancelled),len(records),len(cancelled)):
            raise ValueError('DECLARED_TRUTH_COUNTS_MISMATCH')
        semantics={'schema_version':VERSION,'game':game,'records':records,'cancelled_records':cancelled,
                   'physical_record_count':len(records)+len(cancelled),'physical_active_record_count':len(records),
                   'active_bet_count':len(records),'cancelled_bet_count':len(cancelled),'expanded_text_line_count':len(text_lines)}
        return {'ok':True,'schema_version':VERSION,'game':game,'source_truth_sha256':_json_sha256(truth),
                'human_verified_betguard_text':'\n'.join(text_lines),'bindings':bindings,'source_semantic_result':semantics,
                'identity_policy':'source IDs carried by verified sidecar, never inferred from text',
                'cancelled_text_policy':'audit_metadata_only_no_active_syntax'}
    except (ValueError,TypeError,KeyError) as exc:
        return _fail(str(exc),source_line_id=line_id)


def prove_lossless_round_trip(truth: dict[str, Any], rendered: dict[str, Any], *, game: str) -> dict[str, Any]:
    """Reparse every expansion, reconstruct records and deep-compare semantics.

    Identity/scope/cancellation are proven through the immutable source binding;
    number, type, special expansion and amounts must independently match parser.
    """
    def failure(reason, **details):
        return {'exact':False,'SEMANTIC_ROUND_TRIP_EXACT':False,'reason':reason,**details}
    fresh=render_lossless_human_truth(truth,game=game)
    if not fresh.get('ok'):
        return failure(fresh['reason'])
    if fresh!=rendered:
        return failure('SOURCE_TEXT_OR_BINDING_MISMATCH')
    text=rendered['human_verified_betguard_text']
    validation=validate_with_existing_parser(text,game=game)
    if not validation.get('ok'):
        return failure('EXISTING_PARSER_REJECTED_RENDERED_TEXT',parser_errors=validation.get('parser_errors',[]))
    expected=rendered['source_semantic_result']
    normalized=deepcopy(expected)
    by_id={r['source_line_id']:r for r in normalized['records']}
    for r in normalized['records']:
        r['multiplier_rule_map']={}
    parsed_count=0;errors=[];independent_results=[]
    for binding,line in zip(rendered['bindings'],text.splitlines()):
        p=validate_with_existing_parser(line,game=game)
        if not p.get('ok'):
            return failure('EXPANSION_NOT_STANDALONE',text_line=binding['text_line'])
        bets=p['parser_normalized_result']['bets'];parsed_count+=len(bets)
        independent_results.extend(b['result'] for b in bets)
        r=by_id[binding['source_line_id']];special=r['special_play']
        if special['kind']=='car':
            numbers=[]
            for b in bets:
                result=b['result'];numbers.append(f"{result.get('number'):02d}" if isinstance(result.get('number'),int) else '?')
                if result.get('type')!='car' or _decimal_text(result.get('car_units'))!=special['units'] or result.get('game')!=game:
                    errors.append({'text_line':binding['text_line'],'field':'car_units_type_game'})
            if [numbers]!=r['number_groups']:
                errors.append({'text_line':binding['text_line'],'field':'car_number_groups'})
            r['number_groups']=[numbers]
            continue
        if len(bets)!=1:
            return failure('EXPANSION_PARSER_COUNT_MISMATCH',text_line=binding['text_line'])
        result=bets[0]['result']
        groups=result.get('columns') if result.get('type')=='column' else [result.get('numbers',[])]
        source_groups=[[int(n) for n in g] for g in r['number_groups']]
        if special['kind']=='tail':
            source_groups=source_groups+[expand_tail(int(special['digit']),max_number=GAME_RULES[game].number_max)]
        if groups!=source_groups or result.get('type')!=r['type'] or result.get('game')!=game:
            errors.append({'text_line':binding['text_line'],'field':'number_groups_type_game'})
        decoded_groups=groups[:-1] if special['kind']=='tail' else groups
        r['number_groups']=[[f'{n:02d}' for n in g] for g in decoded_groups]
        r['type']=result.get('type');r['game']=result.get('game')
        actual=_actual_rule_map(result)
        if set(actual)!=set(binding['categories']):
            errors.append({'text_line':binding['text_line'],'field':'multiplier_scope'})
        for c,v in actual.items():
            if c in r['multiplier_rule_map']:
                errors.append({'text_line':binding['text_line'],'field':'duplicate_rule'})
            r['multiplier_rule_map'][c]=v
    if parsed_count!=len(validation['parser_normalized_result']['bets']):
        errors.append({'field':'whole_text_cross_record_merge_or_split'})
    if independent_results!=[b['result'] for b in validation['parser_normalized_result']['bets']]:
        errors.append({'field':'whole_text_semantics_differ_from_independent_expansions'})
    if errors or normalized!=expected:
        return failure('SEMANTIC_ROUND_TRIP_MISMATCH',differences=errors,normalized_semantics=normalized)
    return {'exact':True,'SEMANTIC_ROUND_TRIP_EXACT':True,'schema_version':VERSION,
            'game':game,'source_truth_sha256':rendered['source_truth_sha256'],'bindings':deepcopy(rendered['bindings']),
            'normalized_semantics':normalized,'expected_semantic_sha256':_json_sha256(expected),
            'parser_normalized_result':validation['parser_normalized_result'],
            'parser_normalized_sha256':_json_sha256(validation['parser_normalized_result']),
            'cancelled_metadata_exact':True,'identity_scope_proof':'source-bound sidecar + independently parsed text',
            'parser_candidate_count':parsed_count,'differences':[]}
