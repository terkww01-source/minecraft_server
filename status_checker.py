import os
import time
import logging

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("status_checker")

MAGMA_SERVER_URL = os.environ.get("MAGMANODE_SERVER_URL", "https://magmanode.com/server?id=770999")
CHROME_BIN = os.environ.get("CHROME_BIN", "/usr/bin/chromium")
CHROMEDRIVER_PATH = os.environ.get("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")

def _opts():
    o = Options()
    o.add_argument("--headless=new")
    o.add_argument("--no-sandbox")
    o.add_argument("--disable-dev-shm-usage")
    o.add_argument("--disable-gpu")
    o.add_argument("--window-size=1366,768")
    if CHROME_BIN and os.path.exists(CHROME_BIN):
        o.binary_location = CHROME_BIN
    o.add_experimental_option("excludeSwitches", ["enable-automation"])
    o.add_experimental_option("useAutomationExtension", False)
    o.add_argument("--disable-blink-features=AutomationControlled")
    return o

def main():
    print("🔍 ابزار تحلیل وضعیت (Render/Headless)")
    service = Service(CHROMEDRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=_opts())
    try:
        driver.get(MAGMA_SERVER_URL)
        time.sleep(3)

        spans = driver.find_elements(By.TAG_NAME, "span")
        print(f"📊 span count: {len(spans)}")
        for i, sp in enumerate(spans, start=1):
            try:
                t = (sp.text or "").strip()
                if t and any(w in t.lower() for w in ['running', 'offline', 'starting', 'stopped']):
                    print(f"- span[{i}] text='{t}' class='{sp.get_attribute('class')}'")
            except Exception:
                continue

        buttons = driver.find_elements(By.TAG_NAME, "button")
        print(f"🔘 button count: {len(buttons)}")
        for i, b in enumerate(buttons, start=1):
            try:
                t = (b.text or "").strip()
                cls = b.get_attribute('class') or ''
                if ('start' in t.lower() or 'stop' in t.lower()) or ('bg-green' in cls or 'bg-red' in cls):
                    print(f"- btn[{i}] text='{t}' class='{cls}' enabled={b.is_enabled()}")
            except Exception:
                continue
    finally:
        try:
            driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()
