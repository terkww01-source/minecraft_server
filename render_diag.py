# render_diag.py
import os, json, time, re, traceback
from flask import Flask, request, jsonify
import requests

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

APP = Flask(__name__)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36"

SERVER_URL = (os.getenv("MAGMANODE_SERVER_URL", "") or "").strip()
COOKIES_RAW = os.getenv("MAGMANODE_COOKIES_JSON", "[]")
try:
    COOKIES = json.loads(COOKIES_RAW)
except Exception:
    COOKIES = []

def make_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1366,768")
    opts.add_argument(f"--user-agent={UA}")
    # لاگ شبکه برای دیدن درخواست‌های بعد از کلیک
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL", "browser": "ALL"})
    service = Service("/usr/bin/chromedriver")
    drv = webdriver.Chrome(service=service, options=opts)
    drv.set_page_load_timeout(45)
    return drv

def inject_cookies(drv):
    if not COOKIES:
        return 0
    drv.get("https://magmanode.com/")
    time.sleep(1)
    added = 0
    for c in COOKIES:
        try:
            cookie = {
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain", "magmanode.com").lstrip("."),
                "path": c.get("path", "/"),
                "secure": bool(c.get("secure", False)),
                "httpOnly": bool(c.get("httpOnly", False)),
            }
            drv.add_cookie(cookie)
            added += 1
        except Exception:
            pass
    return added

def find_button(drv, action):
    sels = [
        (By.CSS_SELECTOR, f'button[data-action="{action}"]'),
        (By.XPATH, f'//button[@data-action="{action}"]'),
        (By.XPATH, '//button[normalize-space()="START"]' if action == "start" else '//button[normalize-space()="STOP"]'),
        (By.XPATH, '//button[contains(., "START")]' if action == "start" else '//button[contains(., "STOP")]'),
    ]
    for by, sel in sels:
        try:
            el = WebDriverWait(drv, 10).until(EC.element_to_be_clickable((by, sel)))
            return el, {"by": str(by), "selector": sel}
        except Exception:
            continue
    return None, None

def click_hard(drv, el):
    out = {"clicked_with": []}
    try:
        el.click()
        out["clicked_with"].append("native")
    except Exception as e:
        out["click_native_error"] = repr(e)
    try:
        drv.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        drv.execute_script("arguments[0].click();", el)
        out["clicked_with"].append("js-click")
    except Exception as e:
        out["click_js_error"] = repr(e)
    try:
        drv.execute_script("""
            const ev = new MouseEvent('click', {bubbles:true, cancelable:true, view:window});
            arguments[0].dispatchEvent(ev);
        """, el)
        out["clicked_with"].append("dispatch")
    except Exception as e:
        out["click_dispatch_error"] = repr(e)
    return out

def collect_network(drv):
    events = []
    try:
        logs = drv.get_log("performance")
    except Exception:
        logs = []
    for entry in logs:
        try:
            msg = json.loads(entry["message"])["message"]
            if msg.get("method") == "Network.responseReceived":
                resp = msg["params"]["response"]
                url = resp.get("url", "")
                status = resp.get("status")
                # درخواست‌های واقعی بعد از کلیک (غیر از لود خود صفحه)
                if "magmanode.com" in url and not url.endswith("/server"):
                    events.append({"status": status, "url": url})
        except Exception:
            continue
    # یکتا
    uniq = {}
    for e in events:
        uniq[e["url"]] = e
    return list(uniq.values())[:12]

def page_status_text(drv):
    try:
        body_txt = drv.find_element(By.TAG_NAME, "body").text
        chunk = body_txt[:1200]
        st = "unknown"
        for word in ["online", "starting", "offline", "running", "stopped"]:
            if re.search(rf"\\b{word}\\b", chunk, re.I):
                st = word
                break
        return st, chunk
    except Exception:
        return "unknown", ""

def do_action(action):
    data = {
        "ok": False,
        "action": action,
        "server_url": SERVER_URL,
        "cookies_count": len(COOKIES),
    }
    if not SERVER_URL:
        data["error"] = "MAGMANODE_SERVER_URL not set"
        return data

    drv = None
    try:
        drv = make_driver()
        added = inject_cookies(drv)
        drv.get(SERVER_URL)
        time.sleep(2)

        has_start = len(drv.find_elements(By.CSS_SELECTOR, 'button[data-action="start"]')) > 0
        has_stop  = len(drv.find_elements(By.CSS_SELECTOR, 'button[data-action="stop"]')) > 0
        data.update({"has_start": has_start, "has_stop": has_stop})

        before_status, _ = page_status_text(drv)
        data["status_before"] = before_status

        if action in ("start", "stop"):
            el, sel = find_button(drv, action)
            data["selector"] = sel
            if not el:
                data["error"] = "button_not_found"
            else:
                data["click"] = click_hard(drv, el)
                time.sleep(5)  # صبر کن تا درخواست‌ها برن

        after_status, after_chunk = page_status_text(drv)
        data["status_after"] = after_status
        data["text_after_sample"] = after_chunk
        data["network"] = collect_network(drv)

        data["ok"] = True
        return data
    except Exception as e:
        data["error"] = repr(e)
        data["trace"] = traceback.format_exc()
        return data
    finally:
        if drv:
            drv.quit()

@APP.route("/")
def home():
    return (
        "<h1>Render Diagnostic</h1>"
        "<p>رفتن به گزارش: /diag</p>"
        "<p>JSON: /diag?format=json</p>"
        "<p>کلیک و گزارش همزمان: <a href='/diag?action=start'>start</a> | <a href='/diag?action=stop'>stop</a></p>"
    )

@APP.route("/diag")
def diag():
    action = (request.args.get("action") or "").strip().lower()
    fmt = (request.args.get("format") or "").strip().lower()
    res = do_action(action)
    if fmt == "json":
        return jsonify(res)

    lines = [
        f"ok: {res.get('ok')}",
        f"\naction: {res.get('action')}",
        f"\nserver_url: {res.get('server_url')}",
        f"\ncookies_count: {res.get('cookies_count')}",
        f"\nhas_start: {res.get('has_start')} | has_stop: {res.get('has_stop')}",
        f"\nstatus_before: {res.get('status_before')}",
        f"\nstatus_after: {res.get('status_after')}",
        f"\nselector: {res.get('selector')}",
    ]
    if res.get("network"):
        lines.append("\nnetwork:")
        for ev in res["network"]:
            lines.append(f" - {ev['status']} {ev['url']}")
    if "error" in res:
        lines.append(f"\nerror: {res['error']}")
    return "Diag\n" + "\n".join(lines)

@APP.route("/api/status")
def api_status():
    srv = SERVER_URL
    try:
        cookies = {c["name"]: c["value"] for c in COOKIES}
    except Exception:
        cookies = {}
    try:
        r = requests.get(srv, cookies=cookies, headers={"User-Agent": UA}, timeout=20)
        magma_ok = (r.status_code == 200)
        magma_status = r.status_code
    except Exception:
        magma_ok = False
        magma_status = 0

    return jsonify({
        "service": "render_diag",
        "version": "v3",
        "ok": True,
        "pages": {
            "self": {"ok": True, "status": 200, "url": request.url_root},
            "magma": {"ok": magma_ok, "status": magma_status, "url": srv},
        }
    })

if __name__ == "__main__":
    APP.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
