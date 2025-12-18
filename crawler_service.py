# -*- coding: utf-8 -*-
import time
from typing import List, Dict
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

URL_NCC = "https://pay.ncc.com.tw/s.php?bg=nccsoft&ID=ncc&fw=www"

def _init_driver():
    """初始化 Chrome Driver (Render / Production 設定)"""
    options = webdriver.ChromeOptions()
    
    # Render 必要設定
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    # 偽裝設定
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
    print("等待結果頁面載入...")
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "span.w-blue")))
    except Exception as e:
        print("等待逾時，找不到 span.w-blue")
        raise e
    
    candidates = driver.find_elements(By.CSS_SELECTOR, "div.w10")
    target = driver
    for c in candidates:
        if "四" in c.text and "柱" in c.text:
            target = c
            break
    
    spans = target.find_elements(By.CSS_SELECTOR, "span.w-blue")
    texts = [s.text.strip() for s in spans if s.text.strip()]
    
    print(f"擷取到: {texts}")
    
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
    # 延長超時時間到 30 秒，因為要連續做兩件事
    wait = WebDriverWait(driver, 30) 
    result = {}
    
    try:
        # --- 任務 1: 抓取命主 ---
        print(f"=== [1/2] 正在抓取命主: {name} ===")
        driver.get(URL_NCC)
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")

        # 填寫資料
        name_inp = wait.until(EC.presence_of_element_located((By.ID, "_Name")))
        name_inp.clear()
        name_inp.send_keys(name if name else "命主")

        driver.execute_script(f"document.querySelector(\"input[name='_Sex'][value='{sex_value}']\").click();")
        driver.execute_script("document.querySelector(\"input[name='_YearMode'][value='1']\").click();")
        time.sleep(0.5)

        # 填寫日期
        year_ad = str(_roc_to_ad_year(roc_year))
        
        # 簡易版日期選擇 (直接用 JS 設定最快)
        script_set_val = """
        var el = document.getElementById(arguments[0]);
        if(el){ el.value = arguments[1]; el.dispatchEvent(new Event('change')); }
        """
        driver.execute_script(script_set_val, "_Year", year_ad)
        time.sleep(0.2)
        driver.execute_script(script_set_val, "_Month", str(int(month)))
        driver.execute_script(script_set_val, "_Day", str(int(day)))
        
        if driver.execute_script("return document.getElementById('_Hour') != null;"):
            driver.execute_script(script_set_val, "_Hour", str(int(hour)))
        if driver.execute_script("return document.getElementById('_Min') != null;"):
            driver.execute_script(script_set_val, "_Min", str(int(minute)))

        safe_click_submit(driver, wait)
        result['user_pillars'] = extract_four_pillars(driver, wait)
        
        # --- 任務 2: 抓取今日 (重複使用同一個 driver) ---
        print("=== [2/2] 正在抓取今日盤 ===")
        driver.get(URL_NCC) # 回首頁
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        
        name_inp = wait.until(EC.presence_of_element_located((By.ID, "_Name")))
        name_inp.clear()
        name_inp.send_keys("今日盤")
        
        driver.execute_script("document.querySelector(\"input[name='_Sex'][value='1']\").click();")
        driver.execute_script("document.querySelector(\"input[name='_YearMode'][value='1']\").click();")
        time.sleep(0.5)
        
        safe_click_submit(driver, wait)
        result['today_pillars'] = extract_four_pillars(driver, wait)
        
        return result

    except Exception as e:
        print(f"[Error] Scrape Failed: {e}")
        raise e
    finally:
        print("關閉瀏覽器")
        driver.quit()

# 為了兼容舊代碼，保留原本的函數名，但建議直接用 scrape_all_data
def get_user_pillars(*args, **kwargs):
    pass 
def get_today_pillars(*args, **kwargs):
    pass
