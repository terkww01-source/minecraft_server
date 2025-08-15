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
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(f"--user-agent={UA}")
    # خاموش کردن نشانه‌های اتوماسیون
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    # لاگ شبکه
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL", "browser": "ALL"})
    service = Service("/usr/bin/chromedriver")
    drv = webdriver.Chrome(service=service, options=opts)

    # ضد‌دیـتکت ساده
    drv.execute_cdp_cmd("Network.enable", {})
    drv.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
        Object.defineProperty(navigator, 'platform',  {get: () => 'Linux x86_64'});
        """
    })
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
            drv.add_cookie({
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain","magmanode.com").lstrip("."),
                "path": c.get("path","/"),
                "secure": bool(c.get("secure", False)),
                "httpOnly": bool(c.get("httpOnly", False)),
            })
            added += 1
        except Exception:
            pass
    return added

def instrument_ajax(drv):
    js = """
    window._calls = [];
    (function(){
      const origFetch = window.fetch;
      window.fetch = function(url, opts){
        try {
          const o = opts || {};
          const body = (o && o.body) ? (''+o.body).slice(0,2000) : null;
          window._calls.push({type:'fetch', url: (''+url), method: (o.method||'GET'), body});
        } catch(e){}
        return origFetch.apply(this, arguments);
      };
      const XHR = window.XMLHttpRequest;
      const open = XHR.prototype.open, send = XHR.prototype.send;
      XHR.prototype.open = function(method, url){
        this._m = method; this._u = url; return open.apply(this, arguments);
      };
      XHR.prototype.send = function(body){
        try {
          window._calls.push({type:'xhr', url: (''+(this._u||'')), method: (this._m||''), body: body ? (''+body).slice(0,2000) : null});
        } catch(e){}
        return send.apply(this, arguments);
      };
    })();
    """
    drv.execute_script(js)

def collect_calls(drv):
    try:
        return drv.execute_script("return window._calls || []") or []
    except Exception:
        return []

def collect_network(drv):
    reqs, resps = [], []
    try:
        logs = drv.get_log("performance")
    except Exception:
        logs = []
    for entry in logs:
        try:
            msg = json.loads(entry["message"])["message"]
            if msg.get("method") == "Network.requestWillBeSent":
                r = msg["params"]["request"]
                reqs.append({
                    "phase": "req",
                    "method": r.get("method"),
                    "url": r.get("url"),
                    "hasPostData": "postData" in r
                })
            elif msg.get("method") == "Network.responseReceived":
                r = msg["params"]["response"]
                resps.append({
                    "phase": "resp",
                    "status": r.get("status"),
                    "url": r.get("url")
                })
        except Exception:
            continue
    # یکی‌کردن روی URL
    out = []
    seen = set()
    for item in reqs + resps:
        u = item.get("url")
        if not u or u in seen: 
            continue
        seen.add(u)
        out.append(item)
        if len(out) >= 20:
            break
    return out

def page_status_text(drv):
    try:
        txt = drv.find_element(By.TAG_NAME, "body").text[:1500]
        # تشخیص خیلی ساده
        st = "unknown"
        for w in ["online","running","starting","offline","stopped"]:
            if re.search(rf"\\b{w}\\b", txt, re.I):
                st = w; break
        return st, txt
    except Exception:
        return "unknown", ""

def find_button(drv, action):
    sels = [
        (By.CSS_SELECTOR, f'button[data-action="{action}"]'),
        (By.XPATH, f'//button[@data-action="{action}"]'),
        (By.XPATH, '//button[normalize-space()="START"]' if action=="start" else '//button[normalize-space()="STOP"]'),
        (By.XPATH, '//button[contains(.,"START")]' if action=="start" else '//button[contains(.,"STOP")]'),
    ]
    for by, sel in sels:
        try:
            el = WebDriverWait(drv, 10).until(EC.element_to_be_clickable((by, sel)))
            return el, {"by":"css selector" if by==By.CSS_SELECTOR else "xpath", "selector": sel}
        except Exception:
            continue
    return None, None

def click_hard(drv, el):
    out = {"clicked_with": []}
    try:
        el.click(); out["clicked_with"].append("native")
    except Exception as e:
        out["native_error"] = repr(e)
    try:
        drv.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        drv.execute_script("arguments[0].click();", el); out["clicked_with"].append("js-click")
    except Exception as e:
        out["js_error"] = repr(e)
    try:
        drv.execute_script("arguments[0].dispatchEvent(new MouseEvent('click', {bubbles:true,cancelable:true}));", el)
        out["clicked_with"].append("dispatch")
    except Exception as e:
        out["dispatch_error"] = repr(e)
    return out

def http_replay_if_possible(calls):
    """
    اگر از داخل صفحه آدرس واقعی استارت/استاپ را گرفتیم، مستقیم با requests بزن.
    """
    # کوکی‌ها برای requests
    jar = {c["name"]: c["value"] for c in COOKIES if "name" in c and "value" in c}
    headers = {"User-Agent": UA, "Referer": SERVER_URL, "Origin": "https://magmanode.com"}

    tried = []
    for c in calls:
        url = c.get("url") or ""
        method = (c.get("method") or "GET").upper()
        if "magmanode.com" not in url:
            continue
        if not any(k in (url.lower() + " " + (c.get("body") or "").lower()) for k in ["start","stop","action=start","action=stop"]):
            continue
        try:
            if method == "POST":
                r = requests.post(url, data=(c.get("body") or {}), headers=headers, cookies=jar, timeout=20, allow_redirects=True)
            else:
                r = requests.get(url, headers=headers, cookies=jar, timeout=20, allow_redirects=True)
            tried.append({"url": url, "method": method, "status": r.status_code})
            # اگر 2xx بود، همین کافی است
            if 200 <= r.status_code < 300:
                break
        except Exception as e:
            tried.append({"url": url, "method": method, "error": repr(e)})
    return tried

def do_action(action):
    data = {
        "ok": False,
        "action": action,
        "server_url": SERVER_URL,
        "cookies_count": len(COOKIES),
    }
    if not SERVER_URL:
        data["error"] = "MAGMANODE_SERVER_URL not set"; return data

    drv = None
    try:
        drv = make_driver()
        inject_cookies(drv)
        drv.get(SERVER_URL)
        time.sleep(2)

        has_start = len(drv.find_elements(By.CSS_SELECTOR, 'button[data-action="start"]')) > 0
        has_stop  = len(drv.find_elements(By.CSS_SELECTOR, 'button[data-action="stop"]')) > 0
        data.update({"has_start": has_start, "has_stop": has_stop})

        status_before, _ = page_status_text(drv)
        data["status_before"] = status_before

        instrument_ajax(drv)

        if action in ("start","stop"):
            el, sel = find_button(drv, action)
            data["selector"] = sel
            if not el:
                data["error"] = "button_not_found"
            else:
                data["click"] = click_hard(drv, el)
                time.sleep(5)  # صبر برای رفتن درخواست‌ها
        else:
            data["selector"] = None

        # جمع‌آوری لاگ‌ها
        data["calls"] = collect_calls(drv)
        data["network"] = collect_network(drv)

        # تلاش مستقیم HTTP اگر اندپوینت پیدا شد
        if action in ("start","stop") and data["calls"]:
            data["http_replay"] = http_replay_if_possible(data["calls"])

        status_after, txt_after = page_status_text(drv)
        data["status_after"] = status_after
        data["text_after_sample"] = txt_after

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
        "<p>هویت مرورگر: <a href='/whoami'>/whoami</a></p>"
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
            if ev.get("phase")=="req":
                lines.append(f" - REQ {ev.get('method')} {ev.get('url')}")
            else:
                lines.append(f" - RESP {ev.get('status')} {ev.get('url')}")
    if res.get("http_replay"):
        lines.append("\nhttp_replay:")
        for ev in res["http_replay"]:
            if "status" in ev:
                lines.append(f" - {ev['method']} {ev['url']} -> {ev['status']}")
            else:
                lines.append(f" - {ev['method']} {ev['url']} -> {ev.get('error')}")
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
        "version": "v4",
        "ok": True,
        "pages": {
            "self": {"ok": True, "status": 200, "url": request.url_root},
            "magma": {"ok": magma_ok, "status": magma_status, "url": srv},
        }
    })

@APP.route("/whoami")
def whoami():
    return jsonify({
        "ua": UA,
        "env_ok": True,
        "cookies_count": len(COOKIES),
    })

if __name__ == "__main__":
    APP.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
