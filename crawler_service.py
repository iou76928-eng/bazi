# -*- coding: utf-8 -*-
import time
from datetime import datetime # <--- 新增這個
from typing import List, Dict
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

URL_NCC = "https://pay.ncc.com.tw/s.php?bg=nccsoft&ID=ncc&fw=www"

def _init_driver():
    """初始化 Chrome Driver (穩定極速版)"""
    options = webdriver.ChromeOptions()
    
    # 1. 基本 Headless 設定
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    # 2. 禁止載入圖片與非必要資源 (加速關鍵)
    prefs = {
        "profile.managed_default_content_settings.images": 2, 
        "profile.managed_default_content_settings.stylesheets": 2, 
        "profile.managed_default_content_settings.fonts": 2, 
        "profile.default_content_setting_values.notifications": 2,
        "profile.managed_default_content_settings.popups": 2,
    }
    options.add_experimental_option("prefs", prefs)

    # 3. 偽裝設定
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--lang=zh-TW")
    
    driver = webdriver.Chrome(options=options)
    
    # 進階偽裝
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            })
        """
    })
    
    return driver

def _roc_to_ad_year(roc_year: str) -> int:
    try:
        y = int(str(roc_year).strip())
        return y + 1911
    except:
        return 1911 + 76 

def safe_click_submit(driver, wait):
    """安全點擊送出"""
    submit_xpath = "//*[contains(normalize-space(.),'確定送出')] | //input[@value='確定送出']"
    try:
        btn = wait.until(EC.element_to_be_clickable((By.XPATH, submit_xpath)))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        time.sleep(0.5)
        btn.click()
    except Exception:
        try:
            btn = driver.find_element(By.XPATH, submit_xpath)
            driver.execute_script("arguments[0].click();", btn)
        except Exception as e:
            print(f"點擊失敗: {e}")
            raise e
    print("已點擊送出")

def extract_four_pillars(driver, wait):
    """擷取四柱"""
    print("等待結果頁面...")
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "span.w-blue")))
    except Exception as e:
        print("等待逾時，找不到 span.w-blue")
        raise e
    
    candidates = driver.find_elements(By.CSS_SELECTOR, "div.w10")
    found_spans = []
    
    for c in candidates:
        if "四" in c.text and "柱" in c.text:
            spans = c.find_elements(By.CSS_SELECTOR, "span.w-blue")
            found_spans = [s.text.strip() for s in spans if s.text.strip()]
            break
            
    if len(found_spans) < 4:
         all_spans = driver.find_elements(By.CSS_SELECTOR, "span.w-blue")
         found_spans = [s.text.strip() for s in all_spans if s.text.strip()]

    print(f"擷取到: {found_spans}")
    
    if len(found_spans) >= 4:
        return found_spans[:4]
    else:
        raise ValueError(f"取得四柱資料不足: {found_spans}")

# ==========================================
# ★★★ 核心優化：一次抓取所有資料 ★★★
# ==========================================
def scrape_all_data(
    name: str, sex_value: str, roc_year: str, month: int, day: int, hour: int, minute: int
) -> Dict:
    
    driver = _init_driver()
    wait = WebDriverWait(driver, 40) 
    result = {}
    
    # 預先準備好 JS 填表腳本
    script_set_val = """
    var el = document.getElementById(arguments[0]);
    if(el){ el.value = arguments[1]; el.dispatchEvent(new Event('change')); }
    """
    
    try:
        # --- 任務 1: 抓取命主 ---
        print(f"=== [1/2] 抓取命主 ===")
        driver.get(URL_NCC)
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        
        name_inp = wait.until(EC.presence_of_element_located((By.ID, "_Name")))
        name_inp.clear()
        name_inp.send_keys(name if name else "命主")

        driver.execute_script(f"document.querySelector(\"input[name='_Sex'][value='{sex_value}']\").click();")
        driver.execute_script("document.querySelector(\"input[name='_YearMode'][value='1']\").click();")
        time.sleep(0.2)

        # 填寫命主日期
        year_ad = str(_roc_to_ad_year(roc_year))
        
        driver.execute_script(script_set_val, "_Year", year_ad)
        time.sleep(0.1)
        driver.execute_script(script_set_val, "_Month", str(int(month)))
        driver.execute_script(script_set_val, "_Day", str(int(day)))
        
        if driver.execute_script("return document.getElementById('_Hour') != null;"):
            driver.execute_script(script_set_val, "_Hour", str(int(hour)))
        if driver.execute_script("return document.getElementById('_Min') != null;"):
            driver.execute_script(script_set_val, "_Min", str(int(minute)))

        safe_click_submit(driver, wait)
        result['user_pillars'] = extract_four_pillars(driver, wait)
        
        # --- 中場休息 ---
        print("準備進行第二次抓取...")
        driver.delete_all_cookies()
        time.sleep(1)
        
        # --- 任務 2: 抓取今日 (強制修正日期) ---
        print("=== [2/2] 抓取今日 (強制寫入當下時間) ===")
        driver.get(URL_NCC)
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        
        name_inp = wait.until(EC.presence_of_element_located((By.ID, "_Name")))
        name_inp.clear()
        name_inp.send_keys("今日盤")
        
        driver.execute_script("document.querySelector(\"input[name='_Sex'][value='1']\").click();")
        driver.execute_script("document.querySelector(\"input[name='_YearMode'][value='1']\").click();")
        time.sleep(0.2)

        # ★★★ 修正點：取得當下電腦時間 ★★★
        now = datetime.now()
        print(f"系統時間: {now.year}/{now.month}/{now.day} {now.hour}:{now.minute}")

        # ★★★ 強制覆蓋日期欄位，不依賴網站預設值 ★★★
        driver.execute_script(script_set_val, "_Year", str(now.year))
        time.sleep(0.1)
        driver.execute_script(script_set_val, "_Month", str(now.month))
        driver.execute_script(script_set_val, "_Day", str(now.day))
        
        # 連時分也寫入，確保精準
        if driver.execute_script("return document.getElementById('_Hour') != null;"):
            driver.execute_script(script_set_val, "_Hour", str(now.hour))
        if driver.execute_script("return document.getElementById('_Min') != null;"):
            driver.execute_script(script_set_val, "_Min", str(now.minute))
        
        safe_click_submit(driver, wait)
        result['today_pillars'] = extract_four_pillars(driver, wait)
        
        return result

    except Exception as e:
        print(f"[Error] Scrape Failed: {e}")
        raise e
    finally:
        driver.quit()

# 兼容舊碼
def get_user_pillars(*args, **kwargs): pass 
def get_today_pillars(*args, **kwargs): pass
