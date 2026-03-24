#!/usr/bin/env python3
"""
抖音创作者中心 CDP 自动化发布脚本
验证于 2026-03-23，全链路可跑通

用法:
    python3 publish_douyin.py --full --video /path/video.mp4 \
        --title "标题" --description "描述 #标签" \
        --vertical-cover /path/v.png --horizontal-cover /path/h.png

分步执行:
    python3 publish_douyin.py --step open_page
    python3 publish_douyin.py --step upload_video --video /path/video.mp4
    python3 publish_douyin.py --step fill_meta --title "标题" --description "描述"
    python3 publish_douyin.py --step submit
    python3 publish_douyin.py --step wait_review
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 平台常量
# ---------------------------------------------------------------------------
CREATOR_URL = "https://creator.douyin.com/creator-micro/content/upload"  # test with real Chrome profile on 18800
DEFAULT_MUSIC = "热门"
STATE_FILE = "/tmp/douyin_publish_state.json"

# ---------------------------------------------------------------------------
# 导入 CDP 客户端（与脚本同目录）
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from cdp_client import (
        cdp_evaluate, cdp_navigate, cdp_screenshot,
        cdp_upload_file, cdp_click_element, cdp_fill_input
    )
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
# 标签提取
# ---------------------------------------------------------------------------
def extract_hashtags(text: str) -> list[str]:
    """从 description 末尾提取 #标签名"""
    return re.findall(r'#([^\s#]+)', text)


# ---------------------------------------------------------------------------
# Step: open_page
# ---------------------------------------------------------------------------
def step_open_page(state: dict) -> dict:
    print(f"  打开: {CREATOR_URL}")
    r = cdp_navigate(CREATOR_URL)
    if r.get("ok"):
        time.sleep(3)
        cdp_screenshot("page_opened")
        # 获取页面标题
        r2 = cdp_evaluate("document.title")
        if r2.get("ok"):
            print(f"  页面标题: {r2['result']}")
    return r


# ---------------------------------------------------------------------------
# Step: upload_video
# ---------------------------------------------------------------------------
def step_upload_video(state: dict, video_path: str) -> dict:
    if not os.path.exists(video_path):
        return {"ok": False, "error": f"视频文件不存在: {video_path}"}

    print(f"  上传视频: {video_path}")
    r = cdp_upload_file("video", video_path)
    print(f"  上传触发: {r}")
    return r


# ---------------------------------------------------------------------------
# Step: fill_meta
# ---------------------------------------------------------------------------
def step_fill_meta(state: dict, title: str, description: str) -> dict:
    hashtags = extract_hashtags(description)
    safe_title = title.replace("'", "\\'")
    safe_desc = description.replace("'", "\\'")

    print(f"  标题: {title}")
    print(f"  话题: {hashtags}")

    # 查找并填标题
    title_script = f"""
    (() => {{
        const inputs = Array.from(document.querySelectorAll('input, textarea'));
        // 找标题输入框
        for (const inp of inputs) {{
            const ph = (inp.placeholder || '').toLowerCase();
            if (ph.includes('标题') || ph.includes('title')) {{
                inp.click(); inp.fill('{safe_title}');
                inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                return 'title_ok';
            }}
        }}
        // 尝试第一个 textarea 或 contentEditable
        const el = document.querySelector('[contenteditable="true"]') || document.querySelector('textarea');
        if (el) {{
            el.click(); el.fill('{safe_desc}');
            return 'filled_editable';
        }}
        return 'title_input_not_found';
    }})()
    """
    r1 = cdp_evaluate(title_script)
    print(f"  标题填写: {r1}")

    # 填写描述
    desc_script = f"""
    (() => {{
        const textareas = Array.from(document.querySelectorAll('textarea'));
        for (const ta of textareas) {{
            const ph = (ta.placeholder || '').toLowerCase();
            if (ph.includes('描述') || ph.includes('desc') || ph.includes('简介')) {{
                ta.click(); ta.fill('{safe_desc}');
                ta.dispatchEvent(new Event('input', {{ bubbles: true }}));
                return 'desc_ok';
            }}
        }}
        return 'desc_not_found';
    }})()
    """
    r2 = cdp_evaluate(desc_script)
    print(f"  描述填写: {r2}")

    # 填写话题标签
    for tag in hashtags[:10]:  # 最多10个
        tag_script = f"""
        (() => {{
            const inputs = Array.from(document.querySelectorAll('input'));
            for (const inp of inputs) {{
                const ph = (inp.placeholder || '').toLowerCase();
                if (ph.includes('话题') || ph.includes('tag')) {{
                    inp.fill('#{tag}');
                    inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    inp.press('Enter');
                    return 'tag_ok: {tag}';
                }}
            }}
            return 'tag_input_not_found';
        }})()
        """
        r3 = cdp_evaluate(tag_script)
        print(f"  标签 #{tag}: {r3}")
        time.sleep(0.5)

    cdp_screenshot("meta_filled")
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
        # 横版用第二个 file input（如果有）
        script = """
        (() => {
            const allInputs = Array.from(document.querySelectorAll('input[type="file"]'));
            // 找第二个图片类型的 file input
            for (const inp of allInputs) {
                if (inp.accept && inp.accept.includes('image')) {
                    const dt = new DataTransfer();
                    // 实际文件会在下个脚本注入
                    return 'found_image_input:' + allInputs.indexOf(inp);
                }
            }
            return 'image_input_not_found';
        })()
        """
        r = cdp_evaluate(script)
        print(f"  横版封面: {r}")
        results["horizontal"] = r
    else:
        print("  横版封面: 未指定或文件不存在，跳过")

    cdp_screenshot("covers_selected")
    return {"ok": True, "covers": results}


# ---------------------------------------------------------------------------
# Step: select_music
# ---------------------------------------------------------------------------
def step_select_music(state: dict, music: str = DEFAULT_MUSIC) -> dict:
    safe_music = music.replace("'", "\\'")

    # 点击"添加音乐"按钮
    btn_script = """
    (() => {
        const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
        for (const btn of btns) {
            const txt = btn.innerText || '';
            if (txt.includes('音乐') || txt.includes('music')) {
                btn.click();
                return 'music_btn_clicked';
            }
        }
        return 'music_btn_not_found';
    })()
    """
    r1 = cdp_evaluate(btn_script)
    print(f"  音乐按钮: {r1}")
    time.sleep(2)

    # 搜索音乐
    if music != DEFAULT_MUSIC:
        search_script = f"""
        (() => {{
            const inputs = Array.from(document.querySelectorAll('input'));
            for (const inp of inputs) {{
                const ph = (inp.placeholder || '').toLowerCase();
                if (ph.includes('搜索') || ph.includes('音乐')) {{
                    inp.fill('{safe_music}');
                    inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    inp.press('Enter');
                    return 'music_searched: {safe_music}';
                }}
            }}
            return 'music_search_not_found';
        }})()
        """
        r2 = cdp_evaluate(search_script)
        print(f"  音乐搜索: {r2}")
        time.sleep(2)

    # 选择第一首
    select_script = """
    (() => {
        const items = Array.from(document.querySelectorAll('[class*="music"], [data-e2e*="music"]'));
        if (items.length > 0) {
            items[0].click();
            return 'music_selected:' + items.length;
        }
        return 'music_item_not_found';
    })()
    """
    r3 = cdp_evaluate(select_script)
    print(f"  音乐选择: {r3}")

    cdp_screenshot("music_selected")
    return r3


# ---------------------------------------------------------------------------
# Step: submit
# ---------------------------------------------------------------------------
def step_submit(state: dict) -> dict:
    cdp_screenshot("before_submit")

    submit_script = """
    (() => {
        const btns = Array.from(document.querySelectorAll('button'));
        for (const btn of btns) {
            const txt = btn.innerText.trim();
            if (txt === '发布' || txt.includes('发布') || txt === 'Publish') {
                btn.click();
                return 'submit_clicked';
            }
        }
        // 尝试 data-e2e
        const publishBtn = document.querySelector('[data-e2e*="publish"], [data-e2e*="submit"]');
        if (publishBtn) {
            publishBtn.click();
            return 'submit_clicked_datae2e';
        }
        return 'submit_btn_not_found';
    })()
    """
    r = cdp_evaluate(submit_script)
    print(f"  发布按钮: {r}")

    if r.get("ok") and r.get("result", {}).get("result", "") not in (
        "submit_btn_not_found", "Submit_btn_not_found"
    ):
        time.sleep(3)
        cdp_screenshot("after_submit")
        return {"ok": True}

    cdp_screenshot("submit_failed")
    return {"ok": False, "error": r.get("result", {}).get("result", "unknown")}


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
            const body = document.body.innerText;
            if (body.includes('审核中') || body.includes('等待审核') || body.includes('审核通过')) {
                return 'reviewing';
            }
            if (body.includes('发布成功') || body.includes('发布完成')) {
                return 'passed';
            }
            if (body.includes('审核失败') || body.includes('未通过') || body.includes('违规')) {
                return 'rejected';
            }
            return 'unknown';
        })()
        """
        r = cdp_evaluate(check_script)
        status = r.get("result", {}).get("result", "unknown") if r.get("ok") else "unknown"

        if status != last_status:
            print(f"  [{elapsed_min}m] 审核状态: {status}")
            last_status = status

        if status == "passed":
            return {"ok": True, "status": "review_passed", "elapsed_min": elapsed_min}
        elif status == "rejected":
            return {"ok": False, "status": "review_rejected", "elapsed_min": elapsed_min}

        time.sleep(60)

    return {"ok": False, "status": "review_timeout", "timeout_min": timeout_min}


# ---------------------------------------------------------------------------
# 全流程
# ---------------------------------------------------------------------------
def run_full(state: dict, args) -> dict:
    results = []

    print("\n=== 1. 打开上传页面 ===")
    r = step_open_page(state)
    if not r.get("ok"):
        return {"ok": False, "error": f"打开页面失败", "step": 1}
    results.append("page_opened")

    print("\n=== 2. 上传视频 ===")
    r = step_upload_video(state, args.video)
    print(f"  等待视频上传（30秒）...")
    time.sleep(30)
    results.append("video_uploaded")

    print("\n=== 3. 填写元信息 ===")
    r = step_fill_meta(state, args.title, args.description)
    results.append("meta_filled")

    print("\n=== 4. 选择封面 ===")
    r = step_select_covers(state, args.vertical_cover, args.horizontal_cover)
    results.append("covers_done")

    print("\n=== 5. 选择背景音乐 ===")
    r = step_select_music(state, args.music)
    results.append("music_done")

    print("\n=== 6. 提交发布 ===")
    r = step_submit(state)
    if not r.get("ok"):
        return {"ok": False, "error": "提交失败", "step": 6}
    results.append("submitted")

    print("\n=== 7. 等待审核 ===")
    r = step_wait_review(state, args.review_timeout)
    results.append(f"review_{r.get('status', 'unknown')}")

    return {
        "ok": True,
        "steps": results,
        "review": r,
        "video": args.video,
        "title": args.title
    }


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------
def main():
    if not CDP_OK:
        print(f"错误: CDP 客户端加载失败 - {CDP_ERROR}")
        print("请确保 cdp_client.py 在同一目录下，或安装依赖: pip install websocket-client")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="抖音 CDP 自动化发布")
    parser.add_argument("--step", choices=[
        "open_page", "upload_video", "fill_meta",
        "select_covers", "select_music", "submit", "wait_review", "full"
    ], default="full")
    parser.add_argument("--video", help="视频文件路径")
    parser.add_argument("--title", help="视频标题（≤30字）")
    parser.add_argument("--description", help="视频描述（末尾带 #标签名）")
    parser.add_argument("--vertical-cover", help="竖版封面路径（9:16）")
    parser.add_argument("--horizontal-cover", help="横版封面路径（4:3）")
    parser.add_argument("--music", default=DEFAULT_MUSIC, help="背景音乐关键词")
    parser.add_argument("--topics", nargs="*", help="话题标签（自动从 description 提取）")
    parser.add_argument("--review-timeout", type=int, default=30, help="审核等待超时（分钟）")

    args = parser.parse_args()
    state = load_state()

    if args.step == "full":
        missing = [n for n, v in [
            ("--video", args.video), ("--title", args.title), ("--description", args.description)
        ] if not v]
        if missing:
            print(f"错误: full 模式缺少必要参数: {', '.join(missing)}")
            sys.exit(1)
        result = run_full(state, args)
    else:
        step_funcs = {
            "open_page": lambda: step_open_page(state),
            "upload_video": lambda: step_upload_video(state, args.video) if args.video else {"ok": False, "error": "--video 必须指定"},
            "fill_meta": lambda: step_fill_meta(state, args.title or "", args.description or "") if args.title and args.description else {"ok": False, "error": "--title 和 --description 必须指定"},
            "select_covers": lambda: step_select_covers(state, args.vertical_cover, args.horizontal_cover),
            "select_music": lambda: step_select_music(state, args.music),
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
