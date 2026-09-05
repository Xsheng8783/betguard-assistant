"""Inline, optional region editing in the existing image text panel."""


def render_region_review_ui():
    return r'''
<button type="button" id="rr-open">開啟區域檢查</button>
<section id="rr-panel" hidden style="border:1px solid #cbd5e1;padding:12px;margin:12px 0">
  <div style="display:flex;gap:8px;flex-wrap:wrap">
    <label>操作 <select id="rr-mode"><option value="select">選取</option><option value="add">拖曳新增</option><option value="move">拖曳移動</option><option value="resize">拖曳調整右下角</option></select></label>
    <label>縮放 <input id="rr-zoom" type="range" min="10" max="200" value="50"> <span id="rr-scale"></span></label>
    <button type="button" id="rr-close">收起區域檢查</button>
  </div>
  <p style="font-size:13px">選用工具，不影響直接修改下方主文字框。切分後請填寫各區文字；原文字保留於歷史。取消亦需按確認。</p>
  <div style="display:flex;flex-wrap:wrap;gap:12px">
    <div id="rr-scroll" style="flex:1 1 340px;max-height:580px;overflow:auto;background:#e2e8f0">
      <div id="rr-stage" style="position:relative;line-height:0">
        <img id="rr-image" alt="區域檢查原圖" style="width:100%;display:block;image-orientation:none">
        <svg id="rr-overlay" style="position:absolute;inset:0;width:100%;height:100%;touch-action:none" aria-label="原圖區域框"></svg>
      </div>
    </div>
    <div style="flex:1 1 300px;min-width:0">
      <label>區域 <select id="rr-select" aria-label="選擇區域"></select></label>
      <strong id="rr-state"></strong>
      <canvas id="rr-crop" style="display:block;max-width:100%;max-height:180px;margin:8px 0;border:1px solid #94a3b8" aria-label="目前區域裁切預覽"></canvas>
      <textarea id="rr-text" aria-label="區域 Betguard 文字" style="min-height:150px"></textarea>
      <div id="rr-controls" style="display:flex;gap:5px;flex-wrap:wrap">
        <button type="button" data-rr="prev">上一區</button><button type="button" data-rr="next">下一區</button>
        <button type="button" data-rr="confirm">確認此區域</button>
        <button type="button" data-rr="cancelled" id="rr-cancel">標記取消</button>
        <button type="button" data-rr="horizontal">水平切分</button><button type="button" data-rr="vertical">垂直切分</button>
        <button type="button" data-rr="up">閱讀順序提前</button><button type="button" data-rr="down">閱讀順序延後</button>
        <label>合併至 <select id="rr-merge-target" aria-label="另一合併區域"></select></label><button type="button" data-rr="merge">合併兩區</button>
        <button type="button" data-rr="delete">刪除誤區域</button>
      </div>
    </div>
  </div>
  <p id="rr-message" role="status" style="white-space:pre-wrap"></p>
  <ul id="rr-errors"></ul>
  <button type="button" id="rr-assemble">將已確認區域組合到文字框</button>
</section>
<script>
(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  const panel=$('rr-panel'), image=$('rr-image'), overlay=$('rr-overlay'), editor=$('rr-text');
  let imageId='', state=null, selected='', busy=false, dirty=false, generation=0, timer=null, drag=null;
  const current=()=>state && state.regions.find(r=>r.id===selected);
  function message(text){$('rr-message').textContent=text;}
  function key(){return 'betguard-region-session:'+imageId;}
  function remember(){try{sessionStorage.setItem(key(),state.session_id);}catch(e){}}
  function errors(issues){
    $('rr-errors').replaceChildren();
    (issues||[]).forEach(issue=>{
      const li=document.createElement('li'),b=document.createElement('button');b.type='button';
      const index=state.regions.findIndex(r=>r.id===issue.region_id);
      b.textContent='第 '+(index+1)+' 區：'+(issue.reason||'無法完整解析');
      b.onclick=()=>select(issue.region_id);li.appendChild(b);$('rr-errors').appendChild(li);
    });
  }
  function gate(){
    const pending=state?state.regions.filter(r=>!r.region_confirmed).length:1;
    $('rr-assemble').disabled=busy||dirty||pending>0||!state;
    $('rr-open').disabled=busy;
    panel.querySelectorAll('button,select,input,textarea').forEach(el=>{if(el.id!=='rr-assemble')el.disabled=busy;});
    if(!busy&&state&&pending)message('還有 '+pending+' 個區域待確認（含取消待確認）。');
  }
  function draw(){
    if(!state)return;
    const scale=Number($('rr-zoom').value)/100;
    $('rr-scale').textContent=Math.round(scale*100)+'%';
    $('rr-stage').style.width=(state.width*scale)+'px';
    overlay.setAttribute('viewBox','0 0 '+state.width+' '+state.height);overlay.replaceChildren();
    state.regions.forEach((r,i)=>{
      const rect=document.createElementNS('http://www.w3.org/2000/svg','rect');
      ['x','y','width','height'].forEach((k,j)=>rect.setAttribute(k,r.bbox[j]));
      rect.dataset.id=r.id;rect.setAttribute('fill',r.id===selected?'#38bdf830':'#00000005');
      rect.setAttribute('stroke',r.id===selected?'#0284c7':r.cancelled?'#dc2626':r.region_confirmed?'#15803d':'#f59e0b');
      rect.setAttribute('stroke-width',String(2/scale));overlay.appendChild(rect);
      const label=document.createElementNS('http://www.w3.org/2000/svg','text');
      label.textContent=String(i+1);label.setAttribute('x',r.bbox[0]+3);label.setAttribute('y',r.bbox[1]+18/scale);
      label.setAttribute('font-size',16/scale);label.style.pointerEvents='none';overlay.appendChild(label);
    });
    const r=current();if(!r)return;
    const [x,y,w,h]=r.bbox,canvas=$('rr-crop');
    canvas.width=Math.min(w,900);canvas.height=Math.max(1,Math.round(h*canvas.width/w));
    if(image.complete&&image.naturalWidth)canvas.getContext('2d').drawImage(image,x,y,w,h,0,0,canvas.width,canvas.height);
  }
  function render(){
    if(!state)return;
    if(!current())selected=state.regions.length?state.regions[0].id:'';
    for(const id of ['rr-select','rr-merge-target']){
      $(id).replaceChildren();state.regions.forEach((r,i)=>{
        if(id==='rr-merge-target'&&r.id===selected)return;
        const opt=document.createElement('option');opt.value=r.id;opt.textContent='第 '+(i+1)+' 區';$(id).appendChild(opt);
      });
    }
    $('rr-select').value=selected;const r=current();editor.value=r?r.raw_transcription:'';
    $('rr-state').textContent=r?(r.cancelled?'取消 · ':'')+(r.region_confirmed?'已確認':'待確認'):'請拖曳新增區域';
    $('rr-cancel').textContent=r&&r.cancelled?'恢復此區域':'標記取消';
    errors(state.issues);draw();gate();
  }
  async function request(operation,payload={},extra={}){
    if(busy)throw new Error('請等待保存完成');
    const gen=generation;busy=true;gate();
    try{
      const response=await fetch('/api/vision/v1/region-review',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({image_id:imageId,operation,session_id:state&&state.session_id,revision:state&&state.revision,payload,...extra})});
      const data=await response.json();if(gen!==generation)return null;
      if(!data.ok)throw new Error(data.message||'區域操作失敗');
      state=data.state;remember();return state;
    }finally{busy=false;gate();}
  }
  async function flush(){
    clearTimeout(timer);
    if(dirty&&current()){
      const text=editor.value,rid=selected;
      await request('text',{region_id:rid,text});dirty=false;
    }
  }
  async function select(id){try{await flush();selected=id;render();}catch(e){message(e.message);}}
  async function change(op,payload){
    try{await flush();await request(op,payload);
      if(op==='add')selected=state.regions[state.regions.length-1].id;
      if(op==='split'||op==='merge'){
        const replacement=state.regions.find(r=>(r.source_region_ids||[]).includes(selected));
        if(replacement)selected=replacement.id;
      }
      render();
      if(op==='split')message('已切分。兩區文字保持空白；原文字保存在 revision，請依原圖填寫各區。');
    }catch(e){message(e.message);gate();}
  }
  $('rr-open').onclick=async()=>{
    if(!imageId){message('請先上傳圖片');panel.hidden=false;return;}
    try{
      if(state){await flush();if(state.game!==getAssistGame())await request('game',{game:getAssistGame()});}
      else{
        let sid='';try{sid=sessionStorage.getItem(key())||'';}catch(e){}
        if(sid)await request('load',{}, {session_id:sid});
        else await request('start',{}, {game:getAssistGame(),initial_text:$('vision-transcription-text').value});
        if(!state)return;
        if(state.game!==getAssistGame())await request('game',{game:getAssistGame()});
      }
      panel.hidden=false;image.src='/api/vision/v1/images/'+encodeURIComponent(imageId);
      $('rr-zoom').value=Math.max(10,Math.min(100,Math.round(420/state.width*100)));render();
    }catch(e){panel.hidden=false;message(e.message);}
  };
  $('rr-close').onclick=async()=>{try{await flush();panel.hidden=true;}catch(e){message(e.message);}};
  editor.addEventListener('input',()=>{
    dirty=true;$('rr-state').textContent='待保存／待確認';gate();clearTimeout(timer);
    timer=setTimeout(async()=>{try{await flush();render();}catch(e){message(e.message);}},500);
  });
  $('rr-select').onchange=()=>select($('rr-select').value);
  $('rr-zoom').oninput=draw;image.onload=draw;
  $('rr-controls').onclick=async event=>{
    const op=event.target.dataset.rr,r=current();if(!op||!r||busy)return;
    const ids=state.regions.map(n=>n.id),i=ids.indexOf(r.id);
    if(op==='prev'||op==='next')return select(ids[Math.max(0,Math.min(ids.length-1,i+(op==='next'?1:-1)))]);
    if(op==='up'||op==='down'){
      const j=i+(op==='up'?-1:1);if(j<0||j>=ids.length)return;
      [ids[i],ids[j]]=[ids[j],ids[i]];return change('reorder',{region_ids:ids});
    }
    if(op==='horizontal'||op==='vertical')return change('split',{region_id:r.id,axis:op});
    if(op==='merge')return change('merge',{region_ids:[r.id,$('rr-merge-target').value]});
    return change(op,{region_id:r.id,...(op==='cancelled'?{cancelled:!r.cancelled}:{})});
  };
  function point(e){
    const b=overlay.getBoundingClientRect();
    return [Math.max(0,Math.min(state.width,Math.round((e.clientX-b.left)/b.width*state.width))),
            Math.max(0,Math.min(state.height,Math.round((e.clientY-b.top)/b.height*state.height)))];
  }
  overlay.onpointerdown=e=>{
    if(!state||busy||dirty)return;
    const mode=$('rr-mode').value,id=e.target.dataset.id;
    if(mode==='select'){if(id)select(id);return;}
    if(mode!=='add'&&!id)return;
    if(id)selected=id;
    drag={mode,start:point(e),id:selected,box:current()?current().bbox.slice():null};
    overlay.setPointerCapture(e.pointerId);e.preventDefault();draw();
  };
  overlay.onpointerup=e=>{
    if(!drag||!state)return;const d=drag;drag=null;const [x,y]=point(e),[a,b]=d.start;
    if(d.mode==='add'){
      if(Math.abs(x-a)<1||Math.abs(y-b)<1)return;
      change('add',{bbox:[Math.min(x,a),Math.min(y,b),Math.abs(x-a),Math.abs(y-b)]});
    }else{
      const [u,v,w,h]=d.box;
      const box=d.mode==='move'?[Math.max(0,Math.min(state.width-w,u+x-a)),Math.max(0,Math.min(state.height-h,v+y-b)),w,h]:
        [u,v,Math.max(1,Math.min(state.width-u,w+x-a)),Math.max(1,Math.min(state.height-v,h+y-b))];
      change('bbox',{region_id:d.id,bbox:box});
    }
  };
  overlay.onpointermove=e=>{
    if(!drag||!state)return;
    let ghost=$('rr-drag-preview');
    if(!ghost){ghost=document.createElementNS('http://www.w3.org/2000/svg','rect');ghost.id='rr-drag-preview';
      ghost.setAttribute('fill','#38bdf820');ghost.setAttribute('stroke','#0284c7');
      ghost.setAttribute('stroke-width',3/(Number($('rr-zoom').value)/100));ghost.style.pointerEvents='none';overlay.appendChild(ghost);}
    const [x,y]=point(e),[a,b]=drag.start;let box;
    if(drag.mode==='add')box=[Math.min(x,a),Math.min(y,b),Math.abs(x-a),Math.abs(y-b)];
    else{const [u,v,w,h]=drag.box;box=drag.mode==='move'?
      [Math.max(0,Math.min(state.width-w,u+x-a)),Math.max(0,Math.min(state.height-h,v+y-b)),w,h]:
      [u,v,Math.max(1,Math.min(state.width-u,w+x-a)),Math.max(1,Math.min(state.height-v,h+y-b))];}
    ['x','y','width','height'].forEach((k,j)=>ghost.setAttribute(k,box[j]));
  };
  overlay.onpointercancel=()=>{drag=null;draw();};
  $('rr-assemble').onclick=async()=>{
    const previous=$('vision-transcription-text').value,gen=generation,game=getAssistGame();
    try{
      await flush();if(state.game!==game){await change('game',{game});return;}
      await request('assemble');if(gen!==generation)return;
      const result=state.assembly;render();
      if(!result||!result.ok){message(result?result.message:'無法組合');return;}
      if(previous!==$('vision-transcription-text').value||game!==getAssistGame()){message('主文字或遊戲已變更，未覆寫；請重新組合。');return;}
      $('vision-transcription-text').value=result.text;
      $('vision-transcription-text').dispatchEvent(new Event('input',{bubbles:true}));
      message('已組合到主文字框並重新預檢；尚未輔助填入。');
    }catch(e){message(e.message);}
  };
  document.addEventListener('betguard:image-uploaded',event=>{
    generation++;clearTimeout(timer);imageId=event.detail.imageId;state=null;selected='';dirty=false;drag=null;panel.hidden=true;
  });
  $('assist-game').addEventListener('change',()=>{
    if(state&&!busy)change('game',{game:getAssistGame()});
  });
})();
</script>
'''
