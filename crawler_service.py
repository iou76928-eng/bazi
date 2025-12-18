# -*- coding: utf-8 -*-
import time
from typing import List, Dict
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

URL_NCC = "https://pay.ncc.com.tw/s.php?bg=nccsoft&ID=ncc&fw=www"

def _init_driver():
    """初始化 Chrome Driver (極速版)"""
    options = webdriver.ChromeOptions()
    
    # 1. 基本 Headless 設定
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    # 2. ★關鍵加速 1：頁面載入策略設為 'eager'
    # 預設是 'normal' (會等所有資源跑完)，'eager' 只要 HTML 下載完就開始動作
    options.page_load_strategy = 'eager' 

    # 3. ★關鍵加速 2：禁止載入圖片與非必要資源
    prefs = {
        "profile.managed_default_content_settings.images": 2, # 2=禁止圖片
        "profile.managed_default_content_settings.stylesheets": 2, # 禁止CSS (若排版跑掉可註解此行)
        "profile.managed_default_content_settings.fonts": 2, # 禁止字型
        "profile.default_content_setting_values.notifications": 2, # 禁止通知
        "profile.managed_default_content_settings.popups": 2,
    }
    options.add_experimental_option("prefs", prefs)

    # 4. 偽裝設定 (防擋)
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
    # 尋找按鈕
    submit_xpath = "//*[contains(normalize-space(.),'確定送出')] | //input[@value='確定送出']"
    
    try:
        # 因為用了 eager 模式，元素可能還沒完全準備好，稍微多一點容錯
        btn = wait.until(EC.element_to_be_clickable((By.XPATH, submit_xpath)))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        time.sleep(0.2) # 縮短等待
        btn.click()
    except Exception:
        try:
            # 備用方案：JS 強制點擊
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
        # 等待關鍵元素出現
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "span.w-blue")))
    except Exception as e:
        print("等待逾時")
        raise e
    
    # 因為禁止了 CSS，有時候版面會亂，直接抓全頁的 span.w-blue 比較保險
    spans = driver.find_elements(By.CSS_SELECTOR, "span.w-blue")
    
    # 過濾掉空的，只留有字的
    texts = [s.text.strip() for s in spans if s.text.strip()]
    
    # NCC 的四柱通常是最後幾個或者是特定區塊，這裡做簡單判斷
    # 如果抓到太多，通常前四個是標題或其他，我們要找符合干支格式的
    # 但為了相容性，先維持抓前四個有效的邏輯，若有誤再調整
    
    # 針對 NCC 結構優化：四柱通常在 div.w10 裡面
    candidates = driver.find_elements(By.CSS_SELECTOR, "div.w10")
    for c in candidates:
        if "四" in c.text and "柱" in c.text:
            spans_inner = c.find_elements(By.CSS_SELECTOR, "span.w-blue")
            texts_inner = [s.text.strip() for s in spans_inner if s.text.strip()]
            if len(texts_inner) >= 4:
                return texts_inner[:4]
    
    # Fallback
    if len(texts) >= 4:
        return texts[:4]
    else:
        raise ValueError(f"取得四柱資料不足: {texts}")

# ==========================================
# ★★★ 核心優化：一次抓取所有資料 ★★★
# ==========================================
def scrape_all_data(
    name: str, sex_value: str, roc_year: str, month: int, day: int, hour: int, minute: int
) -> Dict:
    
    driver = _init_driver()
    wait = WebDriverWait(driver, 25) 
    result = {}
    
    try:
        # --- 任務 1: 抓取命主 ---
        print(f"=== [1/2] 抓取命主 ===")
        driver.get(URL_NCC)
        
        # 這裡不需要 wait_dom_complete，因為我們用了 eager 模式
        # 直接等元素出現就好
        name_inp = wait.until(EC.presence_of_element_located((By.ID, "_Name")))
        name_inp.clear()
        name_inp.send_keys(name if name else "命主")

        # 使用 JS 點擊比等待元素可點擊更快
        driver.execute_script(f"document.querySelector(\"input[name='_Sex'][value='{sex_value}']\").click();")
        driver.execute_script("document.querySelector(\"input[name='_YearMode'][value='1']\").click();")
        
        # JS 直接填表，不模擬真人輸入，速度最快
        year_ad = str(_roc_to_ad_year(roc_year))
        script_set_val = """
        var el = document.getElementById(arguments[0]);
        if(el){ el.value = arguments[1]; el.dispatchEvent(new Event('change')); }
        """
        driver.execute_script(script_set_val, "_Year", year_ad)
        # 稍微 sleep 讓 DOM 反應 (無法完全省略)
        time.sleep(0.1) 
        driver.execute_script(script_set_val, "_Month", str(int(month)))
        driver.execute_script(script_set_val, "_Day", str(int(day)))
        
        if driver.execute_script("return document.getElementById('_Hour') != null;"):
            driver.execute_script(script_set_val, "_Hour", str(int(hour)))
        if driver.execute_script("return document.getElementById('_Min') != null;"):
            driver.execute_script(script_set_val, "_Min", str(int(minute)))

        safe_click_submit(driver, wait)
        result['user_pillars'] = extract_four_pillars(driver, wait)
        
        # --- 任務 2: 抓取今日 ---
        print("=== [2/2] 抓取今日 ===")
        driver.get(URL_NCC)
        
        name_inp = wait.until(EC.presence_of_element_located((By.ID, "_Name")))
        name_inp.clear()
        name_inp.send_keys("今日盤")
        
        driver.execute_script("document.querySelector(\"input[name='_Sex'][value='1']\").click();")
        driver.execute_script("document.querySelector(\"input[name='_YearMode'][value='1']\").click();")
        
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
