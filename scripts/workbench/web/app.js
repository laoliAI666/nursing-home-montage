'use strict';
const $ = (s, r = document) => r.querySelector(s);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const E = escapeHTML;
const paths = {
  clapper:'<path d="M4 8h16v12H4zM4 8l-1-4 16-3 1 4M7 3l3 4M13 2l3 4"/>',
  grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  person:'<circle cx="12" cy="8" r="4"/><path d="M4.5 21v-2a7.5 7.5 0 0 1 15 0v2"/>',
  settings:'<path d="m9 3-1 3-3 1-2 3 2 2-1 3 2 3 3-1 3 1 3-1 3 1 2-3-1-3 2-2-2-3-3-1-1-3z"/><circle cx="12" cy="11" r="3"/>',
  folder:'<path d="M3 7V5a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  'arrow-right':'<path d="M4 12h15m-6-6 6 6-6 6"/>',
  'arrow-up-right':'<path d="M6 18 18 6M6 6h12v12"/>',
  'chevron-right':'<path d="m9 5 7 7-7 7"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  help:'<circle cx="12" cy="12" r="9"/><path d="M9.2 9a3 3 0 0 1 5.8 1c0 2-3 2-3 4M12 17h.01"/>',
  video:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
  image:'<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 6"/>',
  text:'<path d="M6 3h8l4 4v14H6zM14 3v5h4M9 12h6M9 16h6"/>',
  shield:'<path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6z"/><path d="m8 12 3 3 5-6"/>',
  music:'<path d="M9 18V5l11-2v13M9 8l11-2"/><ellipse cx="6" cy="18" rx="3" ry="2"/><ellipse cx="17" cy="16" rx="3" ry="2"/>',
  play:'<path d="m8 5 11 7-11 7z"/>',
  download:'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v6l4 2"/>',
  eye:'<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
  close:'<path d="m6 6 12 12M6 18 18 6"/>',
  spark:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z"/>',
  save:'<path d="M4 3h13l4 4v14H3V3h1M7 3v6h10V3M7 21v-8h10v8"/>',
  box:'<path d="m12 2 9 5-9 5-9-5zM3 7v10l9 5 9-5V7M12 12v10M7.5 4.5l9 5"/>',
  retry:'<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7"/>',
  stop:'<rect x="5" y="5" width="14" height="14" rx="2"/>',
  mic:'<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8"/>'
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.clapper}</svg>`;
function hydrateIcons(root = document) { root.querySelectorAll('[data-icon]').forEach(el => { el.innerHTML = icon(el.dataset.icon); }); }
let state, route = 'create', selectedTask, editPerson, worksFilter = 'all', uploadTarget = 'source', scanResult, toastTimer;
let createDraft = {};
const main = $('#main');
const labels = {create:'制作工作台', works:'我的作品', people:'我的数字人', settings:'服务设置', onboarding:'首次使用指南', task:'制作详情', preview:'成品预览'};
const statusLabels = {draft:'待开始', queued:'等待接手', running:'制作中', needs_input:'需要补充', failed:'执行中断', interrupted:'可继续', cancelled:'已停止', timed_out:'制作超时', stopping:'正在停止', completed:'已完成'};
function statusClass(status) { return status === 'completed' ? '' : ['needs_input','billing_hold','interrupted','queued','draft','stopping'].includes(status) ? 'warn' : ['failed','cancelled','timed_out'].includes(status) ? 'error' : 'neutral'; }
function badge(text, cls = '') { return `<span class="badge ${cls}">${E(text)}</span>`; }
function notify(message, error = false) { const el = $('#toast'); clearTimeout(toastTimer); el.textContent = message; el.classList.toggle('error', error); el.hidden = false; toastTimer = setTimeout(() => el.hidden = true, error ? 6500 : 3800); }
async function api(path, data, options = {}) {
  const response = await fetch(path, {method:data === undefined ? 'GET' : 'POST', headers:data === undefined ? {} : {'Content-Type':'application/json'}, body:data === undefined ? undefined : JSON.stringify(data), ...options});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '操作没有完成，请重试');
  return result;
}
let draftTimer, workbenchExited = false;
function saveDraft() {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {
    draftTimer = undefined;
    api('/api/draft', createDraft).catch(() => notify('草稿暂未保存，请检查本地服务', true));
  }, 400);
}
async function exitWorkbench() {
  if (draftTimer !== undefined) {
    clearTimeout(draftTimer);
    draftTimer = undefined;
    await api('/api/draft', createDraft);
  }
  await api('/api/shutdown', {});
  workbenchExited = true;
  $('.app-shell').hidden = true;
  $('.skip').hidden = true;
  const message = $('#workbench-exited');
  message.hidden = false;
  message.querySelector('h2').focus();
}
function setRoute(next) { location.hash = next; }
function updateChrome() {
  const c = state.config;
  $('#skill-version').textContent = 'v' + c.version;
  const archives = state.archives || [];
  const products = state.tasks.filter(t=>!archives.some(a=>a.id===t.id))
    .reduce((n,t)=>n+t.results.filter(r=>r.video).length, 0)
    + archives.reduce((n,t)=>n+t.results.length, 0);
  $('#works-count').textContent = String(products + (state.institutions || []).filter(x=>x.reference).length);
  $('#setup-dot').hidden = allConfigured();
  const a = c.agent;
  $('#agent-dot').className = 'status-dot' + (a.uncertain ? ' muted' : a.state === 'connected' ? '' : a.state === 'error' ? ' error' : ' muted');
  $('#agent-status').textContent = a.mode==='local' ? (a.canDispatch?'本地制作已就绪':'模型待配置') : a.uncertain ? '等待进度回传' : a.state === 'connected' ? a.label+' 已连接' : a.state === 'checking' ? '正在连接 Agent…' : a.state === 'error' ? 'Agent 待连接' : '连接 '+a.label;
  document.querySelectorAll('[data-nav]').forEach(a => { if (a.dataset.nav === (route==='task'?'tasks':['preview','video'].includes(route)?'works':['institution','asset','institution-new'].includes(route)?'library':route)) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current'); });
  $('#page-label').textContent = route==='settings'&&location.hash==='#settings/music'?'配乐管理':labels[route] || labels.create;
}
const voicePlatforms={volcengine:'火山语音',minimax:'MiniMax',aliyun:'阿里百炼'};
const aliyunModels={'qwen-audio-3.1-tts-plus':'千问 Qwen-Audio 3.1 TTS Plus','cosyvoice-v3.5-flash':'CosyVoice 3.5 Flash'};
function aliyunModelOptions(selected) {
  return Object.entries(aliyunModels).map(([id,label])=>`<option value="${id}" ${id===selected?'selected':''}>${label}</option>`).join('');
}
function voicePlatformOptions(selected) {
  return Object.entries(voicePlatforms).map(([id,label])=>`<option value="${id}" ${id===selected?'selected':''}>${label}</option>`).join('');
}
function boundVoicePlatform(person) {return person.voice_provider||'volcengine';}
function voiceConfigured(config, provider) {
  if(provider==='aliyun')return config.voice.aliyunConfigured;
  return provider==='minimax'?config.voice.minimaxConfigured:config.connection.speech;
}
function hasServices() {
  const c=state.config;
  const people=c.people.filter(person=>person.ready);
  const hasVoice=people.length?people.some(person=>voiceConfigured(c,boundVoicePlatform(person))):voiceConfigured(c,c.voice.provider);
  return hasVoice&&c.connection.ak&&c.connection.sk;
}
function hasPerson() { return state.config.people.some(p => p.ready); }
function allConfigured() { return hasServices() && hasPerson() && state.config.agent.state === 'connected'; }
function heading(title, text, action = '') { return `<div class="page-heading"><div><h1>${title}</h1><p>${text}</p></div>${action}</div>`; }
function stages(phase = -1, running = false) {
  return `<section class="workflow ${running ? 'is-running' : ''}" aria-label="制作流程"><div class="workflow-heading"><h2>${running ? '每一步，都看得见' : '一条视频，这样完成'}</h2><p>${phase >= 0 ? '进度按实际制作阶段更新' : '由 Agent 按步骤完成'}</p></div><div class="stage-strip">${state.config.phases.map((name, i) => `<div class="stage ${i < phase ? 'done' : i === phase ? 'active' : ''}"><strong>${E(name)}</strong></div>`).join('')}</div></section>`;
}
function previewPanel(result, task) {
  const institution = state.institutions?.find(x=>x.id===(task?.institutionId||createDraft.institutionId));
  const reference = institution && !institution.reference ? {...state.reference,available:false} : state.reference;
  const cover = result?.cover || (reference.available ? reference.cover : null);
  const video = result?.video || (!task && reference.available ? reference.video : null);
  return `<aside class="panel preview-panel"><div class="panel-top"><h2>${result ? '本次制作预览' : reference.available ? '已认可的效果参考' : '制作预览'}</h2>${video ? `<button class="text-button" data-action="preview" ${task && result ? `data-id="${E(task.id)}" data-index="${result.index}"` : 'data-reference="true"'}>播放 ${icon('arrow-up-right')}</button>` : badge('9:16','neutral')}</div><div class="preview-frame">${cover ? `<img src="${E(cover)}" alt="${result ? '本次视频封面' : '已认可项目样片封面'}">` : `<div class="preview-empty"><h3>你的下一条<br>好视频，<br>从这里开始。</h3><p>实拍素材 · 数字人口播<br>封面、视频与发布文案，一起交付。</p></div>`}${video ? `<button class="play-button" aria-label="播放${result ? '本次成片' : '项目样片'}" data-action="preview" ${task && result ? `data-id="${E(task.id)}" data-index="${result.index}"` : 'data-reference="true"'}>${icon('play')}</button>` : ''}</div><div class="preview-meta"><span>1080 × 1920 · 竖版</span><span>${result ? '本次作品' : reference.available ? '已认可样片' : '等待本次成片'}</span></div><p class="preview-caption">${!result && !reference.available ? '本次封面和视频生成后，会显示在任务详情。<br>完成的作品自动归入这家机构。' : task && !result ? '这里展示该机构已认可样片。<br>本次封面生成后会自动更新。' : result ? '本次作品会在这里呈现。<br>完成后可预览视频、下载发布包。' : '一段真实的养老生活，一份清楚的介绍。<br>把素材交给 Agent，把故事带给更多人。'}</p><div class="deliverables"><span>${icon('video')}竖版视频</span><span>${icon('image')}独立封面</span><span>${icon('text')}发布文案</span></div></aside>`;
}
function taskRows(tasks) {
  if (!tasks.length) return `<div class="empty-row"><div class="empty-mark">${icon('clapper')}<div><strong>下一条好作品，从这里开始</strong><p>选好素材、写下要求，制作记录会保存在这里。</p></div></div><button class="text-button" data-action="focus-source">开始创作 ${icon('arrow-right')}</button></div>`;
  return `<div class="task-list">${tasks.map(t => `<div class="task-row"><div class="task-miniature">${t.results[0]?.cover ? `<img src="${E(t.results[0].cover)}" alt="">` : icon('clapper')}</div><div class="task-info"><strong>${E(t.title)}</strong><p>${E(t.personName)} · ${E(t.message)}</p></div>${badge(statusLabels[t.status],statusClass(t.status))}<div class="task-progress"><span>${['completed','awaiting_acceptance','accepted'].includes(t.status) ? '已完成全部阶段' : `${Math.min(t.phase,6)} / 6 阶段完成`}</span><div class="progress-track"><span style="width:${t.progress}%"></span></div></div><a class="text-button" href="#task/${t.id}">查看 ${icon('chevron-right')}</a></div>`).join('')}</div>`;
}
function renderCreate() {
  const c = state.config;
  const people = c.people;
  const person = people.find(p => p.id === createDraft.person) || people.find(p => p.isDefault) || people[0];
  if (person && !createDraft.person) createDraft.person = person.id;
  const source = createDraft.source || '';
  const folderName = source.split(/[\\/]/).filter(Boolean).pop();
  main.innerHTML = `${heading('让实拍素材，讲一个好故事。','从一份机构资料，到一条完整视频。交给你的 Agent。',`<div class="heading-note">${icon('shield')} 本地保存，随时继续</div>`)}${!allConfigured() ? `<div class="setup-banner">${icon('spark')}<div><h3>${!hasServices() || !hasPerson() ? '先用几分钟，准备好你的工作台' : '素材和人物已就绪，连接 Agent 即可制作'}</h3><p>${!hasServices() ? '填好服务 Key，配置一个数字人，以后每次直接开始。' : !hasPerson() ? '补齐人物照片与音色，让每条视频都有自己的主角。' : '沿用 Agent 的 Agent 与 skill，制作进度会同步到这里。'}</p></div><a class="secondary" href="${hasServices() && hasPerson() ? '#settings' : '#onboarding'}">${hasServices() && hasPerson() ? '连接 '+a.label : '开始准备'} ${icon('arrow-right')}</a></div>` : ''}<div class="create-grid"><section class="panel create-panel"><div class="panel-top"><h2>开始一条新视频</h2><span class="draft-label">${icon('save')} 制作草稿自动保留</span></div><form id="create-form"><div class="form-group"><div class="label-row"><label>机构素材</label><button type="button" class="text-button" data-action="import-source">导入文件夹 ${icon('arrow-up-right')}</button></div><button type="button" class="source-select" id="source-drop" data-action="pick-source"><span class="source-icon">${icon('folder')}</span><span class="source-content"><strong>${E(folderName || '选择本地素材文件夹')}</strong><small>${source ? '已选择 · 点击更换' : '实拍视频、照片，以及机构的文字介绍'}</small></span>${icon(source ? 'check' : 'plus')}</button>${source ? `<p class="source-path">${E(source)}</p>` : ''}<div id="source-info">${sourceInfo()}</div><details class="file-table"><summary>手动填写文件夹路径</summary><input class="input" name="source" value="${E(source)}" placeholder="粘贴本地文件夹路径"><button class="text-button" type="button" data-action="scan-source">读取这个文件夹 ${icon('arrow-right')}</button></details></div><div class="form-group"><div class="label-row"><label for="requirements">你想怎么介绍这家机构？<span class="optional">可选</span></label></div><textarea id="requirements" name="requirements" placeholder="比如：带家属看看这里的一天，突出餐饮、活动和居住环境，语气亲切自然。">${E(createDraft.requirements)}</textarea><p class="form-help">AI 会结合真实资料写文案、选镜头。你只需要说清楚想表达什么。</p></div><details class="form-group" ${createDraft.facts ? 'open' : ''}><summary class="field-label">补充机构介绍<span class="optional">素材里没有文字介绍时填写</span></summary><textarea name="facts" aria-label="机构事实介绍" style="margin-top:11px" placeholder="填写机构名称、位置、设施与服务等真实信息。">${E(createDraft.facts)}</textarea></details><div class="form-group"><div class="label-row"><label for="person-select">这次，谁来出镜？</label><a class="text-button" href="#people">管理数字人 ${icon('chevron-right')}</a></div><div class="person-picker">${person?.hasPortrait ? `<img class="person-thumb" id="selected-portrait" src="/media/person/${E(person.id)}" alt="${E(person.name)}">` : `<span class="person-thumb" style="display:grid;place-items:center">${icon('person')}</span>`}<select name="person" id="person-select" ${!people.length ? 'disabled' : ''}>${people.length ? people.map(p => `<option value="${E(p.id)}" ${person?.id === p.id ? 'selected' : ''}>${E(p.name)}</option>`).join('') : '<option>先添加一个数字人</option>'}</select>${person?.ready ? badge('已就绪') : badge('待配置','warn')}</div></div><div class="form-row"><div class="form-group"><label class="field-label" for="title-input" style="margin-bottom:9px">给作品起个名字<span class="optional">可选</span></label><input class="input" id="title-input" name="title" value="${E(createDraft.title)}" placeholder="例如：养老院探访 · 日常生活"></div><div class="form-group"><label class="field-label" for="count-input" style="margin-bottom:9px">制作数量</label><select name="count" id="count-input">${[1,2,3,5,10].map(n => `<option value="${n}" ${Number(createDraft.count || 1) === n ? 'selected' : ''}>${n} 条${n > 1 ? ' · 同机构不同讲法' : ''}</option>`).join('')}</select></div></div><div class="switch-row"><label for="music-check">${icon('music')} 添加轻柔背景音乐</label><input class="switch" id="music-check" name="music" type="checkbox" ${(createDraft.music ?? c.preferences.music) ? 'checked' : ''}></div><div class="error-slot" id="create-error" role="alert"></div><button class="primary wide" type="submit">${icon('spark')} 开始制作 ${icon('arrow-right')}</button><p class="submit-hint">自动保存任务 · 页面可随时关闭，保持本地服务运行即可</p></form></section>${previewPanel()}</div>${stages()}<div class="section-heading"><h2>最近的制作</h2><a class="text-button" href="#works">全部作品 ${icon('arrow-right')}</a></div><div id="recent-tasks">${taskRows(state.tasks.slice(0,3))}</div>`;
  bindCreate();
}
function sourceInfo() { return scanResult && scanResult.folder === createDraft.source ? `<div class="source-files"><span>${icon('video')}${scanResult.counts.video} 段视频</span><span>${icon('image')}${scanResult.counts.image} 张照片</span><span>${icon('text')}${scanResult.counts.text} 份介绍</span></div><details class="file-table"><summary>查看 ${scanResult.files.length} 个素材文件</summary><ul>${scanResult.files.slice(0,100).map(f => `<li>${icon(f.kind === 'video' ? 'video' : f.kind === 'image' ? 'image' : 'text')}${E(f.name)}</li>`).join('')}</ul></details>` : ''; }
function bindCreate() {
  const f = $('#create-form');
  f.addEventListener('input', () => { const v = Object.fromEntries(new FormData(f)); createDraft = {...v, music:f.elements.music.checked}; saveDraft(); });
  f.addEventListener('submit', async e => {
    e.preventDefault();
    const button = f.querySelector('[type=submit]');
    const error = $('#create-error'); error.className = 'inline-error error-slot'; error.textContent = '';
    await busy(button, async () => {
      try {
        const t = await api('/api/tasks', {...createDraft, source: f.elements.source.value || createDraft.source, music:f.elements.music.checked, person:f.elements.person.value, count:Number(f.elements.count.value)});
        await refresh(false);
        if (state.config.agent.state === 'connected') {
          await api(`/api/tasks/${t.id}/start`, {}); await refresh(false); setRoute('task/' + t.id);
        } else { notify('制作要求已保存，连接 Agent 后即可开始'); setRoute('task/' + t.id); }
      } catch (err) { error.textContent = err.message; }
    });
  });
  $('#person-select').addEventListener('change', () => { const person = state.config.people.find(p => p.id === f.elements.person.value); const img = $('#selected-portrait'); if (img && person?.hasPortrait) {img.src = '/media/person/' + person.id; img.alt = person.name;} });
}
function credentialStatus(saved) {
  return saved ? `<span class="saved-mark">${icon('check')} 已保存</span>` : badge('待填写','warn');
}
function credentialLink(url, label) {
  return `<a class="credential-link" href="${E(url)}" target="_blank" rel="noopener noreferrer">${E(label)} ${icon('arrow-up-right')}</a>`;
}
function modelKeyGuide(provider) {
  if(provider==='ark')return credentialLink('https://ark.volcengine.com/region:cn-beijing/apiKey?apikey=%7B%7D','获取火山方舟 API Key');
  if(provider==='deepseek')return credentialLink('https://platform.deepseek.com/api_keys','获取 DeepSeek API Key');
  return `到 Base URL 对应的服务商后台创建 Key。如果使用 OpenAI，${credentialLink('https://platform.openai.com/api-keys','打开 OpenAI API Keys')}。`;
}
function volcengineSpeechKeyUrl(project) {
  return 'https://console.volcengine.com/speech/new/setting/apikeys?projectName='+encodeURIComponent(project?.trim()||'default');
}
function credentialField(name, label, saved, placeholder, maxLength, guide='') {
  return `<div class="form-group">
    <div class="label-row"><label for="key-${name}">${label}</label>${credentialStatus(saved)}</div>
    <div class="input-wrap">
      <input id="key-${name}" class="input" name="${name}" type="password" autocomplete="new-password" spellcheck="false"
        ${maxLength?`maxlength="${maxLength}"`:''} placeholder="${saved ? '已配置 · 留空保留原值' : placeholder}">
      <button type="button" class="reveal" data-action="reveal" aria-label="显示${label}" aria-pressed="false">${icon('eye')}</button>
    </div>
    ${guide}
  </div>`;
}
function modelSettings() {
  const model = state.config.model;
  const provider = model.providers[model.provider];
  const custom = model.providers.custom;
  const isCustom = model.provider==='custom';
  return `<section class="panel">
    <div class="settings-section-heading"><span>${icon('spark')}</span><div><h2>制作模型</h2>
    <p>用于策划、选片和视觉复核。</p></div></div>
    <div class="form-row">
      <div class="form-group"><label class="field-label" for="model-provider">模型平台</label>
      <select id="model-provider" name="model_provider">${Object.entries(model.providers).map(([id,p])=>`<option value="${E(id)}" ${id===model.provider?'selected':''}>${E(p.label)}</option>`).join('')}</select></div>
      <div class="form-group"><label class="field-label" for="model-id">模型 ID</label>
      <input class="input" id="model-id" name="model_id" value="${E(provider.model)}" placeholder="输入服务提供的模型 ID" spellcheck="false" maxlength="160"></div>
    </div>
    <p class="form-help" id="model-preset-url" ${isCustom?'hidden':''}>官方地址 <code id="model-url">${E(provider.baseUrl)}</code></p>
    <div class="form-group" id="model-base-url-fields" ${isCustom?'':'hidden'}>
      <label class="field-label" for="model-base-url">Base URL</label>
      <input id="model-base-url" name="model_base_url" class="input" type="url" spellcheck="false" maxlength="1024"
        value="${E(custom?.baseUrl || '')}" placeholder="例如 https://api.example.com/v1" ${isCustom?'':'disabled'}>
      <p class="form-help">填服务商给的接口地址。工作台会自动补上 /chat/completions。</p>
    </div>
    ${credentialField('model_key','模型 API Key',provider.configured,'粘贴当前地址的推理 API Key',512,
      `<p class="form-help" id="model-key-guide">${modelKeyGuide(model.provider)}</p>`)}
    <details class="form-group" id="model-advanced" ${isCustom?'':'hidden'}>
      <summary class="field-label">高级选项</summary>
      <p class="form-help">鉴权方式为 Bearer API Key。如果服务不支持 JSON 模式，可以改用提示词。</p>
      <fieldset class="form-group">
        <legend class="field-label">JSON 输出方式</legend>
        <label><input type="radio" name="model_output_mode" value="json" ${custom?.outputMode==='prompt'?'':'checked'}> JSON 模式（默认）</label>
        <label><input type="radio" name="model_output_mode" value="prompt" ${custom?.outputMode==='prompt'?'checked':''}> 用提示词要求 JSON</label>
      </fieldset>
    </details>
    <div class="form-group">
      <label class="field-label" for="production-timeout">单条制作时限（分钟）</label>
      <input class="input" id="production-timeout" name="production_timeout" type="number" min="1" step="1" required value="${E(model.productionTimeoutMinutes)}">
      <p class="form-help">从接单开始计时，超时停止该条并保留已有文件。</p>
    </div>
    <p class="form-help">制作时会向所选平台发送必要的机构说明和审核抽帧。连接测试只发送一张合成色块图。</p>
    ${serviceTestControl('model','测试模型连接')}
  </section>`;
}
function serviceTestControl(kind,label,detail='') {
  return `<div class="form-group"><button type="button" class="secondary" data-test-service="${kind}">${label}</button>
    ${detail?`<p class="form-help">${detail}</p>`:''}<p class="form-help" role="status" id="test-${kind}-result"></p></div>`;
}
function speechTestOptions(config, provider) {
  const people=config.people.filter(person=>person.ready&&boundVoicePlatform(person)===provider);
  const selected=people.find(person=>person.isDefault)||people[0];
  return people.length?people.map(person=>`<option value="${E(person.id)}" ${person===selected?'selected':''}>${E(person.name)}</option>`).join('')
    : '<option value="">还没有绑定此平台音色的人物</option>';
}
function productionServiceSettings(config) {
  const conn = config.connection;
  const tos = conn.storage.provider === 'tos';
  const provider=config.voice.provider, fire=provider==='volcengine';
  const testOptions=speechTestOptions(config,provider);
  const canTest=config.people.some(person=>person.ready&&boundVoicePlatform(person)===provider);
  return `<section class="panel" aria-labelledby="production-services-title">
    <div class="settings-section-heading"><span>${icon('clapper')}</span><h2 id="production-services-title">制作服务</h2></div>
    <section class="settings-service" aria-labelledby="speech-settings-title">
      <div class="settings-service-heading">
        <h3 id="speech-settings-title">语音合成与复刻</h3>
      </div>
      <div class="form-group">
        <h4 class="field-label">音色复刻设置</h4>
        <div class="form-group"><label class="field-label" for="voice-provider">默认语音平台</label>
          <select name="voice_provider" id="voice-provider">${voicePlatformOptions(provider)}</select>
          <p class="form-help">新增数字人默认用这个平台，添加或编辑人物时也能单独选。</p>
        </div>
        <fieldset class="provider-fields" data-speech-provider="volcengine" ${fire?'':'hidden disabled'}>
        ${credentialField('api_key','火山语音 API Key',conn.speech,'填写火山语音 API Key',undefined,
          `<p class="form-help" id="fire-speech-key-guide">${credentialLink(volcengineSpeechKeyUrl(config.voice.project),'获取该项目的火山语音 API Key')}</p>`)}
        <div class="form-group">
          <label class="field-label" for="voice-project">火山项目</label>
          <input class="input" name="voice_project" id="voice-project" value="${E(config.voice.project)}" placeholder="default">
          <p class="form-help">复刻时优先用空槽位。拉取列表需要先保存下方的火山 AK / SK。</p>
          <div class="person-voice-actions">
            <button class="secondary" type="button" data-action="view-voice-slots">拉取音色槽位</button>
            <a href="https://console.volcengine.com/speech/new/overview?projectName=${encodeURIComponent(config.voice.project)}" target="_blank" rel="noreferrer">到火山购买预付费音色</a>
          </div>
        </div>
        </fieldset>
        <fieldset class="provider-fields" data-speech-provider="minimax" ${provider==='minimax'?'':'hidden disabled'}>
        <div class="form-group">
          <div class="label-row"><label for="minimax-api-key">MiniMax API Key</label>${credentialStatus(config.voice.minimaxConfigured)}</div>
          <input class="input" id="minimax-api-key" name="minimax_api_key" type="password" autocomplete="new-password" maxlength="512"
            placeholder="${config.voice.minimaxConfigured?'已配置 · 留空保留原值':'填写 MiniMax 开放平台 API Key'}">
          <p class="form-help">${credentialLink('https://platform.minimax.cn/console/keys','获取 MiniMax 接口密钥')}</p>
        </div>
        <div class="form-group">
          <label class="field-label" for="minimax-model">MiniMax 合成模型</label>
          <select name="minimax_model" id="minimax-model">
            <option value="speech-2.8-hd" ${config.voice.minimaxModel==='speech-2.8-hd'?'selected':''}>2.8 HD · 音质优先</option>
            <option value="speech-2.8-turbo" ${config.voice.minimaxModel==='speech-2.8-turbo'?'selected':''}>2.8 Turbo · 速度优先</option>
          </select>
        </div>
        </fieldset>
        <fieldset class="provider-fields" data-speech-provider="aliyun" ${provider==='aliyun'?'':'hidden disabled'}>
          <div class="form-group">
            <div class="label-row"><label for="aliyun-api-key">百炼 API Key（北京）</label>${credentialStatus(config.voice.aliyunConfigured)}</div>
            <input class="input" id="aliyun-api-key" name="aliyun_api_key" type="password" autocomplete="new-password" maxlength="512"
              placeholder="${config.voice.aliyunConfigured?'已配置 · 留空保留原值':'填写阿里百炼北京区 API Key'}">
            <p class="form-help">${credentialLink('https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key','获取百炼北京区 API Key')}</p>
          </div>
          <div class="form-group">
            <label class="field-label" for="aliyun-workspace">北京区业务空间 ID <span class="optional">使用百炼时必填</span></label>
            <input class="input" name="aliyun_workspace" id="aliyun-workspace" value="${E(config.voice.aliyunWorkspace)}" placeholder="例如 ws-xxxxxxxx" maxlength="63" required aria-describedby="aliyun-workspace-help">
            <p class="form-help" id="aliyun-workspace-help">${credentialLink('https://bailian.console.aliyun.com/cn-beijing','打开百炼北京区控制台')}。点击右上角的「默认业务空间」或当前空间名称，复制「业务空间ID」一行，直接粘贴到这里。</p>
          </div>
          <div class="form-group">
            <label class="field-label" for="aliyun-model">默认百炼语音模型</label>
            <select name="aliyun_model" id="aliyun-model">${aliyunModelOptions(config.voice.aliyunModel)}</select>
            <p class="form-help">新复刻默认用这个模型，也可以在数字人页另选。</p>
          </div>
        </fieldset>
      </div>
      <div class="form-group"><label class="field-label" for="test-person">测试音色</label>
        <select id="test-person" ${canTest?'':'disabled'}>${testOptions}</select>
        <p class="form-help" id="speech-test-note" ${canTest?'hidden':''}>先在「数字人」绑定此平台的音色，再回来测试。</p>
      </div>
      ${serviceTestControl('speech','测试语音连接','用所选音色合成四字短句，按语音平台规则计费。')}
    </section>
    <section class="settings-service" aria-labelledby="avatar-settings-title">
      <div class="settings-service-heading">
        <h3 id="avatar-settings-title">数字人视频 · 火山 OmniHuman 2.0</h3>
      </div>
      <p class="form-help form-group">生成数字人视频时，配音可以来自任一语音平台，视频仍由火山生成。</p>
      <div class="form-row">${credentialField('ak','Access Key ID',conn.ak,'填写 Access Key ID')}${credentialField('sk','Secret Access Key',conn.sk,'填写 Secret Access Key')}</div>
      <p class="form-help form-group">${credentialLink('https://console.volcengine.com/iam/keymanage/','获取火山 Access Key ID 和 Secret Access Key')}</p>
      ${serviceTestControl('avatar','测试数字人连接','只验证任务查询权限。')}
    </section>
    <section class="settings-service" aria-labelledby="storage-settings-title">
      <div class="settings-service-heading"><h3 id="storage-settings-title">文件中转</h3></div>
      <div class="form-group"><label class="field-label" for="storage-provider">音频中转方式</label>
        <select id="storage-provider" name="storage_provider">
          <option value="tmpfile" ${tos?'':'selected'}>tmpfile · 无需账号和 Key</option>
          <option value="tos" ${tos?'selected':''}>TOS · 使用自己的存储桶</option>
        </select>
        <p class="form-help" id="tmpfile-note" ${tos?'hidden':''}>tmpfile 用于向数字人服务传递音频。持有链接者可下载，文件 7 天后自动删除。</p>
      </div>
      <fieldset class="provider-fields" id="tos-fields" ${tos?'':'hidden disabled'}>
        <div class="form-group"><label class="field-label" for="bucket">存储桶名称</label>
          <input class="input" name="bucket" id="bucket" value="${E(conn.storage.bucket || '')}">
        </div>
        <div class="form-row">
          <div class="form-group"><label class="field-label" for="region">区域</label>
            <input class="input" name="region" id="region" placeholder="cn-beijing" value="${E(conn.storage.region || '')}"></div>
          <div class="form-group"><label class="field-label" for="endpoint">Endpoint</label>
            <input class="input" name="endpoint" id="endpoint" placeholder="tos-cn-beijing.volces.com" value="${E(conn.storage.endpoint || '')}"></div>
        </div>
        ${serviceTestControl('storage','测试 TOS 存储桶','使用上面的 AK / SK 检查存储桶访问权限。')}
      </fieldset>
    </section>
  </section>`;
}
function modelFields(form) {
  return {provider:form.elements.model_provider.value,model:form.elements.model_id.value,
          baseUrl:form.elements.model_base_url.value,outputMode:form.elements.model_output_mode.value,
          apiKey:form.elements.model_key.value,
          productionTimeoutMinutes:form.elements.production_timeout.valueAsNumber};
}
function bindModelSettings(form) {
  const model = state.config.model;
  if (!model) return;
  form.insertAdjacentHTML('afterbegin',modelSettings());
  const testResult=$('#test-model-result');
  const modelKey=form.elements.model_key;
  const keyButton=modelKey.parentElement.querySelector('.reveal');
  const resetKeyReveal=()=>{
    hideStoredCredential(keyButton);
    modelKey.type='password';
    setCredentialButton(keyButton,'模型 API Key',false);
  };
  const setKeyHint=()=>{
    const fields=modelFields(form), selected=model.providers[fields.provider];
    const sameAddress=fields.provider!=='custom'||fields.baseUrl.trim().replace(/\/+$/,'').replace(/\/chat\/completions$/,'')===selected.baseUrl;
    const saved=selected.configured&&sameAddress;
    const status=modelKey.closest('.form-group').querySelector('.label-row > span');
    if(status.classList.contains('saved-mark')!==saved)status.outerHTML=credentialStatus(saved);
    modelKey.placeholder=saved?'已配置 · 留空保留原值':'粘贴当前地址的推理 API Key';
  };
  form.elements.model_provider.addEventListener('change',e=>{
    const p=model.providers[e.target.value], isCustom=e.target.value==='custom';
    resetKeyReveal();
    form.elements.model_id.value=p.model;
    modelKey.value='';
    $('#model-url').textContent=p.baseUrl;
    $('#model-preset-url').hidden=isCustom;
    $('#model-base-url-fields').hidden=!isCustom;
    $('#model-advanced').hidden=!isCustom;
    form.elements.model_base_url.disabled=!isCustom;
    $('#model-key-guide').innerHTML=modelKeyGuide(e.target.value);
    setKeyHint();
    testResult.textContent='';
  });
  form.addEventListener('input',e=>{
    if(!['model_base_url','model_id','model_key','model_output_mode'].includes(e.target.name))return;
    if(e.target.name==='model_base_url')resetKeyReveal();
    setKeyHint();
    testResult.textContent='';
  });
}
function toggleProviderFields(fields, visible) {
  fields.hidden=fields.disabled=!visible;
}
function bindServiceSettings(form) {
  bindModelSettings(form);
  form.elements.voice_project.addEventListener('input',event=>{
    $('#fire-speech-key-guide a').href=volcengineSpeechKeyUrl(event.target.value);
  });
  const provider=form.elements.voice_provider;
  const person=$('#test-person');
  const result=$('#test-speech-result');
  const speechButton=form.querySelector('[data-test-service="speech"]');
  const readyForTest=()=>{speechButton.disabled=!person.value||speechButton.hasAttribute('aria-busy');};
  provider.addEventListener('change',()=>{
    for(const fields of form.querySelectorAll('[data-speech-provider]')){
      const visible=fields.dataset.speechProvider===provider.value;
      if(!visible)fields.querySelectorAll('.reveal').forEach(button=>{
        hideStoredCredential(button);
        const input=button.parentElement.querySelector('input[name]');
        input.type='password';
        setCredentialButton(button,input.labels[0].textContent,false);
      });
      toggleProviderFields(fields,visible);
    }
    person.innerHTML=speechTestOptions(state.config,provider.value);
    person.disabled=!person.value;
    $('#speech-test-note').hidden=Boolean(person.value);
    result.textContent='';
    readyForTest();
  });
  person.addEventListener('change',()=>{result.textContent='';});
  readyForTest();
  form.elements.storage_provider.addEventListener('change',e=>{
    const tos=e.target.value==='tos';
    toggleProviderFields($('#tos-fields'),tos);
    $('#tmpfile-note').hidden=tos;
    $('#test-storage-result').textContent='';
  });
  form.querySelectorAll('[data-test-service]').forEach(button=>button.addEventListener('click',()=>{
    const kind=button.dataset.testService,status=$('#test-'+kind+'-result');
    const speechFields={volcengine:['api_key'],minimax:['minimax_api_key','minimax_model'],
      aliyun:['aliyun_api_key','aliyun_workspace']};
    const fields=kind==='speech'?speechFields[provider.value]
      :kind==='storage'?['ak','sk','bucket','region','endpoint']:['ak','sk'];
    const request=()=>kind==='model'?modelFields(form):{kind,
      ...(kind==='speech'?{person:person.value,provider:provider.value}:{}),
      connection:Object.fromEntries(fields.map(name=>[name,form.elements[name].value]))};
    const payload=request();
    const isCurrent=()=>form.isConnected&&(kind!=='storage'||form.elements.storage_provider.value==='tos')
      &&JSON.stringify(request())===JSON.stringify(payload);
    status.className='form-help';
    status.textContent='正在测试…';
    return busy(button,async()=>{
      try {
        const result=await api(kind==='model'?'/api/model/test':'/api/services/test',payload);
        if(isCurrent()){status.className='saved-mark';status.textContent=result.message;}
      } catch(err) {if(isCurrent()){status.className='inline-error';status.textContent=err.message;}}
    }).finally(()=>{if(kind==='speech'&&form.isConnected)readyForTest();});
  }));
}
function renderSettings() {
  const c = state.config;
  main.innerHTML = `${heading('先准备好，以后就简单了。','服务与数字人只需配置一次。设置保存在你的本地项目。')}<div class="settings-layout"><form class="settings-stack" id="settings-form">
    ${productionServiceSettings(c)}
    <section class="panel"><div class="settings-section-heading"><span>${icon('folder')}</span><div><h2>工作区</h2><p>新批次的工程和成品都保存在这里。已有任务继续使用自己的目录。</p></div></div>
      <div class="form-group"><label class="field-label" for="workspace-directory">工作区目录</label>
      <input class="input" id="workspace-directory" name="workspace" value="${E(c.workspaceSetting || '')}" placeholder="${E(c.workspaceDefault || c.project)}">
      <p class="form-help">留空使用启动目录。成品保存在「批次 / 视频 / 交付产物」。</p></div>
    </section>
    ${c.agent.mode === 'desktop' ? `<section class="panel"><div class="settings-section-heading"><span>${icon('clapper')}</span><div><h2>用 Codex 接手制作</h2></div></div><div id="agent-config-status">${agentStatusBlock()}</div></section>` : ''}
    <section class="panel"><div class="settings-section-heading"><span>${icon('settings')}</span><div><h2>你的制作偏好</h2><p>这些设置将用于之后创建的新任务。</p></div></div><div class="form-group"><label class="field-label" for="cover-header" style="margin-bottom:10px">封面固定头标</label><input class="input" name="coverHeader" id="cover-header" value="${E(c.preferences.coverHeader)}" placeholder="例如：重庆养老院探访"></div><div class="switch-row"><label for="default-music">${icon('music')} 默认添加背景音乐</label><input type="checkbox" class="switch" id="default-music" name="music" ${c.preferences.music ? 'checked' : ''}></div></section><div class="sticky-actions"><button class="primary" type="submit">${icon('save')} 保存本地设置</button><button class="secondary" type="submit" data-next="people">下一步：配置数字人 ${icon('arrow-right')}</button><span id="settings-error" class="inline-error error-slot" role="alert"></span></div></form><aside class="settings-guide"><h3>第一次使用，跟着做就好。</h3><ol><li><strong>配置制作服务</strong>在本页配置模型、语音和数字人服务。</li><li><strong>添加你自己的数字人</strong>准备一张正面照片。绑定已有音色，或先用录音单独复刻。</li><li><strong>选择机构素材，开始制作</strong>放入实拍视频和真实介绍，写下这条视频的想法。</li></ol><p>留空的 Key 会保留原配置。保存后输入框自动清空，页面只显示是否已配置。</p><div class="path-note">当前项目<br>${E(c.project)}<p style="margin-top:10px">配置位于项目的 .montage 目录，更新 skill 后仍会保留。</p></div><section class="panel"><h3>本地工作台</h3><p class="form-help">空闲时可退出并停止本地服务。</p><button type="button" class="secondary" data-action="exit-workbench">退出工作台</button></section></aside></div>`;
  bindServiceSettings($('#settings-form'));
  const setupTarget = {'create-model':'#key-model_key', 'create-speech':'#voice-provider', 'create-video':'#key-ak'}[location.hash.split('/')[1]];
  if (setupTarget) {
    const form = $('#settings-form');
    form.querySelector('.sticky-actions [type=submit]:not([data-next])').dataset.next = 'create';
    form.querySelector('[data-next="create"]').textContent = '保存并返回新建制作';
    form.querySelector('[data-next="people"]').hidden = true;
    requestAnimationFrame(() => $(setupTarget)?.focus());
  }
  $('#settings-form').addEventListener('submit', async e => {
    e.preventDefault(); const next = e.submitter?.dataset.next; const form = e.target, data = Object.fromEntries(new FormData(form)); $('#settings-error').textContent = '';
    await busy(form.querySelector('[type=submit]'), async () => {
      try { await api('/api/config', {workspace:data.workspace, connection:data, model:state.config.model?modelFields(form):undefined, preferences:{coverHeader:data.coverHeader, music:form.elements.music.checked}}); await refresh(false); if(next)setRoute(next); else renderSettings(); notify('设置已保存到本地项目'); }
      catch (err) { $('#settings-error').textContent = err.message; }
    });
  });
}
function agentStatusBlock() {
  const a = state.config.agent;
  return `<div class="agent-status-box"><span>${icon('clapper')}</span><div><strong>${a.uncertain ? '等待进度回传' : a.state === 'connected' ? a.label+' 已连接' : a.installed ? '已检测到 Agent' : '等待宿主连接'}</strong><p>${E(a.message)}</p></div><button type="button" class="secondary" data-action="check-agent">${icon('retry')}检查连接</button></div>${a.probe==='passed' ? '<p class="saved-mark">网页提交 → Agent 接收 → 回传进度，连接自检已通过。</p>' : ['pending','claimed'].includes(a.probe) ? '<p class="form-help">连接自检中，正在等待 Agent 回传…</p>' : ''}${a.state !== 'connected' ? `<div class="inline-note"><strong>只需在 Agent 发送一次这句话</strong><p style="margin:8px 0 12px">${E(a.instruction)}</p><button type="button" class="secondary" data-action="copy-agent">复制连接指令 ${icon('text')}</button> <p style="margin:10px 0 0">Agent 接上后，这里自动变为已连接。之后直接在网页点“开始制作”。</p></div>` : ''}`;
}
async function showVoiceSlots(opener, person) {
  const dialog=managementDialog(person?'选择复刻用的音色':'火山音色槽位',opener);
  const body=dialog.querySelector('.management-dialog-content');
  const close='<form method="dialog"><button class="secondary" type="submit">关闭</button></form>';
  try{
    const project=person?undefined:$('#voice-project')?.value;
    const {slots}=await api('/api/voice-slots',{person,project});
    const empty=slots.filter(slot=>slot.empty&&slot.selectable).length;
    const label=slot=>[slot.id,slot.status,slot.owner?`已绑定 ${slot.owner}`:'',slot.remaining==null?'':`还可复刻 ${slot.remaining} 次`].filter(Boolean).join(' · ');
    body.innerHTML=`<p class="form-help">共 ${slots.length} 个槽位，${empty} 个空槽位可用。</p>`;
    if(!person){
      body.insertAdjacentHTML('beforeend',`<ul>${slots.map(slot=>`<li>${E(label(slot))}</li>`).join('')}</ul>${close}`);
      return;
    }
    body.insertAdjacentHTML('beforeend',`<label class="field-label" for="voice-slot-picker">音色槽位</label>
      <select id="voice-slot-picker" data-native-select class="input"><option value="" ${empty?'':'disabled'} selected>${empty?'自动分配空槽位':'没有空槽位，请手动选择'}</option>
      ${slots.map(slot=>`<option value="${E(slot.id)}" ${slot.selectable?'':'disabled'}>${E(label(slot))}</option>`).join('')}</select>
      <p class="form-help" data-slot-warning hidden></p>
      <label data-slot-consent hidden><input type="checkbox"> 我确认覆盖原音色</label>
      <p class="inline-error" role="alert"></p>
      <div class="person-voice-actions"><button class="primary" type="button" data-submit-slot>复刻</button>${close}</div>`);
    const select=body.querySelector('select'), submit=body.querySelector('[data-submit-slot]');
    const warning=body.querySelector('[data-slot-warning]'), consent=body.querySelector('[data-slot-consent]');
    const checkbox=consent.querySelector('input');
    const current=()=>slots.find(slot=>slot.id===select.value);
    const update=()=>{
      const slot=current(), overwrites=Boolean(slot&&!slot.empty);
      warning.hidden=consent.hidden=!overwrites;
      warning.textContent=overwrites?(slot.owner?`复刻会覆盖这个槽位里「${slot.owner}」原来的声音，并将槽位绑定到当前人物。`:'复刻会覆盖这个槽位原来的声音。'):'';
      submit.disabled=overwrites?!checkbox.checked:!slot&&!empty;
    };
    select.addEventListener('change',()=>{checkbox.checked=false;update();});
    checkbox.addEventListener('change',update);
    submit.addEventListener('click',()=>busy(submit,async()=>{
      try{
        await api(`/api/people/${encodeURIComponent(person)}/voice-training`,{provider:'volcengine',slot:select.value,overwrite:checkbox.checked});
        dialog.close();
        await refresh();
      }catch(error){body.querySelector('[role="alert"]').textContent=error.message;}
    }));
    update();
  }catch(error){body.innerHTML=`<p class="inline-error" role="alert">${E(error.message)}</p>${close}`;}
}

function personVoicePlatform(person) {
  return person.voice_provider||(person.speaker?'volcengine':state.config.voice.provider);
}
function voiceUploadControl(person, label, provider='') {
  return `<input type="file" id="person-voice-file" data-person="${E(person.id)}" data-provider="${provider}" accept=".wav,.mp3,.m4a,.aac,.flac,.ogg" hidden><button type="button" class="secondary" data-action="pick-voice-sample">${icon('plus')} ${label}</button>`;
}
function minimaxVoiceSwitch(person, voice) {
  const options=voice.minimaxSwitch;
  const issue=['failed','paused'].includes(voice.state);
  return `<div class="person-voice">
    <p class="form-help">当前使用火山音色。MiniMax 复刻成功后会自动切换。</p>
    ${options.started&&voice.message?`<p class="${issue?'inline-error':'form-help'}" role="status">${E(voice.message)}</p>`:''}
    ${options.canUpload?`<p class="form-help">${options.hasSample?'可以用已保存的录音复刻，也可以更换录音。':'请先上传至少 10 秒的单人录音。'}</p>`:''}
    ${!state.config.voice.minimaxConfigured?'<p class="form-help">复刻前请到<a href="#settings">设置</a>保存 MiniMax API Key。</p>':''}
    <div class="person-voice-actions">
      ${options.canUpload?voiceUploadControl(person,options.hasSample?'更换录音':'上传录音','minimax'):''}
      <button type="button" class="secondary" data-action="start-voice-training" data-id="${E(person.id)}" data-provider="minimax"
        ${options.canStart&&options.hasSample?'':'disabled'}>复刻</button>
    </div>
    ${options.canStart&&options.hasSample?'<p class="form-help">点击「复刻」会把录音发送给 MiniMax。</p>':''}
  </div>`;
}
function aliyunPersonVoice(person, voice, selectedModel) {
  const options=voice.aliyunClone;
  const issue=['failed','paused'].includes(voice.state);
  const model=options.resuming?options.model:selectedModel;
  const showStatus=options.started||(voice.state==='ready'&&voice.attemptProvider==='aliyun');
  return `<div class="person-voice">
    ${person.speaker?'<p class="form-help">新音色复刻成功后会自动换绑。</p>':''}
    ${showStatus&&voice.message?`<p class="${issue?'inline-error':'form-help'}" role="status">${E(voice.message)}</p>`:''}
    ${options.resuming?`<p class="form-help">正在处理的模型为 ${E(aliyunModels[model])}。</p>`:''}
    ${options.canUpload?`<p class="form-help">${options.hasSample?'可用已保存的录音，也可以更换。':'请上传录音。'}建议 10–20 秒清晰单人朗读，最长 60 秒。</p>`:''}
    ${!state.config.voice.aliyunConfigured?'<p class="form-help">请先在<a href="#settings">设置</a>填写百炼 API Key 和北京区业务空间 ID。</p>':''}
    <div class="person-voice-actions">
      ${options.canUpload?voiceUploadControl(person,options.hasSample?'更换录音':'上传录音','aliyun'):''}
      <button type="button" class="secondary" data-action="start-voice-training" data-id="${E(person.id)}" data-provider="aliyun"
        data-model="${E(model)}" ${options.canStart&&(options.hasSample||options.resuming)?'':'disabled'}>复刻</button>
    </div>
    ${options.canStart&&options.hasSample&&!options.resuming?'<p class="form-help">点击「复刻」会把录音发送给阿里百炼。</p>':''}
  </div>`;
}
function personVoice(person, editor=false, provider=personVoicePlatform(person), model=person.aliyun_model||state.config.voice.aliyunModel) {
  const voice=person.voiceTraining;
  if(!voice)return '';
  if(editor&&provider==='aliyun'&&voice.aliyunClone)return aliyunPersonVoice(person,voice,model);
  if(editor&&person.speaker&&provider!==boundVoicePlatform(person)){
    if(provider==='minimax'&&voice.minimaxSwitch)return minimaxVoiceSwitch(person,voice);
    return `<p class="form-help">当前绑定的是 ${voicePlatforms[boundVoicePlatform(person)]} 音色。填入所选平台的音色 ID 并保存，即可切换。</p>`;
  }
  const id=person.custom_speaker_id||person.speaker;
  const active=['queued','checking','submitting','training'].includes(voice.state);
  const canStart=(voice.canStart||voice.hasReplacement)&&!active;
  const platform=voice.needsSlot?provider:voice.attemptProvider||provider;
  const label=voicePlatforms[platform];
  const permission=voice.needsPermission&&(!voice.needsSlot||voice.attemptProvider===platform);
  const text=(voice.replacePostpaid?`上次用了火山后付费。点「复刻」可用${platform==='volcengine'?'已购买的火山槽位':' MiniMax'}重新复刻。`:permission||!voice.needsPermission?voice.message:'')||(!id&&(
    voice.canQuery?'上次复刻还没完成，点「复刻」继续。':voice.hasSample?'录音已保存，可以复刻。':'请上传录音或填写已有音色 ID。'));
  return `<div class="person-voice">
    ${id&&!editor?`<p class="form-help">${voicePlatforms[boundVoicePlatform(person)]}音色 ${person.aliyun_model?`· ${E(aliyunModels[person.aliyun_model])} `:''}<code>${E(id)}</code></p>`:''}
    ${text?`<p class="${['failed','paused'].includes(voice.state)?'inline-error':'form-help'}" role="status">${E(text)}</p>`:''}
    ${permission&&platform==='volcengine'?'<p class="form-help"><a href="https://console.volcengine.com/speech/new/overview?projectName=default" target="_blank" rel="noreferrer">打开火山语音控制台</a></p>':''}
    ${editor&&voice.demoAudio?`<audio controls preload="none" aria-label="试听复刻音色" src="${E(voice.demoAudio)}"></audio>`:''}
    ${editor&&voice.hasReplacement?'<p class="form-help">新录音已保存。点「复刻」会沿用原来的音色 ID。</p>':''}
    <div class="person-voice-actions">
      ${editor&&voice.canUpload?voiceUploadControl(person,voice.hasSample||voice.canRetrain?'更换录音':'上传录音'):''}
      ${canStart?`<button type="button" class="secondary" data-action="start-voice-training" data-id="${E(person.id)}" data-retrain="${Boolean(voice.hasReplacement)}" data-provider="${platform}">复刻</button>`:''}
    </div>
    ${editor&&voice.canRetrain?'<p class="form-help">首次用这个音色点「测试语音连接」也会收音色费，之后就不能重练。</p>':''}
    ${editor&&canStart&&!voiceConfigured(state.config,platform)?`<p class="form-help">请先到<a href="#settings">设置</a>保存 ${label} 的 API Key。</p>`:''}
    ${editor&&canStart&&voice.needsSlot?`<p class="form-help">${platform==='volcengine'?'点「复刻」可选择音色槽位，默认用空位。':'点「复刻」会把录音发给 MiniMax，创建一个新音色。'}</p>`:''}
  </div>`;
}
function personReadiness(person) {
  const status=person.voiceTraining?.state;
  const active=['queued','checking','submitting','training'].includes(status);
  const issue=person.voiceTraining?.needsPermission?'缺少复刻权限':status==='failed'?'复刻失败':status==='paused'?'复刻中断':'';
  return badge(active?'复刻进行中':issue||(person.ready?'已就绪':'待绑定音色'),person.ready&&!active&&!issue?'':'warn');
}
let voicePreviewUrl;
function voicePreviewBinding(person) {
  return person?.speaker?[boundVoicePlatform(person),person.speaker,person.custom_speaker_id??null,
    person.aliyun_model??null,person.speech_rate??null]:null;
}
function clearVoicePreview() {
  const player=$('#voice-preview-player');
  player?.querySelector('audio')?.pause();
  player?.replaceChildren();
  if(voicePreviewUrl)URL.revokeObjectURL(voicePreviewUrl);
  voicePreviewUrl=undefined;
}
function syncVoicePreview(person, previous) {
  const section=$('#voice-preview');
  if(!section)return;
  const binding=voicePreviewBinding(person);
  section.hidden=!binding;
  $('#voice-preview-platform').textContent=binding?voicePlatforms[binding[0]]:'';
  if(JSON.stringify(binding)!==JSON.stringify(voicePreviewBinding(previous))){
    clearVoicePreview();
    $('#voice-preview-status').textContent='';
  }
}
function bindVoicePreview(editing) {
  const button=$('#generate-voice-preview');
  if(!button)return;
  const status=$('#voice-preview-status'), player=$('#voice-preview-player'), text=$('#voice-preview-text');
  button.addEventListener('click',()=>busy(button,async()=>{
    const person=editing(), binding=voicePreviewBinding(person);
    if(!binding)return;
    const current=()=>button.isConnected&&JSON.stringify(voicePreviewBinding(editing()))===JSON.stringify(binding);
    clearVoicePreview();
    status.className='form-help';
    status.textContent='正在合成试听…';
    try{
      const response=await fetch(`/api/people/${encodeURIComponent(person.id)}/voice-preview`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:text.value,binding})
      });
      if(!response.ok)throw new Error((await response.json()).error||'试听合成失败。');
      const audio=await response.blob();
      if(!current())return;
      voicePreviewUrl=URL.createObjectURL(audio);
      const element=document.createElement('audio');
      element.controls=true;
      element.preload='metadata';
      element.setAttribute('aria-label','生成的音色试听');
      element.src=voicePreviewUrl;
      player.append(element);
      status.textContent='试听已生成，点击播放。';
    }catch(error){
      if(current()){
        status.className='inline-error';
        status.textContent=error.message;
      }
    }
  }));
}
function paintVoiceUpdates(previousPeople) {
  const previous=new Map(previousPeople.map(person=>[person.id,person]));
  for(const person of state.config.people){
    const prior=previous.get(person.id);
    if(prior&&JSON.stringify([prior.voiceTraining,voicePreviewBinding(prior)])===JSON.stringify([person.voiceTraining,voicePreviewBinding(person)]))continue;
    const suffix=CSS.escape(person.id);
    const voice=$(`[data-person-voice="${suffix}"]`);
    const badgeSlot=$(`[data-person-ready="${suffix}"]`);
    if(voice)voice.innerHTML=personVoice(person);
    if(badgeSlot)badgeSlot.innerHTML=personReadiness(person);
    if(person.id!==editPerson||!$('#person-voice-editor'))continue;
    syncVoicePreview(person,prior);
    for(const field of $('#person-form').querySelectorAll('[data-person-platform]')){
      const platform=field.dataset.personPlatform;
      const input=field.querySelector('[name="speaker"]');
      const oldValue=prior&&boundVoicePlatform(prior)===platform?prior.speaker||'':'';
      if(input.value===oldValue)input.value=boundVoicePlatform(person)===platform?person.speaker||'':'';
    }
    const custom=$('#custom-speaker'), provider=$('#person-voice-provider');
    if(custom.value===(prior?.custom_speaker_id||''))custom.value=person.custom_speaker_id||'';
    if(prior&&provider.value===personVoicePlatform(prior))provider.value=personVoicePlatform(person);
    const model=$('#person-aliyun-model');
    if(person.aliyun_model&&person.aliyun_model!==prior?.aliyun_model)model.value=person.aliyun_model;
    syncPersonVoice($('#person-form'),person);
  }
}
async function uploadPersonVoice(input) {
  const file=input.files[0];if(!file)return;
  const ident=input.dataset.person;
  const query=new URLSearchParams({name:file.name});
  if(input.dataset.provider)query.set('provider',input.dataset.provider);
  const button=input.nextElementSibling;
  await busy(button,async()=>{
    try {
      const response=await fetch(`/api/people/${encodeURIComponent(ident)}/voice-sample?${query}`,{
        method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});
      const result=await response.json();if(!response.ok)throw new Error(result.error);
      await refresh();notify('录音已保存');
    } catch(error) {notify(error.message,true);}
    finally {input.value='';}
  });
}
function formatTrackDuration(seconds) {
  const n = Math.round(seconds || 0), h = Math.floor(n / 3600), m = Math.floor(n / 60) % 60;
  return (h ? h + ':' : '') + String(m).padStart(h ? 2 : 1, '0') + ':' + String(n % 60).padStart(2, '0');
}
function renderPeople() {
  clearVoicePreview();
  main.innerHTML = `${heading('每条视频，都有自己的主角。','照片、音色、配乐、视频风格、片尾钩子与分发账号统一归档在人物资料中，选好人物即可制作。',`<button class="secondary" data-action="add-person">${icon('plus')} 添加数字人</button>`)}<div class="people-grid">${state.config.people.map(p => {
    const covers = state.styles?.covers || [];
    const videos = state.styles?.videos || [];
    const defCover = covers.find(x => x.id === state.styles?.defaultCoverId) || covers[0];
    const cEntry = p.cover_template_id ? covers.find(x => x.id === p.cover_template_id) : null;
    const vEntry = p.video_style_id ? videos.find(x => x.id === p.video_style_id) : null;
    let styleLine;
    if (!p.cover_template_id && !p.video_style_id) {
      styleLine = defCover ? `视频风格：${defCover.name}（默认）` : '视频风格：跟随工作台默认';
    } else if (cEntry && (!p.video_style_id || cEntry.pairId === p.video_style_id)) {
      styleLine = `视频风格：${cEntry.name}`;
    } else if (vEntry && !p.cover_template_id) {
      styleLine = `视频风格：${vEntry.name}`;
    } else {
      const cName = cEntry?.name || p.cover_template_id || '默认';
      const vName = vEntry?.name || p.video_style_id || '默认';
      styleLine = `封面：${cName} · 视频：${vName}`;
    }
    return `<article class="person-card"><div class="portrait-area">${p.hasPortrait ? `<img src="/media/person/${E(p.id)}" alt="${E(p.name)}的数字人照片">` : ''}${p.isDefault ? badge('默认出镜') : ''}</div><div class="person-card-content"><h2>${E(p.name)} <span data-person-ready="${E(p.id)}">${personReadiness(p)}</span></h2><p class="person-card-styles">${E(styleLine)}</p><button class="secondary" data-action="edit-person" data-id="${E(p.id)}">编辑人物资料 ${icon('arrow-right')}</button></div></article>`;
  }).join('')}<button class="add-person" data-action="add-person">${icon('plus')}<strong>添加一位新数字人</strong><small>形象、声音、风格、配乐与平台账号<br>一次绑好，做片直接复用。</small></button></div><div id="person-editor">${editPerson !== undefined ? personEditor() : ''}</div>`;
  bindPerson();
}
function personUploadFields() {
  return `<div class="form-row person-upload-fields">
    <div class="form-group">
      <label class="field-label" for="person-photo">人物图片 <span class="optional">必填</span></label>
      <input class="input" type="file" id="person-photo" name="photo" accept=".jpg,.jpeg,.png,.webp" aria-describedby="person-photo-help" required>
      <p id="person-photo-help" class="form-help">上传一张正面照片。JPG、PNG 或 WebP，最多 20 MB。</p>
    </div>
    <div class="form-group">
      <label class="field-label" for="person-recording">人物音频 <span class="optional">必填</span></label>
      <input class="input" type="file" id="person-recording" name="recording" accept=".wav,.mp3,.m4a,.aac,.flac,.ogg" aria-describedby="person-recording-help" required>
      <p id="person-recording-help" class="form-help">上传一段干净的单人录音，最多 10 MB。</p>
    </div>
  </div>`;
}
function personMusicOptions(boundId) {
  const tracks = state.music?.tracks || [];
  const available = tracks.filter(t => !t.archived);
  const def = tracks.find(t => t.id === state.music?.defaultId);
  const missing = boundId && !available.some(t => t.id === boundId);
  return `<option value="">跟随工作台默认配乐${def ? '（' + E(def.name) + '）' : ''}</option>` +
    available.map(t => `<option value="${E(t.id)}" ${t.id === boundId ? 'selected' : ''}>${E(t.name)} · ${formatTrackDuration(t.duration)}</option>`).join('') +
    (missing ? `<option value="${E(boundId)}" selected>原绑定配乐已收起，请重新选择</option>` : '');
}
function renderPersonMusicPreview(trackId) {
  const tracks = state.music?.tracks || [];
  const available = tracks.filter(t => !t.archived);
  const def = tracks.find(t => t.id === state.music?.defaultId);
  if (trackId) {
    const t = available.find(x => x.id === trackId);
    if (!t) return '<p class="inline-error">原绑定配乐已收起或不可用，请在上方重新选择一首配乐，或改为跟随默认。</p>';
    return `<div class="batch-music-caption"><span>专属配乐：${E(t.name)}</span><span>${formatTrackDuration(t.duration)}</span></div><audio data-duration="${t.duration}" controls preload="none" aria-label="试听数字人专属配乐" src="${E(t.url)}"></audio>`;
  }
  return def
    ? `<div class="batch-music-caption"><span>当前跟随默认：${E(def.name)}</span><span>${formatTrackDuration(def.duration)}</span></div><audio data-duration="${def.duration}" controls preload="none" aria-label="试听默认配乐" src="${E(def.url)}"></audio>`
    : '<p class="form-help">暂未设置默认配乐，可直接上传一首绑定给该数字人。</p>';
}
function personEditor() {
  const p = state.config.people.find(person=>person.id===editPerson);
  const provider=p?personVoicePlatform(p):state.config.voice.provider;
  const speaker=platform=>p&&boundVoicePlatform(p)===platform?p.speaker||'':'';
  const showCustom=provider==='volcengine'&&(p?.speaker==='custom_speaker_id'||Boolean(p?.custom_speaker_id));
  const boundMusicId = p?.music_track_id || '';
  const closingHook = p?.closing_hook || '';
  const xhsId = p?.xiaohongshu_account_id ?? p?.platform_accounts?.xiaohongshu ?? '';
  const dyId = p?.douyin_account_id ?? p?.platform_accounts?.douyin ?? '';
  const channelsId = p?.channels_account_id ?? p?.platform_accounts?.channels ?? '';
  const modules = [
    ['profile', '个人信息'],
    ['voice', '声音与配音'],
    ['narration', '口播内容'],
    ['visual', '配乐与视觉'],
    ['platforms', '分发账号'],
  ];
  return `<section class="panel editor-panel">
    <div class="editor-heading"><div><h2>${p?'编辑 '+E(p.name):'添加一个数字人'}</h2><p class="form-help" style="margin-top:4px">按模块管理数字人的形象、声音、口播、视觉与平台账号配置。</p></div><button data-action="close-editor" aria-label="关闭人物编辑">${icon('close')}</button></div>
    <div class="person-module-nav" role="navigation" aria-label="人物资料模块">${modules.map(([id, label]) => `<button type="button" class="person-module-pill" data-module-jump="${id}">${label}</button>`).join('')}</div>
    <form id="person-form">
      ${p?`<input type="hidden" name="id" value="${E(p.id)}">`:''}
      <div class="person-modules">
        <section class="person-module" id="person-module-profile" data-module="profile">
          <div class="person-module-head"><span class="person-module-icon">${icon('person')}</span><div><h3>个人信息与形象</h3><p>数字人基础称呼、出镜照片与默认身份。</p></div></div>
          <div class="form-group"><label class="field-label" for="person-name">人物名称</label><input class="input" id="person-name" name="name" value="${E(p?.name)}" placeholder="例如：小林" required></div>
          ${p ? '' : personUploadFields()}
          <div class="switch-row"><label for="person-default">设为默认出镜人物</label><input class="switch" type="checkbox" name="default" id="person-default" ${p?.isDefault||!state.config.people.length?'checked':''}></div>
        </section>

        <section class="person-module" id="person-module-voice" data-module="voice">
          <div class="person-module-head"><span class="person-module-icon">${icon('mic')}</span><div><h3>声音与配音</h3><p>配置语音平台、音色复刻、音色试听与语速。</p></div></div>
          <div class="form-row">
            <div class="form-group"><label class="field-label" for="person-voice-provider">语音平台</label>
              <select id="person-voice-provider" name="person_voice_provider">${voicePlatformOptions(provider)}</select>
            </div>
            <div class="form-group">
              <label class="field-label" for="speech-rate">语速</label>
              <input class="input" type="number" min="-50" max="100" id="speech-rate" name="speech_rate" value="${p?.speech_rate??-3}">
              <p class="form-help">0 为标准速度，负数稍慢，正数稍快。</p>
            </div>
          </div>
          <fieldset class="form-group provider-fields" data-person-platform="volcengine" ${provider==='volcengine'?'':'hidden disabled'}>
            <div class="form-group"><label class="field-label" for="person-speaker">火山音色 ID<span class="optional">已有音色才填</span></label>
              <input class="input" id="person-speaker" name="speaker" value="${E(speaker('volcengine'))}" placeholder="例如：S_xxxxxxxx">
            </div>
            <div class="form-group" id="person-custom-fields" ${showCustom?'':'hidden'}>
              <label class="field-label" for="custom-speaker">火山自定义音色 ID</label>
              <input class="input" id="custom-speaker" name="custom_speaker_id" value="${E(p?.custom_speaker_id)}" ${showCustom?'':'disabled'}>
            </div>
          </fieldset>
          <fieldset class="form-group provider-fields" data-person-platform="minimax" ${provider==='minimax'?'':'hidden disabled'}>
            <label class="field-label" for="person-minimax-speaker">MiniMax Voice ID<span class="optional">已有音色才填</span></label>
            <input class="input" id="person-minimax-speaker" name="speaker" value="${E(speaker('minimax'))}">
          </fieldset>
          <fieldset class="form-group provider-fields" data-person-platform="aliyun" ${provider==='aliyun'?'':'hidden disabled'}>
            <div class="form-group">
              <label class="field-label" for="person-aliyun-model">百炼语音模型</label>
              <select id="person-aliyun-model" name="person_aliyun_model">${aliyunModelOptions(p?.aliyun_model||state.config.voice.aliyunModel)}</select>
            </div>
            <label class="field-label" for="person-aliyun-speaker">百炼音色 ID<span class="optional">已有音色才填</span></label>
            <input class="input" id="person-aliyun-speaker" name="speaker" value="${E(speaker('aliyun'))}">
          </fieldset>
          <div id="person-voice-editor" class="form-group">${p?personVoice(p,true,provider):'<p class="form-help">没有音色可留空，保存人物后点击「复刻」，音色会自动绑定。</p>'}</div>
          ${p?`<details id="voice-preview" class="form-group" ${p.speaker?'':'hidden'}>
            <summary class="field-label">试听音色</summary>
            <label class="field-label" for="voice-preview-text">试听文案 <span class="optional">选填</span></label>
            <textarea id="voice-preview-text" maxlength="200" placeholder="留空会用人物名生成一段约 10 秒的自我介绍。"></textarea>
            <p class="form-help">使用当前绑定的 <span id="voice-preview-platform">${p.speaker?voicePlatforms[boundVoicePlatform(p)]:''}</span> 音色，按平台规则计费。</p>
            <div class="person-voice-actions"><button type="button" class="secondary" id="generate-voice-preview">确认合成试听</button></div>
            <p id="voice-preview-status" class="form-help" role="status"></p>
            <div id="voice-preview-player"></div>
          </details>`:''}
        </section>

        <section class="person-module" id="person-module-narration" data-module="narration">
          <div class="person-module-head"><span class="person-module-icon">${icon('text')}</span><div><h3>口播内容</h3><p>固定该数字人的片尾收口话术与表达偏好。</p></div></div>
          <div class="form-group">
            <div class="label-row">
              <label class="field-label" for="person-closing-hook" style="margin:0">片尾钩子（口播最后一句话）<span class="optional">选填</span></label>
              <span id="person-hook-count" class="person-hook-count">${closingHook.length} / 120 字</span>
            </div>
            <textarea id="person-closing-hook" name="closing_hook" rows="3" maxlength="120" placeholder="例如：养老这件事，交给懂的人。我是税嘉，在重庆替你看100家养老院。想实地参观的，评论区扣参观，我安排。">${E(closingHook)}</textarea>
            <p class="form-help">留空则由 AI 按默认咨询引导生成。填写后，该数字人每条口播最后一句将固定原样念出这段话（请不要包含括号）。</p>
          </div>
          <div class="module-roadmap"><span>规划中</span><p>口播语气风格将在后续版本加入本模块，与片尾钩子统一管理。</p></div>
        </section>

        <section class="person-module" id="person-module-visual" data-module="visual">
          <div class="person-module-head"><span class="person-module-icon">${icon('palette')}</span><div><h3>配乐与视觉风格</h3><p>为该数字人绑定专属背景音乐与视频风格（含配套封面与视频样式），制作时自动套用。</p></div></div>
          <div class="form-group">
            <div class="label-row">
              <label class="field-label" for="person-music-track" style="margin:0">专属背景音乐（BGM）<span class="optional">选填</span></label>
              <div class="person-music-actions">
                <button type="button" class="text-button" data-action="upload-person-music">${icon('plus')} 上传新配乐</button>
                <a class="text-button" href="#settings/music">管理配乐 ${icon('arrow-up-right')}</a>
              </div>
            </div>
            <select id="person-music-track" name="music_track_id">${personMusicOptions(boundMusicId)}</select>
            <input type="file" id="person-music-upload" accept="audio/*,.mp3,.wav,.m4a,.aac,.flac,.ogg,.opus" hidden>
            <p id="person-music-upload-status" class="form-help" role="status"></p>
            <div id="person-music-preview" class="person-music-preview">${renderPersonMusicPreview(boundMusicId)}</div>
            <p class="form-help">绑定后，每次选这位数字人制作视频都会自动使用这首配乐；单次新建制作也可临时覆盖。</p>
          </div>
          <input type="hidden" name="cover_template_id" value="${E(p?.cover_template_id || '')}">
          <input type="hidden" name="video_style_id" value="${E(p?.video_style_id || '')}">
          <div id="person-style-bindings" class="person-style-bindings"></div>
        </section>

        <section class="person-module" id="person-module-platforms" data-module="platforms">
          <div class="person-module-head"><span class="person-module-icon">${icon('arrow-up-right')}</span><div><h3>多平台分发账号</h3><p>全部非必填，绑定后用于后续一键分发到对应平台账号。</p></div></div>
          <div class="platform-accounts-grid">
            <div class="form-group">
              <label class="field-label" for="person-xhs-id">小红书账号 ID<span class="optional">选填</span></label>
              <input class="input" id="person-xhs-id" name="xiaohongshu_account_id" maxlength="100" value="${E(xhsId)}" placeholder="填写小红书账号 ID">
            </div>
            <div class="form-group">
              <label class="field-label" for="person-dy-id">抖音账号 ID<span class="optional">选填</span></label>
              <input class="input" id="person-dy-id" name="douyin_account_id" maxlength="100" value="${E(dyId)}" placeholder="填写抖音账号 ID">
            </div>
            <div class="form-group">
              <label class="field-label" for="person-channels-id">视频号账号 ID<span class="optional">选填</span></label>
              <input class="input" id="person-channels-id" name="channels_account_id" maxlength="100" value="${E(channelsId)}" placeholder="填写视频号账号 ID">
            </div>
          </div>
        </section>
      </div>
      <div id="person-error" class="inline-error error-slot" role="alert"></div>
      <div class="person-editor-footer"><button class="primary" type="submit">${icon('save')} 保存人物资料</button></div>
    </form>
  </section>`;
}
function syncPersonVoice(form, person) {
  const provider=form.elements.person_voice_provider.value;
  for(const fields of form.querySelectorAll('[data-person-platform]')){
    toggleProviderFields(fields,fields.dataset.personPlatform===provider);
  }
  const speaker=$('#person-speaker').value.trim();
  const showCustom=provider==='volcengine'&&(speaker==='custom_speaker_id'||Boolean(person?.custom_speaker_id&&speaker===person.speaker));
  $('#person-custom-fields').hidden=$('#custom-speaker').disabled=!showCustom;
  if(person)$('#person-voice-editor').innerHTML=personVoice(person,true,provider,form.elements.person_aliyun_model.value);
}
function bindPerson() {
  const form = $('#person-form');
  if (!form) return;
  const editorPanel = form.closest('.editor-panel');
  editorPanel?.querySelectorAll('[data-module-jump]').forEach(btn => {
    btn.addEventListener('click', () => {
      const target = editorPanel.querySelector('#person-module-' + btn.dataset.moduleJump);
      target?.scrollIntoView({behavior: 'smooth', block: 'start'});
    });
  });
  const editing=()=>state.config.people.find(person=>person.id===editPerson);
  bindVoicePreview(editing);
  form.elements.person_voice_provider.addEventListener('change',()=>syncPersonVoice(form,editing()));
  const aliSpeaker=$('#person-aliyun-speaker');
  const aliDrafts=new Map([[$('#person-aliyun-model').value,aliSpeaker.value]]);
  aliSpeaker.addEventListener('input',()=>aliDrafts.set($('#person-aliyun-model').value,aliSpeaker.value));
  $('#person-aliyun-model').addEventListener('change',event=>{
    const person=editing(), model=event.target.value;
    aliSpeaker.value=aliDrafts.get(model)||(person?.voice_provider==='aliyun'&&person.aliyun_model===model?person.speaker:'')||'';
    syncPersonVoice(form,person);
  });
  $('#person-speaker').addEventListener('input',()=>{
    const person=editing(), speaker=$('#person-speaker').value.trim();
    const show=speaker==='custom_speaker_id'||Boolean(person?.custom_speaker_id&&speaker===person.speaker);
    $('#person-custom-fields').hidden=$('#custom-speaker').disabled=!show;
  });
  const musicSelect = $('#person-music-track');
  const musicPreview = $('#person-music-preview');
  const musicUploadBtn = form.querySelector('[data-action="upload-person-music"]');
  const musicUploadInput = $('#person-music-upload');
  const musicUploadStatusEl = $('#person-music-upload-status');
  if (musicSelect && musicPreview) {
    const syncMusicPreview = () => { musicPreview.innerHTML = renderPersonMusicPreview(musicSelect.value); };
    musicSelect.addEventListener('change', syncMusicPreview);
    if (musicUploadBtn && musicUploadInput) {
      musicUploadBtn.addEventListener('click', () => musicUploadInput.click());
      musicUploadInput.addEventListener('change', async event => {
        const file = event.target.files?.[0];
        if (!file) return;
        event.target.value = '';
        if (file.size > 256 * 1024 ** 2 || !file.size) {
          musicUploadStatusEl.textContent = '音乐文件应大于 0 且不超过 256MB。';
          return;
        }
        musicUploadBtn.disabled = true;
        musicUploadStatusEl.textContent = `正在上传并检查「${file.name}」…`;
        try {
          const response = await fetch('/api/music/upload?name=' + encodeURIComponent(file.name), {
            method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: file,
          });
          const out = await response.json();
          if (!response.ok) throw new Error(out.error);
          state.music = out.library;
          musicSelect.innerHTML = personMusicOptions(out.trackId);
          musicSelect.value = out.trackId;
          musicSelect.dispatchEvent(new Event('change', {bubbles: true}));
          musicUploadStatusEl.textContent = out.duplicate ? '已选中配乐库中相同曲目，点击下方「保存人物资料」生效。' : '配乐已上传并选中，点击下方「保存人物资料」生效。';
          notify('配乐已就绪，请保存人物资料完成绑定');
        } catch (err) {
          musicUploadStatusEl.textContent = err.message;
          notify(err.message, true);
        } finally {
          musicUploadBtn.disabled = false;
        }
      });
    }
  }
  const hookInput = $('#person-closing-hook');
  const hookCount = $('#person-hook-count');
  if (hookInput && hookCount) {
    const syncHookCount = () => {
      const val = hookInput.value.replace(/[\r\n]+/g, ' ').trim();
      hookCount.textContent = `${val.length} / 120 字`;
      if (/[（()）]/.test(val)) {
        hookInput.setCustomValidity('片尾钩子请不要包含括号，直接写实际要念出的最后一句话。');
      } else {
        hookInput.setCustomValidity('');
      }
    };
    hookInput.addEventListener('input', syncHookCount);
    syncHookCount();
  }
  window.mountPersonStyleSelectors?.(form);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const errorSlot = form.querySelector('#person-error');
    errorSlot.textContent = '';
    if (hookInput && /[（()）]/.test(hookInput.value)) {
      errorSlot.textContent = '片尾钩子请不要包含括号，直接写口播要念出的最后一句话。';
      hookInput.focus();
      return;
    }
    if (musicSelect && musicSelect.value && !(state.music?.tracks || []).some(t => !t.archived && t.id === musicSelect.value)) {
      errorSlot.textContent = '所选配乐已收起，请重新选择一首配乐，或改为跟随工作台默认配乐。';
      musicSelect.focus();
      return;
    }
    const button = form.querySelector('[type=submit]');
    await busy(button, async () => {
      const data = new FormData(form);
      try {
        if (data.has('id')) {
          await api('/api/people', {...Object.fromEntries(data), default:form.elements.default.checked});
          editPerson = undefined;
        } else {
          const label = button.innerHTML;
          button.textContent = '正在上传并保存…';
          try {
            const response = await fetch('/api/people', {method:'POST', body:data});
            const result = await response.json();
            if (!response.ok) throw new Error(result.error);
            editPerson = result.id;
          } finally {
            button.innerHTML = label;
          }
        }
        await refresh(false);
        if (route === 'people') renderPeople();
        notify('人物资料已保存');
      } catch (error) {
        errorSlot.textContent = error.message;
      }
    });
  });
}
function renderWorks() {
  const filtered = state.tasks.filter(t => worksFilter === 'all' || (worksFilter === 'completed' ? ['completed','awaiting_acceptance','accepted'].includes(t.status) : !['completed','awaiting_acceptance','accepted'].includes(t.status)));
  main.innerHTML = `${heading('好故事，慢慢积累。','每一条制作记录和成品，都保存在你的本地工作空间。',`<a class="primary" href="#create">${icon('plus')} 新建制作</a>`)}<div class="filter-tabs" role="group" aria-label="筛选作品">${[['all','全部制作'],['completed','已完成'],['active','进行与待处理']].map(([id,label]) => `<button data-action="filter" data-filter="${id}" class="${worksFilter === id ? 'active' : ''}" aria-pressed="${worksFilter === id}">${label}</button>`).join('')}</div>${filtered.length ? taskRows(filtered) : `<div class="work-empty">${icon('clapper')}<h2>${worksFilter === 'completed' ? '第一条成片，值得期待。' : '还没有制作记录'}</h2><p>选择机构素材、写下想法，Agent 会逐步完成视频。</p><a href="#create" class="primary">开始制作 ${icon('arrow-right')}</a></div>`}${state.tasks.some(t => ['completed','awaiting_acceptance','accepted'].includes(t.status)) ? `<div class="section-heading"><h2>已完成作品</h2></div><div class="works-grid">${state.tasks.filter(t => ['completed','awaiting_acceptance','accepted'].includes(t.status)).flatMap(t => t.results.map(r => `<article class="work-card"><div class="work-art">${r.cover ? `<img src="${E(r.cover)}" alt="${E(r.title)}封面">` : ''}<div class="work-overlay"><button class="play-button" data-action="preview" data-id="${t.id}" data-index="${r.index}" aria-label="播放${E(r.title)}">${icon('play')}</button></div></div><h2>${E(r.title)}</h2><p>${E(t.personName)} · ${new Date(t.created * 1000).toLocaleDateString('zh-CN')}</p><div class="work-actions"><a href="${E(r.package)}" class="primary" download>${icon('download')} 发布包</a><a href="#task/${t.id}" class="secondary">制作详情</a></div></article>`)).join('')}</div>` : ''}`;
}
function renderTask() {
  const t = state.tasks.find(t => t.id === selectedTask);
  if (!t) {main.innerHTML = '<div class="work-empty"><h2>没有找到这条制作记录</h2><a href="#works" class="secondary">返回作品</a></div>';return;}
  const media = t.results.filter(r=>r.video);
  const running = ['queued','running','stopping'].includes(t.status);
  main.innerHTML = `<a href="#works" class="back-link">${icon('arrow-right')} 返回我的作品</a><div class="detail-top"><div><h1>${E(t.title)}</h1><p>${E(t.personName)} · ${t.count} 条视频 · ${new Date(t.created * 1000).toLocaleString('zh-CN')}</p></div><div>${badge(statusLabels[t.status],statusClass(t.status))} <button class="secondary" data-action="open-folder" data-id="${t.id}">${icon('folder')} 打开文件夹</button></div></div><div id="task-stages">${stages(t.phase,running)}</div><div class="detail-layout"><div><section class="panel"><div id="task-current">${taskCurrent(t)}</div><div id="task-controls">${taskControls(t)}</div><div class="section-heading" style="margin-top:25px"><h2 style="font-size:13px">制作动态</h2><span class="subtle" style="font-size:11px">只显示实际制作记录</span></div><div class="event-log" id="event-log">${eventLog(t)}</div><details class="task-brief"><summary>查看本次制作要求</summary><p>${E(t.requirements || '按机构素材完整介绍，使用默认制作规范。')}</p>${t.facts ? `<p>${E(t.facts)}</p>` : ''}<div class="path">素材：${E(t.source)}<br>工程：${E(t.job)}</div></details></section></div><div id="task-preview">${media.length||!Object.keys(t.productDeletions||{}).length?previewPanel(media[0]||t.results[0],t):''}${media.length&&['completed','awaiting_acceptance','accepted'].includes(t.status) ? `<div class="panel" style="margin-top:20px"><h2 style="font-size:15px">带上作品，去发布吧。</h2><p class="subtle">发布包包含视频、封面和发布文案。封面与内容可直接在工作台修改。</p>${media.map(r => `<a class="primary wide" href="${E(r.package)}" download>${icon('download')} 下载${t.count > 1 ? '第 ' + (r.index + 1) + ' 条' : ''}发布包</a>`).join('')}</div>` : ''}</div></div>`;
  bindAnswer();
}
function taskCurrent(t) {return `<div class="current-step"><span>${icon(['completed','awaiting_acceptance','accepted'].includes(t.status) ? 'check' : t.status === 'needs_input' ? 'help' : 'clapper')}</span><div><h2>${['completed','awaiting_acceptance','accepted'].includes(t.status) ? '制作完成，故事准备好了。' : t.status === 'draft' ? '要求已保存，准备开始。' : t.status === 'needs_input' ? '补充一点信息，就能继续。' : E(state.config.phases[Math.min(t.phase,5)])}</h2><p>${E(t.message)}</p></div></div>`;}
function taskControls(t) {
  if (t.status === 'needs_input') return `<form id="answer-form" class="question-box"><h3>需要你确认</h3><p>${E(t.question || '请补充本次制作需要的信息。')}</p><textarea name="answer" aria-label="补充制作所需的信息" placeholder="在此填写答复" required></textarea><button type="submit" class="primary">提交答复 ${icon('arrow-right')}</button></form>`;
  if (t.status === 'stopping') return '<p class="inline-note">已请求停止，等待当前阶段结束。已有文件和云端任务 ID 会保留。</p>';
  if (['running','queued'].includes(t.status)) return `<button class="danger-button" data-action="cancel-task" data-id="${t.id}">${icon('stop')} 请求停止制作</button><p class="form-help">已提交的云端任务会保留，恢复时先查询已有结果。</p>`;
  if (!['completed','awaiting_acceptance','accepted'].includes(t.status)) {
    const local = state.config.agent.mode === 'local';
    return state.config.agent.state === 'connected' ? `<button class="primary" data-action="start-task" data-id="${t.id}">${icon(t.status === 'draft' ? 'spark' : 'retry')}${t.status === 'draft' ? '开始制作' : '从已有阶段继续'}</button>` : `<p class="inline-note">任务已保存在本地。${local ? '配置模型后即可继续。' : '连接 Codex 后即可继续。'}</p><a href="#settings" class="primary">${local ? '配置模型' : '连接 Codex'} ${icon('arrow-right')}</a>`;
  }
  return '';
}
function productionEventMessage(message) {
  const legacy = message.match(/^(.*?：)?voice 阶段暂停：[\s\S]*MiniMax 拒绝了请求（代码 1008）。/);
  return legacy ? (legacy[1] || '') + 'MiniMax 配音余额不足。充值后可继续配音。' : message;
}
function eventLog(t) {return t.events.length ? t.events.slice().reverse().map(e => `<div class="event ${E(e.kind)}"><time>${new Date(e.time * 1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}</time><p>${E(productionEventMessage(e.message))}</p></div>`).join('') : '<p class="subtle">开始后，素材整理、写稿和制作进度会出现在这里。</p>';}
function refreshEventLog(log, task) {
  const {scrollTop, scrollHeight} = log;
  log.innerHTML = eventLog(task);
  log.scrollTop = scrollTop <= 2 ? 0 : Math.max(0, scrollTop + log.scrollHeight - scrollHeight);
}
function bindAnswer() {const form = $('#answer-form'); if(form)form.addEventListener('submit', async e => {e.preventDefault();await busy(form.querySelector('button'), async () => {try {await api(`/api/tasks/${selectedTask}/answer`,{answer:form.elements.answer.value});await refresh(false);renderTask();}catch(err){notify(err.message,true);}});});}
function renderOnboarding() {
  const local = state.config.agent.mode === 'local';
  const steps = [
    {done:hasServices() && (!local || state.config.agent.canDispatch), name:'配置制作服务',
      text:local ? '填写模型 API Key，再配置语音和数字人服务。' : '在设置中准备语音 API Key 和数字人 AK / SK。',
      link:'settings', button:'配置服务'},
    {done:hasPerson(), name:'添加你的数字人', text:'准备人物照片，填好社媒署名，绑定已有音色或提供声音样本。',
      link:'people', button:'配置数字人'},
    {done:state.tasks.length > 0, name:'选好素材，制作第一条视频',
      text:'先在机构资料库添加实拍与介绍，再选择人物、数量和本批偏好。', link:'create', button:'开始制作'},
  ];
  main.innerHTML = `<div class="onboarding-intro"><h1>欢迎，先把工作台准备好。</h1><p>准备好制作服务、数字人和机构素材，就可以提交第一条视频。</p></div><div class="onboarding-list">${steps.map((s,i)=>`<div class="onboard-step"><span class="step-number">${s.done ? icon('check') : i+1}</span><div class="step-content"><h2>${s.name} ${s.done ? badge('已准备') : ''}</h2><p>${s.text}</p></div><a href="#${s.link}" class="secondary">${s.button} ${icon('arrow-right')}</a></div>`).join('')}</div><div class="onboarding-bottom"><a href="#create" class="primary">进入制作工作台 ${icon('arrow-right')}</a><p>可以先看看界面，准备过程中随时回来继续。</p></div>`;
}
function previewReviewNotices(result) {
  const notices = result?.reviewNotices || [];
  if (!notices.length) return '';
  const names = {content:'口播文案', cover:'封面', provider:'数字人原片', layout:'排版', contrast:'对比度', final:'成片'};
  return `<details class="panel preview-review-notices"><summary>制作复核提醒（${notices.length}）</summary><ul>${notices.map(({stage,text})=>`<li><strong>${E(names[stage] || '画面')}</strong> ${E(text)}</li>`).join('')}</ul></details>`;
}
function renderPreview() {
  const hash = location.hash.slice(1).split('/');
  const task = state.tasks.find(t=>t.id===hash[1]);
  const result = task?.results.find(r=>r.index===Number(hash[2]||0));
  const reference = hash[1] === 'reference';
  const video = reference ? state.reference.video : result?.video;
  if (!video) {main.innerHTML = '<div class="work-empty"><h2>视频还没有完成</h2><a href="#works" class="secondary">返回我的作品</a></div>';return;}
  main.innerHTML = `<a class="back-link" href="${reference ? '#create' : '#task/'+E(task.id)}">${icon('arrow-right')} 返回${reference ? '制作工作台' : '制作详情'}</a>${heading(reference ? '已认可的项目样片' : E(result.title),reference ? '真实已交付作品 · 仅作效果参考' : '本次制作完成的成片',result?.package ? `<a class="primary" href="${E(result.package)}" download>${icon('download')} 下载发布包</a>` : '')}<div class="video-view"><video controls playsinline preload="metadata" poster="${E(reference ? state.reference.cover : result.cover)}" src="${E(video)}"></video></div>`;
  main.querySelector('.video-view').insertAdjacentHTML('beforebegin', previewReviewNotices(result));
}
function render() {
  if(!state)return;
  const parts = (location.hash.slice(1) || 'create').split('/');route = parts[0];selectedTask = parts[1];
  if(!labels[route])route='create';
  updateChrome();
  ({create:renderCreate,settings:renderSettings,people:renderPeople,works:renderWorks,onboarding:renderOnboarding,task:renderTask,preview:renderPreview}[route])();
}
async function busy(button, fn) {if (button.disabled)return;button.disabled=true;button.setAttribute('aria-busy','true');try{await fn();}finally{if(button.isConnected){button.disabled=false;button.removeAttribute('aria-busy');}}}
async function scan(folder) {const result = await api('/api/scan',{folder});scanResult=result;createDraft.source=result.folder;saveDraft();renderCreate();notify(`已读取 ${result.counts.video} 段视频、${result.counts.image} 张照片和 ${result.counts.text} 份介绍`);}
async function pickFolder(button) {
  await busy(button, async () => {try {const r=await api('/api/pick-folder',{});if(!r.cancelled)await scan(r.folder);}catch(err){notify(err.message,true);}});
}
const allowedFile = /\.(mp4|mov|m4v|mkv|avi|webm|jpg|jpeg|png|webp|heic|txt|md|docx|pdf|wav|mp3|m4a|aac|flac|ogg)$/i;
async function importFolder(files) {
  const filtered = [...files].filter(f=>allowedFile.test(f.name)&&!(f.webkitRelativePath||f.name).split('/').some(p=>p.startsWith('.')));
  if(!filtered.length){notify('没有可导入的视频、照片、文字或声音文件',true);return;}
  const group=crypto.randomUUID().replaceAll('-','').slice(0,12);
  let folder;
  try {
    for(let i=0;i<filtered.length;i++){
      const f=filtered[i];notify(`正在导入 ${i+1} / ${filtered.length}：${f.name}`);
      const response=await fetch(`/api/upload?group=${group}&name=${encodeURIComponent(f.webkitRelativePath||f.name)}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:f});
      const result=await response.json();if(!response.ok)throw new Error(result.error);folder=result.folder;
    }
    await scan(folder);
  }catch(err){notify('导入未完成：'+err.message,true);}
  $('#folder-upload').value='';
}
document.addEventListener('click', async e=>{
  const button=e.target.closest('[data-action]');if(!button)return;
  const action=button.dataset.action;
  try {
    if(action==='exit-workbench')await busy(button, exitWorkbench);
    if(action==='onboarding')setRoute('onboarding');
    if(action==='agent-settings')setRoute('settings');
    if(action==='focus-source'){if(route!=='create')setRoute('create');else $('#source-drop')?.focus();}
    if(action==='pick-source')await pickFolder(button);
    if(action==='scan-source')await busy(button,()=>scan($('#create-form').elements.source.value));
    if(action==='import-source'){uploadTarget='source';$('#folder-upload').click();}
    if(action==='reveal'){await toggleCredentialVisibility(button);}
    if(action==='check-agent'){await api('/api/check-agent',{});await refresh(false);if($('#agent-config-status'))$('#agent-config-status').innerHTML=agentStatusBlock();updateChrome();notify(state.config.agent.canDispatch?'接单通道已有回传':'恢复连接请求已保存。请在 Codex 中接入本地接单通道。');}
    if(action==='copy-agent'){await navigator.clipboard.writeText(state.config.agent.instruction);notify('已复制，在 Agent 中粘贴并发送即可');}
    if(action==='preview')setRoute('preview/'+(button.dataset.reference?'reference':button.dataset.id+'/'+button.dataset.index));
    if(action==='add-person'){editPerson=null;renderPeople();$('#person-editor').scrollIntoView({block:'start',behavior:'smooth'});}
    if(action==='edit-person'){editPerson=button.dataset.id;renderPeople();$('#person-editor').scrollIntoView({block:'start',behavior:'smooth'});}
    if(action==='close-editor'){editPerson=undefined;renderPeople();}
    if(action==='pick-voice-sample')$('#person-voice-file')?.click();
    if(action==='view-voice-slots')await showVoiceSlots(button);
    if(action==='start-voice-training')await busy(button,async()=>{
      const person=state.config.people.find(p=>p.id===button.dataset.id);
      const retrain=button.dataset.retrain==='true', provider=button.dataset.provider;
      if(!retrain&&person?.voiceTraining.needsSlot&&provider==='volcengine'){
        await showVoiceSlots(button,person.id);
      }else{
        await api(`/api/people/${encodeURIComponent(button.dataset.id)}/voice-training`,
          {retrain,provider,model:button.dataset.model});
        await refresh();
      }
    });
    if(action==='filter'){worksFilter=button.dataset.filter;renderWorks();}
    if(action==='start-task')await busy(button,async()=>{await api(`/api/tasks/${button.dataset.id}/start`,{});await refresh(false);renderTask();});
    if(action==='cancel-task')await busy(button,async()=>{await api(`/api/tasks/${button.dataset.id}/cancel`,{});await refresh(false);renderTask();notify('停止请求已提交，已有制作文件会保留');});
    if(action==='open-folder')await busy(button,async()=>{await api('/api/open-folder',{id:button.dataset.id});notify('已打开本地作品文件夹');});
  }catch(err){notify(err.message,true);}
});
$('#folder-upload').addEventListener('change',e=>importFolder(e.target.files));
document.addEventListener('change',e=>{if(e.target.id==='person-voice-file')uploadPersonVoice(e.target);});
window.addEventListener('hashchange',()=>{clearVoicePreview();render();window.scrollTo({top:0});});
async function refresh(paint=true) {
  const old=state;state=await api('/api/state');
  if(!paint)return;
  if(!old){render();return;}
  updateChrome();
  if(route==='create'){$('#recent-tasks').innerHTML=taskRows(state.tasks.slice(0,3));}
  if(route==='settings'&&$('#agent-config-status')&&JSON.stringify(old.config.agent)!==JSON.stringify(state.config.agent)){$('#agent-config-status').innerHTML=agentStatusBlock();}
  if(route==='works'&&JSON.stringify(old.tasks)!==JSON.stringify(state.tasks))renderWorks();
  if(route==='task'){
    const t=state.tasks.find(t=>t.id===selectedTask),prev=old.tasks.find(t=>t.id===selectedTask);
    if(!t)return;
    if(t.status!==prev?.status || JSON.stringify(t.results)!==JSON.stringify(prev?.results)){renderTask();return;}
    if(t.updated!==prev?.updated){$('#task-current').innerHTML=taskCurrent(t);$('#task-stages').innerHTML=stages(t.phase,['running','queued'].includes(t.status));refreshEventLog($('#event-log'),t);}
  }
}
async function init() {
  hydrateIcons();
  try {
    await refresh(false);
    createDraft=state.draft || {};
    if(!location.hash && !state.config.people.length && !state.config.onboarded)location.hash='onboarding';
    render();
    if(createDraft.source && route==='create') {try {scanResult=await api('/api/scan',{folder:createDraft.source});if($('#source-info'))$('#source-info').innerHTML=sourceInfo();}catch{}}
  }catch(err){main.innerHTML=`<div class="loading-error"><h2>暂时没有连上本地服务</h2><p>${E(err.message)}</p><button class="secondary" data-action="reload">重新连接</button></div>`;main.querySelector('button').onclick=()=>location.reload();}
  setInterval(async()=>{if(document.hidden || workbenchExited)return;try{await refresh();}catch{if(workbenchExited)return; $('#agent-status').textContent='本地服务连接中断';$('#agent-dot').className='status-dot error';}},3500);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden && !workbenchExited)refresh().catch(()=>{});});
}
// The library extension starts the app after registering all screens.
