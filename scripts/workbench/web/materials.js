'use strict';

// Institution-level asynchronous material preparation UI:
// - Global draggable/collapsible progress dock
// - Institution detail summary banner, per-asset summaries, edit dialog & single-asset re-run
// - Create-task gate while preparation is running or incomplete

const PREP_POS_KEY = 'montage_prep_dock_pos_v1';
const PREP_COLLAPSE_KEY = 'montage_prep_dock_collapsed_v1';

const prepSeenRunning = new Set();
const prepDismissed = new Map(); // institutionId -> signature
let prepPollTimer = null;
let prepDockCollapsed = false;
try {
  prepDockCollapsed = localStorage.getItem(PREP_COLLAPSE_KEY) === '1';
} catch {}

function prepSpinnerSvg() {
  return '<svg class="prep-spinner" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a9 9 0 1 1-9 9"/></svg>';
}

function prepGripSvg() {
  return '<svg viewBox="0 0 16 16" aria-hidden="true" fill="currentColor"><circle cx="5" cy="4" r="1.2"/><circle cx="11" cy="4" r="1.2"/><circle cx="5" cy="8" r="1.2"/><circle cx="11" cy="8" r="1.2"/><circle cx="5" cy="12" r="1.2"/><circle cx="11" cy="12" r="1.2"/></svg>';
}

function prepDurationText(sec) {
  const n = Number(sec);
  if (!Number.isFinite(n) || n <= 0) return '';
  return n >= 60 ? `${Math.floor(n / 60)}分${Math.round(n % 60)}秒` : `${n.toFixed(1)}秒`;
}

function prepSignature(p) {
  if (!p) return '';
  return [p.status, p.runId || '', p.completedAt || '', p.error || ''].join(':');
}

function prepStatusBadge(prep) {
  if (!prep || prep.status === 'empty') return badge('暂无素材', 'neutral');
  if (prep.status === 'running') return badge(`整理中 ${prep.processed}/${prep.total}`, 'warn');
  if (prep.status === 'ready') return badge('已整理', '');
  if (prep.status === 'failed') return badge('整理未完成', 'error');
  return badge('待整理', 'warn');
}

function prepBannerHtml(x) {
  const p = x.materialPrep;
  if (!p || p.status === 'empty') return '';
  let headline = '';
  let detail = '';
  let extra = '';
  let actions = '';

  if (p.status === 'running') {
    headline = `正在后台整理素材（${p.processed}/${p.total}）`;
    detail = `${p.currentName ? '当前处理：' + p.currentName + ' · ' : ''}整理完成前暂不能为这家机构创建视频，你可以先切换处理其他事情。`;
    extra = `<div class="prep-banner-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${p.percent}"><span style="width:${Math.max(4, p.percent)}%"></span></div>`;
    actions = `<button type="button" class="secondary" disabled>${prepSpinnerSvg()} 整理中…</button>`;
  } else if (p.status === 'ready') {
    const parts = [];
    const visualTotal = (p.videoCount || 0) + (p.imageCount || 0);
    if (visualTotal) parts.push(`${visualTotal} 个实拍已识别（含 ${p.motionVideoCount || 0} 段移动镜头）`);
    if (p.textCount) parts.push(`${p.textCount} 份文档已提取`);
    if (p.editedCount) parts.push(`${p.editedCount} 条已微调`);
    if (p.completedAt) parts.push('更新于 ' + dateText(p.completedAt));
    headline = '素材整理已完成，后续制作直接复用';
    detail = parts.join(' · ') || '当前已选素材均已完成整理。';
    if (p.motionWarning) {
      extra = '<p class="prep-banner-warning">未检测到移动镜头，片头/片尾可能缺少动态画面；可在下方视频卡片确认或手动微调标记。</p>';
    }
    actions = `<button type="button" class="secondary" data-action="prep-rerun-all" data-id="${E(x.id)}">${icon('retry')} 重新整理</button>`;
  } else if (p.status === 'failed') {
    headline = p.needsModel ? '需先配置制作大模型' : '素材整理尚未完成';
    detail = p.error || '部分素材未能完成识别，请重试整理后再开始制作。';
    actions = p.needsModel
      ? `<a class="primary" href="#settings">去配置模型 ${icon('arrow-right')}</a><button type="button" class="secondary" data-action="prep-start" data-id="${E(x.id)}">重试整理</button>`
      : `<button type="button" class="primary" data-action="prep-start" data-id="${E(x.id)}">${icon('retry')} 重试整理</button>`;
  } else {
    headline = p.needsModel ? '需先配置制作大模型' : `有 ${p.pendingCount || p.totalEnabled} 个素材待整理`;
    detail = p.needsModel
      ? '配置模型后即可提前识别画面与文档，之后每次制作直接复用，速度更快。'
      : '提前整理一次机构素材，后续每次创建视频任务都会直接复用，无需重复识别。';
    actions = p.needsModel
      ? `<a class="primary" href="#settings">去配置模型 ${icon('arrow-right')}</a>`
      : `<button type="button" class="primary" data-action="prep-start" data-id="${E(x.id)}">${icon('spark')} 开始整理素材</button>`;
  }

  return `<section class="prep-banner is-${E(p.status)}" aria-label="素材整理状态">
    <div class="prep-banner-main">
      <div class="prep-banner-head">${prepStatusBadge(p)}<strong>${E(headline)}</strong></div>
      <p class="${p.status === 'failed' ? 'prep-banner-error' : 'prep-banner-summary'}">${E(detail)}</p>
      ${extra}
    </div>
    <div class="prep-banner-actions">${actions}</div>
  </section>`;
}

function assetAnalysisHtml(x, a) {
  const info = a.analysis || {};
  const busy = x.materialPrep?.status === 'running';
  const metaParts = [];
  if (info.width && info.height) metaParts.push(`${info.width}×${info.height}`);
  if (a.kind === 'video' && info.duration) metaParts.push(prepDurationText(info.duration));

  let pills = '';
  if (a.kind === 'video' && info.status === 'ready') {
    pills += info.motion
      ? '<span class="prep-pill is-motion">移动镜头</span>'
      : '<span class="prep-pill">静态镜头</span>';
  }
  if (info.source === 'manual') {
    pills += '<span class="prep-pill is-manual">已微调</span>';
  }
  if (a.kind === 'text') {
    if (info.blocked) pills += '<span class="prep-pill is-error">提取受阻</span>';
    else if (info.status === 'ready') pills += `<span class="prep-pill is-motion">已提取 ${info.textLength || 0} 字</span>`;
  }

  let descText = '';
  let descClass = 'asset-analysis-desc';
  if (info.status === 'running') {
    descText = a.kind === 'text' ? '正在提取文档文字…' : '正在识别画面内容…';
    descClass += ' is-empty';
  } else if (info.blocked) {
    descText = info.blocked;
    descClass += ' is-error';
  } else if (a.kind === 'text') {
    if (info.textPreview) {
      descText = info.textPreview;
    } else {
      descText = info.status === 'ready' ? '文档为空或仅含空白字符' : '等待整理提取文字…';
      descClass += ' is-empty';
    }
  } else if (info.description) {
    descText = info.description;
  } else {
    descText = '等待整理识别画面…';
    descClass += ' is-empty';
  }

  const actions = a.kind === 'text'
    ? `<button type="button" class="text-button" data-action="prep-rerun-asset" data-institution="${E(x.id)}" data-id="${E(a.id)}" ${busy ? 'disabled' : ''}>重新提取</button>`
    : `<button type="button" class="text-button" data-action="prep-edit-asset" data-institution="${E(x.id)}" data-id="${E(a.id)}">编辑摘要</button>
       <button type="button" class="text-button" data-action="prep-rerun-asset" data-institution="${E(x.id)}" data-id="${E(a.id)}" ${busy ? 'disabled' : ''}>重新识别</button>`;

  return `<div class="asset-analysis">
    <div class="asset-analysis-meta">${metaParts.length ? `<span>${E(metaParts.join(' · '))}</span>` : ''}${pills}</div>
    <p class="${descClass}" title="${E(descText)}">${E(descText)}</p>
    <div class="asset-analysis-actions">${actions}</div>
  </div>`;
}

// Enhance institution detail materials & cards
const materialSectionBeforePrep = materialSection;
materialSection = function (x) {
  const base = materialSectionBeforePrep(x);
  const banner = prepBannerHtml(x);
  return banner ? base.replace('<div class="file-filter">', banner + '<div class="file-filter">') : base;
};

const assetCardsBeforePrep = assetCards;
assetCards = function (x) {
  const html = assetCardsBeforePrep(x);
  if (!x.assets?.length) return html;
  const assets = x.assets.filter(a => (assetKind === 'all' || a.kind === assetKind) && a.name.toLowerCase().includes(assetQuery.toLowerCase()));
  if (!assets.length) return html;
  const wrap = document.createElement('div');
  wrap.innerHTML = html;
  const cards = wrap.querySelectorAll('.asset-card');
  cards.forEach((card, idx) => {
    const asset = assets[idx];
    const body = card.querySelector('.asset-card-body');
    if (asset && body) {
      body.insertAdjacentHTML('beforeend', assetAnalysisHtml(x, asset));
    }
  });
  return wrap.innerHTML;
};

// Enhance institution header "为这家机构制作" button when preparing
const paintInstitutionBeforePrep = paintInstitution;
paintInstitution = function (x, tabName) {
  paintInstitutionBeforePrep(x, tabName);
  const p = x.materialPrep;
  const cta = main.querySelector('.institution-header > a.primary');
  if (cta && p && p.status === 'running') {
    cta.setAttribute('aria-disabled', 'true');
    cta.classList.add('is-disabled');
    cta.title = p.blockReason || '素材整理完成后即可制作';
    cta.innerHTML = `${prepSpinnerSvg()} 素材整理中 (${p.processed}/${p.total})`;
    cta.onclick = e => {
      e.preventDefault();
      notify(p.blockReason || '素材整理完成后即可开始制作', true);
    };
  }
};

// Enhance library cards with concise prep state chip
const libraryCardsBeforePrep = libraryCards;
libraryCards = function () {
  const html = libraryCardsBeforePrep();
  const items = state.institutions.filter(x => (x.name + ' ' + x.address).toLowerCase().includes(libraryQuery.toLowerCase()));
  if (!items.length) return html;
  const wrap = document.createElement('div');
  wrap.innerHTML = html;
  const cards = wrap.querySelectorAll('.institution-card');
  cards.forEach((card, idx) => {
    const inst = items[idx];
    const p = inst?.materialPrep;
    if (!p || p.status === 'empty') return;
    const counts = card.querySelector('.material-counts');
    let chipText = '';
    if (p.status === 'running') chipText = `素材整理中 ${p.processed}/${p.total}`;
    else if (p.status === 'ready') chipText = `素材已整理 · ${p.motionVideoCount || 0} 段移动镜头`;
    else if (p.status === 'failed') chipText = p.needsModel ? '待配置大模型后整理' : '素材整理未完成';
    else chipText = `有 ${p.pendingCount || p.totalEnabled} 个素材待整理`;
    if (counts) {
      counts.insertAdjacentHTML('afterend', `<div class="institution-prep-chip is-${E(p.status)}">${p.status === 'running' ? prepSpinnerSvg() : ''}<span>${E(chipText)}</span></div>`);
    }
    if (p.status === 'running') {
      const btn = card.querySelector('.institution-actions a.primary');
      if (btn) {
        btn.setAttribute('aria-disabled', 'true');
        btn.title = p.blockReason || '素材整理中';
      }
    }
  });
  return wrap.innerHTML;
};

// Enhance single asset preview page with full analysis & edit button
const renderAssetBeforePrep = renderAsset;
renderAsset = async function () {
  await renderAssetBeforePrep();
  if (route !== 'asset') return;
  const parts = location.hash.slice(1).split('/');
  const iid = parts[1], aid = parts[2];
  const inst = institutionCache.get(iid);
  const a = inst?.assets?.find(item => item.id === aid);
  if (!inst || !a) return;
  const info = a.analysis || {};
  const busy = inst.materialPrep?.status === 'running';
  const meta = [];
  if (info.width && info.height) meta.push(`${info.width}×${info.height}`);
  if (a.kind === 'video' && info.duration) meta.push(prepDurationText(info.duration));
  let badges = '';
  if (a.kind === 'video' && info.status === 'ready') {
    badges += info.motion ? '<span class="prep-pill is-motion">移动镜头（适合片头/片尾）</span>' : '<span class="prep-pill">静态镜头</span>';
  }
  if (info.source === 'manual') badges += '<span class="prep-pill is-manual">已手动微调</span>';
  const content = a.kind === 'text'
    ? (info.blocked ? `<p class="inline-error">${E(info.blocked)}</p>` : `<p class="subtle">已提取 ${info.textLength || 0} 字，完整内容见上方文档预览。</p>`)
    : `<p class="asset-detail-desc">${E(info.description || '尚未整理识别画面，点击右侧“重新识别”或“编辑摘要”补充。')}</p>`;
  const actions = a.kind === 'text'
    ? `<button type="button" class="secondary" data-action="prep-rerun-asset" data-institution="${E(iid)}" data-id="${E(aid)}" ${busy ? 'disabled' : ''}>${icon('retry')} 重新提取</button>`
    : `<button type="button" class="secondary" data-action="prep-edit-asset" data-institution="${E(iid)}" data-id="${E(aid)}">编辑摘要</button>
       <button type="button" class="secondary" data-action="prep-rerun-asset" data-institution="${E(iid)}" data-id="${E(aid)}" ${busy ? 'disabled' : ''}>${icon('retry')} 重新识别</button>`;
  main.insertAdjacentHTML('beforeend', `<section class="panel asset-detail-analysis" aria-label="素材整理摘要">
    <div class="asset-detail-analysis-head">
      <div>
        <h2>素材整理摘要</h2>
        <div class="asset-analysis-meta" style="margin-top:6px">${meta.length ? `<span>${E(meta.join(' · '))}</span>` : ''}${badges}</div>
      </div>
      <div class="asset-detail-analysis-actions">${actions}</div>
    </div>
    ${content}
  </section>`);
};

// Enhance selected institution panel & Create page gate
const selectedInstitutionBeforePrep = selectedInstitutionPanel;
selectedInstitutionPanel = function (x) {
  const base = selectedInstitutionBeforePrep(x);
  if (!x) return base;
  const p = x.materialPrep;
  if (!p || p.status === 'empty') return base;
  let strip = '';
  if (p.status === 'ready') {
    strip = `<div class="create-prep-strip is-ready">
      <div class="create-prep-strip-text">
        <strong>素材已提前整理，本次制作直接复用</strong>
        <p>${(p.videoCount || 0) + (p.imageCount || 0)} 个实拍已识别（${p.motionVideoCount || 0} 段移动镜头）· ${p.textCount || 0} 份文档已提取</p>
      </div>
      <a class="text-button" href="#institution/${E(x.id)}">查看整理结果 ${icon('chevron-right')}</a>
    </div>`;
  } else if (p.status === 'running') {
    strip = `<div class="create-prep-strip is-running" role="status">
      <div class="create-prep-strip-text">
        <strong>${prepSpinnerSvg()} 素材整理中（${p.processed}/${p.total}）</strong>
        <p>${E(p.currentName ? '正在处理：' + p.currentName + ' · 完成后自动解锁制作' : '整理完成后即可提交制作')}</p>
      </div>
      <a class="text-button" href="#institution/${E(x.id)}">查看素材</a>
    </div>`;
  } else {
    const title = p.needsModel ? '请先配置制作大模型' : p.status === 'failed' ? '素材整理未完成' : '需要先整理这家机构的素材';
    const text = p.error || '整理完成后，之后每次制作都会直接复用，速度更快。';
    const btn = p.needsModel
      ? `<a class="primary" href="#settings">去配置模型</a>`
      : `<button type="button" class="primary" data-action="prep-start" data-id="${E(x.id)}">${icon('spark')} ${p.status === 'failed' ? '重试整理' : '立即开始整理'}</button>`;
    strip = `<div class="create-prep-strip is-${E(p.status)}" role="status">
      <div class="create-prep-strip-text">
        <strong>${E(title)}</strong>
        <p>${E(text)}</p>
      </div>
      ${btn}
    </div>`;
  }
  return base + strip;
};

function syncCreatePrepGate() {
  if (route !== 'create') return;
  const f = $('#create-form');
  if (!f) return;
  const inst = findInstitution(f.elements.institutionId?.value);
  const submit = f.querySelector('[type=submit]');
  const note = $('#create-setup');
  const p = inst?.materialPrep;
  if (!submit || !p || p.status === 'empty' || p.status === 'ready') {
    if (submit && submit.dataset.prepLocked) {
      delete submit.dataset.prepLocked;
      submit.disabled = false;
    }
    return;
  }
  if (p.status === 'running') {
    submit.disabled = true;
    submit.dataset.prepLocked = '1';
    submit.innerHTML = `${prepSpinnerSvg()} 素材整理中（${p.processed}/${p.total}），完成后可提交`;
    if (note) {
      note.hidden = false;
      note.textContent = p.blockReason || '这家机构正在整理素材，完成后会自动解锁。';
    }
  } else if (state.config.agent.mode === 'local') {
    submit.disabled = true;
    submit.dataset.prepLocked = '1';
    submit.innerHTML = p.needsModel ? '请先配置大模型并整理素材' : '请先在上方完成机构素材整理';
    if (note) {
      note.hidden = false;
      note.textContent = p.blockReason || '请先完成机构素材整理再开始制作。';
    }
  }
}

const createBeforePrep = renderCreate;
renderCreate = function () {
  createBeforePrep();
  const f = $('#create-form');
  if (!f) return;
  const oldChange = f.elements.institutionId.onchange;
  f.elements.institutionId.onchange = e => {
    oldChange?.(e);
    syncCreatePrepGate();
  };
  f.addEventListener('change', () => setTimeout(syncCreatePrepGate, 0));
  f.addEventListener('input', () => setTimeout(syncCreatePrepGate, 0));
  syncCreatePrepGate();
};

// Edit asset summary dialog
async function openEditSummaryDialog(iid, aid, opener) {
  const inst = await getInstitution(iid);
  const asset = inst.assets.find(a => a.id === aid);
  if (!asset) {
    notify('找不到这个素材', true);
    return;
  }
  const info = asset.analysis || {};
  const d = managementDialog('编辑画面摘要', opener);
  d.classList.add('prep-edit-dialog');
  const body = d.querySelector('.management-dialog-content');
  const metaParts = [];
  if (info.width && info.height) metaParts.push(`${info.width}×${info.height}`);
  if (asset.kind === 'video' && info.duration) metaParts.push(prepDurationText(info.duration));
  metaParts.push(info.source === 'manual' ? '上次为手动修改' : info.analyzedAt ? 'AI 自动识别' : '尚未识别');

  body.innerHTML = `<form id="prep-edit-form">
    <div class="prep-edit-asset">
      <div class="prep-edit-thumb">${asset.thumbnail ? `<img src="${E(asset.thumbnail)}" alt="">` : icon(asset.kind)}</div>
      <div class="prep-edit-meta">
        <strong title="${E(asset.name)}">${E(asset.name)}</strong>
        <p>${E(metaParts.join(' · '))}</p>
      </div>
    </div>
    <div class="form-group">
      <label class="field-label" for="prep-desc-input">画面内容摘要</label>
      <textarea id="prep-desc-input" name="description" rows="4" maxlength="700" required placeholder="简述这段实拍中能看到的环境、设施或人物活动，制作时 AI 会据此选片配镜头。">${E(info.description || '')}</textarea>
      <div class="prep-char-row">
        <span>客观描述可见画面即可，无需写宣传语</span>
        <span id="prep-desc-count">${(info.description || '').length} / 700</span>
      </div>
    </div>
    ${asset.kind === 'video' ? `<div class="switch-row" style="margin-top:12px">
      <label for="prep-motion-check">标记为移动镜头（可用于片头 / 片尾动态画面）</label>
      <input class="switch" type="checkbox" id="prep-motion-check" name="motion" ${info.motion ? 'checked' : ''}>
    </div>` : ''}
    <p class="inline-error" id="prep-edit-error" role="alert"></p>
    <div class="prep-edit-footer">
      <button type="button" class="text-button" data-prep-rerun-in-dialog>${icon('retry')} 改用 AI 重新识别</button>
      <div class="prep-edit-footer-right">
        <button type="button" class="secondary" data-close>取消</button>
        <button type="submit" class="primary">${icon('save')} 保存摘要</button>
      </div>
    </div>
  </form>`;

  const textarea = body.querySelector('#prep-desc-input');
  const counter = body.querySelector('#prep-desc-count');
  textarea.oninput = () => { counter.textContent = `${textarea.value.length} / 700`; };
  body.querySelector('[data-close]').onclick = () => d.close();
  body.querySelector('[data-prep-rerun-in-dialog]').onclick = async e => {
    await busy(e.currentTarget, async () => {
      try {
        await triggerMaterialPrep(iid, { assetId: aid });
        d.close();
      } catch (err) {
        body.querySelector('#prep-edit-error').textContent = err.message;
      }
    });
  };
  body.querySelector('#prep-edit-form').onsubmit = async e => {
    e.preventDefault();
    const errEl = body.querySelector('#prep-edit-error');
    errEl.textContent = '';
    const saveBtn = body.querySelector('[type=submit]');
    await busy(saveBtn, async () => {
      try {
        const payload = {
          assetId: aid,
          description: textarea.value.trim(),
          motion: asset.kind === 'video' ? Boolean(body.querySelector('#prep-motion-check')?.checked) : false,
        };
        const updated = await api(`/api/library/${iid}/asset-analysis`, payload);
        institutionCache.set(iid, updated);
        await refresh(false);
        d.close();
        if (route === 'institution' && institutionId === iid) {
          paintInstitution(updated, location.hash.split('/')[2] || 'materials');
        } else if (route === 'asset') {
          renderAsset();
        }
        notify('素材摘要已保存，后续制作会直接使用修改后的内容');
      } catch (err) {
        errEl.textContent = err.message;
      }
    });
  };
  textarea.focus();
}

async function triggerMaterialPrep(iid, payload = {}) {
  const result = await api(`/api/library/${iid}/prep`, payload);
  prepDismissed.delete(iid);
  if (result.status === 'running') prepSeenRunning.add(iid);
  institutionCache.delete(iid);
  await refresh(false, true);
  if (route === 'institution' && institutionId === iid) {
    const fresh = await getInstitution(iid, true);
    paintInstitution(fresh, location.hash.split('/')[2] || 'materials');
  } else if (route === 'create') {
    const sel = $('#selected-institution');
    if (sel) sel.innerHTML = selectedInstitutionPanel(findInstitution(createDraft.institutionId));
    syncCreatePrepGate();
  } else if (route === 'library') {
    const cards = $('#library-cards');
    if (cards) cards.innerHTML = libraryCards();
  } else if (route === 'asset') {
    renderAsset();
  }
  renderPrepDock();
  if (result.status === 'running') {
    notify(payload.assetId ? '已开始重新识别这条素材，可在右下角小窗查看进度' : '已开始后台整理素材，可在悬浮小窗查看进度');
  } else if (result.status === 'failed') {
    notify(result.error || '未能开始整理，请检查大模型配置', true);
  } else if (result.status === 'ready') {
    notify('素材均已是最新整理结果');
  }
  return result;
}

// Global draggable floating dock
let prepDockEl = null;

function ensurePrepDock() {
  if (prepDockEl) return prepDockEl;
  const el = document.createElement('aside');
  el.id = 'prep-dock';
  el.className = 'prep-dock';
  el.setAttribute('aria-label', '素材整理进度');
  el.hidden = true;
  document.body.append(el);
  prepDockEl = el;
  restorePrepDockPosition(el);
  bindPrepDockDrag(el);
  window.addEventListener('resize', () => clampPrepDockPosition(el));
  return el;
}

function restorePrepDockPosition(el) {
  try {
    const raw = localStorage.getItem(PREP_POS_KEY);
    if (!raw) return;
    const pos = JSON.parse(raw);
    if (Number.isFinite(pos?.left) && Number.isFinite(pos?.top)) {
      el.style.left = pos.left + 'px';
      el.style.top = pos.top + 'px';
      el.style.right = 'auto';
      el.style.bottom = 'auto';
      clampPrepDockPosition(el);
    }
  } catch {}
}

function clampPrepDockPosition(el) {
  if (el.hidden || !el.style.left) return;
  const rect = el.getBoundingClientRect();
  const maxLeft = Math.max(12, window.innerWidth - rect.width - 12);
  const maxTop = Math.max(12, window.innerHeight - rect.height - 12);
  const left = Math.min(maxLeft, Math.max(12, parseFloat(el.style.left) || 12));
  const top = Math.min(maxTop, Math.max(12, parseFloat(el.style.top) || 12));
  el.style.left = left + 'px';
  el.style.top = top + 'px';
}

function bindPrepDockDrag(el) {
  let drag = null;
  el.addEventListener('pointerdown', e => {
    const head = e.target.closest('.prep-dock-head');
    if (!head || e.target.closest('button, a')) return;
    const rect = el.getBoundingClientRect();
    drag = {
      pointerId: e.pointerId,
      offsetX: e.clientX - rect.left,
      offsetY: e.clientY - rect.top,
    };
    el.style.left = rect.left + 'px';
    el.style.top = rect.top + 'px';
    el.style.right = 'auto';
    el.style.bottom = 'auto';
    el.classList.add('is-dragging');
    head.setPointerCapture?.(e.pointerId);
  });
  window.addEventListener('pointermove', e => {
    if (!drag || e.pointerId !== drag.pointerId) return;
    const rect = el.getBoundingClientRect();
    const maxLeft = Math.max(12, window.innerWidth - rect.width - 12);
    const maxTop = Math.max(12, window.innerHeight - rect.height - 12);
    const left = Math.min(maxLeft, Math.max(12, e.clientX - drag.offsetX));
    const top = Math.min(maxTop, Math.max(12, e.clientY - drag.offsetY));
    el.style.left = left + 'px';
    el.style.top = top + 'px';
  });
  const endDrag = e => {
    if (!drag || (e && e.pointerId !== drag.pointerId)) return;
    el.classList.remove('is-dragging');
    try {
      localStorage.setItem(PREP_POS_KEY, JSON.stringify({
        left: parseFloat(el.style.left),
        top: parseFloat(el.style.top),
      }));
    } catch {}
    drag = null;
  };
  window.addEventListener('pointerup', endDrag);
  window.addEventListener('pointercancel', endDrag);
}

function visiblePrepJobs() {
  const jobs = state?.materialPreps || [];
  const list = [];
  for (const job of jobs) {
    if (job.status === 'running') {
      prepSeenRunning.add(job.institutionId);
      prepDismissed.delete(job.institutionId);
      list.push(job);
      continue;
    }
    const sig = prepSignature(job);
    if (prepDismissed.get(job.institutionId) === sig) continue;
    if (job.status === 'failed' && prepSeenRunning.has(job.institutionId)) {
      list.push(job);
    } else if (job.status === 'ready' && prepSeenRunning.has(job.institutionId)) {
      list.push(job);
    }
  }
  return list;
}

function renderPrepDock() {
  const el = ensurePrepDock();
  const jobs = visiblePrepJobs();
  const anyRunning = (state?.materialPreps || []).some(j => j.status === 'running');
  schedulePrepPolling(anyRunning);

  if (!jobs.length) {
    el.hidden = true;
    return;
  }

  const running = jobs.filter(j => j.status === 'running');
  const failed = jobs.filter(j => j.status === 'failed');
  let statusIcon = '<span class="prep-dot-ok"></span>';
  let titleText = '素材整理已完成';
  let countText = '';
  if (running.length) {
    statusIcon = prepSpinnerSvg();
    titleText = running.length === 1 ? '素材整理中' : `素材整理中 (${running.length} 家)`;
    const cur = running[0];
    countText = `${cur.processed}/${cur.total}`;
  } else if (failed.length) {
    statusIcon = '<span class="prep-dot-err"></span>';
    titleText = '素材整理未完成';
  }

  const canClose = !running.length;
  el.classList.toggle('is-collapsed', prepDockCollapsed);
  el.innerHTML = `<div class="prep-dock-head" title="按住可拖动位置">
    <div class="prep-dock-title">
      <span class="prep-dock-grip">${prepGripSvg()}</span>
      ${statusIcon}
      <strong>${E(titleText)}</strong>
      ${countText ? `<span class="prep-dock-count">${E(countText)}</span>` : ''}
    </div>
    <div class="prep-dock-controls">
      <button type="button" class="prep-dock-icon-btn" data-action="prep-dock-toggle" aria-label="${prepDockCollapsed ? '展开素材整理进度' : '收起素材整理进度'}" title="${prepDockCollapsed ? '展开' : '收起'}">
        <svg viewBox="0 0 24 24"><path d="${prepDockCollapsed ? 'M6 15l6-6 6 6' : 'M6 9l6 6 6-6'}"/></svg>
      </button>
      ${canClose ? `<button type="button" class="prep-dock-icon-btn" data-action="prep-dock-close" aria-label="关闭整理提示" title="关闭"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button>` : ''}
    </div>
  </div>
  <div class="prep-dock-body">
    ${jobs.map(j => {
      const pct = j.status === 'ready' ? 100 : Math.max(4, j.percent || 0);
      const note = j.status === 'running'
        ? (j.currentName ? `正在处理：${j.currentName}` : '正在分析素材…')
        : j.status === 'failed'
          ? (j.error || '整理未完成，请重试')
          : `已整理 ${j.analyzedCount} 项素材（含 ${j.motionVideoCount || 0} 段移动镜头）`;
      const leftLink = `<a class="text-button" href="#institution/${E(j.institutionId)}">${j.status === 'running' ? '查看素材详情' : '查看整理结果'} ${j.status === 'running' ? icon('arrow-right') : ''}</a>`;
      const rightAction = j.status === 'running'
        ? ''
        : j.status === 'failed'
          ? (j.needsModel
            ? `<a class="text-button" href="#settings">去配置模型 ${icon('arrow-right')}</a>`
            : `<button type="button" class="text-button" data-action="prep-start" data-id="${E(j.institutionId)}">重试整理</button>`)
          : `<a class="text-button" href="#create/${E(j.institutionId)}">去制作视频 ${icon('arrow-right')}</a>`;
      return `<div class="prep-dock-item is-${E(j.status)}">
        <div class="prep-dock-item-head">
          <strong title="${E(j.institutionName)}">${E(j.institutionName)}</strong>
          <span>${j.status === 'running' ? `${j.processed} / ${j.total}` : j.status === 'ready' ? '已完成' : '未完成'}</span>
        </div>
        <div class="prep-dock-bar"><span style="width:${pct}%"></span></div>
        <p class="prep-dock-note" title="${E(note)}">${E(note)}</p>
        <div class="prep-dock-links">
          ${leftLink}
          ${rightAction}
        </div>
      </div>`;
    }).join('')}
  </div>`;
  el.hidden = false;
  clampPrepDockPosition(el);
}

function schedulePrepPolling(active) {
  if (active && !prepPollTimer) {
    prepPollTimer = setInterval(() => {
      if (!document.hidden) refresh(true).catch(() => {});
    }, 2500);
  } else if (!active && prepPollTimer) {
    clearInterval(prepPollTimer);
    prepPollTimer = null;
  }
}

// Hook refresh() so institution detail, create page, and floating dock stay live during prep
const refreshBeforePrep = refresh;
refresh = async function (paint = true, force = false) {
  const prevPreps = JSON.stringify(state?.materialPreps || []);
  await refreshBeforePrep(paint, force);
  if (!state) return;
  const nextPreps = JSON.stringify(state.materialPreps || []);
  const prepChanged = prevPreps !== nextPreps;
  renderPrepDock();
  if (!paint || !prepChanged) return;

  if (route === 'library') {
    const cards = $('#library-cards');
    if (cards) cards.innerHTML = libraryCards();
  } else if (route === 'create') {
    const sel = $('#selected-institution');
    if (sel) sel.innerHTML = selectedInstitutionPanel(findInstitution(createDraft.institutionId));
    syncCreatePrepGate();
  } else if (route === 'institution' && institutionId) {
    // Avoid disrupting active text input in profile tab or open dialogs
    const activeTab = location.hash.split('/')[2] || 'materials';
    if (activeTab === 'materials' && !document.querySelector('dialog[open]')) {
      try {
        const fresh = await getInstitution(institutionId, true);
        if (route === 'institution' && institutionId === fresh.id && (location.hash.split('/')[2] || 'materials') === 'materials') {
          paintInstitution(fresh, 'materials');
        }
      } catch {}
    }
  }
};

// Click handlers for preparation actions
document.addEventListener('click', async e => {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const action = btn.dataset.action;
  if (action === 'prep-start') {
    await busy(btn, () => triggerMaterialPrep(btn.dataset.id, { mode: 'incremental' }).catch(err => notify(err.message, true)));
  } else if (action === 'prep-rerun-all') {
    await busy(btn, () => triggerMaterialPrep(btn.dataset.id, { mode: 'full' }).catch(err => notify(err.message, true)));
  } else if (action === 'prep-rerun-asset') {
    await busy(btn, () => triggerMaterialPrep(btn.dataset.institution, { assetId: btn.dataset.id }).catch(err => notify(err.message, true)));
  } else if (action === 'prep-edit-asset') {
    openEditSummaryDialog(btn.dataset.institution, btn.dataset.id, btn).catch(err => notify(err.message, true));
  } else if (action === 'prep-dock-toggle') {
    prepDockCollapsed = !prepDockCollapsed;
    try { localStorage.setItem(PREP_COLLAPSE_KEY, prepDockCollapsed ? '1' : '0'); } catch {}
    renderPrepDock();
  } else if (action === 'prep-dock-close') {
    for (const job of visiblePrepJobs()) {
      if (job.status !== 'running') {
        prepDismissed.set(job.institutionId, prepSignature(job));
        prepSeenRunning.delete(job.institutionId);
      }
    }
    renderPrepDock();
  }
});

// Prevent clicking disabled institution "为这家制作" links while preparing
document.addEventListener('click', e => {
  const link = e.target.closest('.institution-actions a.primary[aria-disabled="true"]');
  if (!link) return;
  e.preventDefault();
  notify(link.title || '这家机构正在整理素材，完成后即可制作', true);
});
