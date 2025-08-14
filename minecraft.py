import os
import time
import random
import logging
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("minecraft_cli")

MAGMA_SERVER_URL = os.environ.get("MAGMANODE_SERVER_URL", "https://magmanode.com/server?id=770999")
CHROME_BIN = os.environ.get("CHROME_BIN", "/usr/bin/chromium")
CHROMEDRIVER_PATH = os.environ.get("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")

class MinecraftAutoClicker:
    def __init__(self):
        self.driver = None
        self._setup()
        self.click_count = 0
        self.failed_clicks = 0
        self.successful_clicks = 0
        self.start_time = datetime.now()
        self.consecutive_failures = 0

    def _opts(self):
        o = Options()
        o.add_argument("--headless=new")
        o.add_argument("--no-sandbox")
        o.add_argument("--disable-dev-shm-usage")
        o.add_argument("--disable-gpu")
        o.add_argument("--window-size=1366,768")
        o.add_experimental_option("excludeSwitches", ["enable-automation"])
        o.add_experimental_option("useAutomationExtension", False)
        if CHROME_BIN and os.path.exists(CHROME_BIN):
            o.binary_location = CHROME_BIN
        o.add_argument("--disable-blink-features=AutomationControlled")
        return o

    def _setup(self):
        s = Service(CHROMEDRIVER_PATH)
        self.driver = webdriver.Chrome(service=s, options=self._opts())
        try:
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator,'webdriver',{get:() => undefined});"},
            )
        except Exception:
            pass
        logger.info("Chrome headless آماده است.")

    def find_start_button(self):
        selectors = [
            (By.CSS_SELECTOR, 'button[data-action="start"]'),
            (By.CSS_SELECTOR, 'button.bg-green-600'),
            (By.XPATH, '//button[contains(text(),"START")]'),
            (By.XPATH, '//button[text()="START"]'),
            (By.CSS_SELECTOR, 'button.bg-green-600.text-white'),
            (By.CSS_SELECTOR, 'button[type="submit"].bg-green-600'),
            (By.CSS_SELECTOR, 'button[class*="bg-green-600"]'),
            (By.CSS_SELECTOR, 'button[class*="bg-green"][class*="text-white"]'),
        ]
        for by, s in selectors:
            try:
                el = WebDriverWait(self.driver, 3).until(EC.element_to_be_clickable((by, s)))
                if "START" in (el.text or "").upper():
                    return el
            except Exception:
                continue
        raise Exception("START button not found")

    def click_start_button(self):
        try:
            btn = self.find_start_button()
            self.driver.execute_script("arguments[0].scrollIntoView({behavior:'smooth',block:'center'});", btn)
            time.sleep(random.uniform(0.5, 1.5))
            try:
                btn.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", btn)
            self.successful_clicks += 1
            self.click_count += 1
            logger.info(f"✅ کلیک موفق #{self.click_count}")
            return True
        except Exception as e:
            self.failed_clicks += 1
            logger.error(f"❌ کلیک ناموفق: {e}")
            return False

    def run(self, url=None, max_clicks=None):
        try:
            target = url or MAGMA_SERVER_URL
            self.driver.get(target)
            time.sleep(3)

            while True:
                ok = self.click_start_button()
                wait_time = random.uniform(60, 300) if ok else random.uniform(30, 120)
                time.sleep(wait_time)
                if max_clicks and self.successful_clicks >= max_clicks:
                    break
        finally:
            try:
                self.driver.quit()
            except Exception:
                pass

def main():
    clicker = MinecraftAutoClicker()
    clicker.run(url=MAGMA_SERVER_URL, max_clicks=None)

if __name__ == "__main__":
    main()
