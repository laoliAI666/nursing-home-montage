'use strict';
// Text Style Presets (口播文本风格), Visual Block Modal Editor,
// Per-batch Creation Override & Global Banned Words Settings (全站公共禁词黑名单).

(function () {
  function deepClone(v) {
    return JSON.parse(JSON.stringify(v));
  }

  function hasP(code) {
    return typeof window.hasPerm === 'function' ? window.hasPerm(code) : true;
  }

  function textStylesList() {
    return state?.styles?.textStyles || [];
  }

  function defaultTextStyleId() {
    return state?.styles?.defaultTextStyleId || textStylesList()[0]?.id || 'standard-guided';
  }

  function findTextStyle(id) {
    const list = textStylesList();
    return list.find(x => x.id === id) || list.find(x => x.id === defaultTextStyleId()) || list[0] || null;
  }

  function currentBannedWords() {
    const bw = state?.styles?.globalBannedWords || state?.config?.globalBannedWords;
    if (bw && Array.isArray(bw.words)) return bw;
    return { enabled: true, action: 'repair', words: [], count: bw?.count || 0 };
  }

  function bannedWordPacks() {
    return state?.styles?.bannedWordPacks || state?.config?.bannedWordPacks || [];
  }

  function buildRulePreviewText(rule) {
    const op = rule.opening || {};
    const bd = rule.body || {};
    const ed = rule.ending || {};
    const gd = rule.guard || {};
    const bw = currentBannedWords();
    const bwCount = Array.isArray(bw.words) ? bw.words.length : (bw.count || 0);
    const lines = [
      `【模板】${rule.name || '未命名'}（${rule.category || '自定义模板'}）`,
      `【总体口吻】${rule.tone || ''}`,
      `【开头规则】${op.prompt || ''}`,
      `  · 第 1 句硬上限：≤ ${op.firstSentenceMaxChars ?? 34} 字 | 开头必提机构名：${op.requireInstitutionInOpening !== false ? '开' : '关'} | 全文仅提 1 次：${op.institutionMentionOnce !== false ? '开' : '关'}`,
      `  · 批次开场轮换写法：${(op.openingStyles || []).join(' / ') || '无'}`,
      `【正文规则】${bd.prompt || ''}`,
      `  · 允许漏讲普通要点：≤ ${bd.omissionLimit ?? 2} 个 | 同批查重阈值：≥ ${bd.similarityLimit ?? 90}%`,
      `  · 全稿字数策略：${bd.wordCountMode === 'custom' ? `手动指定 ${bd.minChars ?? 195}–${bd.maxChars ?? 245} 可读字` : `跟随数字人实测语速自动校准（兜底 ${bd.minChars ?? 195}–${bd.maxChars ?? 245} 字）`}`,
      `【结尾规则】${ed.prompt || ''}`,
      `  · 默认兜底 CTA：${ed.defaultCta || '邀请有意了解参观的家属留言。'}`,
      `  · 数字人固定片尾钩子：${ed.respectPersonHook !== false ? '优先原样保留（一字不改）' : '忽略固定钩子，按本模板生成'}`,
      `【通用硬门禁】禁括号(${gd.forbidParentheses !== false ? '开' : '关'}) · 禁核验口吻(${gd.forbidReportTone !== false ? '开' : '关'}) · 禁正文提前 CTA(${gd.forbidBodyCta !== false ? '开' : '关'})`,
      `【全站公共禁词黑名单】${bw.enabled ? `已启用（${bwCount} 词 · ${bw.action === 'warn' ? '命中仅记提醒' : '命中强制返修'}）` : '当前已停用'}`,
    ];
    return lines.join('\n');
  }

  // ---------------------------------------------------------------------------
  // 1. Style Library -> Text Styles Tab (风格库 · 文本风格)
  // ---------------------------------------------------------------------------
  window.renderTextStylesBanner = function renderTextStylesBanner() {
    const bw = currentBannedWords();
    const count = Array.isArray(bw.words) ? bw.words.length : (bw.count || 0);
    const canSettings = hasP('settings.read') || hasP('settings.update');
    return `<div class="ts-global-banned-bar">
      <div class="ts-global-banned-info">
        ${icon('shield')}
        <div>
          <strong>全站公共禁词黑名单：${bw.enabled ? `已启用（共 ${count} 词 · ${bw.action === 'warn' ? '命中记入待验收提醒' : '命中强制打回定点返修'}）` : '已停用'}</strong>
          <small>公共禁词独立于单个文本风格统一维护，所有文本风格与批次自动叠加生效。</small>
        </div>
      </div>
      ${canSettings ? `<a class="secondary" href="#settings/banned-words">前往设置管理黑名单 ${icon('arrow-right')}</a>` : ''}
    </div>`;
  };

  function textStyleCardHtml(entry) {
    const canManage = hasP('styles.text_manage');
    const canSetDef = hasP('styles.set_default');
    const op = entry.opening || {};
    const bd = entry.body || {};
    const ed = entry.ending || {};
    const wcText = bd.wordCountMode === 'custom'
      ? `手动 ${bd.minChars}–${bd.maxChars} 字`
      : `自动校准（${bd.minChars}–${bd.maxChars}）`;
    return `<article class="ts-card ${entry.isDefault ? 'is-default' : ''}" data-text-style-id="${E(entry.id)}">
      <div class="ts-card-head">
        <h3><span class="style-num-pill">${E(entry.num || '')}</span>${E(entry.name)}</h3>
        <div class="ts-badge-group">
          ${entry.isDefault ? '<span class="ts-chip is-default">全局默认</span>' : ''}
          ${entry.builtin ? '<span class="ts-chip is-builtin">原始模板</span>' : '<span class="ts-chip is-custom">自定义</span>'}
          ${entry.isModified ? '<span class="ts-chip is-modified">已修改</span>' : ''}
        </div>
      </div>
      <div class="ts-cat-line"><span class="style-cat-tag">${E(entry.category || '自定义模板')}</span></div>
      <p class="ts-desc">${E(entry.description || '')}</p>
      <div class="ts-blocks-summary">
        <div class="ts-block-line"><span class="ts-block-tag">开头</span><span>${E(op.prompt || '')}</span></div>
        <div class="ts-block-line"><span class="ts-block-tag">正文</span><span>${E(bd.prompt || '')}</span></div>
        <div class="ts-block-line"><span class="ts-block-tag">结尾</span><span>${E(ed.defaultCta || '')}${ed.respectPersonHook !== false ? '（人物固定钩子优先）' : ''}</span></div>
      </div>
      <div class="ts-metrics">
        <span>首句 ≤ ${E(op.firstSentenceMaxChars ?? 34)} 字</span>
        <span>${E(wcText)}</span>
        <span>漏讲 ≤ ${E(bd.omissionLimit ?? 2)} 点</span>
        <span>查重 ≥ ${E(bd.similarityLimit ?? 90)}%</span>
      </div>
      <div class="ts-card-actions">
        <div class="ts-actions-left">
          <button type="button" class="primary ts-btn-sm" data-ts-edit="${E(entry.id)}">${canManage ? '编辑规则' : '查看规则'}</button>
          ${canManage ? `<button type="button" class="secondary ts-btn-sm" data-ts-copy="${E(entry.id)}">复制</button>` : ''}
          ${canSetDef && !entry.isDefault ? `<button type="button" class="text-button" data-ts-default="${E(entry.id)}">设为全局默认</button>` : ''}
        </div>
        <div class="ts-actions-right">
          ${canManage
            ? (entry.builtin
              ? (entry.isModified
                ? `<button type="button" class="secondary ts-btn-sm ts-btn-warn" data-ts-reset="${E(entry.id)}">恢复出厂</button>`
                : '<span class="ts-protected-note" title="出厂原始模板受保护，不可删除，但可随时修改">原始模板 · 不可删</span>')
              : `<button type="button" class="text-button ts-btn-danger" data-ts-delete="${E(entry.id)}">删除</button>`)
            : ''}
        </div>
      </div>
    </article>`;
  }

  window.paintTextStylesTab = function paintTextStylesTab(host) {
    const list = textStylesList();
    const q = (typeof styleQuery === 'string' ? styleQuery : '').trim().toLowerCase();
    const cat = typeof styleCategory === 'string' ? styleCategory : '全部';
    const filtered = list.filter(item => {
      if (cat !== '全部' && (item.category || '自定义模板') !== cat) return false;
      if (!q) return true;
      const hay = [
        item.num, item.id, item.name, item.category, item.description, item.tone,
        item.opening?.prompt, item.body?.prompt, item.ending?.prompt, item.ending?.defaultCta,
        ...(item.opening?.openingStyles || []),
      ].join(' ').toLowerCase();
      return hay.includes(q);
    });
    if (!filtered.length) {
      host.innerHTML = emptyState('没有找到匹配的文本风格', '试试清空搜索关键词或切换其他分类。', '<button type="button" class="secondary" data-action="reset-style-filters">重置筛选</button>');
      return;
    }
    host.innerHTML = `<div class="ts-grid">${filtered.map(textStyleCardHtml).join('')}</div>`;
  };

  // ---------------------------------------------------------------------------
  // 2. Shared Visual Block Modal Editor (全局编辑 / 新建 / 单批次微调)
  // ---------------------------------------------------------------------------
  function setNested(obj, path, val) {
    const parts = path.split('.');
    let cur = obj;
    for (let i = 0; i < parts.length - 1; i++) {
      if (!cur[parts[i]] || typeof cur[parts[i]] !== 'object') cur[parts[i]] = {};
      cur = cur[parts[i]];
    }
    cur[parts[parts.length - 1]] = val;
  }

  function openTextStyleModal(options) {
    const { mode, source, onApply } = options;
    // mode: 'global-edit' | 'global-new' | 'batch-override' | 'readonly'
    const readOnly = mode === 'readonly';
    const isBatch = mode === 'batch-override';
    const isNew = mode === 'global-new';
    const draft = deepClone(source);
    if (!draft.opening) draft.opening = { prompt: '', firstSentenceMaxChars: 34, requireInstitutionInOpening: true, institutionMentionOnce: true, openingStyles: ['家属提问', '从具体安排讲起', '直接介绍'] };
    if (!draft.body) draft.body = { prompt: '', omissionLimit: 2, wordCountMode: 'auto', minChars: 195, maxChars: 245, similarityLimit: 90 };
    if (!draft.ending) draft.ending = { prompt: '', defaultCta: '邀请有意了解参观的家属留言。', respectPersonHook: true };
    if (!draft.guard) draft.guard = { forbidParentheses: true, forbidReportTone: true, forbidBodyCta: true };

    let activeBlock = 'basic';
    const dialog = document.createElement('dialog');
    dialog.className = 'style-dialog ts-modal-dialog';

    const titleHtml = isBatch
      ? `本批次微调口播规则 <span class="ts-chip is-modified">仅对本次批次生效</span>`
      : isNew
        ? `新建文本风格 <span class="ts-chip is-custom">自定义模板</span>`
        : readOnly
          ? `查看文本风格 · ${E(draft.name)}`
          : `编辑文本风格 · ${E(draft.name)} ${draft.builtin ? '<span class="ts-chip is-builtin">原始模板（不可删）</span>' : '<span class="ts-chip is-custom">自定义模板</span>'}`;

    const saveLabel = isBatch ? '应用到本批次' : isNew ? '创建并保存' : '保存修改';
    const bw = currentBannedWords();
    const bwCount = Array.isArray(bw.words) ? bw.words.length : (bw.count || 0);

    dialog.innerHTML = `
      <div class="style-dialog-head">
        <h2>${titleHtml}</h2>
        <button type="button" data-close aria-label="关闭">${icon('close')}</button>
      </div>
      <div class="ts-modal-body">
        <nav class="ts-modal-nav" aria-label="规则配置分块">
          <button type="button" data-ts-block="basic" class="active">1. 基础与口吻</button>
          <button type="button" data-ts-block="opening">2. 开头编排</button>
          <button type="button" data-ts-block="body">3. 正文编排</button>
          <button type="button" data-ts-block="ending">4. 结尾引导</button>
          <button type="button" data-ts-block="guard">5. 通用硬门禁</button>
          <button type="button" data-ts-block="preview">规则摘要预览</button>
        </nav>
        <div class="ts-modal-content" data-ts-content></div>
      </div>
      <div class="ts-modal-foot">
        <div class="ts-modal-foot-left">
          ${mode === 'global-edit' && draft.builtin ? `<button type="button" class="secondary ts-btn-sm ts-btn-warn" data-modal-reset>恢复出厂设置</button>` : ''}
          ${isBatch ? '<span class="subtle">修改仅冻结到本批次，不会改动风格库中的全局模板。</span>' : ''}
          <span class="inline-error" data-modal-error role="alert"></span>
        </div>
        <div class="ts-modal-foot-right">
          <button type="button" class="secondary" data-close>${readOnly ? '关闭' : '取消'}</button>
          ${readOnly ? '' : `<button type="button" class="primary" data-modal-save>${saveLabel}</button>`}
        </div>
      </div>`;

    document.body.append(dialog);
    dialog.showModal();

    const close = () => { dialog.close(); dialog.remove(); };
    dialog.querySelectorAll('[data-close]').forEach(b => { b.onclick = close; });
    dialog.addEventListener('click', e => { if (e.target === dialog) close(); });

    const dis = readOnly ? 'disabled' : '';
    const metaDis = (readOnly || isBatch) ? 'disabled' : '';

    function paintBlock() {
      dialog.querySelectorAll('[data-ts-block]').forEach(b => b.classList.toggle('active', b.dataset.tsBlock === activeBlock));
      const host = dialog.querySelector('[data-ts-content]');
      const op = draft.opening, bd = draft.body, ed = draft.ending, gd = draft.guard;

      if (activeBlock === 'basic') {
        host.innerHTML = `
          <div class="ts-sec-card">
            <h3>基础信息</h3>
            <p class="form-help">${isBatch ? '当前基于所选模板微调本批次规则，名称与分类不可更改。' : '用于风格库展示与新建批次时快速选用。'}</p>
            <div class="form-row">
              <div class="form-group">
                <label class="field-label">模板名称</label>
                <input class="input" data-bind="name" maxlength="40" value="${E(draft.name || '')}" ${metaDis} placeholder="例如：重庆探院·交通先导">
              </div>
              <div class="form-group">
                <label class="field-label">适用分类</label>
                <input class="input" data-bind="category" maxlength="24" value="${E(draft.category || '')}" ${metaDis} placeholder="例如：综合通用 / 小红书种草">
              </div>
            </div>
            <div class="form-group" style="margin-bottom:0">
              <label class="field-label">一句话简介</label>
              <input class="input" data-bind="description" maxlength="200" value="${E(draft.description || '')}" ${metaDis} placeholder="说明这套文本风格适合什么机构或平台节奏">
            </div>
          </div>
          <div class="ts-sec-card">
            <h3>总体口吻与表达原则</h3>
            <p class="form-help">控制整篇口播的语气、受众称呼与表述原则（事实核对与防编造底线始终保留）。</p>
            <textarea class="input ts-textarea" data-bind="tone" rows="4" maxlength="800" ${dis}>${E(draft.tone || '')}</textarea>
          </div>`;
      } else if (activeBlock === 'opening') {
        host.innerHTML = `
          <div class="ts-sec-card">
            <h3>开头编排说明（第 1 段 · 数字人出场）</h3>
            <p class="form-help">控制开头句数、切入方式与信息交代顺序。</p>
            <textarea class="input ts-textarea" data-bind="opening.prompt" rows="4" maxlength="800" ${dis}>${E(op.prompt || '')}</textarea>
          </div>
          <div class="ts-sec-card">
            <h3>开头联动硬校验</h3>
            <p class="form-help">以下参数同时驱动写稿提示词与后端自动返修判定，避免提示词与程序校验打架。</p>
            <div class="form-group">
              <div class="label-row">
                <label class="field-label">开头第 1 句字数硬上限</label>
                <strong data-first-max-label>${E(op.firstSentenceMaxChars)} 字</strong>
              </div>
              <input type="range" min="18" max="45" value="${E(op.firstSentenceMaxChars)}" data-bind-num="opening.firstSentenceMaxChars" class="ts-range" ${dis}>
              <p class="form-help">建议 ≤ 34 字以匹配数字人 10 秒退场物理上限（默认 34 字）。</p>
            </div>
            <div class="switch-row">
              <label><strong>开头第 1 段必须自然说出机构名称</strong><small class="ts-sw-desc">关闭后允许开头先做纯悬念或痛点切入，正文再报院名</small></label>
              <input class="switch" type="checkbox" data-bind-bool="opening.requireInstitutionInOpening" ${op.requireInstitutionInOpening ? 'checked' : ''} ${dis}>
            </div>
            <div class="switch-row">
              <label><strong>全文机构名称仅出现 1 次</strong><small class="ts-sw-desc">防止正文与结尾反复念完整院名显得机械（人物固定钩子不计入）</small></label>
              <input class="switch" type="checkbox" data-bind-bool="opening.institutionMentionOnce" ${op.institutionMentionOnce ? 'checked' : ''} ${dis}>
            </div>
          </div>
          <div class="ts-sec-card">
            <h3>同批开场写法轮换池</h3>
            <p class="form-help">同一机构批量制作多条时，程序自动将不同核心主题与这里的开场写法打散组合。</p>
            ${readOnly ? '' : `<div class="ts-tag-input-row">
              <input class="input" data-new-ostyle placeholder="输入开场写法后回车，如：对比选院切入" maxlength="24">
              <button type="button" class="secondary ts-btn-sm" data-add-ostyle>添加</button>
            </div>`}
            <div class="ts-tag-list">
              ${(op.openingStyles || []).map((st, idx) => `<span class="ts-tag">${E(st)}${readOnly ? '' : `<button type="button" data-rm-ostyle="${idx}" aria-label="删除 ${E(st)}">×</button>`}</span>`).join('')}
            </div>
          </div>`;
      } else if (activeBlock === 'body') {
        host.innerHTML = `
          <div class="ts-sec-card">
            <h3>正文编排说明（中间段 · 核心事实展开）</h3>
            <p class="form-help">控制如何组织机构必讲要点、细节展开数量与段落衔接。</p>
            <textarea class="input ts-textarea" data-bind="body.prompt" rows="4" maxlength="1000" ${dis}>${E(bd.prompt || '')}</textarea>
          </div>
          <div class="ts-sec-card">
            <h3>要点覆盖、字数范围与批次去重</h3>
            <div class="form-row">
              <div class="form-group">
                <label class="field-label">允许漏讲普通要点上限</label>
                <select class="input" data-native-select data-bind-num="body.omissionLimit" ${dis}>
                  ${[0, 1, 2, 3, 4, 5].map(n => `<option value="${n}" ${Number(bd.omissionLimit) === n ? 'selected' : ''}>最多漏讲 ${n} 个${n === 0 ? '（必须全覆盖）' : n === 2 ? '（默认）' : ''}</option>`).join('')}
                </select>
                <p class="form-help">用户明确指定的要求始终不可漏讲。</p>
              </div>
              <div class="form-group">
                <div class="label-row">
                  <label class="field-label">同批正文查重打回阈值</label>
                  <strong data-sim-label>${E(bd.similarityLimit)}%</strong>
                </div>
                <input type="range" min="60" max="99" value="${E(bd.similarityLimit)}" data-bind-num="body.similarityLimit" class="ts-range" ${dis}>
                <p class="form-help">越低要求差异越大，达到阈值自动触发局部重写。</p>
              </div>
            </div>
            <div class="form-group" style="margin-bottom:0;padding-top:12px;border-top:1px solid var(--line)">
              <label class="field-label">全稿可读字数控制</label>
              <div class="ts-radio-row">
                <label><input type="radio" name="ts_wc_mode" value="auto" ${bd.wordCountMode !== 'custom' ? 'checked' : ''} ${dis}> 按数字人实测语速自动校准（推荐）</label>
                <label><input type="radio" name="ts_wc_mode" value="custom" ${bd.wordCountMode === 'custom' ? 'checked' : ''} ${dis}> 手动指定字数区间</label>
              </div>
              <div class="form-row">
                <div class="form-group" style="margin-bottom:0">
                  <label class="field-label">最少可读字数</label>
                  <input class="input" type="number" min="60" max="600" data-bind-num="body.minChars" value="${E(bd.minChars)}" ${readOnly || bd.wordCountMode !== 'custom' ? 'disabled' : ''}>
                </div>
                <div class="form-group" style="margin-bottom:0">
                  <label class="field-label">最多可读字数</label>
                  <input class="input" type="number" min="80" max="700" data-bind-num="body.maxChars" value="${E(bd.maxChars)}" ${readOnly || bd.wordCountMode !== 'custom' ? 'disabled' : ''}>
                </div>
              </div>
            </div>
          </div>`;
      } else if (activeBlock === 'ending') {
        host.innerHTML = `
          <div class="ts-sec-card">
            <h3>结尾引导说明（最后 1 段 · 咨询动作）</h3>
            <p class="form-help">控制最后一段的收尾语气、承接方式与行动号召规范。</p>
            <textarea class="input ts-textarea" data-bind="ending.prompt" rows="4" maxlength="600" ${dis}>${E(ed.prompt || '')}</textarea>
          </div>
          <div class="ts-sec-card">
            <h3>默认兜底引导与人物固定片尾钩子</h3>
            <div class="form-group">
              <label class="field-label">默认咨询引导文案（数字人未绑定固定钩子时使用）</label>
              <input class="input" data-bind="ending.defaultCta" maxlength="120" value="${E(ed.defaultCta || '')}" ${dis}>
            </div>
            <div class="switch-row">
              <label><strong>数字人绑定固定片尾钩子时原样优先</strong><small class="ts-sw-desc">开启后最后一段一字不改保留人物固定钩子；关闭则忽略固定钩子改用本模板收尾</small></label>
              <input class="switch" type="checkbox" data-bind-bool="ending.respectPersonHook" ${ed.respectPersonHook !== false ? 'checked' : ''} ${dis}>
            </div>
          </div>`;
      } else if (activeBlock === 'guard') {
        host.innerHTML = `
          <div class="ts-sec-card">
            <h3>通用口播硬门禁</h3>
            <p class="form-help">出稿后由后端程序自动逐段校验，未通过时自动触发局部定点返修。</p>
            <div class="switch-row">
              <label><strong>口播全链路禁括号（）()</strong><small class="ts-sw-desc">拦截括号及括号内注释文字，避免 TTS 念出或字幕残留</small></label>
              <input class="switch" type="checkbox" data-bind-bool="guard.forbidParentheses" ${gd.forbidParentheses !== false ? 'checked' : ''} ${dis}>
            </div>
            <div class="switch-row">
              <label><strong>禁止后台核验口吻</strong><small class="ts-sw-desc">拦截“根据机构资料/原文提到/实拍画面可见”等不自然的后台报告腔</small></label>
              <input class="switch" type="checkbox" data-bind-bool="guard.forbidReportTone" ${gd.forbidReportTone !== false ? 'checked' : ''} ${dis}>
            </div>
            <div class="switch-row">
              <label><strong>禁止正文提前出现留言/私信引导</strong><small class="ts-sw-desc">确保咨询动作统一放在最后一段，避免正文重复催单</small></label>
              <input class="switch" type="checkbox" data-bind-bool="guard.forbidBodyCta" ${gd.forbidBodyCta !== false ? 'checked' : ''} ${dis}>
            </div>
          </div>
          <div class="ts-sec-card ts-sec-muted">
            <h3>全站公共禁词黑名单（统一生效）</h3>
            <p class="form-help" style="margin-bottom:10px">
              当前状态：<strong>${bw.enabled ? `已启用（共 ${bwCount} 词 · ${bw.action === 'warn' ? '命中记提醒' : '命中强制返修'}）` : '已停用'}</strong>。
              公共禁词统一在「设置 → 全站公共禁词黑名单」配置，自动叠加到所有文本风格，无需在单个模板重复维护。
            </p>
            ${hasP('settings.read') || hasP('settings.update') ? `<a class="secondary ts-btn-sm" href="#settings/banned-words" data-jump-banned>前往管理全站禁词黑名单 ${icon('arrow-right')}</a>` : ''}
          </div>`;
      } else {
        host.innerHTML = `
          <div class="ts-sec-card">
            <h3>生效规则摘要预览</h3>
            <p class="form-help">保存后工作台将按以下规则动态生成写稿指令并联动程序校验。</p>
            <pre class="ts-preview-code">${E(buildRulePreviewText(draft))}</pre>
          </div>`;
      }

      // Bind inputs inside current block
      host.querySelectorAll('[data-bind]').forEach(el => {
        el.addEventListener('input', () => setNested(draft, el.dataset.bind, el.value));
      });
      host.querySelectorAll('[data-bind-num]').forEach(el => {
        el.addEventListener('input', () => {
          const val = Number(el.value);
          setNested(draft, el.dataset.bindNum, val);
          if (el.dataset.bindNum === 'opening.firstSentenceMaxChars') {
            const lbl = host.querySelector('[data-first-max-label]');
            if (lbl) lbl.textContent = val + ' 字';
          }
          if (el.dataset.bindNum === 'body.similarityLimit') {
            const lbl = host.querySelector('[data-sim-label]');
            if (lbl) lbl.textContent = val + '%';
          }
        });
      });
      host.querySelectorAll('[data-bind-bool]').forEach(el => {
        el.addEventListener('change', () => setNested(draft, el.dataset.bindBool, el.checked));
      });
      host.querySelectorAll('input[name="ts_wc_mode"]').forEach(el => {
        el.addEventListener('change', () => {
          draft.body.wordCountMode = el.value;
          paintBlock();
        });
      });
      const addOStyle = () => {
        const inp = host.querySelector('[data-new-ostyle]');
        const v = (inp?.value || '').trim();
        if (!v) return;
        draft.opening.openingStyles = draft.opening.openingStyles || [];
        if (!draft.opening.openingStyles.includes(v)) draft.opening.openingStyles.push(v);
        paintBlock();
      };
      host.querySelector('[data-add-ostyle]')?.addEventListener('click', addOStyle);
      host.querySelector('[data-new-ostyle]')?.addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); addOStyle(); }
      });
      host.querySelectorAll('[data-rm-ostyle]').forEach(btn => {
        btn.addEventListener('click', () => {
          const idx = Number(btn.dataset.rmOstyle);
          if ((draft.opening.openingStyles || []).length <= 1) {
            notify('至少保留 1 种开场写法', true);
            return;
          }
          draft.opening.openingStyles.splice(idx, 1);
          paintBlock();
        });
      });
      host.querySelector('[data-jump-banned]')?.addEventListener('click', () => close());
    }

    dialog.querySelectorAll('[data-ts-block]').forEach(btn => {
      btn.addEventListener('click', () => {
        activeBlock = btn.dataset.tsBlock;
        paintBlock();
      });
    });

    dialog.querySelector('[data-modal-reset]')?.addEventListener('click', async e => {
      const errEl = dialog.querySelector('[data-modal-error]');
      errEl.textContent = '';
      await busy(e.currentTarget, async () => {
        try {
          const res = await api('/api/styles/text/reset', { id: draft.id });
          state.styles = res.styles;
          state.config = res.config;
          notify(`已将原始模板「${res.entry.name}」恢复出厂设置`);
          close();
          if (route === 'styles') renderStylesPage();
        } catch (err) {
          errEl.textContent = err.message;
        }
      });
    });

    dialog.querySelector('[data-modal-save]')?.addEventListener('click', async e => {
      const errEl = dialog.querySelector('[data-modal-error]');
      errEl.textContent = '';
      if (!isBatch && !(draft.name || '').trim()) {
        errEl.textContent = '请填写模板名称';
        activeBlock = 'basic';
        paintBlock();
        return;
      }
      if (draft.body.wordCountMode === 'custom' && Number(draft.body.minChars) > Number(draft.body.maxChars)) {
        errEl.textContent = '正文最少可读字数不能大于最多可读字数';
        activeBlock = 'body';
        paintBlock();
        return;
      }
      if (isBatch) {
        onApply?.(draft);
        close();
        return;
      }
      await busy(e.currentTarget, async () => {
        try {
          const payload = deepClone(draft);
          if (isNew) delete payload.id;
          const res = await api('/api/styles/text', payload);
          state.styles = res.styles;
          state.config = res.config;
          notify(isNew ? `已创建文本风格「${res.entry.name}」` : `已保存文本风格「${res.entry.name}」`);
          close();
          if (route === 'styles') renderStylesPage();
        } catch (err) {
          errEl.textContent = err.message;
        }
      });
    });

    paintBlock();
  }

  // Delegated actions for Text Styles Tab
  document.addEventListener('click', async e => {
    const newBtn = e.target.closest('[data-action="new-text-style"]');
    if (newBtn) {
      const base = findTextStyle('standard-guided') || textStylesList()[0];
      if (!base) return;
      const copy = deepClone(base);
      delete copy.id;
      copy.builtin = false;
      copy.isDefault = false;
      copy.isModified = false;
      copy.name = '';
      copy.category = '自定义模板';
      copy.description = '';
      openTextStyleModal({ mode: 'global-new', source: copy });
      return;
    }
    const editBtn = e.target.closest('[data-ts-edit]');
    if (editBtn) {
      const entry = findTextStyle(editBtn.dataset.tsEdit);
      if (!entry) return;
      openTextStyleModal({ mode: hasP('styles.text_manage') ? 'global-edit' : 'readonly', source: entry });
      return;
    }
    const copyBtn = e.target.closest('[data-ts-copy]');
    if (copyBtn) {
      const entry = findTextStyle(copyBtn.dataset.tsCopy);
      if (!entry) return;
      const copy = deepClone(entry);
      delete copy.id;
      copy.builtin = false;
      copy.isDefault = false;
      copy.isModified = false;
      copy.name = `${entry.name} · 副本`.slice(0, 40);
      copy.category = '自定义模板';
      openTextStyleModal({ mode: 'global-new', source: copy });
      return;
    }
    const defBtn = e.target.closest('[data-ts-default]');
    if (defBtn) {
      const id = defBtn.dataset.tsDefault;
      await busy(defBtn, async () => {
        try {
          const res = await api('/api/styles/text/default', { id });
          state.styles = res.styles;
          state.config = res.config;
          notify(`已将「${res.entry.name}」设为全局默认文本风格`);
          if (route === 'styles') renderStylesPage();
        } catch (err) { notify(err.message, true); }
      });
      return;
    }
    const resetBtn = e.target.closest('[data-ts-reset]');
    if (resetBtn) {
      const id = resetBtn.dataset.tsReset;
      const entry = findTextStyle(id);
      if (!entry) return;
      await busy(resetBtn, async () => {
        try {
          const res = await api('/api/styles/text/reset', { id });
          state.styles = res.styles;
          state.config = res.config;
          notify(`已将原始模板「${res.entry.name}」恢复出厂设置`);
          if (route === 'styles') renderStylesPage();
        } catch (err) { notify(err.message, true); }
      });
      return;
    }
    const delBtn = e.target.closest('[data-ts-delete]');
    if (delBtn) {
      const id = delBtn.dataset.tsDelete;
      const entry = findTextStyle(id);
      if (!entry || entry.builtin) return;
      if (!window.confirm(`确定永久删除自定义文本风格「${entry.name}」吗？历史已提交任务不受影响。`)) return;
      await busy(delBtn, async () => {
        try {
          const res = await api('/api/styles/text/delete', { id });
          state.styles = res.styles;
          state.config = res.config;
          notify(`已删除自定义文本风格「${entry.name}」`);
          if (route === 'styles') renderStylesPage();
        } catch (err) { notify(err.message, true); }
      });
    }
  });

  // ---------------------------------------------------------------------------
  // 3. Create Page -> Per-batch Text Style Selection & Override (单批次配置)
  // ---------------------------------------------------------------------------
  function effectiveBatchStyle() {
    const selectedId = createDraft.textStyleId || defaultTextStyleId();
    const base = findTextStyle(selectedId);
    if (!base) return { base: null, effective: null, overridden: false };
    const ov = createDraft.textStyleOverride && typeof createDraft.textStyleOverride === 'object'
      ? createDraft.textStyleOverride : null;
    return { base, effective: ov ? { ...deepClone(base), ...deepClone(ov), name: base.name, id: base.id } : base, overridden: Boolean(ov) };
  }

  function renderBatchTextStyleBox(container) {
    const list = textStylesList();
    if (!list.length || !container) return;
    const { base, effective, overridden } = effectiveBatchStyle();
    if (!base) return;
    if (!createDraft.textStyleId) createDraft.textStyleId = base.id;
    const op = effective.opening || {};
    const bd = effective.body || {};
    const ed = effective.ending || {};
    const bw = currentBannedWords();
    const bwCount = Array.isArray(bw.words) ? bw.words.length : (bw.count || 0);

    container.innerHTML = `
      <div class="label-row">
        <label for="batch-text-style-select">口播文本风格</label>
        <a class="text-button" href="#styles/text">管理风格库模板 ${icon('chevron-right')}</a>
      </div>
      <div class="ts-batch-box ${overridden ? 'is-overridden' : ''}">
        <div class="ts-batch-top">
          <select id="batch-text-style-select" class="input" data-native-select>
            ${list.map(s => `<option value="${E(s.id)}" ${s.id === base.id ? 'selected' : ''}>${E(s.num)} · ${E(s.name)}${s.isDefault ? '（全局默认）' : ''}${s.builtin ? '' : ' [自定义]'}</option>`).join('')}
          </select>
          <button type="button" class="secondary ts-btn-sm" id="btn-batch-ts-tweak">本批次微调规则</button>
          ${overridden ? '<button type="button" class="secondary ts-btn-sm ts-btn-warn" id="btn-batch-ts-reset">还原模板默认</button>' : ''}
        </div>
        <div class="ts-batch-meta">
          ${overridden
            ? '<span class="ts-chip is-modified">已针对本批次覆写（不影响全局模板）</span>'
            : '<span class="ts-chip is-builtin">沿用所选模板规则</span>'}
          <span>首句 ≤ ${E(op.firstSentenceMaxChars ?? 34)} 字</span>
          <span>${bd.wordCountMode === 'custom' ? `手动 ${E(bd.minChars)}–${E(bd.maxChars)} 字` : '字数自动校准'}</span>
          <span>漏讲 ≤ ${E(bd.omissionLimit ?? 2)} 点</span>
          <span>查重 ≥ ${E(bd.similarityLimit ?? 90)}%</span>
          ${bw.enabled ? `<span>公共禁词 ${E(bwCount)} 词</span>` : ''}
        </div>
        <div class="ts-blocks-summary ts-blocks-compact">
          <div class="ts-block-line"><span class="ts-block-tag">开头</span><span>${E(op.prompt || '')}</span></div>
          <div class="ts-block-line"><span class="ts-block-tag">正文</span><span>${E(bd.prompt || '')}</span></div>
          <div class="ts-block-line"><span class="ts-block-tag">结尾</span><span>${E(ed.defaultCta || '')}${ed.respectPersonHook !== false ? '（人物固定钩子优先）' : ''}</span></div>
        </div>
      </div>
      <p class="form-help">提交后将冻结本批次文本规则与当前全站公共禁词快照，后续修改全局模板不会影响已提交批次。</p>`;

    container.querySelector('#batch-text-style-select')?.addEventListener('change', e => {
      createDraft.textStyleId = e.target.value;
      delete createDraft.textStyleOverride;
      saveDraft();
      renderBatchTextStyleBox(container);
    });
    container.querySelector('#btn-batch-ts-tweak')?.addEventListener('click', () => {
      const cur = effectiveBatchStyle();
      openTextStyleModal({
        mode: 'batch-override',
        source: cur.effective,
        onApply: updated => {
          createDraft.textStyleId = cur.base.id;
          createDraft.textStyleOverride = {
            tone: updated.tone,
            opening: updated.opening,
            body: updated.body,
            ending: updated.ending,
            guard: updated.guard,
          };
          saveDraft();
          renderBatchTextStyleBox(container);
          notify('已应用本批次专属口播规则微调');
        },
      });
    });
    container.querySelector('#btn-batch-ts-reset')?.addEventListener('click', () => {
      delete createDraft.textStyleOverride;
      saveDraft();
      renderBatchTextStyleBox(container);
      notify('已还原为所选模板的默认规则');
    });
  }

  const createBeforeTextStyle = renderCreate;
  renderCreate = function () {
    createBeforeTextStyle();
    const form = $('#create-form');
    if (!form || !textStylesList().length) return;
    if ($('#batch-text-style-group', form)) return;
    const reqGroup = $('#requirements', form)?.closest('.form-group');
    const holder = document.createElement('div');
    holder.className = 'form-group';
    holder.id = 'batch-text-style-group';
    if (reqGroup) reqGroup.insertAdjacentElement('afterend', holder);
    else form.insertAdjacentElement('afterbegin', holder);
    renderBatchTextStyleBox(holder);
  };

  // Show frozen text style summary in Task Detail page
  const taskBeforeTextStyle = renderTask;
  renderTask = function () {
    taskBeforeTextStyle();
    const t = state.tasks.find(x => x.id === selectedTask);
    const nr = t?.narrationRules;
    if (!nr) return;
    const brief = main.querySelector('.task-brief');
    if (!brief) return;
    const bw = nr.globalBannedWords || {};
    const bwCount = Array.isArray(bw.words) ? bw.words.length : 0;
    const op = nr.rules?.opening || {};
    const bd = nr.rules?.body || {};
    brief.insertAdjacentHTML('beforeend',
      `<h3>本次固定文本风格</h3>
      <p>模板：${E(nr.styleName || nr.styleId || '标准综合带看')}${nr.overridden ? '（本批次已微调）' : ''} · 首句 ≤ ${E(op.firstSentenceMaxChars ?? 34)} 字 · 漏讲 ≤ ${E(bd.omissionLimit ?? 2)} 点 · 查重 ≥ ${E(bd.similarityLimit ?? 90)}%${bw.enabled ? ` · 公共禁词 ${bwCount} 词（${bw.action === 'warn' ? '命中提醒' : '命中返修'}）` : ''}</p>`);
  };

  // ---------------------------------------------------------------------------
  // 4. Settings Page -> Global Banned Words Blacklist Panel (全站公共禁词黑名单)
  // ---------------------------------------------------------------------------
  let settingsBannedDraft = null;

  function renderSettingsBannedPanel() {
    const canEdit = hasP('settings.update');
    const bw = settingsBannedDraft || deepClone(currentBannedWords());
    if (!Array.isArray(bw.words)) bw.words = [];
    settingsBannedDraft = bw;
    const packs = bannedWordPacks();
    const dis = canEdit ? '' : 'disabled';

    return `<section class="panel" id="settings-banned-words-panel" aria-labelledby="banned-words-title">
      <div class="settings-section-heading">
        <span>${icon('shield')}</span>
        <div>
          <h2 id="banned-words-title">全站公共禁词黑名单</h2>
          <p>全局生效于所有口播文本风格与所有新建批次。写稿时自动注入禁词约束，出稿后程序逐段扫描校验。</p>
        </div>
      </div>

      <div class="switch-row">
        <label for="global-banned-enabled"><strong>启用全站公共禁词黑名单</strong></label>
        <input type="checkbox" class="switch" id="global-banned-enabled" ${bw.enabled ? 'checked' : ''} ${dis}>
      </div>

      <div class="form-group" style="margin-top:14px">
        <label class="field-label">命中禁词后的处理方式</label>
        <div class="ts-radio-row">
          <label><input type="radio" name="global_banned_action" value="repair" ${bw.action !== 'warn' ? 'checked' : ''} ${dis}> <strong>强制打回定点返修（推荐）</strong>：自动指出命中词并改写</label>
          <label><input type="radio" name="global_banned_action" value="warn" ${bw.action === 'warn' ? 'checked' : ''} ${dis}> 仅写入待验收提醒，不阻断出片</label>
        </div>
      </div>

      ${canEdit && packs.length ? `<div class="form-group">
        <label class="field-label">一键导入行业预设禁词包</label>
        <div class="ts-pack-row">
          ${packs.map(p => `<button type="button" class="secondary ts-btn-sm" data-bw-import-pack="${E(p.id)}" title="${E(p.description || '')}">＋ ${E(p.label)}（${(p.words || []).length}词）</button>`).join('')}
          <button type="button" class="text-button" data-bw-reset-default>恢复默认推荐词库</button>
        </div>
      </div>` : ''}

      <div class="form-group">
        <div class="label-row">
          <label class="field-label" for="global-banned-input">当前生效禁词（共 <span id="global-banned-count">${bw.words.length}</span> 个）</label>
          ${canEdit && bw.words.length ? '<button type="button" class="text-button ts-btn-danger" data-bw-clear>清空全部</button>' : ''}
        </div>
        ${canEdit ? `<div class="ts-tag-input-row">
          <input class="input" id="global-banned-input" placeholder="输入禁词后回车（支持用逗号、顿号或空格批量添加）" maxlength="300">
          <button type="button" class="secondary ts-btn-sm" id="btn-bw-add">添加禁词</button>
        </div>` : ''}
        <div class="ts-tag-list" id="global-banned-tags">
          ${bw.words.length
            ? bw.words.map((w, i) => `<span class="ts-tag is-sensitive">${E(w)}${canEdit ? `<button type="button" data-bw-rm="${i}" aria-label="删除 ${E(w)}">×</button>` : ''}</span>`).join('')
            : '<span class="subtle">暂未配置公共禁词</span>'}
        </div>
      </div>

      ${canEdit ? `<div class="ts-banned-actions">
        <button type="button" class="secondary" id="btn-bw-save">${icon('save')} 单独保存公共禁词黑名单</button>
        <span class="form-help" id="bw-save-status" role="status" style="margin:0"></span>
      </div>` : '<p class="form-help">当前账号仅可查看全站公共禁词配置，如需修改请联系超级管理员。</p>'}
    </section>`;
  }

  function bindSettingsBannedPanel(panel) {
    if (!panel) return;
    const repaint = () => {
      const html = renderSettingsBannedPanel();
      const wrap = document.createElement('div');
      wrap.innerHTML = html;
      const fresh = wrap.firstElementChild;
      panel.replaceWith(fresh);
      bindSettingsBannedPanel(fresh);
    };

    panel.querySelector('#global-banned-enabled')?.addEventListener('change', e => {
      settingsBannedDraft.enabled = e.target.checked;
    });
    panel.querySelectorAll('input[name="global_banned_action"]').forEach(el => {
      el.addEventListener('change', () => {
        settingsBannedDraft.action = el.value;
      });
    });

    const addWords = () => {
      const inp = panel.querySelector('#global-banned-input');
      const raw = (inp?.value || '').trim();
      if (!raw) return;
      raw.split(/[,，、;\s]+/).map(x => x.trim()).filter(Boolean).forEach(w => {
        if (!settingsBannedDraft.words.includes(w)) settingsBannedDraft.words.push(w.slice(0, 40));
      });
      repaint();
    };
    panel.querySelector('#btn-bw-add')?.addEventListener('click', addWords);
    panel.querySelector('#global-banned-input')?.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); addWords(); }
    });
    panel.querySelectorAll('[data-bw-rm]').forEach(btn => {
      btn.addEventListener('click', () => {
        settingsBannedDraft.words.splice(Number(btn.dataset.bwRm), 1);
        repaint();
      });
    });
    panel.querySelector('[data-bw-clear]')?.addEventListener('click', () => {
      settingsBannedDraft.words = [];
      repaint();
    });
    panel.querySelectorAll('[data-bw-import-pack]').forEach(btn => {
      btn.addEventListener('click', () => {
        const pack = bannedWordPacks().find(p => p.id === btn.dataset.bwImportPack);
        if (!pack) return;
        let added = 0;
        (pack.words || []).forEach(w => {
          if (!settingsBannedDraft.words.includes(w)) {
            settingsBannedDraft.words.push(w);
            added++;
          }
        });
        repaint();
        notify(added ? `已导入「${pack.label}」新增 ${added} 个禁词（记得保存）` : `「${pack.label}」中的词汇已在黑名单中`);
      });
    });
    panel.querySelector('[data-bw-reset-default]')?.addEventListener('click', async e => {
      await busy(e.currentTarget, async () => {
        try {
          const res = await api('/api/settings/banned-words', { resetDefault: true });
          state.styles = res.styles;
          state.config = res.config;
          settingsBannedDraft = deepClone(res.globalBannedWords);
          repaint();
          notify('已恢复默认推荐的全站公共禁词黑名单');
        } catch (err) { notify(err.message, true); }
      });
    });
    panel.querySelector('#btn-bw-save')?.addEventListener('click', async e => {
      const statusEl = panel.querySelector('#bw-save-status');
      if (statusEl) statusEl.textContent = '';
      await busy(e.currentTarget, async () => {
        try {
          const res = await api('/api/settings/banned-words', settingsBannedDraft);
          state.styles = res.styles;
          state.config = res.config;
          settingsBannedDraft = deepClone(res.globalBannedWords);
          if (statusEl) statusEl.textContent = '已保存';
          notify('全站公共禁词黑名单已保存');
        } catch (err) {
          notify(err.message, true);
        }
      });
    });
  }

  const settingsBeforeBanned = renderSettings;
  renderSettings = function () {
    settingsBeforeBanned();
    const form = $('#settings-form');
    if (!form || $('#settings-banned-words-panel', form)) return;
    settingsBannedDraft = deepClone(currentBannedWords());
    const sticky = form.querySelector('.sticky-actions');
    const html = renderSettingsBannedPanel();
    if (sticky) sticky.insertAdjacentHTML('beforebegin', html);
    else form.insertAdjacentHTML('beforeend', html);
    const panel = $('#settings-banned-words-panel', form);
    bindSettingsBannedPanel(panel);
    if (location.hash === '#settings/banned-words' && panel) {
      requestAnimationFrame(() => panel.scrollIntoView({ behavior: 'smooth', block: 'start' }));
    }
  };

  // ---------------------------------------------------------------------------
  // 5. Transparently attach textStyleId/Override & globalBannedWords on API calls
  // ---------------------------------------------------------------------------
  const apiBeforeTextStyles = api;
  api = async function textStyleAwareApi(path, data, options = {}) {
    if (data && typeof data === 'object') {
      if ((path === '/api/tasks' || path === '/api/draft') && !('textStyleId' in data)) {
        const extra = {};
        if (createDraft.textStyleId) extra.textStyleId = createDraft.textStyleId;
        if (createDraft.textStyleOverride && typeof createDraft.textStyleOverride === 'object') {
          extra.textStyleOverride = createDraft.textStyleOverride;
        }
        if (Object.keys(extra).length) data = { ...data, ...extra };
      } else if (path === '/api/config' && !('globalBannedWords' in data) && $('#settings-banned-words-panel') && settingsBannedDraft) {
        data = { ...data, globalBannedWords: settingsBannedDraft };
      }
    }
    return apiBeforeTextStyles(path, data, options);
  };
})();
