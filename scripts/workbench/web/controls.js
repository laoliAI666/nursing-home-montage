'use strict';
// One shared interaction and visual system for every form and audio preview.
paths.search='<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>';
paths['chevron-down']='<path d="m6 9 6 6 6-6"/>';
paths.pause='<path d="M9 5v14M15 5v14"/>';
paths.volume='<path d="m11 5-6 4H2v6h3l6 4zM15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14"/>';
paths.muted='<path d="m11 5-6 4H2v6h3l6 4zM16 9l5 6M21 9l-5 6"/>';
const styledSelects=new WeakMap(),styledAudio=new WeakMap(),styledDisclosures=new WeakSet();let activeSelect=null,controlFrame;
function closeSelect(focus=false){if(!activeSelect)return;const open=activeSelect;activeSelect=null;open.popup.remove();open.button.setAttribute('aria-expanded','false');open.button.removeAttribute('aria-activedescendant');if(focus&&open.button.isConnected)open.button.focus();}
function selectLabel(select){return select.getAttribute('aria-label')||[...(select.labels||[])].map(x=>x.textContent.trim()).join(' ')||select.getAttribute('title')||'选择选项';}
function syncSelect(select,ui){const option=select.selectedOptions[0];const text=option?.textContent||'请选择';if(ui.value.textContent!==text)ui.value.textContent=text;if(ui.button.disabled!==select.disabled)ui.button.disabled=select.disabled;const label=selectLabel(select);if(ui.button.getAttribute('aria-label')!==label)ui.button.setAttribute('aria-label',label);ui.button.setAttribute('aria-required',String(select.required));ui.wrap.classList.toggle('is-disabled',select.disabled);}
function enhanceSelect(select){
  if(select.multiple||select.size>1||select.hasAttribute('data-native-select'))return;
  if(styledSelects.has(select)){syncSelect(select,styledSelects.get(select));return;}
  const wrap=document.createElement('div');wrap.className='field-select';select.before(wrap);wrap.append(select);select.classList.add('field-select-source');select.tabIndex=-1;select.setAttribute('aria-hidden','true');
  const button=document.createElement('button');button.type='button';button.className='select-trigger';button.setAttribute('role','combobox');button.setAttribute('aria-haspopup','listbox');button.setAttribute('aria-expanded','false');button.innerHTML='<span class="select-value"></span>'+icon('chevron-down');wrap.append(button);
  const ui={wrap,button,value:button.querySelector('.select-value')};styledSelects.set(select,ui);syncSelect(select,ui);
  [...(select.labels||[])].forEach(label=>label.addEventListener('click',e=>{if(button.isConnected){e.preventDefault();button.focus();}}));
  const pick=index=>{const option=select.options[index];if(!option||option.disabled)return;select.selectedIndex=index;button.removeAttribute('aria-invalid');syncSelect(select,ui);select.dispatchEvent(new Event('input',{bubbles:true}));select.dispatchEvent(new Event('change',{bubbles:true}));closeSelect(true);};
  const open=()=>{
    if(select.disabled)return;if(activeSelect?.select===select){closeSelect(true);return;}closeSelect();
    const popup=document.createElement('div');popup.className='select-popover';const id='options-'+(select.id||crypto.randomUUID());popup.id=id;button.setAttribute('aria-controls',id);button.setAttribute('aria-expanded','true');
    const searchable=select.options.length>6&&select.dataset.search!=='false';popup.innerHTML=(searchable?'<label class="select-search">'+icon('search')+'<input type="search" aria-label="搜索选项" placeholder="搜索选项" autocomplete="off"></label>':'')+'<div class="select-options" role="listbox" aria-label="'+E(selectLabel(select))+'"></div>';
    const list=popup.querySelector('.select-options');let filtered=[],current=select.selectedIndex;
    const paint=query=>{filtered=[...select.options].map((o,i)=>({o,i})).filter(({o})=>o.textContent.toLowerCase().includes(query.toLowerCase()));if(!filtered.some(x=>x.i===current&&!x.o.disabled))current=filtered.find(x=>!x.o.disabled)?.i??-1;list.innerHTML=filtered.length?filtered.map(({o,i})=>`<button type="button" role="option" id="${id}-${i}" data-option="${i}" aria-selected="${select.selectedIndex===i}" aria-disabled="${o.disabled}" class="select-option ${i===current?'is-active':''}" tabindex="-1"><span>${E(o.textContent)}</span>${i===select.selectedIndex?icon('check'):''}</button>`).join(''):'<p class="select-no-match">没有匹配的选项</p>';button.setAttribute('aria-activedescendant',id+'-'+current);};
    const position=()=>{const r=button.getBoundingClientRect(),bottomSpace=innerHeight-r.bottom-12-(innerWidth<=520?72:0),height=Math.min(350,popup.scrollHeight||350);popup.style.width=Math.min(Math.max(r.width,220),innerWidth-24)+'px';popup.style.left=Math.max(12,Math.min(r.left,innerWidth-popup.offsetWidth-12))+'px';if(bottomSpace<150&&r.top>bottomSpace){popup.style.top='auto';popup.style.bottom=(innerHeight-r.top+7)+'px';popup.style.maxHeight=Math.max(130,r.top-18)+'px';}else{popup.style.bottom='auto';popup.style.top=(r.bottom+7)+'px';popup.style.maxHeight=Math.max(130,bottomSpace)+'px';}};
    activeSelect={select,button,popup,position};document.body.append(popup);paint('');position();
    const key=e=>{if(e.key==='Escape'){e.preventDefault();closeSelect(true);return;}if(e.key==='Tab'){closeSelect(true);return;}if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();const enabled=filtered.filter(x=>!x.o.disabled),at=enabled.findIndex(x=>x.i===current);current=(e.key==='Home'?enabled[0]:e.key==='End'?enabled.at(-1):enabled[(at+(e.key==='ArrowDown'?1:-1)+enabled.length)%enabled.length])?.i??-1;paint(popup.querySelector('input')?.value||'');document.getElementById(id+'-'+current)?.scrollIntoView({block:'nearest'});}else if(e.key==='Enter'||e.key===' '&&e.target===button){e.preventDefault();pick(current);}};
    popup.addEventListener('keydown',key);activeSelect.key=key;
    list.addEventListener('click',e=>{const option=e.target.closest('[data-option]');if(option)pick(Number(option.dataset.option));});
    if(searchable){const search=popup.querySelector('input');search.addEventListener('input',()=>{paint(search.value);position();});search.focus();}
  };
  button.addEventListener('click',open);button.addEventListener('keydown',e=>{if(activeSelect?.select===select){activeSelect.key(e);return;}if(['ArrowDown','ArrowUp','Enter',' '].includes(e.key)){e.preventDefault();open();}});
  select.addEventListener('invalid',e=>{e.preventDefault();button.setAttribute('aria-invalid','true');button.focus();notify('请'+selectLabel(select),true);});
  select.addEventListener('change',()=>syncSelect(select,ui));
}
document.addEventListener('pointerdown',e=>{if(activeSelect&&!activeSelect.popup.contains(e.target)&&!activeSelect.button.contains(e.target))closeSelect();});
document.addEventListener('scroll',e=>{if(activeSelect&&!activeSelect.popup.contains(e.target))closeSelect();},true);
window.addEventListener('resize',()=>closeSelect());
function enhanceAudio(audio){
  if(styledAudio.has(audio))return;
  const label=audio.getAttribute('aria-label')||'音乐';const player=document.createElement('div');player.className='audio-player';audio.before(player);player.append(audio);audio.controls=false;audio.classList.add('audio-source');
  player.insertAdjacentHTML('beforeend',`<button type="button" class="audio-play" aria-label="播放 ${E(label)}">${icon('play')}</button><div class="audio-position"><input type="range" min="0" max="100" value="0" step="0.1" aria-label="${E(label)}播放进度" disabled><span class="audio-time">0:00 / ${audio.dataset.duration?musicDuration(Number(audio.dataset.duration)):'—'}</span></div><button type="button" class="audio-mute" aria-label="静音" aria-pressed="false">${icon('volume')}</button>`);
  const play=player.querySelector('.audio-play'),mute=player.querySelector('.audio-mute'),seek=player.querySelector('input'),time=player.querySelector('.audio-time');
  const sync=()=>{play.innerHTML=icon(audio.paused?'play':'pause');play.setAttribute('aria-label',(audio.paused?'播放 ':'暂停 ')+label);const duration=Number.isFinite(audio.duration)?audio.duration:Number(audio.dataset.duration)||0;seek.disabled=!audio.readyState;seek.max=duration||100;seek.value=audio.currentTime;time.textContent=musicDuration(audio.currentTime)+' / '+(duration?musicDuration(duration):'—');seek.style.setProperty('--played',(duration?audio.currentTime/duration*100:0)+'%');};
  play.onclick=async()=>{try{if(audio.paused)await audio.play();else audio.pause();}catch{notify('这首音乐暂时无法播放，请检查文件或重新上传。',true);}sync();};seek.oninput=()=>{audio.currentTime=Number(seek.value);sync();};mute.onclick=()=>{audio.muted=!audio.muted;mute.setAttribute('aria-pressed',String(audio.muted));mute.setAttribute('aria-label',audio.muted?'取消静音':'静音');mute.innerHTML=icon(audio.muted?'muted':'volume');};
  ['play','pause','timeupdate','loadedmetadata','ended','durationchange'].forEach(event=>audio.addEventListener(event,sync));audio.addEventListener('error',()=>{play.disabled=true;time.textContent='文件不可用';});styledAudio.set(audio,player);sync();
}
function refreshControls(){document.querySelectorAll('summary').forEach(summary=>{if(styledDisclosures.has(summary))return;summary.insertAdjacentHTML('beforeend','<span class="disclosure-chevron">'+icon('chevron-down')+'</span>');styledDisclosures.add(summary);});document.querySelectorAll('select').forEach(enhanceSelect);document.querySelectorAll('audio').forEach(enhanceAudio);if(activeSelect&&!activeSelect.select.isConnected)closeSelect();}
const controlObserver=new MutationObserver(()=>{cancelAnimationFrame(controlFrame);controlFrame=requestAnimationFrame(refreshControls);});
controlObserver.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','selected','aria-label']});
document.addEventListener('input',e=>{if(e.target.tagName==='SELECT'&&styledSelects.has(e.target))syncSelect(e.target,styledSelects.get(e.target));});
refreshControls();
