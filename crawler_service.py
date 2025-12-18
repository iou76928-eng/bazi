# -*- coding: utf-8 -*-
import time
from typing import List
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

URL_NCC = "https://pay.ncc.com.tw/s.php?bg=nccsoft&ID=ncc&fw=www"

def _init_driver():
    """初始化 Chrome Driver (Render / Production 設定)"""
    options = webdriver.ChromeOptions()
    
    # ==========================================
    # 🚀 Render 上傳設定
    # ==========================================
    # 1. 必開 Headless (Render 沒有螢幕)
    options.add_argument("--headless=new")
    
    # 2. 系統資源設定 (防止在 Docker/Render 中崩潰)
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    # 3. ★關鍵偽裝★：設定 User-Agent，讓網站以為是真人
    # 如果不加這行，Headless 模式下會被 NCC 網站擋住
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # 4. ★進階偽裝★：移除自動化控制特徵
    # 這行可以防止網站偵測到 "navigator.webdriver" 屬性
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    # 5. 其他優化
    options.add_argument("--lang=zh-TW")
    
    driver = webdriver.Chrome(options=options)
    
    # 額外執行 CDP 命令來隱藏 webdriver 特徵 (雙重保險)
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

# 共用的「安全點擊」函數
def safe_click_submit(driver, wait):
    """
    使用最強的 XPath 尋找送出按鈕，並確保它真的被點到。
    """
    print("尋找送出按鈕...")
    # 這是最完整的寫法，同時尋找 <button>文字</button> 和 <input value='文字'>
    submit_xpath = "//*[contains(normalize-space(.),'確定送出')] | //input[@value='確定送出']"
    
    try:
        btn = wait.until(EC.element_to_be_clickable((By.XPATH, submit_xpath)))
        # 確保按鈕在畫面中可見
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        time.sleep(0.5) 
        btn.click()
    except Exception:
        # 如果一般點擊失敗，嘗試重新抓取並用 JS 強制點擊
        try:
            btn = driver.find_element(By.XPATH, submit_xpath)
            driver.execute_script("arguments[0].click();", btn)
        except Exception as e:
            print(f"點擊失敗: {e}")
            raise e
    
    print("已點擊送出")

# 共用的「四柱擷取」函數
def extract_four_pillars(driver, wait):
    """
    等待並擷取四柱文字
    """
    print("等待結果頁面載入...")
    try:
        # 先確認有沒有出現藍色字體 (四柱特徵)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "span.w-blue")))
    except Exception as e:
        print("等待逾時，找不到 span.w-blue，可能被阻擋或沒換頁成功")
        # 若在本機除錯，可取消下面註解存截圖
        # driver.save_screenshot("debug_headless.png")
        raise e
    
    # 尋找包含"四 柱"的區塊
    candidates = driver.find_elements(By.CSS_SELECTOR, "div.w10")
    target = driver # 預設全頁找
    
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
        raise ValueError(f"取得四柱資料不足，只抓到: {texts}")

# ==========================================
# 1. 命主抓取
# ==========================================
def get_user_pillars(
    name: str, sex_value: str, year_mode_value: str,
    roc_year: str, month: int, day: int, hour: int, minute: int
) -> List[str]:
    
    driver = _init_driver()
    wait = WebDriverWait(driver, 25) # Render 有時較慢，延長到 25秒
    
    try:
        print(f"=== [命主] 正在連線 NCC ({name}) ===")
        driver.get(URL_NCC)
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")

        # 1. 填寫資料
        name_inp = wait.until(EC.presence_of_element_located((By.ID, "_Name")))
        name_inp.clear()
        name_inp.send_keys(name if name else "命主")

        sex_radio = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, f"input[name='_Sex'][value='{sex_value}']")))
        driver.execute_script("arguments[0].click();", sex_radio)

        ym_radio = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, f"input[name='_YearMode'][value='{year_mode_value}']")))
        driver.execute_script("arguments[0].click();", ym_radio)
        
        time.sleep(0.5)

        # 2. 填寫日期
        year_ad = str(_roc_to_ad_year(roc_year))
        
        def safe_select(eid, val):
            val = str(int(val))
            try:
                sel_el = driver.find_element(By.ID, eid)
                Select(sel_el).select_by_value(val)
            except:
                driver.execute_script(f"document.getElementById('{eid}').value = '{val}';")
                driver.execute_script(f"document.getElementById('{eid}').dispatchEvent(new Event('change'));")
        
        print(f"填寫日期: {year_ad}/{month}/{day} {hour}:{minute}")
        safe_select("_Year", year_ad)
        time.sleep(0.2)
        safe_select("_Month", month)
        safe_select("_Day", day)
        
        if driver.execute_script("return document.getElementById('_Hour') != null;"):
            safe_select("_Hour", hour)
        if driver.execute_script("return document.getElementById('_Min') != null;"):
            safe_select("_Min", minute)

        # 3. 送出
        safe_click_submit(driver, wait)

        # 4. 擷取
        return extract_four_pillars(driver, wait)

    except Exception as e:
        print(f"[Error] User Scrape Failed: {e}")
        raise e
    finally:
        driver.quit()

# ==========================================
# 2. 今日抓取
# ==========================================
def get_today_pillars() -> List[str]:
    driver = _init_driver()
    wait = WebDriverWait(driver, 25)
    
    try:
        print("=== [今日] 正在連線 NCC ===")
        driver.get(URL_NCC)
        
        # 1. 填寫預設值
        name_inp = wait.until(EC.presence_of_element_located((By.ID, "_Name")))
        name_inp.clear()
        name_inp.send_keys("今日盤")

        # 點男生
        sex_male = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[name='_Sex'][value='1']")))
        driver.execute_script("arguments[0].click();", sex_male)

        # 點國曆
        solar = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[name='_YearMode'][value='1']")))
        driver.execute_script("arguments[0].click();", solar)
        
        time.sleep(0.5)

        # 2. 送出
        safe_click_submit(driver, wait)

        # 3. 擷取
        return extract_four_pillars(driver, wait)

    except Exception as e:
        print(f"[Error] Today Scrape Failed: {e}")
        raise e
    finally:
        driver.quit()