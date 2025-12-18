# -*- coding: utf-8 -*-
import time
from datetime import datetime
from typing import List, Dict
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

URL_NCC = "https://pay.ncc.com.tw/s.php?bg=nccsoft&ID=ncc&fw=www"

# ==========================================
# 🧠 全域快取 (Global Cache)
# 用來暫存「今天的四柱」，避免每次都要重新爬
# ==========================================
_TODAY_CACHE = {
    "date": None,  # 格式: "2025-12-18"
    "data": None   # 格式: ['乙巳', '戊子', '辛酉', '癸巳']
}

def _init_driver():
    """初始化 Chrome Driver (穩定極速版)"""
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    # 禁止載入圖片與資源 (加速)
    prefs = {
        "profile.managed_default_content_settings.images": 2, 
        "profile.managed_default_content_settings.stylesheets": 2, 
        "profile.managed_default_content_settings.fonts": 2, 
        "profile.default_content_setting_values.notifications": 2,
        "profile.managed_default_content_settings.popups": 2,
    }
    options.add_experimental_option("prefs", prefs)

    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--lang=zh-TW")
    
    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
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
        time.sleep(0.2) # 縮短等待
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
# ★★★ 核心優化：智慧快取 (Smart Cache) ★★★
# ==========================================
def scrape_all_data(
    name: str, sex_value: str, roc_year: str, month: int, day: int, hour: int, minute: int
) -> Dict:
    
    # 1. 檢查快取
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    
    global _TODAY_CACHE
    cached_today_pillars = None
    
    # 如果快取裡面有今天的資料，就直接拿來用
    if _TODAY_CACHE["date"] == today_str and _TODAY_CACHE["data"] is not None:
        print(f"⚡ 命中快取！今日四柱已存在: {_TODAY_CACHE['data']}")
        cached_today_pillars = _TODAY_CACHE["data"]
    
    driver = _init_driver()
    wait = WebDriverWait(driver, 40) 
    result = {}
    
    # JS 填表腳本
    script_set_val = """
    var el = document.getElementById(arguments[0]);
    if(el){ el.value = arguments[1]; el.dispatchEvent(new Event('change')); }
    """
    
    try:
        # --- 任務 1: 抓取命主 (每個人不同，一定要抓) ---
        print(f"=== [1/2] 抓取命主 ===")
        driver.get(URL_NCC)
        # 用 eager 策略等待：只要 readyState complete 即可
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        
        name_inp = wait.until(EC.presence_of_element_located((By.ID, "_Name")))
        name_inp.clear()
        name_inp.send_keys(name if name else "命主")

        driver.execute_script(f"document.querySelector(\"input[name='_Sex'][value='{sex_value}']\").click();")
        driver.execute_script("document.querySelector(\"input[name='_YearMode'][value='1']\").click();")
        
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
        
        # --- 任務 2: 抓取今日 (如果有快取就跳過) ---
        if cached_today_pillars:
            print("🚀 跳過第二次抓取，使用快取資料。")
            result['today_pillars'] = cached_today_pillars
        else:
            print("=== [2/2] 抓取今日 (無快取，需執行) ===")
            
            # 清除 Cookie 避免干擾，但動作要快
            driver.delete_all_cookies()
            
            driver.get(URL_NCC)
            wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
            
            name_inp = wait.until(EC.presence_of_element_located((By.ID, "_Name")))
            name_inp.clear()
            name_inp.send_keys("今日盤")
            
            driver.execute_script("document.querySelector(\"input[name='_Sex'][value='1']\").click();")
            driver.execute_script("document.querySelector(\"input[name='_YearMode'][value='1']\").click();")
            
            print(f"系統時間: {now.year}/{now.month}/{now.day} {now.hour}:{now.minute}")

            # 強制寫入當下時間
            driver.execute_script(script_set_val, "_Year", str(now.year))
            time.sleep(0.1)
            driver.execute_script(script_set_val, "_Month", str(now.month))
            driver.execute_script(script_set_val, "_Day", str(now.day))
            
            if driver.execute_script("return document.getElementById('_Hour') != null;"):
                driver.execute_script(script_set_val, "_Hour", str(now.hour))
            if driver.execute_script("return document.getElementById('_Min') != null;"):
                driver.execute_script(script_set_val, "_Min", str(now.minute))
            
            safe_click_submit(driver, wait)
            today_data = extract_four_pillars(driver, wait)
            
            result['today_pillars'] = today_data
            
            # ★★★ 寫入快取 ★★★
            _TODAY_CACHE["date"] = today_str
            _TODAY_CACHE["data"] = today_data
            print("✅ 已將今日四柱寫入快取")

        return result

    except Exception as e:
        print(f"[Error] Scrape Failed: {e}")
        raise e
    finally:
        driver.quit()

# 兼容舊碼
def get_user_pillars(*args, **kwargs): pass 
def get_today_pillars(*args, **kwargs): pass
