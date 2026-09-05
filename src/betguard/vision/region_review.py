"""Optional local ROI editing, using semantic ROI fields and production preflight.

No inference, executable bets or new betting grammar. Immutable session snapshots
retain retired regions too. Human verification is an explicit action, never a
consequence of successful parsing. Text-only identity is not a lossless proof.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from uuid import uuid4

from PIL import Image
from .semantic_roi import SemanticRoiGroup, image_sha256, VERIFICATION_NEEDS_REVIEW, VERIFICATION_VERIFIED
from .roi_annotations import ROI_SCHEMA_VERSION
from .service import preflight_image_text

_LOCK = threading.RLock()


def _now():
    return datetime.now(timezone.utc).isoformat()


class RegionReviewStore:
    def __init__(self, root, image_path):
        self.root = Path(root)
        self.image_path = Path(image_path)
        self.image_sha = image_sha256(str(self.image_path))
        with Image.open(self.image_path) as image:
            self.width, self.height = image.size

    def _box(self, bbox):
        if not isinstance(bbox, list) or len(bbox)!=4 or any(type(n) is not int for n in bbox):
            raise ValueError('區域座標必須是原圖整數像素')
        x,y,w,h=bbox
        if x<0 or y<0 or w<1 or h<1 or x+w>self.width or y+h>self.height:
            raise ValueError('區域超出原圖或大小無效')
        return list(bbox)

    def _region(self, bbox, text='', source='manual'):
        group=SemanticRoiGroup(id=uuid4().hex, bbox=self._box(bbox), raw_transcription=text).to_dict()
        group.update(region_id=group['id'], initial_text=text, initial_proposal_source=source,
                     region_revision=0, cancelled=False, region_confirmed=False, verified_at=None,
                     human_verified=False, human_verified_region_text=None)
        return group

    def _directory(self, sid):
        if not isinstance(sid,str) or not re.fullmatch('[a-f0-9]{32}',sid):
            raise ValueError('檢查工作不存在')
        return self.root/self.image_sha/sid

    def load(self, sid):
        files=sorted(self._directory(sid).glob('revision-*.json'))
        if not files:raise ValueError('檢查工作不存在')
        state=json.loads(files[-1].read_text(encoding='utf-8'))
        if state['source_image_sha256']!=self.image_sha:raise ValueError('圖片已變更')
        return state

    def _reset(self, region):
        region.update(region_confirmed=False, verification_status=VERIFICATION_NEEDS_REVIEW,
                      verified_at=None, human_verified=False, human_verified_region_text=None)

    def _save(self, state, event, touched):
        state['revision']+=1
        state['last_event']={'operation':event,'at':_now(),'region_ids':list(touched)}
        for index,r in enumerate(state['regions'],1):
            if r.get('reading_order',index)!=index:
                self._reset(r)
                touched.add(r['id'])
            r['reading_order']=index
            if r['id'] in touched:r['region_revision']+=1
            r['source_image_sha256']=self.image_sha
            if not r.get('crop_sha256'):
                with Image.open(self.image_path) as im:
                    x,y,w,h=r['bbox'];crop=im.crop((x,y,x+w,y+h)).convert('RGB')
                    # Canonical RGB pixels + dimensions, independent of encoding.
                    r['crop_sha256']=hashlib.sha256(f'RGB:{w}:{h}:'.encode()+crop.tobytes()).hexdigest()
            r['crop_hash_format']='RGB:width:height: followed by raw RGB pixels'
        state['last_event']['region_ids']=sorted(touched)
        m=state['metrics']
        m['final_region_count']=len(state['regions'])
        m['confirmed_active_count']=sum(r['region_confirmed'] and not r['cancelled'] for r in state['regions'])
        m['cancelled_count']=sum(r['cancelled'] for r in state['regions'])
        m['parser_unresolved_count']=len(state.get('issues',[]))
        directory=self._directory(state['session_id']);directory.mkdir(parents=True,exist_ok=True)
        # Publish only a complete snapshot. Hard-link creation is atomic and
        # exclusive, even if another local process tries the same revision.
        fd,temporary=tempfile.mkstemp(prefix='.pending-',dir=directory)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as f:
                json.dump(state,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
            os.link(temporary,directory/f"revision-{state['revision']:06d}.json")
        finally:
            os.unlink(temporary)
        return deepcopy(state)

    def start(self, game, *, initial_text=''):
        if game not in {'539','六合'}:raise ValueError('請明確選擇 539 或六合')
        with _LOCK:
            # Uploads expire; keep an exact private source copy for future bbox
            # training. Never annotate/alter the original upload itself.
            destination=self.root/self.image_sha/('source'+self.image_path.suffix.lower())
            destination.parent.mkdir(parents=True,exist_ok=True)
            if not destination.exists():
                with destination.open('xb') as f:f.write(self.image_path.read_bytes())
            if image_sha256(str(destination))!=self.image_sha:raise ValueError('保存的來源圖片不一致')
            s={'schema_version':'betguard-region-assisted-review-v1','roi_schema_version':ROI_SCHEMA_VERSION,
               'session_id':uuid4().hex,'source_image_sha256':self.image_sha,
               'source_image_reference':str(destination),'width':self.width,'height':self.height,
               'game':game,'revision':0,'regions':[self._region([0,0,self.width,self.height],initial_text,'whole_image_fallback')],
               'retired_regions':[],'issues':[],'auto_submit':False,'auto_confirm':False,
               'metrics':{'started_at':_now(),'completed_at':None,'timing_kind':'wall_clock_session_not_benchmark',
                          'initial_region_count':1,'add':0,'delete':0,'split':0,'merge':0,'reorder':0,
                          'bbox_adjustments':0,'text_edits':0}}
            return self._save(s,'start',{s['regions'][0]['id']})

    def assemble(self, s):
        active=[r for r in s['regions'] if not r['cancelled']]
        pending=[r['id'] for r in active if not r['region_confirmed']]
        pending_cancelled=[r['id'] for r in s['regions'] if r['cancelled'] and not r['region_confirmed']]
        if pending or pending_cancelled:
            return {'ok':False,'message':f'尚有 {len(pending)} 個待確認區域、{len(pending_cancelled)} 個取消待確認',
                    'pending_active_count':len(pending),'pending_cancelled_count':len(pending_cancelled),'issues':[]}
        text='\n\n'.join(r['raw_transcription'] for r in active)
        mapping=[];line=1;issues=[];independent=[]
        for r in active:
            end=line+len(r['raw_transcription'].split('\n'))-1
            mapping.append({'region_id':r['id'],'start_line':line,'end_line':end})
            p=preflight_image_text(r['raw_transcription'],game=s['game'])
            independent.extend(b['result'] for b in p['parser_normalized_result']['bets'])
            for issue in p.get('issues',[]):issues.append({**issue,'region_id':r['id']})
            line=end+2
        p=preflight_image_text(text,game=s['game']) if active else None
        if p:
            for issue in p.get('issues',[]):
                owners=[m['region_id'] for m in mapping if m['start_line']<=issue.get('line_no',0)<=m['end_line']]
                issues.extend({**issue,'region_id':rid} for rid in (owners or [r['id'] for r in active]))
            whole=[b['result'] for b in p['parser_normalized_result']['bets']]
            if whole!=independent:
                issues.extend({'region_id':r['id'],'reason':'組合後解析範圍不同，請檢查各區完整倍率'} for r in active)
            if not p['all_parseable'] and not issues:
                issues.extend({'region_id':r['id'],'reason':'文字尚無法完整解析'} for r in active)
        if issues:return {'ok':False,'message':'請返回對應區域修改文字','issues':issues}
        return {'ok':True,'text':text,'line_regions':mapping,'parser_preflight':p,'issues':[],
                'auto_submit':False,'auto_confirm':False}

    def update(self, sid, expected_revision, operation, **data):
        with _LOCK:
            s=self.load(sid)
            if type(expected_revision) is not int or s['revision']!=expected_revision:
                raise ValueError('內容已更新，請重新開啟區域檢查')
            regions=s['regions'];byid={r['id']:r for r in regions}
            r=byid.get(data.get('region_id'));touched=set();s['issues']=[]
            if operation in {'text','bbox','confirm','cancelled','delete','split'} and r is None:
                raise ValueError('區域不存在')
            if operation=='add':
                r=self._region(data.get('bbox'));regions.append(r);touched.add(r['id'])
            elif operation=='text':
                if not isinstance(data.get('text'),str) or len(data['text'])>50000:raise ValueError('文字格式或長度無效')
                r['raw_transcription']=data['text'];touched.add(r['id'])
            elif operation=='bbox':
                r['bbox']=self._box(data.get('bbox'));r.pop('crop_sha256',None);touched.add(r['id'])
            elif operation=='cancelled':
                if type(data.get('cancelled')) is not bool:raise ValueError('取消狀態無效')
                r['cancelled']=data['cancelled'];touched.add(r['id'])
            elif operation=='delete':
                self._reset(r);s['retired_regions'].append({**r,'retired_by':'delete'});regions.remove(r)
            elif operation=='split':
                x,y,w,h=r['bbox'];axis=data.get('axis')
                if axis not in {'horizontal','vertical'}:raise ValueError('切分方向無效')
                length=h if axis=='horizontal' else w;cut=length//2
                if cut<1:raise ValueError('區域太小，無法切分')
                boxes=([[x,y,w,cut],[x,y+cut,w,h-cut]] if axis=='horizontal' else [[x,y,cut,h],[x+cut,y,w-cut,h]])
                new=[self._region(b,source='human_split') for b in boxes]
                for n in new:n['source_region_ids']=[r['id']]
                i=regions.index(r);regions[i:i+1]=new
                self._reset(r);s['retired_regions'].append({**r,'retired_by':'split'})
                touched.update(n['id'] for n in new)
            elif operation=='merge':
                ids=data.get('region_ids')
                if not isinstance(ids,list) or len(set(ids))!=2 or any(i not in byid for i in ids):raise ValueError('請選兩個區域')
                a,b=sorted([byid[i] for i in ids],key=lambda n:n['reading_order'])
                if a['cancelled']!=b['cancelled']:raise ValueError('請先明確恢復或取消兩個區域')
                x,y,w,h=a['bbox'];u,v,p,q=b['bbox']
                adjacent=((x+w==u or u+p==x) and min(y+h,v+q)>max(y,v) or
                          (y+h==v or v+q==y) and min(x+w,u+p)>max(x,u))
                # Only rectangular unions: cannot absorb a third region's gap.
                box=[min(x,u),min(y,v),max(x+w,u+p)-min(x,u),max(y+h,v+q)-min(y,v)]
                if not adjacent or box[2]*box[3]!=w*h+p*q:raise ValueError('只能合併邊緣相鄰且組成矩形的區域')
                n=self._region(box,source='human_merge');n['source_region_ids']=[a['id'],b['id']]
                n['raw_transcription']='\n\n'.join(t['raw_transcription'] for t in [a,b] if t['raw_transcription'])
                n['cancelled']=a['cancelled'];i=regions.index(a)
                regions.remove(a);regions.remove(b);regions.insert(i,n);touched.add(n['id'])
                for old in [a,b]:self._reset(old);s['retired_regions'].append({**old,'retired_by':'merge'})
            elif operation=='reorder':
                ids=data.get('region_ids')
                if not isinstance(ids,list) or len(ids)!=len(byid) or set(ids)!=set(byid):raise ValueError('區域順序不完整')
                s['regions']=regions=[byid[i] for i in ids];touched.update(ids)
            elif operation=='game':
                if data.get('game') not in {'539','六合'}:raise ValueError('遊戲設定無效')
                s['game']=data['game'];touched.update(byid)
            elif operation=='confirm':
                p=None if r['cancelled'] else preflight_image_text(r['raw_transcription'],game=s['game'])
                if p and not p['all_parseable']:
                    self._reset(r)
                    s['issues']=[{**i,'region_id':r['id']} for i in p.get('issues',[])] or [{'region_id':r['id'],'reason':'請輸入完整投注文字'}]
                else:r.update(region_confirmed=True,verification_status=VERIFICATION_VERIFIED,verified_at=_now(),
                              human_verified=True,human_verified_region_text=r['raw_transcription'])
                touched.add(r['id'])
            elif operation=='assemble':
                result=self.assemble(s);s['issues']=result['issues']
                if result['ok']:s['metrics']['completed_at']=_now()
                saved=self._save(s,operation,set());saved['assembly']=result;return saved
            else:raise ValueError('不支援的區域操作')
            if operation!='confirm':
                for item in regions:
                    if item['id'] in touched:self._reset(item)
                s['metrics']['completed_at']=None
            counter={'text':'text_edits','bbox':'bbox_adjustments'}.get(operation,operation)
            if counter in s['metrics']:s['metrics'][counter]+=1
            return self._save(s,operation,touched)
