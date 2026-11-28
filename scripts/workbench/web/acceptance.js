'use strict';
Object.assign(statusLabels,{queued:'排队中',awaiting_acceptance:'待验收',accepted:'已验收'});
const statusClassBeforeAcceptance=statusClass;
statusClass=function(status){return status==='accepted'?'':status==='awaiting_acceptance'?'warn':statusClassBeforeAcceptance(status);};

function storageText(bytes){return bytes>=1024**3?(bytes/1024**3).toFixed(2)+' GB':bytes>=1024**2?(bytes/1024**2).toFixed(1)+' MB':Math.max(0,Math.round(bytes/1024))+' KB';}
function cleanupBusy(t){return ['queued','running'].includes(t.cleanupStatus);}
function cleanupSpinner(){return '<svg class="cleanup-spinner" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/></svg>';}
function acceptanceButton(t){
  if(window.hasPerm&&!window.hasPerm('tasks.accept'))return '';
  if(cleanupBusy(t))return `<button class="secondary accept-task-button" disabled>${cleanupSpinner()}后台清理中</button>`;
  if(t.status==='accepted'&&t.cleanupStatus==='failed')return `<button class="secondary accept-task-button" data-action="review-task" data-id="${t.id}" ${t.canRetryCleanup?'':'disabled'}>${icon('arrow-right')}重试清理</button>`;
  if(t.status!=='awaiting_acceptance')return '';
  if(t.workspaceMissing)return `<button class="secondary accept-task-button" disabled title="${E(t.acceptanceBlockedReason||'本地工程目录已移走或缺失，可直接删除任务记录')}">工程已移走</button>`;
  return `<button class="secondary accept-task-button" data-action="review-task" data-id="${t.id}" ${t.canAccept?'':`disabled title="${E(t.acceptanceBlockedReason||'等待当前制作完全结束')}"`}>${icon('check')} 验收成品</button>`;
}
const taskCurrentBeforeAcceptance=taskCurrent;
taskCurrent=function(t){
  if(!['accepted','awaiting_acceptance'].includes(t.status))return taskCurrentBeforeAcceptance(t);
  const accepted=t.status==='accepted',pending=cleanupBusy(t),failed=t.cleanupStatus==='failed',missing=!accepted&&t.workspaceMissing;
  const removed=Object.values(t.productDeletions||{}).includes('deleted')&&!t.results.some(r=>r.video||r.deletionPending);
  const title=missing?'本地工程目录已移走或缺失':removed?'这批成品已删除':pending?'已验收，正在后台清理':failed?'已验收，清理尚未完成':accepted?'已验收，成品已归档':'制作完成，等待你验收';
  const message=missing?'该任务对应的工作区批次文件夹已不在硬盘上，无法验收；可以直接删除这条历史任务记录。':removed?'制作记录仍可查看。':pending?'你可以继续查看或下载成品，清理完成后这里会自动更新。':failed?'成品已保留，可以重试清理。':accepted?'视频、封面和发布包已保留。已释放 '+storageText(t.acceptance?.releasedBytes||0)+' 中间文件空间。':'确认满意后点击验收，中间文件会在后台清理。';
  return `<div class="current-step"><span>${pending?cleanupSpinner():icon(missing?'folder':accepted?'check':'play')}</span><div><h2>${title}</h2><p>${message}</p></div></div>`;
};
const taskControlsBeforeAcceptance=taskControls;
taskControls=function(t){
  if(t.status==='accepted'&&Object.values(t.productDeletions||{}).includes('deleted')&&!t.results.some(r=>r.video))return '';
  if(t.status==='accepted')return `<div class="acceptance-entry" role="status">${acceptanceButton(t)}${cleanupBusy(t)?'<p class="form-help">成品可正常查看和下载，清理后可继续修改封面或发起重做。</p>':t.cleanupStatus==='failed'?'<p class="inline-error">'+E(t.cleanupError||'上次清理未完成，成品已保留。')+'</p>':'<p class="form-help">后续可以修改封面，或从单条成品发起新的重做任务。</p>'}</div>`;
  if(t.status==='awaiting_acceptance')return `<div class="acceptance-entry">${acceptanceButton(t)}<p class="form-help">${E(t.acceptanceBlockedReason||'点击即验收，自动后台清理中间文件，保留成品、封面和发布文案。')}</p></div>`;
  return taskControlsBeforeAcceptance(t);
};
const taskBeforeAcceptance=renderTask;
renderTask=function(){taskBeforeAcceptance();const t=state.tasks.find(x=>x.id===selectedTask);if(t&&['accepted','awaiting_acceptance'].includes(t.status))$('#task-stages').hidden=true;};
function paintAcceptance(ident,full=false){
  if(route==='tasks'){$('#tasks-body').innerHTML=tasksBody();enhanceManagement();}
  else if(route==='task'&&selectedTask===ident){
    if(full)renderTask();
    else{const task=state.tasks.find(t=>t.id===ident);if(!task)return;const current=$('#task-current'),controls=$('#task-controls');if(current)current.innerHTML=taskCurrent(task);if(controls)controls.innerHTML=taskControls(task);renderLiveProgress();}
  }
}
async function submitAcceptance(task,button){
  await busy(button,async()=>{
    let accepted;
    try{accepted=await api('/api/tasks/'+task.id+'/accept',{});}
    catch(err){notify(err.message,true);return;}
    state.tasks=state.tasks.map(t=>t.id===task.id?accepted:t);
    paintAcceptance(task.id,true);
    notify(accepted.cleanupStatus==='completed'?'已验收，中间文件已清理':'已验收，中间文件正在后台清理');
    try{await refresh(false);paintAcceptance(task.id);}
    catch{notify('已验收，连接恢复后会继续更新清理状态');}
  });
}
document.addEventListener('click',e=>{const button=e.target.closest('[data-action="review-task"]');if(!button||button.disabled)return;const task=state.tasks.find(t=>t.id===button.dataset.id);if(task&&(task.canAccept||task.canRetryCleanup))submitAcceptance(task,button);});

// Shared catalog management and honest production feedback.
let managementState={entries:[]},managementEndpoint='',managementAvailable=false;
const managementKey=x=>[x.kind,x.parent||'',x.id].join('/');
async function manageRequest(action,data){
  const bases=managementEndpoint?[managementEndpoint]:[''];
  let last;
  for(const base of bases){try{const r=await fetch(base+'/api/manage/'+action,{credentials:'include',signal:AbortSignal.timeout(4000),method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});const v=await r.json();if(!r.ok){const err=new Error(v.error||'管理服务暂不可用');err.status=r.status;err.code=v.code||'';throw err;}managementEndpoint=base;managementAvailable=true;return v;}catch(e){last=e;}}
  throw last;
}
const apiBeforeManagement=api;
api=async function(path,data,options){
  const value=await apiBeforeManagement(path,data,options);
  if(data===undefined&&path==='/api/state'){
    try{managementState=await manageRequest('state');}catch{managementAvailable=false;}
    value.tasks=value.tasks.map(t=>(managementState.deletions||[]).some(r=>r.taskId===t.id)?{...t,canAccept:false,canRetryCleanup:false,acceptanceBlockedReason:'正在清理任务数据'}:t);
    for(const item of value.queue.items){const activity=managementState.activity?.[item.id];if(activity&&item.status==='running'&&activity.phase>=item.phase){Object.assign(item,{phase:activity.phase,activityMessage:activity.message,activityUpdated:activity.updated,source:activity.source});}}
    for(const task of value.tasks){if(!task.queueItems?.length)continue;task.queueItems=task.queueItems.map(x=>value.queue.items.find(q=>q.id===x.id)||x);if(task.planning)task.planning=value.queue.items.find(q=>q.id===task.planning.id)||task.planning;task.progress=batchProgress(task);const active=task.queueItems.find(x=>x.status==='running');if(active?.source){task.phase=active.phase;task.updated=Math.max(task.updated,active.activityUpdated||0);}}
    if(value.queue.items.some(x=>x.status==='running'&&!x.settled)&&value.config.agent.state!=='connected')value.config.agent.uncertain=true;
    document.documentElement.dataset.lastSync=String(Date.now());
  }

  return value;
};
let managementSelection=new Map(),managementRoute='',liveSignature='';
const liveVisibilityObserver=new IntersectionObserver(entries=>entries.forEach(e=>e.target.classList.toggle('live-offscreen',!e.isIntersecting)));
document.addEventListener('visibilitychange',()=>document.documentElement.classList.toggle('workbench-hidden',document.hidden));
function managementObjects(){
  const objects=[];
  const add=(el,kind,id,parent='',name='')=>{if(id)objects.push({el,kind,id:String(id),parent,name});};
  main.querySelectorAll('.task-table .task-row').forEach(el=>add(el,'task',el.querySelector('.task-title')?.hash.split('/')[1],'',el.querySelector('.task-title')?.textContent));
  main.querySelectorAll('.institution-card').forEach(el=>add(el,'institution',el.querySelector('.institution-photo')?.hash.split('/')[1],'',el.querySelector('h2')?.textContent));
  main.querySelectorAll('.person-card').forEach(el=>add(el,'person',el.querySelector('[data-action="edit-person"]')?.dataset.id,'',el.querySelector('h2')?.textContent));
  main.querySelectorAll('.music-row').forEach(el=>add(el,'music',el.querySelector('[data-action="rename-music-track"]')?.dataset.id,'',el.querySelector('h2')?.textContent));
  main.querySelectorAll('.asset-card').forEach(el=>{const p=el.querySelector('.asset-photo')?.hash.split('/');if(p)add(el,'asset',p[2],p[1],el.querySelector('.asset-name')?.textContent);});

  return objects;
}
function deleteControl(item){const b=document.createElement('button');b.type='button';b.className='text-button manage-delete';b.textContent='删除';b.setAttribute('aria-label','删除'+item.name);b.onclick=()=>openDelete([item],b);return b;}
function enhanceManagement(){
  if(!state)return;
  if(managementRoute!==location.hash){managementSelection.clear();managementRoute=location.hash;}
  const items=managementObjects().filter(item=>!window.canDeleteKind||window.canDeleteKind(item.kind));
  for(const item of items){
    if(item.el.querySelector('.manage-selection'))continue;
    const label=document.createElement('label');label.className='manage-selection';
    const c=document.createElement('input');c.type='checkbox';c.setAttribute('aria-label','选择'+item.name);c.checked=managementSelection.has(managementKey(item));
    c.onchange=()=>{if(c.checked)managementSelection.set(managementKey(item),item);else managementSelection.delete(managementKey(item));updateSelectionBar();};
    label.append(c);const text=document.createElement('span');text.textContent='选择';label.append(text);
    if(item.kind==='task'){item.el.querySelector('.task-info').prepend(label);item.el.querySelector('.task-row-actions').append(deleteControl(item));}
    else{const bar=document.createElement('div');bar.className='manage-item-bar';bar.append(label,deleteControl(item));item.el.prepend(bar);}
  }
  let toolbar=main.querySelector('.management-toolbar');
  if(!toolbar&&items.length&&main.querySelector('.page-heading,.detail-top')){
    toolbar=document.createElement('div');toolbar.className='management-toolbar';
    toolbar.innerHTML='<label class="manage-selection"><input type="checkbox" aria-label="全选当前列表"><span>全选当前列表</span></label><span class="management-count">已选 0 项</span><button type="button" class="danger-button" data-manage-bulk disabled>删除所选</button>';
    (main.querySelector('#project-overview-area') || main.querySelector('.page-heading,.detail-top')).after(toolbar);
    toolbar.querySelector('input').onchange=e=>{managementSelection.clear();for(const x of managementObjects().filter(i=>!window.canDeleteKind||window.canDeleteKind(i.kind))){const selected=e.target.checked;x.el.querySelector('.manage-selection input').checked=selected;if(selected)managementSelection.set(managementKey(x),x);}updateSelectionBar();};
    toolbar.querySelector('[data-manage-bulk]').onclick=e=>openDelete([...managementSelection.values()],e.currentTarget);
  }
  if(toolbar&&!items.length){toolbar.remove();toolbar=null;}
  if(toolbar&&route==='task'&&!items.length){toolbar.classList.add('management-toolbar-compact');const target=main.querySelector('.detail-top>div:last-child');if(target&&toolbar.parentElement!==target)target.append(toolbar);}
  if(toolbar){toolbar.querySelector('label').hidden=!items.length;toolbar.querySelector('.management-count').hidden=!items.length;toolbar.querySelector('[data-manage-bulk]').hidden=!items.length;updateSelectionBar();}
  if(route==='task'&&(!window.canDeleteKind||window.canDeleteKind('task'))&&!main.querySelector('.detail-top .manage-delete')){const t=state.tasks.find(x=>x.id===selectedTask);if(t)main.querySelector('.detail-top>div:last-child')?.append(deleteControl({kind:'task',id:t.id,parent:'',name:t.title}));}
  if(route==='institution'&&(!window.canDeleteKind||window.canDeleteKind('institution'))&&!main.querySelector('.page-heading .manage-delete')){const id=location.hash.split('/')[1];const inst=state.institutions.find(x=>x.id===id);if(inst)main.querySelector('.page-heading')?.append(deleteControl({kind:'institution',id,parent:'',name:inst.name}));}
  main.querySelectorAll('.work-card').forEach(card=>{if(card.querySelector('.work-art')?.hash.startsWith('#preview/reference')&&!card.querySelector('.manage-reference-note')){const note=document.createElement('p');note.className='subtle manage-reference-note';note.textContent='已认可样片受保护';card.append(note);}});
  renderLiveProgress();
  paintTaskDeletions();
}
function updateSelectionBar(){const bar=main.querySelector('.management-toolbar');if(!bar)return;const n=managementSelection.size,items=managementObjects();const count=bar.querySelector('.management-count'),text='已选 '+n+' 项';if(count.textContent!==text)count.textContent=text;bar.querySelector('[data-manage-bulk]').disabled=!n;const all=bar.querySelector('input');all.checked=!!items.length&&items.every(x=>managementSelection.has(managementKey(x)));all.indeterminate=n>0&&!all.checked;}
function managementDialog(title,opener){const d=document.createElement('dialog');d.className='acceptance-dialog management-dialog';d.setAttribute('aria-label',title);d.innerHTML='<div class="acceptance-dialog-body"><h2>'+E(title)+'</h2><div class="management-dialog-content">正在读取…</div></div>';document.body.append(d);d.addEventListener('close',()=>{d.remove();opener?.focus();});d.showModal();return d;}
async function openDelete(items,opener){
  if(!items.length)return;const d=managementDialog('确认删除 '+items.length+' 项内容',opener),body=d.querySelector('.management-dialog-content');
  try{const first=items[0];if(items.some(x=>x.kind!==first.kind||x.parent!==first.parent))throw new Error('请在同一列表中选择要删除的内容');const preview=await manageRequest('preview',{kind:first.kind,parent:first.parent,ids:items.map(x=>x.id)});
    body.innerHTML='<p class="acceptance-note">'+E(preview.notice)+'</p><ul class="management-preview">'+preview.items.map(x=>'<li><strong>'+E(x.name)+'</strong>'+(x.blocked?'<p class="inline-error">'+E(x.blocked)+'</p>':'')+'</li>').join('')+'</ul><p class="inline-error" role="alert"></p><div class="acceptance-actions"><button class="secondary" data-close>取消</button><button class="danger-button" data-confirm '+(preview.canDelete?'':'disabled')+'>删除并清理</button></div>';
    body.querySelector('[data-close]').onclick=()=>d.close();body.querySelector('[data-close]').focus();
    body.querySelector('[data-confirm]').onclick=async e=>{await busy(e.currentTarget,async()=>{try{managementState=await manageRequest('delete',{token:preview.token});managementSelection.clear();institutionCache.clear();await refresh(false);d.close();if(route==='task'&&!state.tasks.some(t=>t.id===selectedTask))setRoute('tasks');else render();notify(first.kind==='task'?'已提交后台清理，成品会保留在成品库':'已删除所选资料');}catch(err){body.querySelector('[role=alert]').textContent=err.message;}});};
  }catch(err){body.innerHTML='<p class="inline-error">'+E(err.message)+'</p><button class="secondary">关闭</button>';body.querySelector('button').onclick=()=>d.close();}
}
let diagnosticOpen=false;
function timeSince(value){if(!value)return '尚无记录';const seconds=Math.max(0,Math.floor(Date.now()/1000-value));return seconds<60?seconds+' 秒前':Math.floor(seconds/60)+' 分钟前';}
function productionDiagnosticHtml(current){
  const d=managementState.diagnostics?.[current?.id]||{},pending=['awaiting_coordinator','awaiting_agent'].includes(d.recoveryState)||current?.recoveryRequested;
  return `<details class="production-diagnostics" ${diagnosticOpen?'open':''}><summary>状态诊断与进度说明</summary><dl><div><dt>Agent 最近回传</dt><dd data-age-at="${d.reportedAt||''}">${timeSince(d.reportedAt)}</dd></div><div><dt>本地工程最近更新</dt><dd data-age-at="${d.lastArtifactAt||current?.activityUpdated||''}">${timeSince(d.lastArtifactAt||current?.activityUpdated)}</dd></div><div><dt>进度依据</dt><dd>${E(current?.source||'Agent 阶段回传')}</dd></div><div><dt>执行状态</dt><dd>${current&&!current.settled?'当前执行锁仍被持有，尚未收到制作结束确认':'当前没有未回收的执行者'}</dd></div></dl>${pending?`<p class="production-recovery-note">${E(d.recoveryMessage||'上次核查请求仍待 Agent 处理，不代表当前制作已暂停。')}</p>`:d.recoveryResult?'<p class="subtle">'+E(d.recoveryResult)+'</p>':''}<p class="production-help">进度按已确认阶段估算，不代表剩余时间。页面可连接、Agent 已回传和本地产物有更新是不同信号；仅凭旧心跳不能判定断连。</p>${current&&!current.settled?`<details class="production-advanced"><summary>异常执行处理</summary><p>请求主 Agent 核查执行者。主对话等待子任务期间，核查可能暂未领取；不会自行终止、重做或释放当前执行锁。</p><button class="secondary" data-action="queue-recover" data-id="${current.id}" ${pending?'disabled':''}>${pending?'核查请求待处理':'请求 Agent 核查执行者'}</button></details>`:''}</details>`;
}
function renderLiveProgress(){
  if(route!=='task'){liveSignature='';delete main.dataset.productionUnified;return;}
  const t=state.tasks.find(x=>x.id===selectedTask);if(!t)return;
  const rows=t.queueItems||[],covers=batchCoverItems(t),planning=t.planning,current=(planning?.id===t.activeItemId?planning:null)||rows.find(x=>x.position)||rows.find(x=>!['completed','cancelled'].includes(x.status))||covers.find(x=>x.position);
  const running=t.status==='running'||['assigned','running','stopping'].includes(current?.status),queued=t.status==='queued'||current?.status==='queued',percent=Math.max(0,Math.min(100,Number(t.progress)||0)),d=managementState.diagnostics?.[current?.id];
  const key=JSON.stringify([t.id,t.status,percent,t.message,rows,covers,planning,t.results,d,diagnosticOpen,state.config.agent,state.queue?.headId]);let panel=main.querySelector('#live-production');
  if(panel&&liveSignature===key)return;liveSignature=key;main.dataset.productionUnified='true';
  // Keep existing stage DOM, question fields and cancellation controls intact.
  const stageArea=$('#task-stages'),batch=main.querySelector('.batch-items');stageArea?.remove();batch?.remove();
  if(!panel){panel=document.createElement('section');panel.id='live-production';panel.className='production-live panel';panel.setAttribute('aria-label','本批制作');main.querySelector('.detail-top')?.after(panel);liveVisibilityObserver.disconnect();liveVisibilityObserver.observe(panel);}
  const message=current?queueDisplayMessage(current):t.message;
  const lastUpdate=Math.max(current?.activityUpdated||current?.updated||0,t.updated||0,planning?.updated||0);
  panel.innerHTML=`<div class="production-live-heading"><div><h2>本批制作</h2><p class="production-batch-summary">本批 ${t.count} 条视频${current?(current.kind==='planning'?' · 整批准备':current.kind==='cover'?' · 成品修改 · 第 '+(current.index+1)+' 条视频':' · 当前 '+E(current.personName||'视频')+' · 本批第 '+(current.index+1)+' 条'):''}</p></div><div class="production-live-tools"><div class="production-percent"><strong>${percent}<small>%</small></strong><span>阶段进度</span></div><button class="text-button" data-action="inspect-progress">刷新状态</button></div></div><div class="production-progress" role="progressbar" aria-label="已确认的阶段进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><span style="transform:scaleX(${percent/100})"></span></div><div class="production-state">${running?'<svg class="live-spinner" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a9 9 0 1 1-9 9"/></svg>':icon(queued?'clapper':'check')}<p class="production-message" role="status">${E(message)}</p></div><div class="production-freshness"><span class="live-age" data-updated="${lastUpdate}"></span><span class="live-sync"></span></div>${connectionHelp(current)}<div class="production-stage-slot"></div><div class="production-video-slot"></div>${productionDiagnosticHtml(current)}`;
  if(stageArea){panel.querySelector('.production-stage-slot').append(stageArea);stageArea.dataset.livePhase=t.phase+':'+t.status;stageArea.innerHTML=stages(t.phase,running||queued);}
  if(batch){panel.querySelector('.production-video-slot').append(batch);batch.classList.remove('panel');}
  const details=panel.querySelector('.production-diagnostics');details.addEventListener('toggle',()=>{diagnosticOpen=details.open;});
  updateLiveClock();
}
document.addEventListener('click',async e=>{const button=e.target.closest('[data-action="inspect-progress"]');if(!button)return;await busy(button,async()=>{try{await refresh(false);if(route==='task'){diagnosticOpen=true;renderLiveProgress();}else if(route==='tasks'){diagnosticOpen=true;renderTasks();notify('已读取当前记录，未向制作 Agent 发送操作');}}catch(err){notify(err.message,true);}});});
function updateLiveClock(){main.querySelectorAll('[data-age-at]').forEach(el=>{const value=timeSince(Number(el.dataset.ageAt));if(el.textContent!==value)el.textContent=value;});const age=main.querySelector('.live-age'),sync=main.querySelector('.live-sync');if(age){const seconds=Math.max(0,Math.floor(Date.now()/1000-Number(age.dataset.updated)));const value='状态更新于 '+(seconds<60?seconds+' 秒前':Math.floor(seconds/60)+' 分钟前')+(seconds>180&&main.querySelector('.live-spinner')?' · 等待新的阶段回传':'');if(age.textContent!==value)age.textContent=value;}if(sync){const last=Number(document.documentElement.dataset.lastSync)||0,value=Date.now()-last<12000?'页面同步正常':'页面连接待恢复';if(sync.textContent!==value)sync.textContent=value;}}
const refreshBeforeLive=refresh;let refreshChain=Promise.resolve();
refresh=function(paint=true,force=false){const next=refreshChain.then(()=>refreshBeforeLive(paint,force)).then(()=>{if(paint)enhanceManagement();});refreshChain=next.catch(()=>{});return next;};
new MutationObserver(()=>enhanceManagement()).observe(main,{childList:true,subtree:true});
setInterval(()=>{if(!document.hidden)updateLiveClock();},1000);

// Saved credentials are fetched only on an explicit reveal click.
// The temporary read-only display has no name and is never part of FormData.
const revealedCredentials=new Map();
function setCredentialButton(button,label,show){
  button.setAttribute('aria-pressed',String(show));button.setAttribute('aria-label',(show?'隐藏':'显示')+label);
}
function hideStoredCredential(button){
  const record=revealedCredentials.get(button);if(!record)return;
  clearTimeout(record.timer);record.display.value='';record.display.remove();record.input.hidden=false;
  setCredentialButton(button,record.label,false);revealedCredentials.delete(button);
}
function hideAllStoredCredentials(){for(const button of revealedCredentials.keys())hideStoredCredential(button);}
function credentialRequest(input){
  const payload={field:input.name};
  if(input.name==='model_key'){
    payload.provider=input.form.elements.model_provider.value;
    if(payload.provider==='custom')payload.baseUrl=input.form.elements.model_base_url.value;
  }
  return payload;
}
async function toggleCredentialVisibility(button){
  if(revealedCredentials.has(button)){hideStoredCredential(button);return;}
  const input=button.parentElement.querySelector('input[name]');if(!input)return;
  const label=input.closest('.form-group')?.querySelector('label')?.textContent||input.name;
  if(input.value){const show=input.type==='password';input.type=show?'text':'password';setCredentialButton(button,label,show);return;}
  if(!input.closest('.form-group').querySelector('.saved-mark')){notify('请先填写这项凭据');return;}
  if(window.currentUser&&!window.currentUser.isSuperAdmin){notify('仅超级管理员可查看已保存的 API 密钥明文。',true);return;}
  const payload=credentialRequest(input);
  await busy(button,async()=>{try{
    if(window.promptSuperAdminReauth&&Date.now()>(window._reauthVerifiedUntil||0)&&!(window.currentUser?.reauthRemainingSeconds>5)){
      const verified=await window.promptSuperAdminReauth(button);
      if(!verified)return;
    }
    let result;
    try{
      result=await manageRequest('credential',payload);
    }catch(err){
      if(err.code==='reauth_required'&&window.promptSuperAdminReauth){
        window._reauthVerifiedUntil=0;
        const verified=await window.promptSuperAdminReauth(button);
        if(!verified)return;
        result=await manageRequest('credential',payload);
      }else{
        throw err;
      }
    }
    if(!input.isConnected||route!=='settings'||document.hidden||input.value||JSON.stringify(payload)!==JSON.stringify(credentialRequest(input)))return;
    const display=document.createElement('input');display.className='input credential-readonly';display.type='text';display.readOnly=true;display.autocomplete='off';display.spellcheck=false;display.setAttribute('aria-label','已保存的'+label+'（只读）');display.value=result.value;
    input.hidden=true;input.after(display);setCredentialButton(button,label,true);
    revealedCredentials.set(button,{input,display,label,timer:setTimeout(()=>hideStoredCredential(button),30000)});
  }catch(err){notify(err.message,true);}});
}
window.addEventListener('hashchange',hideAllStoredCredentials);
document.addEventListener('visibilitychange',()=>{if(document.hidden)hideAllStoredCredentials();});
const settingsBeforeCredentialReveal=renderSettings;
renderSettings=function(){hideAllStoredCredentials();return settingsBeforeCredentialReveal();};

function archivedTaskNotice(id){
  return state.tasks.some(task=>task.id===id) ? '原制作任务已开始清理' : '原制作任务已删除';
}

function archivedCards(institution=''){
  return (state.archives||[]).filter(t=>!institution||t.institutionId===institution).flatMap(t=>t.results.map(r=>{
    const preview = `#preview/archive/${t.id}/${r.index}/${r.version}`;
    return `<article class="work-card"><a class="work-art" href="${preview}" aria-label="播放${E(r.title)}"><img src="${E(r.cover)}" alt="${E(r.title)}封面" loading="lazy"><span class="play-button">${icon('play')}</span></a><h2>${E(r.title)}</h2><p>${E(t.institutionName||'未关联机构')} · ${E(t.personName||'')} · ${E(r.label)}</p><p>已归档 · ${archivedTaskNotice(t.id)}</p><div class="work-card-actions"><a href="${E(r.package)}" class="primary" download>${icon('download')} 下载发布包</a><a href="${preview}" class="secondary">${icon('play')} 预览成品</a>${deleteProductButton(t,r)}</div></article>`;
  })).join('');
}

function deleteProductButton(task, product){
  if(window.hasPerm&&!window.hasPerm('works.delete'))return '';
  return `<button type="button" class="danger-button work-delete" data-action="delete-product" data-id="${task.id}" data-index="${product.index}" data-title="${E(product.title)}">${product.deletionPending?'重试删除':'删除成品'}</button>`;
}

document.addEventListener('click',e=>{
  const button=e.target.closest('[data-action="delete-product"]');
  if(!button)return;
  const {id,index,title}=button.dataset;
  const dialog=managementDialog('永久删除成品',button);
  const body=dialog.querySelector('.management-dialog-content');
  body.innerHTML=`<p><strong>${E(title)}</strong></p><p class="acceptance-note">这条视频的全部版本、封面、发布文案和发布包都将永久删除，无法恢复。</p><p class="inline-error" role="alert"></p><div class="acceptance-actions"><button type="button" class="secondary" data-close>取消</button><button type="button" class="danger-button" data-confirm>永久删除</button></div>`;
  const cancel=body.querySelector('[data-close]'),confirm=body.querySelector('[data-confirm]');
  dialog.addEventListener('cancel',event=>{if(confirm.disabled)event.preventDefault();});
  cancel.onclick=()=>dialog.close();
  cancel.focus();
  confirm.onclick=()=>busy(confirm,async()=>{
    body.querySelector('[role=alert]').textContent='';
    cancel.disabled=true;
    try{
      await api(`/api/products/${id}/${index}/delete`,{confirm:true});
    }catch(err){
      body.querySelector('[role=alert]').textContent=err.message;
      cancel.disabled=false;
      return;
    }
    dialog.close();
    try{
      await refresh(false,true);
      institutionCache.clear();
      render();
    }catch{
      notify('成品已删除，列表将自动更新');
      return;
    }
    notify('成品已永久删除');
  });
});
const previewBeforeArchive=renderPreview;
renderPreview=function(){
  const parts=location.hash.split('/');if(parts[1]!=='archive'){previewBeforeArchive();return;}
  const task=(state.archives||[]).find(t=>t.id===parts[2]),product=task?.results.flatMap(r=>r.versions||[r]).find(r=>r.index===Number(parts[3])&&r.version===parts[4]);
  if(!product){main.innerHTML=emptyState('没有找到这份成品','返回成品库查看已保留的作品。','<a href="#works" class="secondary">返回成品库</a>');return;}
  main.innerHTML=`<a class="back-link" href="#works">${icon('arrow-right')} 返回成品库</a>${heading(E(product.title),archivedTaskNotice(task.id)+'，视频、封面和发布包独立保留。',`<a class="primary" href="${E(product.package)}" download>${icon('download')} 下载发布包</a>`)}<nav class="content-tabs" aria-label="已保留的成品版本">${(task.results.find(r=>r.index===product.index)?.versions||[product]).map(v=>`<a href="#preview/archive/${task.id}/${v.index}/${v.version}" ${v.version===product.version?'aria-current="page"':''}>${E(v.label)}</a>`).join('')}</nav><div class="video-view"><video controls playsinline preload="metadata" poster="${E(product.cover)}" src="${E(product.video)}"></video></div><div class="work-actions"><a class="secondary" href="${E(product.cover)}" download>下载封面</a><a class="secondary" href="${E(product.video)}?download=1" download>下载视频</a></div>`;
};
function paintTaskDeletions(){
  if(route==='task'&&!state.tasks.some(t=>t.id===selectedTask)){setRoute('tasks');return;}
  const rows=managementState.deletions||[];
  let panel=main.querySelector('#task-deletion-status');
  const shown=route==='tasks'?rows:route==='task'?rows.filter(r=>r.taskId===selectedTask):[];
  if(!shown.length){panel?.remove();return;}
  if(!panel){panel=document.createElement('section');panel.id='task-deletion-status';panel.className='panel deletion-status';panel.setAttribute('aria-label','任务数据清理');main.querySelector('.page-heading,.detail-top')?.after(panel);}
  const key=JSON.stringify(shown);if(panel.dataset.state!==key){panel.dataset.state=key;panel.innerHTML=shown.map(row=>`<div class="acceptance-entry" role="status"><strong>${row.status==='failed'?'清理尚未完成':cleanupSpinner()+'正在清理任务数据'} · ${E(row.title)}</strong><p class="${row.status==='failed'?'inline-error':'form-help'}">${E(row.error||'所有成品都会保留。清理完成后，任务会从列表移除，作品仍可在成品库查看。')}</p>${row.status==='failed'?`<button class="secondary" data-action="retry-task-deletion" data-id="${row.taskId}">重试清理</button>`:''}</div>`).join('');}
  main.querySelectorAll('.task-row').forEach(el=>{const id=el.querySelector('.task-title')?.hash.split('/')[1],row=rows.find(r=>r.taskId===id),button=el.querySelector('.manage-delete');if(row&&button&&row.status!=='failed'){button.disabled=true;if(button.textContent!=='清理中')button.textContent='清理中';}});
}
document.addEventListener('click',e=>{const b=e.target.closest('[data-action="retry-task-deletion"]');if(!b)return;busy(b,async()=>{try{managementState=await manageRequest('retry-delete',{id:b.dataset.id});await refresh(false);render();notify('已重新提交后台清理，成品保留');}catch(err){notify(err.message,true);}});});

window.bootWorkbench = init;
