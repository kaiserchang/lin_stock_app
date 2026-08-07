import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import json
import os
import time
import random

from concurrent.futures import ThreadPoolExecutor, as_completed
from analysis_engine import LinJiaYangEngine
from scan_orchestrator import TaiwanStockDataFetcher

# ==========================================
# 1. 系統設定與存檔設定
# ==========================================
st.set_page_config(page_title="林家洋 - 全市場雷達 (圖示說明化)", layout="wide")

CONFIG_FILE = "user_settings.json"
CACHE_FILES = {
    "模式 A：自訂關注清單": "cache_mode_a.csv",
    "模式 B：台灣50成分股掃描": "cache_mode_b.csv",
    "模式 C：全市場掃描 (⚠️ 高耗時)": "cache_mode_c.csv",
    "模式 D：上傳 CSV 檔案": "cache_mode_d.csv"
}

META_FILES = {
    "模式 A：自訂關注清單": "cache_mode_a_meta.json",
    "模式 B：台灣50成分股掃描": "cache_mode_b_meta.json",
    "模式 C：全市場掃描 (⚠️ 高耗時)": "cache_mode_c_meta.json",
    "模式 D：上傳 CSV 檔案": "cache_mode_d_meta.json"
}

TW50_MAPPING = {
    "1101": "台泥", "1216": "統一", "1301": "台塑", "1303": "南亞", "1326": "台化",
    "1590": "亞德客-KY", "2002": "中鋼", "2207": "和泰車", "2301": "光寶科", "2303": "聯電",
    "2308": "台達電", "2317": "鴻海", "2327": "國巨", "2330": "台積電", "2345": "智邦",
    "2353": "宏碁", "2357": "華碩", "2379": "瑞昱", "2382": "廣達", "2385": "群光",
    "2395": "研華", "2412": "中華電", "2449": "京元電子", "2454": "聯發科", "2603": "長榮",
    "2609": "陽明", "2615": "萬海", "2880": "華南金", "2881": "富邦金", "2882": "國泰金",
    "2883": "開發金", "2884": "玉山金", "2885": "元大金", "2886": "兆豐金", "2887": "台新金",
    "2890": "永豐金", "2891": "中信金", "2892": "第一金", "3008": "大立光", "3034": "聯詠",
    "3037": "欣興", "3231": "緯創", "3293": "鈊象", "3661": "世芯-KY", "3711": "日月光投控",
    "4938": "和碩", "5871": "中租-KY", "5880": "合庫金", "6505": "台塑化", "6669": "緯穎"
}

def load_saved_stocks():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return ["2330", "2317", "2454"]

def save_stocks(stocks):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(stocks, f)

@st.cache_data
def load_all_stocks_for_dropdown():
    """讀取全市場股票清單，供下拉選單使用，避免每次重讀"""
    all_options = list(TW50_MAPPING.keys())
    mapping = TW50_MAPPING.copy()
    if os.path.exists("all_stocks.csv"):
        try:
            df = pd.read_csv("all_stocks.csv", dtype=str)
            code_col = [col for col in df.columns if '代碼' in str(col) or 'Ticker' in str(col)]
            name_col = [col for col in df.columns if '名稱' in str(col) or 'Name' in str(col)]
            if code_col:
                codes = df[code_col[0]].astype(str).tolist()
                all_options.extend(codes)
                if name_col:
                    names = df[name_col[0]].astype(str).tolist()
                    mapping.update(dict(zip(codes, names)))
        except Exception:
            pass
    # 確保清單不重複
    return list(dict.fromkeys(all_options)), mapping

def get_formatted_signal(score, signal, above_ma60):
    if signal == '黑K吞噬' or score < 0:
        return "💣 黑K吞噬 (逃命警示)"
    elif signal == '攻擊K線' or score >= 100:
        return "🚀 順勢強攻 (突破高點)"
    elif signal == '多頭吞噬' or score >= 75:
        return "🔥 多頭反轉 (吞噬賣壓)"
    elif signal == '多頭蓄勢 (高檔量縮)' or (0 < score < 75 and above_ma60):
        return "🔋 多頭蓄勢 (蓄力中)"
    elif signal == '弱勢反彈 (空頭壓制)' or (0 < score < 75 and not above_ma60):
        return "⏳ 弱勢反彈 (空頭壓制)"
    else:
        return "➖ 無明顯型態"

# ==========================================
# 2. 注入自訂 CSS
# ==========================================
custom_css = """
<style>
/* 全域底色與文字 */
.stApp {
    background-color: #0B1120;
    color: #FFFFFF !important;
}

/* 標題顏色 */
h1, h2, h3, h4, h5, h6 {
    color: #FB923C !important;
    font-weight: bold !important;
}
h1 {
    font-size: 2.2rem !important;
}

/* 1. 修正側邊欄：強制背景為深色，且內部所有標籤、選項文字強制為白色 */
section[data-testid="stSidebar"] {
    background-color: #0f172a !important;
}
section[data-testid="stSidebar"] * {
    color: #FFFFFF !important;
}

/* 2. 修正 Expander 折疊面板：包含外框、標題列 (summary) 與內容區 (details) 全面強制深底白字 */
div[data-testid="stExpander"], 
div[data-testid="stExpander"] details, 
div[data-testid="stExpander"] summary {
    background-color: #0f172a !important;
    color: #FFFFFF !important;
    border-color: #1e293b !important;
}
div[data-testid="stExpander"] * {
    color: #FFFFFF !important;
}

/* 3. 強制表格與數據框底色與文字 */
div[data-testid="stDataFrame"], div[data-testid="stTable"], table {
    background-color: #0f172a !important;
    color: #FFFFFF !important;
}

/* 4. 修正 st.button：強制醒目橘色背景與白色文字，徹底解決手機端按鈕反白問題 */
div[data-testid="stButton"] > button,
section[data-testid="stSidebar"] div[data-testid="stButton"] > button {
    background-color: #EA580C !important; /* 亮橘色背景 */
    color: #FFFFFF !important;            /* 白色粗體文字 */
    border: none !important;
    border-radius: 8px !important;
    font-weight: bold !important;
}

/* 手指按壓、懸停與焦點狀態強制鎖定深橘色 */
div[data-testid="stButton"] > button:hover,
div[data-testid="stButton"] > button:focus,
div[data-testid="stButton"] > button:active,
section[data-testid="stSidebar"] div[data-testid="stButton"] > button:hover,
section[data-testid="stSidebar"] div[data-testid="stButton"] > button:focus,
section[data-testid="stSidebar"] div[data-testid="stButton"] > button:active {
    background-color: #C2410C !important; /* 按下時轉為深橘色 */
    color: #FFFFFF !important;
    box-shadow: none !important;
}

/* 5. 【關鍵修正】強制Dataframe內部的Checkbox文字標籤為白色 */
div[data-testid="stDataFrame"] div[class*="StyledTableCellLabel"] {
    color: #FFFFFF !important;
}
</style>
"""
st.markdown(custom_css, unsafe_allow_html=True)

def highlight_signals(row):
    status = str(row['最新形態'])
    try: score = int(row['推薦分數'])
    except: score = 0
        
    if "順勢強攻" in status: color = '#22D3EE'
    elif "多頭反轉" in status: color = '#4ADE80'
    elif "多頭蓄勢" in status: color = '#38BDF8'
    elif "弱勢反彈" in status: color = '#FDE047'
    elif "黑K吞噬" in status or score < 0: color = '#EF4444'
    elif score > 0: color = '#A7F3D0'
    else: color = '#FFFFFF'
        
    # 【關鍵修正】：加上 background-color: #0f172a，強制將表格儲存格背景鎖定為深色
    return [f'background-color: #0f172a; color: {color}; font-weight: bold'] * len(row)

# ==========================================
# 3. 初始化 Session State 與本機快取恢復
# ==========================================
if 'scan_results' not in st.session_state:
    st.session_state.scan_results = {}
    for mode, csv_path in CACHE_FILES.items():
        meta_path = META_FILES[mode]
        if os.path.exists(csv_path) and os.path.exists(meta_path):
            try:
                df_cached = pd.read_csv(csv_path, dtype={'代碼': str})
                with open(meta_path, 'r', encoding='utf-8') as mf:
                    meta_data = json.load(mf)
                
                if "模式 A" in mode or "模式 D" in mode:
                    st.session_state.scan_results[mode] = {
                        "type": "single",
                        "data": df_cached.to_dict('records'),
                        "date": meta_data.get("date", "未知日期")
                    }
                else:
                    buy_df_path = csv_path.replace(".csv", "_buy.csv")
                    sell_df_path = csv_path.replace(".csv", "_sell.csv")
                    
                    buy_data = pd.read_csv(buy_df_path, dtype={'代碼': str}).to_dict('records') if os.path.exists(buy_df_path) else []
                    sell_data = pd.read_csv(sell_df_path, dtype={'代碼': str}).to_dict('records') if os.path.exists(sell_df_path) else []
                    
                    st.session_state.scan_results[mode] = {
                        "type": "split",
                        "buy": buy_data,
                        "sell": sell_data,
                        "date": meta_data.get("date", "未知日期")
                    }
            except Exception as e:
                pass

# ==========================================
# 4. 網頁 UI 與 4 大模式切換
# ==========================================
st.title("📡 林家洋技術分析 - 全市場掃描雷達")

tw50_list = list(TW50_MAPPING.keys())
target_stocks = []

# 載入全市場清單供選單使用
all_stock_codes, full_mapping = load_all_stocks_for_dropdown()
dynamic_name_mapping = full_mapping.copy()

# 【關鍵修正】：初始化 Session State (只在第一次啟動時從 JSON 讀取)
if 'watch_list' not in st.session_state:
    st.session_state.watch_list = load_saved_stocks()

# 【關鍵修正】：建立 Callback 函式，當選單有變動時，自動將新狀態存入 JSON
def sync_watch_list_to_file():
    save_stocks(st.session_state.watch_list)

with st.sidebar:
    st.header("⚙️ 掃描模式設定")
    scan_mode = st.radio(
        "請選擇掃描範圍：",
        (
            "模式 A：自訂關注清單", 
            "模式 B：台灣50成分股掃描",
            "模式 C：全市場掃描 (⚠️ 高耗時)",
            "模式 D：上傳 CSV 檔案"
        )
    )
    
    if scan_mode == "模式 A：自訂關注清單":
        # 確保已存股票在選項中，避免 Streamlit 報錯
        options = list(dict.fromkeys(all_stock_codes + st.session_state.watch_list))
        
        # 使用綁定 Key 與 Callback 的寫法，徹底解決慢半拍問題
        st.multiselect(
            "🔍 請選擇或輸入股票代碼 (支援中英文搜尋)",
            options=options,
            key="watch_list",  # 綁定 Session State，不用再寫 default
            on_change=sync_watch_list_to_file,  # 變動時自動執行存檔
            format_func=lambda x: f"{x} {dynamic_name_mapping.get(x, '')}".strip()
        )
        # 將選取結果賦值給 target_stocks，供下方的運算核心使用
        target_stocks = st.session_state.watch_list
            
    elif scan_mode == "模式 B：台灣50成分股掃描":
        st.info(f"系統將自動掃描台灣 50 大權值股 (共 {len(tw50_list)} 檔)。")
        target_stocks = tw50_list
        
    elif scan_mode == "模式 C：全市場掃描 (⚠️ 高耗時)":
        st.warning("⚡ **溫和多執行緒防封鎖機制啟動**：採用 5 個平行 Worker，每檔隨機間隔 0.3 至 0.7 秒。全市場 2,157 檔預計耗時約 15 至 20 分鐘。")
        
        if os.path.exists("all_stocks.csv"):
            try:
                all_df = pd.read_csv("all_stocks.csv", dtype=str)
                code_col = [col for col in all_df.columns if '代碼' in str(col) or 'Ticker' in str(col)]
                name_col = [col for col in all_df.columns if '名稱' in str(col) or 'Name' in str(col)]
                
                if code_col:
                    target_stocks = all_df[code_col[0]].astype(str).tolist()
                    if name_col:
                        new_mapping = dict(zip(all_df[code_col[0]], all_df[name_col[0]]))
                        dynamic_name_mapping.update(new_mapping)
                    st.success(f"✅ 成功載入 {len(target_stocks)} 檔全市場股票代碼！")
                else:
                    st.error("❌ 找不到「代碼」欄位，請檢查 all_stocks.csv 格式。")
            except Exception as e:
                st.error(f"❌ 讀取檔案失敗: {e}")
        else:
            st.error("❌ 找不到 `all_stocks.csv`！目前僅啟動「54檔測試模式」。")
            target_stocks = tw50_list + ["2301", "2337", "2344", "2408", "3006"]
            
    elif scan_mode == "模式 D：上傳 CSV 檔案":
        uploaded_file = st.file_uploader("📂 請上傳 CSV 檔案", type=["csv"])
        st.info("💡 CSV 檔案必須包含名為「代碼」的欄位。")
        if uploaded_file is not None:
            try:
                uploaded_df = pd.read_csv(uploaded_file, dtype=str)
                code_col = [col for col in uploaded_df.columns if '代碼' in str(col) or 'Ticker' in str(col)]
                name_col = [col for col in uploaded_df.columns if '名稱' in str(col) or 'Name' in str(col)]
                
                if code_col:
                    target_stocks = uploaded_df[code_col[0]].astype(str).tolist()
                    if name_col:
                        new_mapping = dict(zip(uploaded_df[code_col[0]], uploaded_df[name_col[0]]))
                        dynamic_name_mapping.update(new_mapping)
                    st.success(f"成功載入 {len(target_stocks)} 檔股票代碼！")
                else:
                    st.error("❌ 找不到「代碼」欄位，請檢查 CSV 格式。")
            except Exception as e:
                st.error(f"❌ 讀取檔案失敗: {e}")

    st.divider()
    analyze_btn = st.button("🚀 開始批次掃描", use_container_width=True)

# ==========================================
# 5. 批次運算與持久化儲存核心 (動態模式版)
# ==========================================
if analyze_btn:
    if not target_stocks:
        st.warning("請至少選擇或提供一檔股票！")
    else:
        target_stocks = list(dict.fromkeys([str(x).strip() for x in target_stocks if str(x).strip() and str(x) != 'nan']))
        
        scan_date_str = datetime.today().strftime('%Y-%m-%d %H:%M')
        end_date_str = datetime.today().strftime('%Y-%m-%d')
        start_date_str = (datetime.today() - timedelta(days=120)).strftime('%Y-%m-%d')
        fetcher = TaiwanStockDataFetcher()

        def process_single_stock(stock_id):
            time.sleep(random.uniform(0.3, 0.7))
            stock_name = dynamic_name_mapping.get(stock_id, "")
            try:
                df = fetcher.get_stock_daily_data(stock_id, start_date_str, end_date_str)
                if not df.empty and len(df) >= 20:
                    engine = LinJiaYangEngine(df)
                    result_df = engine.run_analysis()
                    latest = result_df.iloc[-1]
                    score = int(latest['RecommendationScore'])
                    raw_signal = latest['Signal']
                    above_ma60 = latest['Above_MA60']
                    formatted_signal = get_formatted_signal(score, raw_signal, above_ma60)
                    
                    return {
                        '代碼': stock_id,
                        '名稱': stock_name if stock_name else "-",
                        '收盤價': latest['Close'],
                        '成交量': int(latest['Volume']),
                        '季線(MA60)': latest['MA60'],
                        '最新形態': formatted_signal,
                        '推薦分數': score,
                        '季線之上': "✅" if above_ma60 else "❌"
                    }
            except Exception:
                pass
            return None

        # 多執行緒併發處理
        all_results = []
        buy_signals = []
        sell_signals = []
        total_stocks = len(target_stocks)
        completed_count = 0

        progress_bar = st.progress(0)
        status_text = st.empty()

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(process_single_stock, sid) for sid in target_stocks]
            for future in as_completed(futures):
                res = future.result()
                completed_count += 1
                progress_bar.progress(completed_count / total_stocks)
                status_text.text(f"掃描進度 ({completed_count}/{total_stocks})...")
                if res:
                    all_results.append(res)
                    if res['推薦分數'] > 0:
                        buy_signals.append(res)
                    else:
                        sell_signals.append(res)

        # 【關鍵修復】：根據當前 scan_mode 動態更新正確的 Cache 與 Session State
        curr_csv = CACHE_FILES[scan_mode]
        curr_meta = META_FILES[scan_mode]
        
        # 1. 更新 metadata 時間戳記
        with open(curr_meta, 'w', encoding='utf-8') as mf:
            json.dump({"date": scan_date_str}, mf, ensure_ascii=False)

        # 2. 依據模式類型寫入快取與更新 Session State
        if "模式 A" in scan_mode or "模式 D" in scan_mode:
            pd.DataFrame(all_results).to_csv(curr_csv, index=False)
            st.session_state.scan_results[scan_mode] = {
                "type": "single",
                "data": all_results,
                "date": scan_date_str
            }
        else:
            buy_csv = curr_csv.replace(".csv", "_buy.csv")
            sell_csv = curr_csv.replace(".csv", "_sell.csv")
            pd.DataFrame(buy_signals).to_csv(buy_csv, index=False)
            pd.DataFrame(sell_signals).to_csv(sell_csv, index=False)
            pd.DataFrame(all_results).to_csv(curr_csv, index=False)
            st.session_state.scan_results[scan_mode] = {
                "type": "split",
                "buy": buy_signals,
                "sell": sell_signals,
                "date": scan_date_str
            }

        status_text.success("🎉 掃描完成！正在更新報表...")
        st.rerun()

# ==========================================
# 6. 說明折疊面板與結果呈現
# ==========================================
with st.expander("📖 林家洋技術分析型態與圖示說明 (戰術指南)", expanded=True):
    st.markdown("""
    - 🚀 **順勢強攻 (突破高點)**：季線之上的攻擊K線，已突破近 20 日波段高點。
      - **戰術意義**：主力帶量突破前方壓力區，最強勢的買進訊號，勝率與爆發力最高。
    - 🔥 **多頭反轉 (吞噬賣壓)**：季線之上的多頭吞噬，買盤強勢吃掉昨日賣壓。
      - **戰術意義**：多頭格局明確的回檔轉折契機。
    - 🔋 **多頭蓄勢 (蓄力中)**：季線之上的量縮內困型態。
      - **戰術意義**：多頭籌碼正在沉澱，屬於正面觀察區，等待帶量突破 20 日高點即可轉為攻擊。
    - ⏳ **弱勢反彈 (空頭壓制)**：季線之下的無量反彈或內困。
      - **戰術意義**：空頭趨勢中的逃命波或短線陷阱，上方均線壓制，絕對禁止追價。
    - 💣 **黑K吞噬 (逃命警示)**：出現黑K吞噬。
      - **戰術意義**：絕對的警戒與停損訊號。
    """)

# 【修正1】加上 '成交量': '{:,}' 來啟用千分位分隔符號
format_dict = {'收盤價': '{:.2f}', '成交量': '{:,}', '季線(MA60)': '{:.2f}', '推薦分數': '{} 分'}

# 【修正2】定義期望的欄位顯示順序
desired_cols = ['名稱', '收盤價', '成交量', '最新形態', '推薦分數', '季線(MA60)', '季線之上']

current_cache = st.session_state.scan_results.get(scan_mode, None)

if current_cache:
    scan_time = current_cache.get("date", "未知時間")
    st.info(f"📅 **本組數據掃描時間**：{scan_time} (已自動載入歷史快取)")
    
    if current_cache["type"] == "single":
        st.subheader(f"📋 {scan_mode.split('：')[0]} 掃描結果 (完整列出)")
        data_list = current_cache["data"]
        if data_list:
            df_out = pd.DataFrame(data_list).set_index('代碼')
            # 【修正3】強制套用欄位順序
            df_out = df_out[desired_cols]
            styled_out = df_out.style.format(format_dict).apply(highlight_signals, axis=1)
            st.dataframe(styled_out, use_container_width=True)
        else:
            st.info("尚無資料。")
    else:
        col1, col2 = st.columns(2)
        with col1:
            st.subheader(f"🟩 今日強勢買進與蓄勢訊號")
            buy_list = current_cache["buy"]
            if buy_list:
                df_buy = pd.DataFrame(buy_list).set_index('代碼')
                # 【修正3】強制套用欄位順序
                df_buy = df_buy[desired_cols]
                styled_buy = df_buy.style.format(format_dict).apply(highlight_signals, axis=1)
                st.dataframe(styled_buy, use_container_width=True)
            else:
                st.info("尚無強勢股票紀錄。")
                
        with col2:
            st.subheader(f"🟥 今日弱勢與警戒清單")
            sell_list = current_cache["sell"]
            if sell_list:
                df_sell = pd.DataFrame(sell_list).set_index('代碼')
                # 【修正3】強制套用欄位順序
                df_sell = df_sell[desired_cols]
                styled_sell = df_sell.style.format(format_dict).apply(highlight_signals, axis=1)
                st.dataframe(styled_sell, use_container_width=True)
else:
    st.warning("⚠️ 目前此模式尚無掃描紀錄，請點擊左側的「🚀 開始批次掃描」按鈕來產生報表。")
