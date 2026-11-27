"""Text style presets (口播文本风格) and global banned words blacklist (全站公共禁词黑名单)."""
from __future__ import annotations

from copy import deepcopy
import re
import uuid
from typing import Any


DEFAULT_TEXT_STYLE_ID = "standard-guided"
DEFAULT_CTA = "邀请有意了解参观的家属留言。"

PRESET_BANNED_PACKS: dict[str, dict[str, Any]] = {
    "medical": {
        "id": "medical",
        "label": "医疗夸大词（如根治、治愈）",
        "description": "避免触碰医疗广告与未经核实的康复、疗效或认知改善承诺",
        "words": ["根治", "治愈", "疗效", "药到病除", "逆转衰老", "改善认知", "延缓痴呆", "临床有效", "包治"],
    },
    "extreme": {
        "id": "extreme",
        "label": "广告极限词（如第一、唯一）",
        "description": "过滤绝对化与排他性极限表述，降低平台限流与下架风险",
        "words": ["第一", "唯一", "顶级", "最棒", "全网最低", "100%", "绝对安全", "绝不会摔倒"],
    },
    "promise": {
        "id": "promise",
        "label": "未确认承诺词（如免费接送）",
        "description": "防止口播擅自承诺未经确认的优惠、免费接送或效果保障",
        "words": ["免费接送", "包满意", "保证康复", "立减优惠", "零风险"],
    },
}

DEFAULT_GLOBAL_BANNED_WORDS: dict[str, Any] = {
    "enabled": True,
    "action": "repair",  # "repair" | "warn"
    "words": [
        "根治", "治愈", "疗效", "药到病除", "逆转衰老",
        "改善认知", "延缓痴呆", "绝对安全", "绝不会摔倒",
        "第一", "唯一", "100%",
    ],
}

FACTORY_TEXT_STYLES: list[dict[str, Any]] = [
    {
        "id": "standard-guided",
        "num": "01",
        "name": "标准综合带看",
        "category": "综合通用",
        "builtin": True,
        "description": "当前工作台默认规则：客观销售带看视角，全维覆盖机构核心要点，稳妥适配抖音/视频号/小红书常规探访。",
        "tone": "客观自然的销售带看口吻，面向长辈和子女用日常大白话讲清具体安排；保留正式名称、适用对象与服务条件，省去宣传评价和未经核实的效果承诺。",
        "opening": {
            "prompt": "开头最多两句，按「本条变化参数」切入，并自然交代机构名称。开头第 1 句作为数字人出场句，控制在 15–30 字（约 5–8 秒自然说完，不要写得过长）。完整机构名只说一次。开头已经交代的事实，正文接着往下讲。",
            "firstSentenceMaxChars": 34,
            "requireInstitutionInOpening": True,
            "institutionMentionOnce": True,
            "openingStyles": ["家属提问", "从具体安排讲起", "直接介绍"],
        },
        "body": {
            "prompt": "逐项覆盖「必讲要点」中的关键信息。相关内容可以合在一句或一段，每个事实讲清一次。全文只扩展一个重要细节，原文中的其余细节用来核对。「展开重点」可以用一个有原文依据的细节讲具体。",
            "omissionLimit": 2,
            "wordCountMode": "auto",
            "minChars": 195,
            "maxChars": 245,
            "similarityLimit": 90,
        },
        "ending": {
            "prompt": "结尾用一句话完成「咨询引导」，紧扣本条主题；不堆叠留言、关注、转发等无关诉求，不增加未经确认的发资料、接送、预约或优惠承诺。",
            "defaultCta": DEFAULT_CTA,
            "respectPersonHook": True,
        },
        "guard": {
            "forbidParentheses": True,
            "forbidReportTone": True,
            "forbidBodyCta": True,
        },
    },
    {
        "id": "painpoint-hook",
        "num": "02",
        "name": "家属痛点切入",
        "category": "高停留引流",
        "builtin": True,
        "description": "先直击子女选养老院的典型顾虑（独居安全、失能照护、探望不便），再引出机构具体解法，适合信息流拉高 3 秒完播。",
        "tone": "懂家属顾虑的务实带看口吻，句式干脆利落；先抛真实生活场景痛点，再用原文可核验的具体配置作答，不制造焦虑夸大，不编造案例。",
        "opening": {
            "prompt": "第 1 句用子女常见选院痛点或场景提问切入（15–28 字），第 2 句自然引出机构名称并点明能解决的核心问题。完整机构名全文只说一次。",
            "firstSentenceMaxChars": 34,
            "requireInstitutionInOpening": True,
            "institutionMentionOnce": True,
            "openingStyles": ["子女焦虑提问", "选院常见困扰", "场景痛点直击"],
        },
        "body": {
            "prompt": "按「常见担心 → 院内具体安排 → 有原文依据的实拍细节」组织正文，优先把照护响应、安全配置和就医保障讲透，其余必讲要点简洁串联，每项只讲一次。",
            "omissionLimit": 2,
            "wordCountMode": "auto",
            "minChars": 195,
            "maxChars": 245,
            "similarityLimit": 85,
        },
        "ending": {
            "prompt": "呼应开头痛点给出明确、单一的咨询动作，引导有同类需求的家属评论区留言了解参观。",
            "defaultCta": "家里长辈也有类似照护需求，评论区留言，带你进一步了解参观安排。",
            "respectPersonHook": True,
        },
        "guard": {
            "forbidParentheses": True,
            "forbidReportTone": True,
            "forbidBodyCta": True,
        },
    },
    {
        "id": "xhs-visit-diary",
        "num": "03",
        "name": "实地踩点体验感",
        "category": "小红书种草",
        "builtin": True,
        "description": "像帮家里长辈提前踩点做功课的邻里分享风，重居住氛围、采光花园与日常餐食细节，弱化推销感。",
        "tone": "亲切平实的帮看分享口吻，少用书面词与宣传腔，多用日常看得见的居住与生活细节表达，保持客观真实，不虚构个人主观故事或对话。",
        "opening": {
            "prompt": "从进院最直观的居住氛围、院区环境或生活细节自然切入，顺带交代机构名称，第 1 句轻松顺耳控制在 15–28 字。完整机构名全文只说一次。",
            "firstSentenceMaxChars": 34,
            "requireInstitutionInOpening": True,
            "institutionMentionOnce": True,
            "openingStyles": ["第一印象切入", "居住细节切入", "帮长辈踩点视角"],
        },
        "body": {
            "prompt": "重点展开居室空间、公共活动、餐食与环境等贴近日常的卖点，照护与医护要点用温和通俗的话自然带过，避免机械念清单。",
            "omissionLimit": 2,
            "wordCountMode": "auto",
            "minChars": 195,
            "maxChars": 245,
            "similarityLimit": 85,
        },
        "ending": {
            "prompt": "用轻松友好的轻互动单句收尾，邀请感兴趣的家属留言交流或咨询参观。",
            "defaultCta": "想了解这家更多房型和参观方式，评论区留个言我告诉你。",
            "respectPersonHook": True,
        },
        "guard": {
            "forbidParentheses": True,
            "forbidReportTone": True,
            "forbidBodyCta": True,
        },
    },
    {
        "id": "medical-care-pro",
        "num": "04",
        "name": "照护与医养专业版",
        "category": "刚需决策",
        "builtin": True,
        "description": "面向失能/半失能、认知症及慢病护理刚需家庭，条理清晰、核心要点漏讲容差收紧，突出照护分级与就医确定性。",
        "tone": "沉稳、专业、严谨的顾问带看口吻；严格保留照护分区、服务方案正式名称、适用对象与就医条件，杜绝夸张医疗承诺或疗效暗示。",
        "opening": {
            "prompt": "开篇直接点明机构面向的照护类型、特色照护体系或医养配置，并在第 1 段自然交代完整机构名称，第 1 句控制在 15–30 字。完整机构名全文只说一次。",
            "firstSentenceMaxChars": 34,
            "requireInstitutionInOpening": True,
            "institutionMentionOnce": True,
            "openingStyles": ["照护需求直切", "分区能力亮点", "就医保障切入"],
        },
        "body": {
            "prompt": "按「照护分区与适用对象 → 特色照护与医护支持 → 适老居住配置 → 日常生活服务」顺序分层讲清，核心要点尽量完整覆盖。",
            "omissionLimit": 1,
            "wordCountMode": "auto",
            "minChars": 205,
            "maxChars": 250,
            "similarityLimit": 88,
        },
        "ending": {
            "prompt": "引导家属按长辈当前身体和照护需求留言咨询对应安排，保持单一咨询动作。",
            "defaultCta": "想按长辈实际情况了解照护分级和参观预约，可以在评论区留言咨询。",
            "respectPersonHook": True,
        },
        "guard": {
            "forbidParentheses": True,
            "forbidReportTone": True,
            "forbidBodyCta": True,
        },
    },
    {
        "id": "fast-reel-40s",
        "num": "05",
        "name": "短平快精炼版",
        "category": "快节奏测款",
        "builtin": True,
        "description": "适合 35–45 秒短平快矩阵铺量或信息流测爆款：开篇极简抓眼，正文只打 3 个最核心卖点，收尾干脆。",
        "tone": "节奏明快、信息浓缩的短句口播；每句只讲一个明确卖点，去掉所有铺垫和过渡废话，直接讲清事实。",
        "opening": {
            "prompt": "1 句话直接抛出机构最具辨识度的核心卖点并说出机构名，控制在 12–24 字内，迅速进入画面展示。完整机构名全文只说一次。",
            "firstSentenceMaxChars": 28,
            "requireInstitutionInOpening": True,
            "institutionMentionOnce": True,
            "openingStyles": ["一句话直出卖点", "区位亮点速览", "核心优势快切"],
        },
        "body": {
            "prompt": "只精选 3 个最直观、有实拍支撑的必讲要点（如区位交通、核心照护、居室或活动），每点用 1–2 句短句打穿，其余次要细节果断舍弃。",
            "omissionLimit": 3,
            "wordCountMode": "custom",
            "minChars": 130,
            "maxChars": 175,
            "similarityLimit": 80,
        },
        "ending": {
            "prompt": "极简短句完成咨询动作（10–18 字），干脆利落不拖沓。",
            "defaultCta": "想预约实地看看，评论区直接留言。",
            "respectPersonHook": True,
        },
        "guard": {
            "forbidParentheses": True,
            "forbidReportTone": True,
            "forbidBodyCta": True,
        },
    },
]

FACTORY_BY_ID: dict[str, dict[str, Any]] = {row["id"]: row for row in FACTORY_TEXT_STYLES}


def _clean_text(value: Any, fallback: str = "", *, max_len: int = 1200) -> str:
    if not isinstance(value, str):
        return fallback
    text = value.strip()
    return text[:max_len] if text else fallback


def _clean_int(value: Any, fallback: int, *, low: int, high: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, num))


def _clean_word_list(words: Any, *, max_items: int = 300, max_word_len: int = 40) -> list[str]:
    if isinstance(words, str):
        raw_items = re.split(r"[,，、;\n\r]+", words)
    elif isinstance(words, list):
        raw_items = words
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, str):
            continue
        w = item.strip()[:max_word_len]
        if not w or w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= max_items:
            break
    return out


def normalize_style_rule(raw: Any, *, fallback: dict[str, Any] | None = None, strict: bool = False) -> dict[str, Any]:
    """Validate and normalize a text style template payload."""
    base = deepcopy(fallback or FACTORY_BY_ID[DEFAULT_TEXT_STYLE_ID])
    if not isinstance(raw, dict):
        if strict:
            raise ValueError("文本风格配置格式不正确。")
        return base

    name = _clean_text(raw.get("name"), "" if strict else base["name"], max_len=40)
    if strict and not name:
        raise ValueError("请填写文本风格名称（1–40 字）。")
    category = _clean_text(raw.get("category"), base.get("category") or "自定义模板", max_len=24)
    description = _clean_text(raw.get("description"), base.get("description") or "", max_len=200)
    tone = _clean_text(raw.get("tone"), base["tone"], max_len=800)

    raw_opening = raw.get("opening") if isinstance(raw.get("opening"), dict) else {}
    base_opening = base["opening"]
    opening_styles = _clean_word_list(raw_opening.get("openingStyles"), max_items=12, max_word_len=24)
    if not opening_styles:
        opening_styles = list(base_opening["openingStyles"])
    opening = {
        "prompt": _clean_text(raw_opening.get("prompt"), base_opening["prompt"], max_len=800),
        "firstSentenceMaxChars": _clean_int(
            raw_opening.get("firstSentenceMaxChars"),
            base_opening["firstSentenceMaxChars"],
            low=18,
            high=45,
        ),
        "requireInstitutionInOpening": bool(
            raw_opening.get("requireInstitutionInOpening", base_opening["requireInstitutionInOpening"])
        ),
        "institutionMentionOnce": bool(
            raw_opening.get("institutionMentionOnce", base_opening["institutionMentionOnce"])
        ),
        "openingStyles": opening_styles,
    }

    raw_body = raw.get("body") if isinstance(raw.get("body"), dict) else {}
    base_body = base["body"]
    wc_mode = str(raw_body.get("wordCountMode") or base_body["wordCountMode"]).strip()
    if wc_mode not in ("auto", "custom"):
        wc_mode = base_body["wordCountMode"]
    min_chars = _clean_int(raw_body.get("minChars"), base_body["minChars"], low=60, high=600)
    max_chars = _clean_int(raw_body.get("maxChars"), base_body["maxChars"], low=80, high=700)
    if min_chars > max_chars:
        if strict:
            raise ValueError("正文最少可读字数不能大于最多可读字数。")
        min_chars, max_chars = max_chars, min_chars
    body = {
        "prompt": _clean_text(raw_body.get("prompt"), base_body["prompt"], max_len=1000),
        "omissionLimit": _clean_int(raw_body.get("omissionLimit"), base_body["omissionLimit"], low=0, high=5),
        "wordCountMode": wc_mode,
        "minChars": min_chars,
        "maxChars": max_chars,
        "similarityLimit": _clean_int(
            raw_body.get("similarityLimit"),
            base_body["similarityLimit"],
            low=60,
            high=99,
        ),
    }

    raw_ending = raw.get("ending") if isinstance(raw.get("ending"), dict) else {}
    base_ending = base["ending"]
    ending = {
        "prompt": _clean_text(raw_ending.get("prompt"), base_ending["prompt"], max_len=600),
        "defaultCta": _clean_text(raw_ending.get("defaultCta"), base_ending["defaultCta"], max_len=120),
        "respectPersonHook": bool(raw_ending.get("respectPersonHook", base_ending["respectPersonHook"])),
    }

    raw_guard = raw.get("guard") if isinstance(raw.get("guard"), dict) else {}
    base_guard = base["guard"]
    guard = {
        "forbidParentheses": bool(raw_guard.get("forbidParentheses", base_guard["forbidParentheses"])),
        "forbidReportTone": bool(raw_guard.get("forbidReportTone", base_guard["forbidReportTone"])),
        "forbidBodyCta": bool(raw_guard.get("forbidBodyCta", base_guard["forbidBodyCta"])),
    }

    return {
        "id": base["id"],
        "num": base.get("num", ""),
        "name": name or base["name"],
        "category": category,
        "builtin": bool(base.get("builtin", False)),
        "description": description,
        "tone": tone,
        "opening": opening,
        "body": body,
        "ending": ending,
        "guard": guard,
    }


def normalize_global_banned_words(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return deepcopy(DEFAULT_GLOBAL_BANNED_WORDS)
    action = str(raw.get("action") or DEFAULT_GLOBAL_BANNED_WORDS["action"]).strip()
    if action not in ("repair", "warn"):
        action = "repair"
    enabled = bool(raw.get("enabled", True))
    if "words" in raw:
        words = _clean_word_list(raw.get("words"), max_items=500, max_word_len=40)
    else:
        words = list(DEFAULT_GLOBAL_BANNED_WORDS["words"])
    return {"enabled": enabled, "action": action, "words": words}


def _strip_meta(style: dict[str, Any]) -> dict[str, Any]:
    keys = ("name", "category", "description", "tone", "opening", "body", "ending", "guard")
    return {k: style[k] for k in keys if k in style}


def is_builtin_modified(style: dict[str, Any]) -> bool:
    sid = style.get("id")
    if sid not in FACTORY_BY_ID:
        return False
    return _strip_meta(style) != _strip_meta(FACTORY_BY_ID[sid])


def load_text_styles_state(cfg: dict[str, Any]) -> dict[str, Any]:
    """Build the full public text-style + global-banned-words state from project.json."""
    settings = cfg.get("settings") or {}
    stored = settings.get("text_styles") if isinstance(settings.get("text_styles"), dict) else {}
    overrides = stored.get("builtinOverrides") if isinstance(stored.get("builtinOverrides"), dict) else {}
    custom_raw = stored.get("custom") if isinstance(stored.get("custom"), list) else []

    items: list[dict[str, Any]] = []
    for factory in FACTORY_TEXT_STYLES:
        ov = overrides.get(factory["id"])
        merged = normalize_style_rule(ov, fallback=factory, strict=False) if isinstance(ov, dict) else deepcopy(factory)
        merged["id"] = factory["id"]
        merged["num"] = factory["num"]
        merged["builtin"] = True
        merged["isModified"] = is_builtin_modified(merged)
        items.append(merged)

    next_num = len(items) + 1
    seen_ids = {row["id"] for row in items}
    for row in custom_raw:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("id") or "").strip()
        if not cid or cid in seen_ids or cid in FACTORY_BY_ID:
            continue
        seen_ids.add(cid)
        normalized = normalize_style_rule(row, fallback=FACTORY_BY_ID[DEFAULT_TEXT_STYLE_ID], strict=False)
        normalized["id"] = cid
        normalized["num"] = f"{next_num:02d}"
        normalized["builtin"] = False
        normalized["isModified"] = False
        next_num += 1
        items.append(normalized)

    default_id = str(stored.get("defaultId") or DEFAULT_TEXT_STYLE_ID).strip()
    if default_id not in seen_ids:
        default_id = DEFAULT_TEXT_STYLE_ID

    for row in items:
        row["isDefault"] = row["id"] == default_id

    banned = normalize_global_banned_words(settings.get("global_banned_words"))
    return {
        "defaultTextStyleId": default_id,
        "textStyles": items,
        "globalBannedWords": banned,
        "bannedWordPacks": list(PRESET_BANNED_PACKS.values()),
    }


def get_text_style(cfg: dict[str, Any], style_id: str | None = None) -> dict[str, Any]:
    state = load_text_styles_state(cfg)
    target = str(style_id or state["defaultTextStyleId"]).strip() or state["defaultTextStyleId"]
    for row in state["textStyles"]:
        if row["id"] == target:
            return row
    raise ValueError(f"未找到指定的文本风格模板：{target}")


def save_text_style(cfg: dict[str, Any], payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a custom text style or update an existing builtin/custom text style in cfg (mutates cfg)."""
    if not isinstance(payload, dict):
        raise ValueError("请提交有效的文本风格配置。")
    settings = cfg.setdefault("settings", {})
    stored = settings.setdefault("text_styles", {})
    overrides = stored.setdefault("builtinOverrides", {})
    custom = stored.setdefault("custom", [])

    raw_id = str(payload.get("id") or "").strip()
    if raw_id and raw_id in FACTORY_BY_ID:
        factory = FACTORY_BY_ID[raw_id]
        updated = normalize_style_rule(payload, fallback=factory, strict=True)
        updated["id"] = raw_id
        updated["num"] = factory["num"]
        updated["builtin"] = True
        if is_builtin_modified(updated):
            overrides[raw_id] = _strip_meta(updated)
        else:
            overrides.pop(raw_id, None)
        return updated, load_text_styles_state(cfg)

    if raw_id:
        for idx, row in enumerate(custom):
            if isinstance(row, dict) and row.get("id") == raw_id:
                existing = normalize_style_rule(row, fallback=FACTORY_BY_ID[DEFAULT_TEXT_STYLE_ID], strict=False)
                updated = normalize_style_rule(payload, fallback=existing, strict=True)
                updated["id"] = raw_id
                updated["builtin"] = False
                custom[idx] = {"id": raw_id, **_strip_meta(updated)}
                return updated, load_text_styles_state(cfg)

    # Create a new custom text style
    new_id = "ts-" + uuid.uuid4().hex[:10]
    created = normalize_style_rule(payload, fallback=FACTORY_BY_ID[DEFAULT_TEXT_STYLE_ID], strict=True)
    created["id"] = new_id
    created["builtin"] = False
    custom.append({"id": new_id, **_strip_meta(created)})
    return created, load_text_styles_state(cfg)


def reset_builtin_text_style(cfg: dict[str, Any], style_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    sid = str(style_id or "").strip()
    if sid not in FACTORY_BY_ID:
        raise ValueError("仅出厂原始模板支持恢复出厂设置。")
    settings = cfg.setdefault("settings", {})
    stored = settings.setdefault("text_styles", {})
    overrides = stored.setdefault("builtinOverrides", {})
    overrides.pop(sid, None)
    state = load_text_styles_state(cfg)
    entry = next(x for x in state["textStyles"] if x["id"] == sid)
    return entry, state


def delete_custom_text_style(cfg: dict[str, Any], style_id: str) -> tuple[str, dict[str, Any]]:
    sid = str(style_id or "").strip()
    if sid in FACTORY_BY_ID:
        raise ValueError("原始模板受保护，不允许删除；你可以修改它或一键恢复出厂设置。")
    settings = cfg.setdefault("settings", {})
    stored = settings.setdefault("text_styles", {})
    custom = stored.get("custom") if isinstance(stored.get("custom"), list) else []
    before = len(custom)
    deleted_name = ""
    kept = []
    for row in custom:
        if isinstance(row, dict) and row.get("id") == sid:
            deleted_name = str(row.get("name") or sid)
            continue
        kept.append(row)
    if len(kept) == before:
        raise ValueError("未找到要删除的自定义文本风格模板。")
    stored["custom"] = kept
    if stored.get("defaultId") == sid:
        stored["defaultId"] = DEFAULT_TEXT_STYLE_ID
    return deleted_name, load_text_styles_state(cfg)


def set_default_text_style(cfg: dict[str, Any], style_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    entry = get_text_style(cfg, style_id)
    settings = cfg.setdefault("settings", {})
    stored = settings.setdefault("text_styles", {})
    stored["defaultId"] = entry["id"]
    state = load_text_styles_state(cfg)
    updated = next(x for x in state["textStyles"] if x["id"] == entry["id"])
    return updated, state


def save_global_banned_words(cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("全站公共禁词配置格式不正确。")
    if payload.get("resetDefault") is True:
        normalized = deepcopy(DEFAULT_GLOBAL_BANNED_WORDS)
    else:
        normalized = normalize_global_banned_words(payload)
    cfg.setdefault("settings", {})["global_banned_words"] = normalized
    return normalized


def freeze_batch_narration_rules(
    cfg: dict[str, Any],
    *,
    style_id: str | None = None,
    override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve and freeze a task's immutable narrationRules snapshot at batch creation time."""
    base = get_text_style(cfg, style_id)
    if isinstance(override, dict) and override:
        merged = normalize_style_rule(override, fallback=base, strict=False)
        merged["id"] = base["id"]
        merged["num"] = base.get("num", "")
        merged["builtin"] = base.get("builtin", False)
        merged["name"] = base["name"]
        overridden = _strip_meta(merged) != _strip_meta(base)
    else:
        merged = deepcopy(base)
        overridden = False
    banned = normalize_global_banned_words((cfg.get("settings") or {}).get("global_banned_words"))
    return {
        "styleId": base["id"],
        "styleName": base["name"],
        "category": base.get("category", ""),
        "overridden": overridden,
        "rules": _strip_meta(merged),
        "globalBannedWords": banned,
    }


def extract_rules(snapshot: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract (style_rules, global_banned_words) from task['narrationRules'] with safe defaults."""
    default_rule = _strip_meta(FACTORY_BY_ID[DEFAULT_TEXT_STYLE_ID])
    if not isinstance(snapshot, dict):
        return deepcopy(default_rule), {"enabled": False, "action": "repair", "words": []}
    raw_rules = snapshot.get("rules") if isinstance(snapshot.get("rules"), dict) else snapshot
    normalized = _strip_meta(normalize_style_rule(raw_rules, fallback=FACTORY_BY_ID[DEFAULT_TEXT_STYLE_ID], strict=False))
    raw_banned = snapshot.get("globalBannedWords")
    banned = (
        normalize_global_banned_words(raw_banned)
        if isinstance(raw_banned, dict)
        else {"enabled": False, "action": "repair", "words": []}
    )
    return normalized, banned


def find_banned_hits(lines: list[str], banned: dict[str, Any] | None) -> dict[int, list[str]]:
    """Return {paragraph_index: [matched_words]} for paragraphs matching active global banned words."""
    if not isinstance(banned, dict) or not banned.get("enabled"):
        return {}
    words = [w for w in (banned.get("words") or []) if isinstance(w, str) and w.strip()]
    if not words:
        return {}
    hits: dict[int, list[str]] = {}
    for idx, line in enumerate(lines):
        if not isinstance(line, str) or not line:
            continue
        low = line.lower()
        matched = [w for w in words if w.lower() in low]
        if matched:
            hits[idx] = matched
    return hits


def build_narration_system_prompt(snapshot: dict[str, Any] | None) -> str:
    """Dynamically build the narration drafting system prompt from a frozen rules snapshot."""
    rules, banned = extract_rules(snapshot)
    opening = rules["opening"]
    body = rules["body"]
    ending = rules["ending"]
    guard = rules["guard"]

    parts: list[str] = [
        f"为长辈和家属写一条中文养老机构介绍口播，让第一次听到这家机构的人也能听懂。\n总体口吻：{rules['tone']}",
    ]

    # Opening block
    open_constraints: list[str] = [opening["prompt"]]
    open_constraints.append(f"开头第 1 句作为数字人出场句，字数严禁超过 {opening['firstSentenceMaxChars']} 字。")
    if opening["requireInstitutionInOpening"]:
        open_constraints.append("第 1 段必须自然说出完整机构名称。")
    if opening["institutionMentionOnce"]:
        open_constraints.append("完整机构名称全文只说一次，正文和结尾不要重复报院名。")
    parts.append("【开头要求】" + " ".join(open_constraints))

    # Guard & banned words block
    guard_lines: list[str] = []
    if guard["forbidParentheses"]:
        guard_lines.append(
            "口播台词严禁使用任何括号（含中英文括号）。若机构名称或原文中带有括号说明（例如「椿萱茂（重庆回兴）老年公寓」中的「（重庆回兴）」），口播直接省略括号及括号内文字（只念「椿萱茂老年公寓」），需要交代地名或补充信息时用正常语句自然说出。"
        )
    if guard["forbidReportTone"]:
        guard_lines.append("直接面向家属介绍，严禁出现“根据机构资料/原文提到/介绍显示/实拍画面可见”等后台核验口吻。")
    if guard["forbidBodyCta"]:
        guard_lines.append("正文中间段落不要提前插入“欢迎留言/评论区扣/预约参观/私信”等结尾引导，咨询动作统一留到最后一段。")
    if banned.get("enabled") and banned.get("words"):
        guard_lines.append("【全站禁词黑名单】全文严禁出现以下任何敏感词/禁用词：" + "、".join(banned["words"]) + "。")
    if guard_lines:
        parts.append("\n".join(guard_lines))

    # Body & ending block
    end_tail = (
        "若「咨询引导」标明为该数字人绑定的固定片尾钩子，最后一段必须原样使用该钩子全文（一字不改），并将其字数计入全文总字数范围内。"
        if ending["respectPersonHook"]
        else "最后一段按本条「咨询引导」自然收尾，并将其字数计入全文总字数范围内。"
    )
    parts.append(f"【正文要求】{body['prompt']}\n【结尾要求】{ending['prompt']}{end_tail}")

    parts.append(
        "机构原文是事实依据。用日常的话说清具体安排，保留正式名称、适用对象和服务条件。省去宣传评价和未经核实的效果承诺。"
    )
    parts.append(
        "全文含开头、正文和结尾，须落在用户给定的字数范围内。标点和空格不计，数字按实际读法计。"
        "超长时，删掉重复表述、额外例子和修饰语。偏短时，把已有的一个要点解释清楚。交稿前逐项检查关键信息和全文长度。"
    )
    parts.append(
        '只返回一个 JSON 对象，格式为 {"paragraphs":["开头","正文段","结尾"]}。正文可以有多段，每项只写实际要念的话。'
    )
    return "\n\n".join(parts)


def build_narration_repair_prompt(snapshot: dict[str, Any] | None) -> str:
    """Dynamically build the local repair system prompt aligned with the frozen rules snapshot."""
    rules, banned = extract_rules(snapshot)
    opening = rules["opening"]
    ending = rules["ending"]
    guard = rules["guard"]

    lines: list[str] = [
        f"局部修订一条给长辈和家属听的养老机构口播。只改指定段落，其他段落原样保留。总体口吻：{rules['tone']}",
        "「必须保留」里的要求必须落实到实际念出的台词。",
    ]
    open_notes: list[str] = [f"改开头时第 1 句字数不得超过 {opening['firstSentenceMaxChars']} 字"]
    if opening["requireInstitutionInOpening"]:
        open_notes.append("须在第 1 段自然说出机构名称（省略名称中的括号及括号内文字），即使上一稿漏了也要补回")
    if opening["institutionMentionOnce"]:
        open_notes.append("正文和结尾不重复院名")
    lines.append("；".join(open_notes) + "。")

    if guard["forbidParentheses"]:
        lines.append("全稿严禁出现任何括号或括号注释。")
    if guard["forbidReportTone"]:
        lines.append("直接介绍具体安排，严禁出现“资料显示/原文提到/实拍可见”等核验口吻。")
    if guard["forbidBodyCta"]:
        lines.append("中间正文段不要包含留言/评论/私信引导，咨询动作只留在最后一段。")
    if banned.get("enabled") and banned.get("words"):
        lines.append("全文严禁出现全站禁词黑名单中的词汇：" + "、".join(banned["words"]) + "。")

    lines.append(
        "按替换段落的字数目标改写，替换段落合计不得超出给定范围。工作台会把改后的段落替回全文计数。"
        "继续覆盖本条必讲清单，保留适用对象、服务条件和本条切入方式。"
        "超长时先压缩重复、次要例子和修饰语，不能用删除必须保留项来凑字数。偏短时解释一个有原文依据的要点。"
    )
    if ending["respectPersonHook"]:
        lines.append("结尾保留一个咨询动作；若绑定了固定片尾钩子，最后一段原样保留不得改写。")
    else:
        lines.append("结尾保留一个单一清晰的咨询动作。")
    lines.append('只返回 {"edits":[{"index":2,"text":"修订段落"}]}，段落编号从 1 起，每个指定编号返回一次。')
    return "".join(lines)
