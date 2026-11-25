'use strict';
let musicFilter='active',musicRenaming=null,musicUploadProgress=null;
const musicDuration=seconds=>{const n=Math.round(seconds||0),h=Math.floor(n/3600),m=Math.floor(n/60)%60;return (h?h+':':'')+String(m).padStart(h?2:1,'0')+':'+String(n%60).padStart(2,'0');};
const musicDefault=()=>state.music.tracks.find(t=>t.id===state.music.defaultId);
const musicSize=n=>n>=1024**2?(n/1024**2).toFixed(1)+' MB':Math.ceil(n/1024)+' KB';
function musicEntry(){const track=musicDefault(),count=state.music.tracks.filter(t=>!t.archived).length;return `<div class="music-settings-entry"><span class="music-entry-icon">${icon('music')}</span><div><strong>配乐管理</strong><p>${count} 首可用 · 默认 ${E(track?.name||'待选择')}</p></div><a class="secondary" href="#settings/music">管理配乐 ${icon('arrow-right')}</a></div>`;}
const settingsBeforeMusic=renderSettings;
renderSettings=function(){if(location.hash==='#settings/music'){renderMusicManager();return;}settingsBeforeMusic();const row=$('#default-music')?.closest('.switch-row');if(row)row.insertAdjacentHTML('afterend',musicEntry());};

function musicUploadStatus(){return musicUploadProgress?`<div class="music-upload-status inline-note" role="status"><strong>正在上传并检查 ${musicUploadProgress.index} / ${musicUploadProgress.total} 首</strong><p>${E(musicUploadProgress.name)} · 请保持当前页面打开</p></div>`:'';}
function musicRows(){
  const rows=state.music.tracks.filter(t=>musicFilter==='archived'?t.archived:!t.archived).sort((a,b)=>Number(b.isDefault)-Number(a.isDefault)||b.created-a.created);
  if(!rows.length)return emptyState('没有已收起的配乐','收起的曲目仍保存在本地，可以随时恢复。');
  return rows.map(t=>`<article class="music-row"><div class="music-track-info"><div class="music-track-name"><h2>${E(t.name)}</h2>${t.isDefault?badge('默认配乐'):''}${t.builtin?badge('原有配乐','neutral'):''}</div><p>${E(t.filename)}<span>·</span>${musicDuration(t.duration)}<span>·</span>${musicSize(t.bytes)}</p>${musicRenaming===t.id?`<form class="music-rename-form" data-music-rename="${t.id}"><label class="field-label" for="music-name-${t.id}">配乐名称</label><div><input class="input" id="music-name-${t.id}" name="name" value="${E(t.name)}" maxlength="100" required><button class="primary" type="submit">保存名称</button><button class="secondary" type="button" data-action="cancel-music-rename">取消</button></div><p class="inline-error" role="alert"></p></form>`:''}</div><div class="music-track-actions">${!t.archived&&!t.isDefault?`<button class="secondary" data-action="default-music-track" data-id="${t.id}">设为默认</button>`:''}<button class="text-button" data-action="rename-music-track" data-id="${t.id}">重命名</button>${!t.isDefault?`<button class="text-button" data-action="archive-music-track" data-id="${t.id}" data-archive="${!t.archived}">${t.archived?'恢复使用':'收起'}</button>`:''}</div><audio class="music-audio" data-duration="${t.duration}" controls preload="none" aria-label="试听 ${E(t.name)}" src="${E(t.url)}"></audio></article>`).join('');
}
function renderMusicManager(){
  $('#page-label').textContent='配乐管理';
  main.innerHTML=`<a href="#settings" class="back-link">${icon('arrow-right')} 返回设置</a>${heading('配乐管理','配乐保存在本地。设好默认曲，每批也可以单独选择。',`<button class="primary" data-action="upload-music" ${musicUploadProgress?'disabled':''}>${icon('plus')} 上传音乐</button>`)}<div class="music-library-intro"><div>${icon('music')}<span>当前默认<strong>${E(musicDefault()?.name||'待选择')}</strong></span></div><p>支持 MP3、WAV、M4A、AAC、FLAC、OGG、OPUS，单首不超过 256MB。上传后可以试听。</p></div><div id="music-upload-status">${musicUploadStatus()}</div><div class="filter-tabs music-library-tabs" role="group" aria-label="配乐分类">${[['active','可用配乐'],['archived','已收起']].map(([id,name])=>`<button data-action="filter-music" data-filter="${id}" class="${musicFilter===id?'active':''}" aria-pressed="${musicFilter===id}">${name} ${state.music.tracks.filter(t=>id==='archived'?t.archived:!t.archived).length}</button>`).join('')}</div><div id="music-library-rows" class="music-library-rows">${musicRows()}</div><p class="music-library-note">每批任务创建时都会保存所选音乐的副本。之后改名、收起或更换默认曲，都不会改变已有任务的配乐。</p>`;
}
async function updateMusicLibrary(){await refresh(false);if(location.hash==='#settings/music')renderMusicManager();}
document.addEventListener('play',e=>{if(e.target.tagName==='AUDIO')document.querySelectorAll('audio').forEach(a=>{if(a!==e.target)a.pause();});},true);
document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-action]');if(!b)return;const action=b.dataset.action;
  if(action==='upload-music'){if(!musicUploadProgress)$('#music-upload').click();return;}
  if(action==='filter-music'){musicFilter=b.dataset.filter;musicRenaming=null;renderMusicManager();return;}
  if(action==='rename-music-track'){musicRenaming=b.dataset.id;$('#music-library-rows').innerHTML=musicRows();$('#music-name-'+musicRenaming)?.focus();return;}
  if(action==='cancel-music-rename'){musicRenaming=null;$('#music-library-rows').innerHTML=musicRows();return;}
  if(!['default-music-track','archive-music-track'].includes(action))return;
  await busy(b,async()=>{try{await api('/api/music/'+b.dataset.id,action==='default-music-track'?{default:true}:{archived:b.dataset.archive==='true'});await updateMusicLibrary();notify(action==='default-music-track'?'已设为默认配乐，已有任务不变':b.dataset.archive==='true'?'已收起，原文件与已有任务保留':'配乐已恢复，可以在新批次中选择');}catch(err){notify(err.message,true);}});
});
document.addEventListener('submit',async e=>{const f=e.target;if(!f.matches('[data-music-rename]'))return;e.preventDefault();await busy(f.querySelector('[type=submit]'),async()=>{try{await api('/api/music/'+f.dataset.musicRename,{name:f.elements.name.value});musicRenaming=null;await updateMusicLibrary();notify('配乐名称已保存');}catch(err){f.querySelector('[role=alert]').textContent=err.message;}});});
$('#music-upload').addEventListener('change',async e=>{
  const files=[...e.target.files];if(!files.length||musicUploadProgress)return;
  let added=0,duplicate=0;
  try{
    for(let i=0;i<files.length;i++){
      const file=files[i];if(file.size>256*1024**2||!file.size)throw new Error('“'+file.name+'”应大于 0 且不超过 256MB');
      musicUploadProgress={index:i+1,total:files.length,name:file.name};if(location.hash==='#settings/music'){ $('#music-upload-status').innerHTML=musicUploadStatus();$('[data-action=upload-music]').disabled=true;}
      const response=await fetch('/api/music/upload?name='+encodeURIComponent(file.name),{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});const out=await response.json();if(!response.ok)throw new Error(out.error);if(out.duplicate)duplicate++;else added++;
    }
    musicFilter='active';notify(`配乐已保存${added?' · 新增 '+added+' 首':''}${duplicate?' · 相同文件 '+duplicate+' 首已保留原曲':''}`);
  }catch(err){notify((added?'已保存 '+added+' 首；':'')+err.message,true);}
  finally{musicUploadProgress=null;e.target.value='';await updateMusicLibrary();}
});

const createBeforeMusic=renderCreate;
renderCreate=function(){
  createBeforeMusic();const f=$('#create-form');if(!f)return;
  const selected=createDraft.musicTrackId||'',available=state.music.tracks.filter(t=>!t.archived),missing=selected&&!available.some(t=>t.id===selected);
  $('#music-check').closest('.switch-row').insertAdjacentHTML('afterend',`<div class="batch-music" id="batch-music" ${$('#music-check').checked?'':'hidden'}><div class="label-row"><label for="batch-music-track">本批次背景音乐</label><a class="text-button" href="#settings/music">管理配乐 ${icon('arrow-up-right')}</a></div><select name="musicTrackId" id="batch-music-track"><option value="">按数字人绑定配乐（未绑定用默认 · ${E(musicDefault()?.name||'请选择')}）</option>${available.map(t=>`<option value="${t.id}" ${t.id===selected?'selected':''}>统一覆盖：${E(t.name)} · ${musicDuration(t.duration)}</option>`).join('')}${missing?`<option value="${E(selected)}" selected>原选择已收起，请重新选择</option>`:''}</select><div id="batch-music-preview"></div><p class="form-help">默认优先使用每位数字人绑定的专属配乐；也可以在这里统一指定一首覆盖整批。创建后固定保存。</p></div>`);
  const preview=()=>{
    const id=$('#batch-music-track').value;
    if(id){
      const t=available.find(x=>x.id===id);
      $('#batch-music-preview').innerHTML=t?`<div class="batch-music-caption">${E(t.name)}（整批统一覆盖）<span>${musicDuration(t.duration)}</span></div><audio data-duration="${t.duration}" controls preload="none" aria-label="试听本批次背景音乐" src="${E(t.url)}"></audio>`:'<p class="inline-error">这首配乐已收起，请重新选择。</p>';
      return;
    }
    const checkedIds=[...f.querySelectorAll('[data-queue-person]:checked')].map(el=>el.dataset.queuePerson);
    const chosenPeople=(checkedIds.length?checkedIds:[createDraft.person||state.config.defaultPerson].filter(Boolean)).map(pid=>state.config.people.find(p=>p.id===pid)).filter(Boolean);
    const def=musicDefault();
    if(!chosenPeople.length){
      $('#batch-music-preview').innerHTML=def?`<div class="batch-music-caption">默认配乐：${E(def.name)}<span>${musicDuration(def.duration)}</span></div><audio data-duration="${def.duration}" controls preload="none" aria-label="试听默认配乐" src="${E(def.url)}"></audio>`:'';
      return;
    }
    const rows=[],players=new Map();
    for(const person of chosenPeople){
      const bound=person.music_track_id||'';
      if(bound){
        const t=available.find(x=>x.id===bound);
        if(!t){rows.push(`<li class="batch-person-music-warn"><strong>${E(person.name)}</strong><span>原绑定配乐已收起，请先在数字人管理中更换</span></li>`);continue;}
        rows.push(`<li><strong>${E(person.name)}</strong><span>专属 · ${E(t.name)} (${musicDuration(t.duration)})</span></li>`);
        players.set(t.id,t);
      }else if(def){
        rows.push(`<li><strong>${E(person.name)}</strong><span>默认 · ${E(def.name)} (${musicDuration(def.duration)})</span></li>`);
        players.set(def.id,def);
      }
    }
    const audioHtml=[...players.values()].map(t=>`<div class="batch-music-caption">${E(t.name)}<span>${musicDuration(t.duration)}</span></div><audio data-duration="${t.duration}" controls preload="none" aria-label="试听 ${E(t.name)}" src="${E(t.url)}"></audio>`).join('');
    $('#batch-music-preview').innerHTML=`<ul class="batch-person-music-list">${rows.join('')}</ul>${audioHtml}`;
  };
  window.refreshBatchMusicPreview=preview;
  preview();$('#batch-music-track').onchange=preview;$('#music-check').addEventListener('change',()=>{$('#batch-music').hidden=!$('#music-check').checked;if(!$('#music-check').checked)$('#batch-music audio')?.pause();});
};
const taskBeforeMusic=renderTask;
renderTask=function(){
  taskBeforeMusic();
  const t=state.tasks.find(t=>t.id===selectedTask),brief=main.querySelector('.task-brief');
  if(!t||!brief)return;
  const perPerson=t.videoMusicTracks&&Object.keys(t.videoMusicTracks).length?t.videoMusicTracks:null;
  const uniqueTracks=perPerson?[...new Map(Object.values(perPerson).map(x=>[x.id,x])).values()]:[];
  let musicHtml='';
  if(!t.music){
    musicHtml='<p>不添加背景音乐</p>';
  }else if(perPerson&&uniqueTracks.length>1){
    const nameById=new Map((t.videoSpecs||[]).map(s=>[s.person,s.personName]));
    musicHtml=Object.entries(perPerson).map(([pid,track])=>{
      const pName=nameById.get(pid)||state.config.people.find(p=>p.id===pid)?.name||pid;
      return `<div class="task-person-music"><p><strong>${E(pName)}</strong> · ${E(track.name)} · ${musicDuration(track.duration)} · 已固定保存</p><audio class="task-music-audio" data-duration="${track.duration}" controls preload="none" aria-label="试听 ${E(pName)} 固定配乐" src="/media/task/${t.id}/music?person=${encodeURIComponent(pid)}"></audio></div>`;
    }).join('');
  }else if(t.musicTrack){
    musicHtml=`<p>${E(t.musicTrack.name)} · ${musicDuration(t.musicTrack.duration)} · 已固定保存</p><audio class="task-music-audio" data-duration="${t.musicTrack.duration}" controls preload="none" aria-label="试听此任务固定配乐" src="/media/task/${t.id}/music"></audio>`;
  }else{
    musicHtml='<p>沿用原工程保存的配乐</p>';
  }
  brief.insertAdjacentHTML('beforeend',`<h3>本批次配乐</h3>${musicHtml}`);
  const hooks=t.personClosingHooks&&Object.keys(t.personClosingHooks).length?t.personClosingHooks:null;
  if(hooks){
    const nameById=new Map((t.videoSpecs||[]).map(s=>[s.person,s.personName]));
    const hookHtml=Object.entries(hooks).map(([pid,text])=>{
      const pName=nameById.get(pid)||state.config.people.find(p=>p.id===pid)?.name||pid;
      return `<p class="task-bound-hook"><strong>${E(pName)}</strong>：${E(text)}</p>`;
    }).join('');
    brief.insertAdjacentHTML('beforeend',`<h3>固定片尾钩子</h3>${hookHtml}`);
  }
};
// queue.js starts the app after all shared controls register.
