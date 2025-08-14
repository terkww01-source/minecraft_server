# tests/e2e_start_stop.py
import os, time, json, sys, re, argparse, pathlib
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, JavascriptException, NoSuchElementException, ElementClickInterceptedException

ARTIFACTS = pathlib.Path("artifacts")
ARTIFACTS.mkdir(exist_ok=True)

def ts():
    return datetime.utcnow().strftime("%Y%m%d-%H%M%S")

def save_page(driver, name):
    p = ARTIFACTS / f"{ts()}_{name}"
    (ARTIFACTS / "html").mkdir(exist_ok=True)
    (ARTIFACTS / "png").mkdir(exist_ok=True)
    with open(ARTIFACTS / "html" / f"{p.name}.html", "w", encoding="utf-8") as f:
        f.write(driver.page_source)
    driver.save_screenshot(str(ARTIFACTS / "png" / f"{p.name}.png"))
    print(f"💾 ذخیره شد: {p.name}.html / {p.name}.png")

def make_driver(headless=True, use_system_driver=False):
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1365,900")
    opts.add_argument("--lang=en-US")
    opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    if use_system_driver:
        # Render/لینوکس که chromedriver سیستمی دارد
        driver = webdriver.Chrome(options=opts)
    else:
        # ویندوز/لوکال: chromedriver خودکار دانلود شود
        from webdriver_manager.chrome import ChromeDriverManager
        driver = webdriver.Chrome(ChromeDriverManager().install(), options=opts)
    return driver

def inject_cookies(driver, url, cookies_path):
    if not cookies_path:
        return
    if not os.path.exists(cookies_path):
        print(f"⚠️ cookie file not found: {cookies_path}")
        return
    with open(cookies_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # باید اول به دامنه بریم تا اجازه ست‌کردن کوکی داشته باشیم
    driver.get(url)
    time.sleep(1.0)
    count = 0
    for ck in data:
        # انتظار ساختار: {"name":"...", "value":"...", "domain":".example.com"} و...
        try:
            driver.add_cookie(ck)
            count += 1
        except Exception as e:
            print("Cookie inject error:", e)
    print(f"✅ {count} کوکی تزریق شد.")
    driver.get(url)  # ریفرش با کوکی
    return

def visible(driver, by, value, timeout=20):
    return WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((by, value))
    )

def find_start_stop(driver):
    # دقیقاً با data-action همان چیزی که دادی
    start = driver.find_element(By.CSS_SELECTOR, 'button[data-action="start"]')
    stop  = driver.find_element(By.CSS_SELECTOR, 'button[data-action="stop"]')
    # یک چک کوچک برای متن
    assert "START" in start.text.upper(), f"start button text mismatch: {start.text}"
    assert "STOP" in stop.text.upper(), f"stop button text mismatch: {stop.text}"
    return start, stop

def smart_click(driver, el, name="button"):
    # قبل از کلیک اسکرول و هایلایت
    driver.execute_script("""
        arguments[0].scrollIntoView({block:'center', inline:'center'});
        arguments[0].style.outline='3px solid #00f';
    """, el)
    time.sleep(0.15)
    try:
        ActionChains(driver).move_to_element(el).pause(0.05).click().perform()
        print(f"✅ کلیک با ActionChains روی {name}")
        return True
    except ElementClickInterceptedException:
        pass
    # fallback 1: click مستقیم
    try:
        el.click()
        print(f"✅ کلیک مستقیم روی {name}")
        return True
    except Exception:
        pass
    # fallback 2: JS
    try:
        driver.execute_script("arguments[0].click();", el)
        print(f"✅ کلیک JS روی {name}")
        return True
    except JavascriptException as e:
        print("❌ JS click failed:", e)
        return False

def read_status_guess(driver):
    # حدس‌های رایج: کلماتی مثل online/offline/starting در صفحه
    txt = driver.page_source.lower()
    if "online" in txt: return "online"
    if "starting" in txt or "starting..." in txt: return "starting"
    if "offline" in txt: return "offline"
    return None

def wait_for_status_change(driver, old_status, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        st = read_status_guess(driver)
        if st and st != old_status:
            return st
        time.sleep(1.0)
    return read_status_guess(driver)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="لینک اصلی پنل (مثلاً https://magmanode.com/server?id=770999)")
    ap.add_argument("--backup-url", default=None, help="لینک پشتیبان اگر اصلی باز نشد")
    ap.add_argument("--cookies", default=None, help="مسیر فایل JSON کوکی‌ها (اختیاری)")
    ap.add_argument("--headless", action="store_true", help="اجرای بی‌سر (برای سرور)")
    ap.add_argument("--system-driver", action="store_true", help="استفاده از chromedriver سیستمی (Render/Linux)")
    ap.add_argument("--action", default="start", choices=["start","stop"], help="اقدام: start یا stop")
    args = ap.parse_args()

    driver = make_driver(headless=args.headless, use_system_driver=args.system_driver)
    try:
        # اول URL اصلی
        try:
            inject_cookies(driver, args.url, args.cookies)
            visible(driver, By.CSS_SELECTOR, 'button[data-action="start"]', timeout=20)
            current_url = args.url
            print(f"🌐 صفحه اصلی در دسترس است: {current_url}")
        except TimeoutException:
            if not args.backup_url:
                raise
            print("⚠️ صفحه اصلی پیدا نشد؛ سوییچ به بک‌آپ ...")
            inject_cookies(driver, args.backup_url, args.cookies)
            visible(driver, By.CSS_SELECTOR, 'button[data-action="start"]', timeout=20)
            current_url = args.backup_url
            print(f"🌐 صفحه پشتیبان در دسترس است: {current_url}")

        save_page(driver, "before")

        start_btn, stop_btn = find_start_stop(driver)
        old_status = read_status_guess(driver)
        print(f"🔎 وضعیت قبل از کلیک: {old_status}")

        target = start_btn if args.action == "start" else stop_btn
        ok = smart_click(driver, target, name=args.action.upper())
        time.sleep(0.5)  # فرصت به JS
        save_page(driver, f"after_click_{args.action}")

        # بعد از کلیک، انتظار برای تغییر وضعیت
        new_status = wait_for_status_change(driver, old_status, timeout=30)
        print(f"📈 وضعیت بعد از کلیک: {new_status}")

        # در صورتی که هیچ تغییری دیده نشد، با dispatchEvent هم یک بار امتحان کن
        if (new_status is None or new_status == old_status):
            try:
                driver.execute_script("""
                    const el = arguments[0];
                    const ev = new MouseEvent('click', {bubbles:true, cancelable:true, view:window});
                    el.dispatchEvent(ev);
                """, target)
                print("↻ dispatchEvent کلیک شد. منتظر تغییر...")
                time.sleep(1.5)
                save_page(driver, f"after_dispatch_{args.action}")
                newer = wait_for_status_change(driver, old_status, timeout=15)
                if newer and newer != old_status:
                    new_status = newer
            except Exception as e:
                print("dispatchEvent error:", e)

        if new_status and new_status != old_status:
            print("✅ به نظر میاد کلیک مؤثر بوده.")
            sys.exit(0)
        else:
            print("❌ تغییری دیده نشد. برای بررسی، artifacts را ببین.")
            # لاگ کنسول
            try:
                logs = driver.get_log("browser")
                log_path = ARTIFACTS / f"{ts()}_console.log"
                with open(log_path, "w", encoding="utf-8") as f:
                    for l in logs:
                        f.write(json.dumps(l, ensure_ascii=False) + "\n")
                print(f"💾 لاگ کنسول ذخیره شد: {log_path.name}")
            except Exception as e:
                print("log capture error:", e)
            sys.exit(2)

    finally:
        driver.quit()

if __name__ == "__main__":
    main()
