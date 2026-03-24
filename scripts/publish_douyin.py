#!/usr/bin/env python3
"""
抖音创作者中心 CDP 自动化发布脚本

新增能力：
1. 读取 Douyin Publish Pack（--pack）
2. 支持可见性参数（--visibility private|public）
3. 支持仅校验发布包（--step validate_pack）

用法：
    python3 publish_douyin.py --pack /path/to/douyin-pack.md --step validate_pack

    python3 publish_douyin.py --full \
        --pack /path/to/douyin-pack.md

    python3 publish_douyin.py --full \
        --video /path/to/video.mp4 \
        --title "标题" \
        --description "描述 #AI" \
        --vertical-cover /path/to/v.jpg \
        --horizontal-cover /path/to/h.jpg \
        --music "热门" \
        --visibility private
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# 平台常量
# ---------------------------------------------------------------------------
CREATOR_URL = "https://creator.douyin.com/creator-micro/content/upload"
DEFAULT_MUSIC = "热门"
DEFAULT_VISIBILITY = "private"
STATE_FILE = "/tmp/douyin_publish_state.json"

PACK_FIELD_LABELS = {
    "platform": ["平台", "platform"],
    "content_id": ["内容ID", "content_id", "id"],
    "title": ["标题", "title"],
    "description": ["描述", "正文", "description", "body"],
    "video_path": ["视频路径", "video_path", "video"],
    "vertical_cover_path": ["竖封面路径", "vertical_cover_path", "vertical-cover", "vertical_cover"],
    "horizontal_cover_path": ["横封面路径", "horizontal_cover_path", "horizontal-cover", "horizontal_cover"],
    "music": ["音乐", "music"],
    "visibility": ["可见性", "visibility"],
    "schedule_at": ["定时发布", "schedule_at"],
    "notes": ["备注", "notes"],
}
MULTILINE_FIELDS = {"description", "notes"}


# ---------------------------------------------------------------------------
# 导入 CDP 客户端（与脚本同目录）
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from cdp_client import cdp_evaluate, cdp_navigate, cdp_screenshot, cdp_upload_file
    CDP_OK = True
except ImportError as e:
    CDP_OK = False
    CDP_ERROR = str(e)


# ---------------------------------------------------------------------------
# State 管理
# ---------------------------------------------------------------------------
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}



def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def cdp_value(resp, default=None):
    if not isinstance(resp, dict) or not resp.get("ok"):
        return default
    result = resp.get("result", {})
    if isinstance(result, dict):
        return result.get("value", default)
    return result



def js_string(text: str) -> str:
    return json.dumps(text or "", ensure_ascii=False)



def extract_hashtags(text: str) -> list[str]:
    """从 description 末尾提取 #标签名"""
    return re.findall(r'#([^\s#]+)', text or "")



def normalize_visibility(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip().lower()
    mapping = {
        "private": "private",
        "public": "public",
        "仅自己可见": "private",
        "自己可见": "private",
        "私密": "private",
        "仅自己": "private",
        "公开": "public",
        "公开可见": "public",
    }
    return mapping.get(raw)


# ---------------------------------------------------------------------------
# Publish Pack 解析 / 校验
# ---------------------------------------------------------------------------
def _field_alias_map() -> dict[str, str]:
    out = {}
    for canonical, labels in PACK_FIELD_LABELS.items():
        for label in labels:
            out[label.strip().lower()] = canonical
    return out



def parse_publish_pack(pack_path: str) -> dict:
    if not os.path.exists(pack_path):
        raise FileNotFoundError(f"发布包不存在: {pack_path}")

    raw = Path(pack_path).read_text(encoding="utf-8")
    stripped = raw.strip()
    if not stripped:
        raise ValueError("发布包为空")

    # JSON 优先
    if stripped.startswith("{"):
        data = json.loads(stripped)
        if not isinstance(data, dict):
            raise ValueError("JSON 发布包必须是对象")
        return data

    alias_map = _field_alias_map()
    data: dict[str, str] = {}
    current_field = None
    buffer: list[str] = []

    def flush_buffer():
        nonlocal current_field, buffer
        if current_field:
            data[current_field] = "\n".join(buffer).strip()
        current_field = None
        buffer = []

    for line in raw.splitlines():
        stripped_line = line.strip()
        m = re.match(r"^([A-Za-z_一-龥0-9\-]+)\s*[：:]\s*(.*)$", stripped_line)
        if m:
            label = m.group(1).strip().lower()
            value = m.group(2)
            canonical = alias_map.get(label)
            if canonical:
                flush_buffer()
                if canonical in MULTILINE_FIELDS:
                    current_field = canonical
                    buffer = [value] if value else []
                else:
                    data[canonical] = value.strip()
                continue
        if current_field:
            buffer.append(line)

    flush_buffer()
    return data



def apply_pack_to_args(args) -> Tuple[argparse.Namespace, Optional[dict]]:
    if not args.pack:
        if args.visibility is None:
            args.visibility = DEFAULT_VISIBILITY
        else:
            args.visibility = normalize_visibility(args.visibility)
        return args, None

    pack = parse_publish_pack(args.pack)
    mapping = {
        "title": "title",
        "description": "description",
        "video_path": "video",
        "vertical_cover_path": "vertical_cover",
        "horizontal_cover_path": "horizontal_cover",
        "music": "music",
        "visibility": "visibility",
    }
    for pack_field, arg_name in mapping.items():
        current = getattr(args, arg_name, None)
        if current in (None, "", []):
            setattr(args, arg_name, pack.get(pack_field))

    args.content_id = pack.get("content_id")
    args.platform = pack.get("platform", "douyin")
    args.notes = pack.get("notes")
    args.pack_data = pack

    if args.visibility is None:
        args.visibility = DEFAULT_VISIBILITY
    args.visibility = normalize_visibility(args.visibility)
    return args, pack



def validate_publish_inputs(args, require_files: bool = True) -> dict:
    errors: list[str] = []

    platform = getattr(args, "platform", "douyin") or "douyin"
    if str(platform).strip().lower() != "douyin":
        errors.append(f"platform 必须为 douyin，当前是: {platform}")

    required = {
        "title": args.title,
        "description": args.description,
        "video": args.video,
        "vertical_cover": args.vertical_cover,
        "horizontal_cover": args.horizontal_cover,
    }
    for name, value in required.items():
        if not value:
            errors.append(f"缺少字段: {name}")

    if args.description and not extract_hashtags(args.description):
        errors.append("description 末尾必须带 #标签")

    if args.visibility not in ("private", "public"):
        errors.append(f"visibility 非法: {args.visibility}")

    if require_files:
        for label, path in {
            "video": args.video,
            "vertical_cover": args.vertical_cover,
            "horizontal_cover": args.horizontal_cover,
        }.items():
            if path:
                if not os.path.isabs(path):
                    errors.append(f"{label} 必须是绝对路径: {path}")
                elif not os.path.exists(path):
                    errors.append(f"{label} 文件不存在: {path}")

    if errors:
        return {"ok": False, "errors": errors}

    return {
        "ok": True,
        "title": args.title,
        "hashtags": extract_hashtags(args.description or ""),
        "visibility": args.visibility,
        "content_id": getattr(args, "content_id", None),
    }


# ---------------------------------------------------------------------------
# Step: open_page
# ---------------------------------------------------------------------------
def step_open_page(state: dict) -> dict:
    print(f"  打开: {CREATOR_URL}")
    r = cdp_navigate(CREATOR_URL)
    if r.get("ok"):
        time.sleep(3)
        cdp_screenshot("page_opened")
        title = cdp_value(cdp_evaluate("document.title"), "")
        if title:
            print(f"  页面标题: {title}")
        state["page"] = CREATOR_URL
    return r


# ---------------------------------------------------------------------------
# Step: upload_video
# ---------------------------------------------------------------------------
def step_upload_video(state: dict, video_path: str) -> dict:
    if not video_path or not os.path.exists(video_path):
        return {"ok": False, "error": f"视频文件不存在: {video_path}"}

    print(f"  上传视频: {video_path}")
    r = cdp_upload_file("video", video_path)
    print(f"  上传触发: {r}")
    if r.get("ok"):
        state["video"] = video_path
    return r


# ---------------------------------------------------------------------------
# Step: fill_meta
# ---------------------------------------------------------------------------
def step_fill_meta(state: dict, title: str, description: str) -> dict:
    hashtags = extract_hashtags(description)
    print(f"  标题: {title}")
    print(f"  话题: {hashtags}")

    title_js = js_string(title)
    desc_js = js_string(description)

    script = f"""
    (() => {{
        const setValue = (el, value) => {{
            if (!el) return false;
            const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            const desc = Object.getOwnPropertyDescriptor(proto, 'value');
            if (desc && desc.set) desc.set.call(el, value);
            else el.value = value;
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
        }};

        let titleDone = false;
        let descDone = false;

        const titleCandidates = Array.from(document.querySelectorAll('input, textarea')).filter(el => {{
            const ph = (el.placeholder || '').toLowerCase();
            return ph.includes('标题') || ph.includes('title');
        }});
        if (titleCandidates.length) {{
            titleCandidates[0].focus();
            titleDone = setValue(titleCandidates[0], {title_js});
        }}

        const descCandidates = Array.from(document.querySelectorAll('textarea, input')).filter(el => {{
            const ph = (el.placeholder || '').toLowerCase();
            return ph.includes('描述') || ph.includes('简介') || ph.includes('desc');
        }});
        if (descCandidates.length) {{
            descCandidates[0].focus();
            descDone = setValue(descCandidates[0], {desc_js});
        }} else {{
            const editable = document.querySelector('[contenteditable="true"]');
            if (editable) {{
                editable.focus();
                editable.innerText = {desc_js};
                editable.dispatchEvent(new InputEvent('input', {{ bubbles: true, data: {desc_js} }}));
                descDone = true;
            }}
        }}

        return JSON.stringify({{ ok: titleDone || descDone, titleDone, descDone }});
    }})()
    """

    raw = cdp_value(cdp_evaluate(script), "")
    print(f"  元信息填写: {raw}")
    cdp_screenshot("meta_filled")
    state["title"] = title
    state["description"] = description
    return {"ok": True, "title": title, "hashtags": hashtags}


# ---------------------------------------------------------------------------
# Step: select_covers
# ---------------------------------------------------------------------------
def step_select_covers(state: dict, vertical_cover: str = None, horizontal_cover: str = None) -> dict:
    results = {}

    if vertical_cover and os.path.exists(vertical_cover):
        print(f"  上传竖版封面: {vertical_cover}")
        r = cdp_upload_file("image", vertical_cover)
        print(f"  竖版封面: {r}")
        results["vertical"] = r
    else:
        print("  竖版封面: 未指定或文件不存在，跳过")

    if horizontal_cover and os.path.exists(horizontal_cover):
        print(f"  上传横版封面: {horizontal_cover}")
        r = cdp_upload_file("image", horizontal_cover)
        print(f"  横版封面: {r}")
        results["horizontal"] = r
    else:
        print("  横版封面: 未指定或文件不存在，跳过")

    cdp_screenshot("covers_selected")
    state["vertical_cover"] = vertical_cover
    state["horizontal_cover"] = horizontal_cover
    return {"ok": True, "covers": results}


# ---------------------------------------------------------------------------
# Step: select_music
# ---------------------------------------------------------------------------
def step_select_music(state: dict, music: str = DEFAULT_MUSIC) -> dict:
    safe_music = js_string(music)

    btn_script = """
    (() => {
        const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
        for (const btn of btns) {
            const txt = (btn.innerText || '').trim();
            if (txt.includes('音乐') || txt.toLowerCase().includes('music')) {
                btn.click();
                return 'music_btn_clicked';
            }
        }
        return 'music_btn_not_found';
    })()
    """
    r1 = cdp_value(cdp_evaluate(btn_script), "")
    print(f"  音乐按钮: {r1}")
    time.sleep(2)

    if music and music != DEFAULT_MUSIC:
        search_script = f"""
        (() => {{
            const setValue = (el, value) => {{
                const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                if (desc && desc.set) desc.set.call(el, value);
                else el.value = value;
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }};
            const inputs = Array.from(document.querySelectorAll('input'));
            for (const inp of inputs) {{
                const ph = (inp.placeholder || '').toLowerCase();
                if (ph.includes('搜索') || ph.includes('音乐')) {{
                    setValue(inp, {safe_music});
                    return 'music_searched';
                }}
            }}
            return 'music_search_not_found';
        }})()
        """
        r2 = cdp_value(cdp_evaluate(search_script), "")
        print(f"  音乐搜索: {r2}")
        time.sleep(2)

    select_script = """
    (() => {
        const items = Array.from(document.querySelectorAll('[class*="music"], [data-e2e*="music"]'));
        const visible = items.filter(el => el.offsetWidth > 0 && el.offsetHeight > 0);
        if (visible.length > 0) {
            visible[0].click();
            return 'music_selected';
        }
        return 'music_item_not_found';
    })()
    """
    r3 = cdp_value(cdp_evaluate(select_script), "")
    print(f"  音乐选择: {r3}")

    cdp_screenshot("music_selected")
    state["music"] = music
    return {"ok": r3 != "music_item_not_found", "result": r3}


# ---------------------------------------------------------------------------
# Step: set_visibility
# ---------------------------------------------------------------------------
def step_set_visibility(state: dict, visibility: str = DEFAULT_VISIBILITY) -> dict:
    normalized = normalize_visibility(visibility)
    if normalized not in ("private", "public"):
        return {"ok": False, "error": f"非法 visibility: {visibility}"}

    print(f"  设置可见性: {normalized}")
    desired_private = normalized == "private"
    script = f"""
    (() => {{
        const findCheckbox = () => {{
            const candidates = Array.from(document.querySelectorAll('input[type="checkbox"], [role="checkbox"]'));
            for (const el of candidates) {{
                const text = [
                    el.innerText || '',
                    el.textContent || '',
                    el.parentElement ? el.parentElement.innerText || '' : '',
                    el.closest('label,div') ? el.closest('label,div').innerText || '' : ''
                ].join(' ');
                if (text.includes('仅自己可见')) return el;
            }}
            const textNodes = Array.from(document.querySelectorAll('*')).filter(el => (el.innerText || '').trim() === '仅自己可见');
            for (const el of textNodes) {{
                const box = (el.closest('label,div') || el.parentElement || el).querySelector('input[type="checkbox"], [role="checkbox"]');
                if (box) return box;
            }}
            return null;
        }};

        const checkbox = findCheckbox();
        if (!checkbox) return JSON.stringify({{ ok: false, error: 'private_checkbox_not_found' }});

        const isChecked = !!(
            checkbox.checked === true ||
            checkbox.getAttribute('aria-checked') === 'true' ||
            checkbox.closest('[aria-checked="true"]')
        );

        const desiredPrivate = {str(desired_private).lower()};
        const shouldClick = desiredPrivate ? !isChecked : isChecked;
        const clickTarget = checkbox.closest('label') || checkbox.parentElement || checkbox;
        if (shouldClick && clickTarget && clickTarget.click) clickTarget.click();

        const finalChecked = shouldClick ? !isChecked : isChecked;
        return JSON.stringify({{
            ok: true,
            desired: desiredPrivate ? 'private' : 'public',
            changed: shouldClick,
            checked: finalChecked
        }});
    }})()
    """
    raw = cdp_value(cdp_evaluate(script), "")
    print(f"  可见性结果: {raw}")
    cdp_screenshot(f"visibility_{normalized}")
    state["visibility"] = normalized
    return {"ok": True, "visibility": normalized, "raw": raw}


# ---------------------------------------------------------------------------
# Step: submit
# ---------------------------------------------------------------------------
def step_submit(state: dict) -> dict:
    cdp_screenshot("before_submit")

    submit_script = """
    (() => {
        const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
        for (const btn of btns) {
            const txt = (btn.innerText || '').trim();
            if (txt === '发布' || txt.includes('发布') || txt === 'Publish') {
                btn.click();
                return 'submit_clicked';
            }
        }
        const publishBtn = document.querySelector('[data-e2e*="publish"], [data-e2e*="submit"]');
        if (publishBtn) {
            publishBtn.click();
            return 'submit_clicked_datae2e';
        }
        return 'submit_btn_not_found';
    })()
    """
    raw = cdp_value(cdp_evaluate(submit_script), "submit_btn_not_found")
    print(f"  发布按钮: {raw}")

    if raw not in ("submit_btn_not_found", None):
        time.sleep(3)
        cdp_screenshot("after_submit")
        return {"ok": True, "result": raw}

    cdp_screenshot("submit_failed")
    return {"ok": False, "error": raw or "unknown"}


# ---------------------------------------------------------------------------
# Step: wait_review
# ---------------------------------------------------------------------------
def step_wait_review(state: dict, timeout_min: int = 30) -> dict:
    print(f"  开始等待审核（超时 {timeout_min} 分钟）...")
    start = time.time()
    deadline = start + timeout_min * 60
    last_status = "pending"

    while time.time() < deadline:
        elapsed_min = int((time.time() - start) / 60)
        cdp_screenshot(f"review_check_{elapsed_min}m")

        check_script = """
        (() => {
            const body = document.body.innerText || '';
            if (body.includes('发布成功') || body.includes('发布完成')) return 'passed';
            if (body.includes('审核失败') || body.includes('未通过') || body.includes('违规')) return 'rejected';
            if (body.includes('审核中') || body.includes('等待审核') || body.includes('审核通过')) return 'reviewing';
            return 'unknown';
        })()
        """
        status = cdp_value(cdp_evaluate(check_script), "unknown")

        if status != last_status:
            print(f"  [{elapsed_min}m] 审核状态: {status}")
            last_status = status

        if status == "passed":
            return {"ok": True, "status": "review_passed", "elapsed_min": elapsed_min}
        if status == "rejected":
            return {"ok": False, "status": "review_rejected", "elapsed_min": elapsed_min}

        time.sleep(60)

    return {"ok": False, "status": "review_timeout", "timeout_min": timeout_min}


# ---------------------------------------------------------------------------
# Step: validate_pack
# ---------------------------------------------------------------------------
def step_validate_pack(args) -> dict:
    result = validate_publish_inputs(args, require_files=True)
    if result.get("ok"):
        return {
            "ok": True,
            "content_id": getattr(args, "content_id", None),
            "title": args.title,
            "visibility": args.visibility,
            "hashtags": result.get("hashtags", []),
            "pack": args.pack,
        }
    return result


# ---------------------------------------------------------------------------
# 全流程
# ---------------------------------------------------------------------------
def run_full(state: dict, args) -> dict:
    validation = validate_publish_inputs(args, require_files=True)
    if not validation.get("ok"):
        return validation

    results = []

    print("\n=== 0. 校验发布包/参数 ===")
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    results.append("validated")

    print("\n=== 1. 打开上传页面 ===")
    r = step_open_page(state)
    if not r.get("ok"):
        return {"ok": False, "error": "打开页面失败", "step": 1}
    results.append("page_opened")

    print("\n=== 2. 上传视频 ===")
    r = step_upload_video(state, args.video)
    if not r.get("ok"):
        return {"ok": False, "error": "上传视频失败", "step": 2}
    print("  等待视频上传（30秒）...")
    time.sleep(30)
    results.append("video_uploaded")

    print("\n=== 3. 填写元信息 ===")
    r = step_fill_meta(state, args.title, args.description)
    if not r.get("ok"):
        return {"ok": False, "error": "填写元信息失败", "step": 3}
    results.append("meta_filled")

    print("\n=== 4. 选择封面 ===")
    r = step_select_covers(state, args.vertical_cover, args.horizontal_cover)
    if not r.get("ok"):
        return {"ok": False, "error": "选择封面失败", "step": 4}
    results.append("covers_done")

    print("\n=== 5. 选择背景音乐 ===")
    r = step_select_music(state, args.music)
    results.append("music_done")

    print("\n=== 6. 设置可见性 ===")
    r = step_set_visibility(state, args.visibility)
    if not r.get("ok"):
        return {"ok": False, "error": "设置可见性失败", "step": 6}
    results.append(f"visibility_{args.visibility}")

    print("\n=== 7. 提交发布 ===")
    r = step_submit(state)
    if not r.get("ok"):
        return {"ok": False, "error": "提交失败", "step": 7}
    results.append("submitted")

    print("\n=== 8. 等待审核 ===")
    r = step_wait_review(state, args.review_timeout)
    results.append(f"review_{r.get('status', 'unknown')}")

    return {
        "ok": True,
        "steps": results,
        "review": r,
        "video": args.video,
        "title": args.title,
        "visibility": args.visibility,
        "content_id": getattr(args, "content_id", None),
    }


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="抖音 CDP 自动化发布")
    parser.add_argument("--step", choices=[
        "validate_pack", "open_page", "upload_video", "fill_meta",
        "select_covers", "select_music", "set_visibility",
        "submit", "wait_review", "full"
    ], default="full")
    parser.add_argument("--pack", help="Douyin Publish Pack 路径（md/json）")
    parser.add_argument("--video", help="视频文件路径")
    parser.add_argument("--title", help="视频标题（≤30字）")
    parser.add_argument("--description", help="视频描述（末尾带 #标签名）")
    parser.add_argument("--vertical-cover", help="竖版封面路径（9:16）")
    parser.add_argument("--horizontal-cover", help="横版封面路径（4:3）")
    parser.add_argument("--music", default=None, help="背景音乐关键词")
    parser.add_argument("--visibility", default=None, help="可见性：private|public")
    parser.add_argument("--topics", nargs="*", help="话题标签（自动从 description 提取）")
    parser.add_argument("--review-timeout", type=int, default=30, help="审核等待超时（分钟）")

    args = parser.parse_args()
    args, _pack = apply_pack_to_args(args)

    if args.music in (None, ""):
        args.music = DEFAULT_MUSIC

    if args.step != "validate_pack" and not CDP_OK:
        print(f"错误: CDP 客户端加载失败 - {CDP_ERROR}")
        print("请确保 cdp_client.py 在同一目录下，或安装依赖: pip install websocket-client")
        sys.exit(1)

    state = load_state()

    if args.step == "validate_pack":
        result = step_validate_pack(args)
    elif args.step == "full":
        result = run_full(state, args)
    else:
        step_funcs = {
            "open_page": lambda: step_open_page(state),
            "upload_video": lambda: step_upload_video(state, args.video),
            "fill_meta": lambda: step_fill_meta(state, args.title or "", args.description or ""),
            "select_covers": lambda: step_select_covers(state, args.vertical_cover, args.horizontal_cover),
            "select_music": lambda: step_select_music(state, args.music),
            "set_visibility": lambda: step_set_visibility(state, args.visibility),
            "submit": lambda: step_submit(state),
            "wait_review": lambda: step_wait_review(state, args.review_timeout),
        }
        result = step_funcs[args.step]()

    save_state(state)
    print(f"\n{'='*50}")
    print(f"结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
