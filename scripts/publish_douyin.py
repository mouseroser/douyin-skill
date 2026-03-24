#!/usr/bin/env python3
"""
抖音创作者中心 CDP 自动化发布脚本

新增能力：
1. 读取 Douyin Publish Pack（--pack）
2. 支持可见性参数（--visibility private|public）
3. 支持仅校验发布包（--step validate_pack）
4. 支持发布前同标题去重检查（--step check_duplicate）
5. 支持发布后作品管理页核验（--step verify_publish）

用法：
    python3 publish_douyin.py --pack /path/to/douyin-pack.md --step validate_pack

    python3 publish_douyin.py --pack /path/to/douyin-pack.md --step full

    python3 publish_douyin.py --video /path/to/video.mp4 \
        --title "标题" \
        --description "描述 #AI" \
        --vertical-cover /path/to/v.jpg \
        --horizontal-cover /path/to/h.jpg \
        --music "热门" \
        --visibility private \
        --step full
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# 平台常量
# ---------------------------------------------------------------------------
CREATOR_URL = "https://creator.douyin.com/creator-micro/content/upload"
MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage?enter_from=publish"
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
    import websocket
    import urllib.request
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
BROWSER_TIMEOUT_MS = 60000
DOUYIN_TARGET_HINT = "creator.douyin.com"


def cdp_value(resp, default=None):
    if not isinstance(resp, dict) or not resp.get("ok"):
        return default
    result = resp.get("result", {})
    if isinstance(result, dict):
        return result.get("value", default)
    return result



def js_string(text: str) -> str:
    return json.dumps(text or "", ensure_ascii=False)



def run_browser_cmd(*args, timeout: int = 90, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["openclaw", "browser", "--timeout", str(BROWSER_TIMEOUT_MS), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "browser command failed").strip())
    return proc



def get_douyin_target_id() -> Optional[str]:
    proc = run_browser_cmd("tabs", check=False, timeout=30)
    text = (proc.stdout or "") + (proc.stderr or "")
    blocks = [b.strip() for b in re.split(r"\n(?=\d+\.)", text) if b.strip()]
    for block in blocks:
        if DOUYIN_TARGET_HINT in block:
            m = re.search(r"id:\s*([A-F0-9]+)", block)
            if m:
                return m.group(1)
    return None



def browser_snapshot_text(limit: int = 400) -> str:
    target_id = get_douyin_target_id()
    args = ["snapshot", "--limit", str(limit)]
    if target_id:
        args += ["--target-id", target_id]
    proc = run_browser_cmd(*args, check=False, timeout=45)
    return ((proc.stdout or "") + (proc.stderr or "")).strip()



def browser_find_ref(patterns) -> Optional[str]:
    text = browser_snapshot_text(limit=800)
    for line in text.splitlines():
        for pattern in patterns:
            if pattern in line:
                m = re.search(r"\[ref=(e\d+)\]", line)
                if m:
                    return m.group(1)
    return None



def browser_click_ref(ref: str) -> str:
    target_id = get_douyin_target_id()
    args = ["click", ref]
    if target_id:
        args += ["--target-id", target_id]
    proc = run_browser_cmd(*args, timeout=45)
    return (proc.stdout or proc.stderr or "").strip()



def browser_type_ref(ref: str, text: str, submit: bool = False) -> str:
    target_id = get_douyin_target_id()
    args = ["type", ref, text]
    if submit:
        args.append("--submit")
    if target_id:
        args += ["--target-id", target_id]
    proc = run_browser_cmd(*args, timeout=45)
    return (proc.stdout or proc.stderr or "").strip()



def prepare_upload_path(path: str) -> str:
    src = Path(path).expanduser().resolve()
    upload_dir = Path('/tmp/openclaw/uploads')
    upload_dir.mkdir(parents=True, exist_ok=True)
    dst = upload_dir / src.name
    if src != dst:
        shutil.copy2(src, dst)
    return str(dst)



def browser_upload_and_click(path: str, ref: str) -> str:
    target_id = get_douyin_target_id()
    upload_path = prepare_upload_path(path)
    args = ["upload", upload_path, "--ref", ref, "--timeout-ms", "120000"]
    if target_id:
        args += ["--target-id", target_id]
    proc = run_browser_cmd(*args, timeout=150)
    return (proc.stdout or proc.stderr or "").strip()



def browser_wait_text(text: str, gone: bool = False, timeout_ms: int = 30000) -> str:
    target_id = get_douyin_target_id()
    args = ["wait", "--timeout-ms", str(timeout_ms)]
    args += ["--text-gone" if gone else "--text", text]
    if target_id:
        args += ["--target-id", target_id]
    proc = run_browser_cmd(*args, timeout=max(30, int(timeout_ms / 1000) + 10))
    return (proc.stdout or proc.stderr or "").strip()



def browser_wait_url(pattern: str, timeout_ms: int = 30000) -> str:
    target_id = get_douyin_target_id()
    args = ["wait", "--url", pattern, "--timeout-ms", str(timeout_ms)]
    if target_id:
        args += ["--target-id", target_id]
    proc = run_browser_cmd(*args, timeout=max(30, int(timeout_ms / 1000) + 10))
    return (proc.stdout or proc.stderr or "").strip()



def browser_press(key: str) -> str:
    target_id = get_douyin_target_id()
    args = ["press", key]
    if target_id:
        args += ["--target-id", target_id]
    proc = run_browser_cmd(*args, timeout=30)
    return (proc.stdout or proc.stderr or "").strip()



def get_target_ws_url() -> str:
    target_id = get_douyin_target_id()
    with urllib.request.urlopen('http://127.0.0.1:18800/json', timeout=5) as resp:
        targets = json.loads(resp.read())
    if target_id:
        for t in targets:
            if t.get('id') == target_id:
                return t['webSocketDebuggerUrl']
    for t in targets:
        if DOUYIN_TARGET_HINT in (t.get('url') or ''):
            return t['webSocketDebuggerUrl']
    if targets:
        return targets[0]['webSocketDebuggerUrl']
    raise RuntimeError('未找到 CDP target')



def cdp_raw_call(ws, method: str, params: dict = None, msg_id: int = 1) -> dict:
    payload = {'id': msg_id, 'method': method}
    if params:
        payload['params'] = params
    ws.send(json.dumps(payload))
    while True:
        data = json.loads(ws.recv())
        if data.get('id') == msg_id:
            return data



def cdp_set_file_input_files(file_path: str, picker_mode: str = 'vertical') -> dict:
    upload_path = prepare_upload_path(file_path)
    ws = websocket.create_connection(get_target_ws_url(), timeout=20)
    try:
        cdp_raw_call(ws, 'Runtime.enable', msg_id=1)
        cdp_raw_call(ws, 'DOM.enable', msg_id=2)

        if not cdp_value(cdp_evaluate("document.body.innerText.includes('上传封面')"), False):
            open_expr = r'''(() => {
              const labels = Array.from(document.querySelectorAll('*')).filter(el => (el.innerText || '').trim() === '选择封面');
              if (!labels.length) return 'no_select_cover';
              const el = labels[0];
              const candidates = [el, el.parentElement, el.parentElement && el.parentElement.parentElement, el.closest('div')];
              for (const c of candidates) {
                if (c && c.click) { c.click(); return 'clicked_select_cover'; }
              }
              return 'select_cover_not_clickable';
            })()'''
            cdp_evaluate(open_expr, timeout=10)
            time.sleep(2)

        if picker_mode == 'horizontal':
            switch_expr = r'''(() => {
              const nodes = Array.from(document.querySelectorAll('*')).filter(el => (el.innerText || '').trim() === '设置横封面');
              if (!nodes.length) return 'no_switch_horizontal';
              const el = nodes[0];
              const candidates = [el, el.parentElement, el.parentElement && el.parentElement.parentElement, el.closest('div')];
              for (const c of candidates) {
                if (c && c.click) { c.click(); return 'clicked_switch_horizontal'; }
              }
              return 'switch_not_clickable';
            })()'''
            cdp_evaluate(switch_expr, timeout=10)
            time.sleep(2)

        select_expr = r'''(() => {
          const inputs = Array.from(document.querySelectorAll('input[type=file]')).filter(inp => {
            const accept = inp.accept || '';
            const parent = inp.parentElement;
            const grand = parent && parent.parentElement;
            const text = ((grand && grand.innerText) || (parent && parent.innerText) || '');
            return accept.includes('image') && text.includes('点击上传文件或拖拽文件到这里');
          });
          return inputs[0] || null;
        })()'''
        res = cdp_raw_call(ws, 'Runtime.evaluate', {
            'expression': select_expr,
            'returnByValue': False,
            'awaitPromise': True,
        }, msg_id=3)
        obj = res.get('result', {}).get('result', {})
        object_id = obj.get('objectId')
        if not object_id:
            return {'ok': False, 'error': '未找到封面 file input objectId', 'raw': res}

        set_res = cdp_raw_call(ws, 'DOM.setFileInputFiles', {
            'files': [upload_path],
            'objectId': object_id,
        }, msg_id=4)
        if 'error' in set_res:
            return {'ok': False, 'error': set_res['error']}

        time.sleep(2)
        return {'ok': True, 'path': upload_path, 'mode': picker_mode}
    finally:
        try:
            ws.close()
        except Exception:
            pass



def cdp_complete_cover_dialog() -> dict:
    expr = r'''(() => {
      const nodes = Array.from(document.querySelectorAll('button,*')).filter(el => (el.innerText || '').trim() === '完成');
      if (!nodes.length) return 'no_complete';
      const el = nodes[0];
      const candidates = [el, el.parentElement, el.closest('div')];
      for (const c of candidates) {
        if (c && c.click) { c.click(); return 'clicked_complete'; }
      }
      return 'complete_not_clickable';
    })()'''
    raw = cdp_value(cdp_evaluate(expr, timeout=10), '')
    time.sleep(2)
    return {'ok': raw in ('clicked_complete',), 'raw': raw}



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
    if not r.get("ok"):
        return r

    time.sleep(4)
    title = cdp_value(cdp_evaluate("document.title"), "")
    if title:
        print(f"  页面标题: {title}")

    body = cdp_value(cdp_evaluate("document.body.innerText.slice(0,1500)"), "") or ""
    if "继续编辑" in body:
        try:
            ref = browser_find_ref(["继续编辑"])
            if ref:
                print(f"  检测到草稿，点击继续编辑: {ref}")
                print(f"  {browser_click_ref(ref)}")
            else:
                print("  snapshot 未拿到继续编辑 ref，改用 CDP 点击")
                click_result = cdp_value(cdp_evaluate(r'''(() => {
                    const els = Array.from(document.querySelectorAll('button, [role="button"], div, span'));
                    for (const el of els) {
                        const txt = (el.innerText || '').trim();
                        if (txt === '继续编辑') { el.click(); return 'clicked_continue_edit'; }
                    }
                    return 'continue_edit_not_found';
                })()'''), "")
                print(f"  {click_result}")
            time.sleep(5)
            try:
                browser_wait_url("**/content/post/video**", timeout_ms=30000)
            except Exception:
                pass
        except Exception as e:
            print(f"  继续编辑失败: {e}")

    cdp_screenshot("page_opened")
    state["page"] = cdp_value(cdp_evaluate("location.href"), CREATOR_URL)
    return {"ok": True, "url": state["page"]}


# ---------------------------------------------------------------------------
# Step: upload_video
# ---------------------------------------------------------------------------
def step_upload_video(state: dict, video_path: str) -> dict:
    if not video_path or not os.path.exists(video_path):
        return {"ok": False, "error": f"视频文件不存在: {video_path}"}

    print(f"  上传视频: {video_path}")
    current_url = cdp_value(cdp_evaluate("location.href"), "") or ""
    body = cdp_value(cdp_evaluate("document.body.innerText.slice(0,1500)"), "") or ""
    if (
        "/content/post/video" in current_url
        or "设置封面" in body
        or "重新上传" in body
        or "预览视频" in body
    ):
        print("  当前草稿页已有视频，跳过重新上传")
        state["video"] = video_path
        return {"ok": True, "skipped": True, "reason": "draft_already_loaded", "url": current_url}

    ref = browser_find_ref(["点击上传 或直接将视频文件拖入此区域", "上传视频"])
    if not ref:
        return {"ok": False, "error": "未找到视频上传入口 ref"}

    try:
        output = browser_upload_and_click(video_path, ref)
        print(f"  上传结果: {output}")
        browser_wait_url("**/content/post/video**", timeout_ms=90000)
        time.sleep(5)
        state["video"] = video_path
        return {"ok": True, "result": output}
    except Exception as e:
        return {"ok": False, "error": f"视频上传失败: {e}"}


# ---------------------------------------------------------------------------
# Step: fill_meta
# ---------------------------------------------------------------------------
def step_fill_meta(state: dict, title: str, description: str) -> dict:
    hashtags = extract_hashtags(description)
    print(f"  标题: {title}")
    print(f"  话题: {hashtags}")

    title_ref = browser_find_ref(["填写作品标题，为作品获得更多流量", 'textbox "填写作品标题'])
    if title_ref:
        try:
            print(f"  标题输入框: {title_ref}")
            print(f"  {browser_type_ref(title_ref, title)}")
        except Exception as e:
            print(f"  browser type 标题失败，回退 CDP: {e}")
    else:
        print("  未找到标题 ref，回退 CDP 填写")

    title_js = js_string(title)
    desc_js = js_string(description)
    script = f"""
    (() => {{
        const setValue = (el, value) => {{
            if (!el) return false;
            if ('value' in el) {{
                const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                if (desc && desc.set) desc.set.call(el, value);
                else el.value = value;
            }} else {{
                el.innerText = value;
            }}
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
            const editables = Array.from(document.querySelectorAll('[contenteditable="true"]'));
            const target = editables.find(el => !((el.innerText || '').includes('预览视频')) ) || editables[0];
            if (target) {{
                target.focus();
                target.innerText = {desc_js};
                target.dispatchEvent(new InputEvent('input', {{ bubbles: true, data: {desc_js} }}));
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
    return {"ok": True, "title": title, "hashtags": hashtags, "raw": raw}


# ---------------------------------------------------------------------------
# Step: select_covers
# ---------------------------------------------------------------------------
def step_select_covers(state: dict, vertical_cover: str = None, horizontal_cover: str = None) -> dict:
    results = {}

    body = cdp_value(cdp_evaluate("document.body.innerText.slice(0,2600)"), "") or ""
    if "横/竖双封面缺失" not in body and "设置封面" not in body:
        print("  页面未显示封面缺失提示，跳过封面步骤")
        return {"ok": True, "skipped": True, "reason": "no_cover_prompt"}

    if vertical_cover and os.path.exists(vertical_cover):
        print(f"  通过 CDP objectId 直写竖版封面: {vertical_cover}")
        results['vertical'] = cdp_set_file_input_files(vertical_cover, 'vertical')
        print(f"  竖版封面结果: {results['vertical']}")
        time.sleep(2)
    else:
        results['vertical'] = {"ok": False, "error": "竖版封面不存在"}

    if horizontal_cover and os.path.exists(horizontal_cover):
        print(f"  通过 CDP objectId 直写横版封面: {horizontal_cover}")
        results['horizontal'] = cdp_set_file_input_files(horizontal_cover, 'horizontal')
        print(f"  横版封面结果: {results['horizontal']}")
        time.sleep(2)
    else:
        results['horizontal'] = {"ok": False, "error": "横版封面不存在"}

    complete = cdp_complete_cover_dialog()
    print(f"  完成封面设置: {complete}")
    results['complete'] = complete
    time.sleep(2)

    body_after = cdp_value(cdp_evaluate("document.body.innerText.slice(0,2800)"), "") or ""
    missing = "横/竖双封面缺失" in body_after
    cdp_screenshot("covers_selected")
    state["vertical_cover"] = vertical_cover
    state["horizontal_cover"] = horizontal_cover
    return {"ok": not missing, "covers": results, "missing_after": missing}


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

    if r1 == 'music_btn_not_found':
        print("  页面未提供可操作的音乐入口，按可选步骤跳过")
        state["music"] = music
        return {"ok": True, "skipped": True, "reason": "music_optional_not_found"}

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
    return {"ok": True, "result": r3, "selected": r3 == 'music_selected'}


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

    submit_ref = browser_find_ref(['button "发布"'])
    if submit_ref:
        try:
            print(f"  点击发布 ref: {submit_ref}")
            output = browser_click_ref(submit_ref)
            print(f"  发布按钮: {output}")
            time.sleep(3)
            cdp_screenshot("after_submit")
            return {"ok": True, "result": output, "ref": submit_ref}
        except Exception as e:
            print(f"  browser click 发布失败，回退 CDP: {e}")

    submit_script = """
    (() => {
        const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
        for (const btn of btns) {
            const txt = (btn.innerText || '').trim();
            if (txt === '发布' || txt === 'Publish') {
                btn.click();
                return 'submit_clicked';
            }
        }
        const publishBtn = Array.from(document.querySelectorAll('[data-e2e*="publish"], [data-e2e*="submit"]')).find(el => {
            const txt = (el.innerText || '').trim();
            return txt === '' || txt === '发布' || txt === 'Publish';
        });
        if (publishBtn) {
            publishBtn.click();
            return 'submit_clicked_datae2e';
        }
        return 'submit_btn_not_found';
    })()
    """
    raw = cdp_value(cdp_evaluate(submit_script), "submit_btn_not_found")
    print(f"  发布按钮(CDP): {raw}")

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
            if (body.includes('审核中') || body.includes('等待审核') || body.includes('审核通过')) return 'reviewing';
            if (body.includes('发布成功') || body.includes('发布完成')) return 'passed';
            if (body.includes('审核失败') || body.includes('违规')) return 'rejected';
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

    return {"ok": True, "status": "review_pending", "timeout_min": timeout_min}


# ---------------------------------------------------------------------------
# Step: check_duplicate
# ---------------------------------------------------------------------------
def step_check_duplicate(state: dict, title: str) -> dict:
    print("  检查作品管理页是否已有同标题作品...")
    nav = cdp_navigate(MANAGE_URL, timeout=20)
    if not nav.get("ok"):
        return {"ok": False, "error": "跳转作品管理页失败", "raw": nav}

    time.sleep(5)
    body = cdp_value(cdp_evaluate("document.body.innerText.slice(0,6000)"), "") or ""
    count = body.count(title) if title else 0
    duplicate = count > 0
    state["duplicate_check_url"] = cdp_value(cdp_evaluate("location.href"), MANAGE_URL)
    state["duplicate_check_count"] = count
    return {
        "ok": True,
        "title": title,
        "duplicate": duplicate,
        "count": count,
        "url": state["duplicate_check_url"],
    }


# ---------------------------------------------------------------------------
# Step: verify_publish
# ---------------------------------------------------------------------------
def step_verify_publish(state: dict, title: str, visibility: str = DEFAULT_VISIBILITY) -> dict:
    print("  跳转作品管理页核验发布结果...")
    nav = cdp_navigate(MANAGE_URL, timeout=20)
    if not nav.get("ok"):
        return {"ok": False, "error": "跳转作品管理页失败", "raw": nav}

    time.sleep(5)
    snapshot = browser_snapshot_text(limit=500)
    body = cdp_value(cdp_evaluate("document.body.innerText.slice(0,4000)"), "") or ""

    found_in_snapshot = title in snapshot
    found_in_body = title in body
    private_ok = (visibility != "private") or ("私密" in body)
    reviewing = "审核中" in body
    published = "已发布" in body and found_in_body

    state["verify_manage_url"] = cdp_value(cdp_evaluate("location.href"), MANAGE_URL)
    state["verify_found_title"] = found_in_snapshot or found_in_body

    return {
        "ok": found_in_snapshot or found_in_body,
        "title_found": found_in_snapshot or found_in_body,
        "private_marker": private_ok,
        "reviewing": reviewing,
        "published_marker": published,
        "url": state["verify_manage_url"],
    }


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

    print("\n=== 0.5 同标题去重检查 ===")
    dup = step_check_duplicate(state, args.title)
    print(json.dumps(dup, ensure_ascii=False, indent=2))
    if not dup.get("ok"):
        return {"ok": False, "error": "去重检查失败", "step": 0.5, "duplicate": dup}
    if dup.get("duplicate"):
        return {
            "ok": False,
            "error": f"检测到同标题作品已存在: {args.title}",
            "step": 0.5,
            "duplicate": dup,
            "title": args.title,
            "content_id": getattr(args, "content_id", None),
        }
    results.append("duplicate_check_passed")

    print("\n=== 1. 打开上传页面 ===")
    r = step_open_page(state)
    if not r.get("ok"):
        return {"ok": False, "error": "打开页面失败", "step": 1}
    results.append("page_opened")

    print("\n=== 2. 上传视频 ===")
    r = step_upload_video(state, args.video)
    if not r.get("ok"):
        return {"ok": False, "error": "上传视频失败", "step": 2}
    if r.get("skipped"):
        print(f"  跳过视频上传: {r.get('reason')}")
        results.append("video_reused_from_draft")
    else:
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

    print("\n=== 9. 作品管理页核验 ===")
    verify = step_verify_publish(state, args.title, args.visibility)
    if verify.get("ok"):
        results.append("verify_manage_ok")
    else:
        results.append("verify_manage_failed")

    structured = {
        "content_id": getattr(args, "content_id", None),
        "title": args.title,
        "visibility": args.visibility,
        "manage_url": verify.get("url"),
        "title_found": verify.get("title_found"),
        "private_marker": verify.get("private_marker"),
        "review_state": r.get("status"),
        "reviewing": verify.get("reviewing"),
        "published_marker": verify.get("published_marker"),
    }

    return {
        "ok": bool(r.get("ok") and verify.get("ok")),
        "steps": results,
        "review": r,
        "verify": verify,
        "result": structured,
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
        "validate_pack", "check_duplicate", "open_page", "upload_video", "fill_meta",
        "select_covers", "select_music", "set_visibility",
        "submit", "wait_review", "verify_publish", "full"
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
            "check_duplicate": lambda: step_check_duplicate(state, args.title or ""),
            "open_page": lambda: step_open_page(state),
            "upload_video": lambda: step_upload_video(state, args.video),
            "fill_meta": lambda: step_fill_meta(state, args.title or "", args.description or ""),
            "select_covers": lambda: step_select_covers(state, args.vertical_cover, args.horizontal_cover),
            "select_music": lambda: step_select_music(state, args.music),
            "set_visibility": lambda: step_set_visibility(state, args.visibility),
            "submit": lambda: step_submit(state),
            "wait_review": lambda: step_wait_review(state, args.review_timeout),
            "verify_publish": lambda: step_verify_publish(state, args.title or "", args.visibility),
        }
        result = step_funcs[args.step]()

    save_state(state)
    print(f"\n{'='*50}")
    print(f"结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
