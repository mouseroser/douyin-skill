#!/usr/bin/env python3
"""
抖音 CDP 客户端 - 通过 Chrome DevTools Protocol 执行浏览器自动化
依赖: pip install websocket-client
"""

import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

CDP_PORT = 18800  # openclaw managed Chrome (profile=openclaw)
DEBUGGER_URL_CACHE = None


def find_debugger_url() -> str:
    """发现 Chrome CDP debugger URL"""
    global DEBUGGER_URL_CACHE
    if DEBUGGER_URL_CACHE:
        return DEBUGGER_URL_CACHE

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=5) as resp:
            targets = json.loads(resp.read())
        if targets:
            DEBUGGER_URL_CACHE = targets[0]["webSocketDebuggerUrl"]
            return DEBUGGER_URL_CACHE
    except Exception as e:
        raise RuntimeError(f"无法连接 CDP (port {CDP_PORT}): {e}")

    raise RuntimeError(f"CDP: 未找到 Chrome 实例 (port {CDP_PORT})")


def cdp_command(method: str, params: dict = None, timeout: int = 30) -> dict:
    """发送 CDP 命令并返回结果"""
    try:
        import websocket

        ws_url = find_debugger_url()
        ws = websocket.create_connection(ws_url, timeout=timeout)
        import uuid
        msg_id = int(time.time() * 1000) % 100000

        cmd = {"id": msg_id, "method": method}
        if params:
            cmd["params"] = params

        ws.send(json.dumps(cmd))

        while True:
            resp = json.loads(ws.recv())
            if resp.get("id") == msg_id:
                ws.close()
                if "error" in resp:
                    return {"ok": False, "error": resp["error"]}
                return {"ok": True, "result": resp.get("result", {})}

        ws.close()
    except ImportError:
        return {"ok": False, "error": "缺少 websocket-client: pip install websocket-client"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cdp_evaluate(expression: str, timeout: int = 30) -> dict:
    """在页面上下文执行 JavaScript，返回结果"""
    result = cdp_command("Runtime.evaluate",
                         {"expression": expression, "returnByValue": True},
                         timeout=timeout)
    if result.get("ok"):
        return {"ok": True, "result": result["result"].get("result", {})}
    return result


def cdp_navigate(url: str, wait_until: str = "networkidle2", timeout: int = 30) -> dict:
    """导航到 URL"""
    return cdp_command("Page.navigate", {"url": url}, timeout=timeout)


def cdp_screenshot(label: str = "screenshot") -> str:
    """截图并保存"""
    out_dir = Path("/tmp/douyin_screenshots")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{label}_{int(time.time())}.png"

    script = f"""
    (async () => {{
        const data = await page.screenshot({{
            path: '{out_path}',
            type: 'png',
            fullPage: false
        }});
        return '截图已保存: {out_path}';
    }})()
    """
    r = cdp_evaluate(script)
    print(f"  [{label}] {r}")
    return str(out_path)


def cdp_upload_file(input_accept: str, file_path: str) -> dict:
    """通过 file input 上传文件"""
    if not os.path.exists(file_path):
        return {"ok": False, "error": f"文件不存在: {file_path}"}

    abs_path = os.path.abspath(file_path)
    filename = os.path.basename(abs_path)

    script = f"""
    (async () => {{
        const allInputs = Array.from(document.querySelectorAll('input[type="file"]'));
        let targetInput = allInputs.find(inp =>
            inp.accept && inp.accept.includes('{input_accept}')
        );
        if (!targetInput) {{
            // 尝试找 video 或 image accept
            targetInput = allInputs.find(inp =>
                inp.accept && (inp.accept.includes('video') || inp.accept.includes('image'))
            );
        }}
        if (!targetInput) {{
            targetInput = allInputs[0]; // fallback 到第一个 file input
        }}
        if (!targetInput) {{
            return JSON.stringify({{ ok: false, error: '未找到 file input' }});
        }}
        const dt = new DataTransfer();
        dt.items.add(new File([], '{filename}'));
        targetInput.files = dt.files;
        targetInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
        return JSON.stringify({{ ok: true, file: '{filename}', input: targetInput.accept }});
    }})()
    """
    return cdp_evaluate(script)


def cdp_click_element(selector: str, timeout: int = 10) -> dict:
    """点击元素"""
    script = f"""
    (async () => {{
        const el = document.querySelector('{selector}');
        if (!el) return JSON.stringify({{ ok: false, error: '元素未找到: {selector}' }});
        el.click();
        return JSON.stringify({{ ok: true, selector: '{selector}' }});
    }})()
    """
    return cdp_evaluate(script)


def cdp_fill_input(selector: str, text: str, press_enter: bool = False) -> dict:
    """填写输入框"""
    safe_text = text.replace("'", "\\'").replace("\n", "\\n")
    script = f"""
    (async () => {{
        const el = document.querySelector('{selector}');
        if (!el) return JSON.stringify({{ ok: false, error: '元素未找到' }});
        await el.click();
        await el.fill('{safe_text}');
        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
        if ({'true' if press_enter else 'false'}) {{
            await el.press('Enter');
        }}
        return JSON.stringify({{ ok: true, selector: '{selector}', text: '{safe_text[:50]}' }});
    }})()
    """
    return cdp_evaluate(script)


if __name__ == "__main__":
    print("CDP 客户端测试")
    url = find_debugger_url()
    print(f"Debugger URL: {url}")

    r = cdp_evaluate("navigator.userAgent")
    print(f"User Agent: {r}")
