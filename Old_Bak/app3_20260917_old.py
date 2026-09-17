import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import zoneinfo  # 👈 新增這行，用於載入系統時區資料庫
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
st.set_page_config(page_title="林家洋 - 全市場雷達 (v8.4 旗艦版)", layout="wide")

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
    "0050": "元大台灣50", "1101": "台泥", "1216": "統一", "1301": "台塑", "1303": "南亞", "1326": "台化",
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
    if signal == '大底破繭 (長期整理突破)':
        return "👑 大底破繭 (長期整理突破)"
    elif signal == '跌破上升趨勢線':
        return "⚠️ 跌破趨勢 (波段停利)"
    elif signal == '黑K吞噬' or score < 0:
        return "💣 黑K吞噬 (逃命警示)"
    elif signal == '攻擊K線 (扭轉突破)':
        return "⚡ 順勢強攻 (扭轉突破)"
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
        
    if "大底破繭" in status: color = '#FACC15' # 👑 金黃色頂級高亮
    elif "順勢強攻" in status or "扭轉突破" in status: color = '#22D3EE'
    elif "多頭反轉" in status: color = '#4ADE80'
    elif "多頭蓄勢" in status: color = '#38BDF8'
    elif "跌破趨勢" in status: color = '#FB923C'
    elif "弱勢反彈" in status: color = '#FDE047'
    elif "黑K吞噬" in status or score < 0: color = '#EF4444'
    elif score > 0: color = '#A7F3D0'
    else: color = '#FFFFFF'
        
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
                        "date": meta_data.get("date", "未知日期"),
                        "market_data_date": meta_data.get("market_data_date", "歷史交易日"),
                        "source": "cache",  # 標記為快取載入
                        "failed_logs": meta_data.get("failed_logs", []),
                        "market_status": meta_data.get("market_status", None)
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
                        "date": meta_data.get("date", "未知日期"),
                        "market_data_date": meta_data.get("market_data_date", "歷史交易日"),
                        "source": "cache",  # 標記為快取載入
                        "failed_logs": meta_data.get("failed_logs", []),
                        "market_status": meta_data.get("market_status", None)
                    }
            except Exception as e:
                pass


@st.cache_data(ttl=1800)
def get_market_regime():
    """獲取台股加權指數 (^TWII) 月線與季線狀態，判定大盤環境燈號 (2026-09-05 新增)"""
    try:
        import yfinance as yf
        tz_taipei = zoneinfo.ZoneInfo("Asia/Taipei")
        end_d = (datetime.now(tz_taipei) + timedelta(days=1)).strftime('%Y-%m-%d')
        start_d = (datetime.now(tz_taipei) - timedelta(days=400)).strftime('%Y-%m-%d')
        df_twii = yf.download("^TWII", start=start_d, end=end_d, progress=False)
        if df_twii.empty: return None
        if isinstance(df_twii.columns, pd.MultiIndex):
            df_twii.columns = df_twii.columns.get_level_values(0)
        df_twii['MA20'] = df_twii['Close'].rolling(window=20).mean()
        df_twii['MA60'] = df_twii['Close'].rolling(window=60).mean()
        latest = df_twii.iloc[-1]
        prev = df_twii.iloc[-2] if len(df_twii) >= 2 else latest
        
        close = float(latest['Close'])
        prev_close = float(prev['Close'])
        ma20 = float(latest['MA20'])
        ma60 = float(latest['MA60'])
        pct = float(((close - prev_close) / prev_close) * 100)
        
        if close < ma20 or pct <= -1.0:
            status = "BEAR_DEFENSE"
            if close < ma20 and pct <= -1.0:
                cond_desc = f"實體跌破 20MA月線 ({ma20:,.1f}點) 且單日回檔"
            elif close < ma20:
                cond_desc = f"處於 20MA月線 ({ma20:,.1f}點) 之下"
            else:
                cond_desc = f"單日跌幅達 {pct:.2f}%"
            msg = f"加權指數收 {close:,.1f}點 (漲跌 {pct:+.2f}%)，{cond_desc}。提醒提高防禦意識、謹慎控管持股成數並嚴格執行停損停利，切忌盲目追高；多空行情皆有獲利良機，精選抗跌強勢標的順勢操作！"
        elif close >= ma20 and close >= ma60:
            status = "BULL_NORMAL"
            msg = f"加權指數收 {close:,.1f}點 (漲跌 {pct:+.2f}%)，穩居月線 ({ma20:,.1f}點) 與季線 ({ma60:,.1f}點) 之上，多頭順風，可依準則順勢佈局！"
        else:
            status = "NEUTRAL"
            msg = f"加權指數收 {close:,.1f}點，處於月線與季線震盪整理區，建議縮小部位、嚴選低乖離起漲標的。"
            
        return {'close': close, 'ma20': ma20, 'ma60': ma60, 'pct': pct, 'status': status, 'msg': msg}
    except Exception as e:
        return None

# ==========================================
# 4. 網頁 UI 與 4 大模式切換
# ==========================================
st.title("📡 林家洋技術分析 - 全市場掃描雷達")
st.markdown("##### 🚀 **v8.4 旗艦版** `(2026-09-16 最新升級)` ｜ 🏛️ 三大法人籌碼矩陣・外資隔日沖警示・高持股微妙空間雙數張獲利體系")


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
    with st.expander("✨ 系統版本：v8.4 旗艦版 (2026-09-16)", expanded=False):
        st.markdown("""
        **【v8.4 核心重大升級摘要】**
        * 🏛️ **三大法人全自動日報串接**：自動同步證交所 (TWSE T86) 與櫃買中心 (TPEx)，取得外資/投信/自營商進出張數。
        * 🔥 **土洋合擊波段強攻**：投信連買且外資加碼，推薦評分額外 **+20 分**。
        * 💎 **投信波段鎖碼**：投信連買或持股創高，推薦評分額外 **+15 分**。
        * ⚠️ **外資隔日沖疑慮標籤**：前日外資脈衝暴買佔比 > 35%，自動標註警語「T+1 衝高 +5%~+8% 堅決鎖利」。
        * ⚡ **高法人重倉雙數張鐵律**：法人持股 >= 20% 標的（如強茂 9/16 實戰），第 1 張衝高鎖利、第 2 張成本保本博漲停！
        * 📊 **CSV 報表匯出擴充**：全自動納入三大法人狀態與進出張數。
        """)
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
        st.warning("⚡ **溫和多執行緒防封鎖機制啟動**：採用 5 個平行 Worker，每檔隨機間隔 0.3 至 0.7 秒。台股全市場掃描預計耗時約 15 至 20 分鐘。")
        
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

st.sidebar.markdown("---")

# ==========================================
# 5. 批次運算與持久化儲存核心 (斷點續傳升級版 - 側邊欄修正)
# ==========================================
st.sidebar.info("""
⏰ **盤後資料取得時程指南：**
* 🕒 **15:30 後**：可取得當日收盤與外資/投信買賣超資料 (流量)。
* 🕔 **17:00 後**：證交所結算完畢，可取得當日最完整外資持股總數與持股比率 (存量)。
""")
analyze_btn = st.sidebar.button("🚀 開始全新批次掃描", width="stretch")

# 👇 偵測備份檔，提供下載與接續按鈕 (改為側邊欄垂直排列) 👇
resume_btn = False
if os.path.exists("backup_temp_results.csv"):
    st.sidebar.warning("⚠️ 系統偵測到前次未完成的掃描備份！")
    resume_btn = st.sidebar.button("▶️ 接續未完成的掃描 (斷點續傳)", width="stretch")
    with open("backup_temp_results.csv", "rb") as f:
        st.sidebar.download_button("📥 先下載目前已備份進度 (CSV)", f, file_name="中斷備份檔.csv", width="stretch")

st.sidebar.markdown("---") 
if st.sidebar.button("🧹 強制清除系統快取", width="stretch"):
    st.cache_data.clear()
    st.session_state.scan_results = {}
    
    # 徹底刪除硬碟上的所有快取與備份檔案
    for mode, csv_f in CACHE_FILES.items():
        for f_path in [csv_f, csv_f.replace(".csv", "_buy.csv"), csv_f.replace(".csv", "_sell.csv")]:
            if os.path.exists(f_path):
                try: os.remove(f_path)
                except: pass
    for mode, meta_f in META_FILES.items():
        if os.path.exists(meta_f):
            try: os.remove(meta_f)
            except: pass
    if os.path.exists("backup_temp_results.csv"):
        try: os.remove("backup_temp_results.csv")
        except: pass
        
    st.sidebar.success("✅ 所有本機與記憶體快取已徹底清除！")
    time.sleep(0.5)
    st.rerun()

# 定義承接資料的容器
all_results = []
buy_signals = []
sell_signals = []

if analyze_btn or resume_btn:
    st.session_state.scan_results.pop(scan_mode, None) 
    
    if not target_stocks:
        st.warning("請至少選擇或提供一檔股票！")
    else:
        target_stocks = list(dict.fromkeys([str(x).strip() for x in target_stocks if str(x).strip() and str(x) != 'nan']))

        # 🌟 斷點續傳核心邏輯 🌟
        if resume_btn and os.path.exists("backup_temp_results.csv"):
            try:
                # 讀取備份檔
                backup_df = pd.read_csv("backup_temp_results.csv", dtype={'代碼': str})
                processed_sids = backup_df['代碼'].astype(str).tolist()

                # 恢復已經算好的資料到容器中
                all_results = backup_df.to_dict('records')
                for res in all_results:
                    # 容錯處理：若是舊版無分數資料預設為 0
                    if int(res.get('推薦分數', 0)) > 0:
                        buy_signals.append(res)
                    else:
                        sell_signals.append(res)

                # 關鍵：把已經做過的股票從目標清單中「剃除」
                target_stocks = [s for s in target_stocks if s not in processed_sids]
                st.info(f"🔄 讀取備份成功！跳過已完成的 {len(processed_sids)} 檔，剩下 {len(target_stocks)} 檔即將接續掃描...")
            except Exception as e:
                st.error(f"備份檔讀取失敗，請考慮使用全新掃描: {e}")
        elif analyze_btn:
            st.info("🔄 舊有快取已清除，正在重新向市場獲取最新資料...")
            # 若是全新掃描，順便清空舊備份，避免下次混淆
            if os.path.exists("backup_temp_results.csv"):
                os.remove("backup_temp_results.csv")

        # 👈 以下接續原有的時區設定與 fetcher 宣告
        tz_taipei = zoneinfo.ZoneInfo("Asia/Taipei")
        scan_date_str = datetime.now(tz_taipei).strftime('%Y-%m-%d %H:%M')
        # 🌟 關鍵修復：yfinance 的 end 參數為 Exclusive (不包含該日)，因此必須 +1 天才能正確抓取今日最新收盤資料！
        end_date_str = (datetime.now(tz_taipei) + timedelta(days=1)).strftime('%Y-%m-%d')
        start_date_str = (datetime.now(tz_taipei) - timedelta(days=400)).strftime('%Y-%m-%d')
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
                    
                    market_date_val = str(df.index[-1]).split(' ')[0] if len(df) > 0 else '未知'
                    inst_tag = latest.get('InstitutionalTag', '➖ 一般籌碼')
                    foreign_lots = int(round(latest.get('Foreign_Buy', 0) / 1000.0)) if 'Foreign_Buy' in latest else 0
                    trust_lots = int(round(latest.get('Trust_Buy', 0) / 1000.0)) if 'Trust_Buy' in latest else 0

                    # 外資持有總張數
                    f_hold_shares = latest.get('Foreign_Hold_Shares', None)
                    if f_hold_shares is not None and not pd.isna(f_hold_shares) and str(f_hold_shares).strip() not in ['None', '', '-']:
                        try: foreign_hold_lots = int(round(float(f_hold_shares) / 1000.0))
                        except: foreign_hold_lots = '-'
                    else:
                        foreign_hold_lots = '-'

                    # 外資持股率%
                    f_pct_val = latest.get('Foreign_Hold_Pct', None)
                    if f_pct_val is not None and not pd.isna(f_pct_val) and str(f_pct_val).strip() not in ['None', '', '-']:
                        try: f_pct_str = f"{float(f_pct_val):.2f}%"
                        except: f_pct_str = "-"
                    else:
                        f_pct_str = "-"

                    # 🌟 方案 A：投信波段累積鎖碼張數（近 60 日推估）
                    t_accum_val = latest.get('Trust_60D_Accum', None)
                    if t_accum_val is not None and not pd.isna(t_accum_val) and str(t_accum_val).strip() not in ['None', '', '-']:
                        try:
                            trust_accum_val = int(round(float(t_accum_val)))
                        except:
                            trust_accum_val = 0
                    else:
                        trust_accum_val = trust_lots if trust_lots != 0 else 0

                    return {
                        '代碼': stock_id,
                        '名稱': stock_name if stock_name else "-",
                        '收盤價': latest['Close'],
                        '成交量': int(latest['Volume']),
                        '三大法人狀態': inst_tag,
                        '外資買賣(張)': foreign_lots,
                        '投信買賣(張)': trust_lots,
                        '外資持股(張)': foreign_hold_lots,
                        '外資持股率%': f_pct_str,
                        '投信波段累積鎖碼張數（近 60 日推估）': trust_accum_val,
                        '最新形態': formatted_signal,
                        '推薦分數': score,
                        '季線(MA60)': latest['MA60'],
                        '季線之上': "✅" if above_ma60 else "❌",
                        '資料來源': df['Data_Source'].iloc[-1] if 'Data_Source' in df.columns else '未知',
                        '行情日期': market_date_val
                    }
            except Exception as e:
                # 捕捉具體錯誤原因回傳
                return {"代碼": stock_id, "名稱": stock_name, "狀態": "失敗", "原因": f"程式執行錯誤: {str(e)}"}

            # 若執行到最後都沒有成功進到 return 字典，代表沒抓到資料
            return {"代碼": stock_id, "名稱": stock_name, "狀態": "失敗", "原因": "API無回傳資料或日線不足20天"}

        # 多執行緒併發處理
        all_results = []
        buy_signals = []
        sell_signals = []
        
        import concurrent.futures
        import pandas as pd

        total_stocks = len(target_stocks)
        completed_count = 0
        BATCH_SIZE = 100  # 設定每 100 檔執行一次暫時存檔

        # 建立前端 UI 佔位符
        progress_bar = st.progress(0)
        status_text = st.empty()
        save_status = st.empty()
        failed_logs = []
        
        # 啟動多執行緒掃描
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(process_single_stock, sid): sid for sid in target_stocks}
            
            for future in concurrent.futures.as_completed(futures):
                sid = futures[future]
                try:
                    res = future.result()
                    if res:
                        # 🌟 破除黑箱：將失敗紀錄與成功紀錄分流
                        if res.get("狀態") == "失敗":
                            failed_logs.append(res)
                        else:
                            all_results.append(res)
                            if res.get('推薦分數', 0) > 0:
                                buy_signals.append(res)
                            else:
                                sell_signals.append(res)
                except Exception as e:
                    failed_logs.append({"代碼": sid, "狀態": "失敗", "原因": f"執行緒崩潰: {e}"})
                    print(f"股票 {sid} 處理失敗: {e}")

                # 1. 即時更新進度條 (防止雲端判定閒置而強制中斷)
                completed_count += 1
                progress_bar.progress(completed_count / total_stocks)
                status_text.text(f"🚀 掃描進度 ({completed_count} / {total_stocks})...")

                # 2. 分批存檔機制 (Checkpoint)
                if completed_count % BATCH_SIZE == 0 or completed_count == total_stocks:
                    if all_results:
                        pd.DataFrame(all_results).to_csv("backup_temp_results.csv", index=False, encoding="utf-8-sig")
                        save_status.success(f"💾 系統存檔點建立：已備份至第 {completed_count} 筆。")

        # 迴圈結束，清除備份提示字樣
        save_status.empty() 

        # 👇 新增：順利完成全部掃描後，自動刪除備份檔，避免下次再跳出警告 👇
        if completed_count == total_stocks and os.path.exists("backup_temp_results.csv"):
            os.remove("backup_temp_results.csv")

        # 【關鍵修復】：根據當前 scan_mode 動態更新正確的 Cache 與 Session State
        curr_csv = CACHE_FILES[scan_mode]
        curr_meta = META_FILES[scan_mode]
        
        # 1. 取得本次掃描獲取之市場行情基準日
        latest_market_date = "未知交易日"
        if all_results and '行情日期' in all_results[0]:
            latest_market_date = all_results[0]['行情日期']
        elif all_results and len(all_results) > 0:
            latest_market_date = "最新盤後結算"

        # 2. 更新 metadata 時間戳記、數據基準日與失敗日誌
        with open(curr_meta, 'w', encoding='utf-8') as mf:
            scan_market_status = get_market_regime()
            json.dump({
                "date": scan_date_str,
                "market_data_date": latest_market_date,
                "failed_logs": failed_logs,
                "market_status": scan_market_status
            }, mf, ensure_ascii=False)

        # 3. 依據模式類型寫入快取與更新 Session State (標記 source='fresh')
        if "模式 A" in scan_mode or "模式 D" in scan_mode:
            pd.DataFrame(all_results).to_csv(curr_csv, index=False, encoding="utf-8-sig")
            st.session_state.scan_results[scan_mode] = {
                "type": "single",
                "data": all_results,
                "date": scan_date_str,
                "market_data_date": latest_market_date,
                "source": "fresh",  # 🌟 剛掃描完成標記為全新即時
                "failed_logs": failed_logs,
                "market_status": scan_market_status
            }
        else:
            buy_df_path = curr_csv.replace(".csv", "_buy.csv")
            sell_df_path = curr_csv.replace(".csv", "_sell.csv")
            pd.DataFrame(all_results).to_csv(curr_csv, index=False, encoding="utf-8-sig")
            pd.DataFrame(buy_signals).to_csv(buy_df_path, index=False, encoding="utf-8-sig")
            pd.DataFrame(sell_signals).to_csv(sell_df_path, index=False, encoding="utf-8-sig")
            
            st.session_state.scan_results[scan_mode] = {
                "type": "split",
                "buy": buy_signals,
                "sell": sell_signals,
                "date": scan_date_str,
                "market_data_date": latest_market_date,
                "source": "fresh",  # 🌟 剛掃描完成標記為全新即時
                "failed_logs": failed_logs,
                "market_status": scan_market_status
            }

        status_text.success("🎉 掃描完成！正在更新報表...")
        st.rerun()

# ==========================================
# 6. 說明折疊面板與結果呈現
# ==========================================
with st.expander("📖 林家洋技術分析型態、TOP 5 前置濾網與四大防線戰術指南 (2026 v8.3 實戰週線與破億防護旗艦版)", expanded=True):
    st.markdown('''
    ### 🎯 一、 核心型態判讀與實戰戰術指引 (含林家洋大底破繭與多週期趨勢風控)
    * 👑 **大底破繭 (長期整理突破)**：40～60 天以上長期橫盤打底（振幅 <= 22%）、月線與季線緊密糾結（<= 4.0%）後，首度帶量長紅突破箱頂第一根！
      * **林家洋前輩核心精髓**：**「最好的股票還是要有長期整理，然後剛剛開始突破的股票」**。基期極低、籌碼經數月沉澱洗淨，上方無近期套牢反壓，為波段勝率與期望值最高的頂級起漲點！
      * 🌟 **v8.3 剛性門檻**：必須同時滿足**「單日成交金額 >= 1.0 億元台幣（破億門檻）」**、**「排除上方 250 天歷史套牢巨量反壓」**與**「週線走平翻揚」**，徹底排除如華經這類未破億之歷史炒作妖股！
    * 🚫 **上方套牢巨量反壓過濾 (250D)**：前波 250 個交易日（1 整年）內若曾自歷史高點崩跌 > 28% 且上方存在密集歷史套牢山頭，一票否決短線深水區反彈假突破（徹底排除如華經、定穎投控這類高空套牢陷阱）。
    * 📈 **週K線宏觀趨勢過濾 (Weekly Trend Filter)**：大週期定方向，小週期找買點！凡最新週K收在週 5MA 之下且週 5MA 下彎，或近 4 週自高點急挫 > 8%（如華碩自 1030 見頂下殺），日K出現的任何多頭吞噬一律視為「弱勢反彈」，嚴禁追價！
    * 🚀⚡ **順勢強攻 (突破高點 / 扭轉突破)**：季線之上的攻擊K線，已突破近 20 日高點，或在下降趨勢中出現結構扭轉。
      * **戰術定位**：主力表態發動訊號。
      * **林家洋核心買點精髓 (扭轉突破)**：
        * ✅ **空方慣性破壞 (破底翻起漲點)**：當股價處於下降趨勢（高點越來越低、低點越來越低），若出現**「伴隨高成交量實體突破前波反彈高點」**，代表空頭結構被徹底瓦解，為多方絕佳起漲轉折進場點！
        * ✅ **首日橫盤突破**：近 20 日累積漲幅 <= 15%、季線乖離 <= 12% 之「底部/平台橫盤 10 天以上突破第一根」，勝率最高。
        * ⚠️ **力竭警戒**：若 20 日漲幅已 > 25% 或季線乖離 > 18%，屬高檔力竭出貨風險區，**嚴禁追高**。
        * 🚫 **雜魚與一日遊排除**：5 日均量需 >= 800 張，當日成交量需 >= 1,200 張，且**成交金額必須 >= 1.0 億元**；排除平時無量、單日突兀暴衝 4 倍之隔日沖雜魚股。
      * **防守停損點**：以 **發動日攻擊K最低點 (Low)** 為唯一基準線 (破線果斷停損)。
    * 🔥 **多頭反轉 (吞噬賣壓)**：季線之上的多頭吞噬，紅K實體完全包覆前日黑K (60至90 分)。
      * **戰術定位**：**高勝率起漲轉折訊號 (大數據實證 T+5 勝率達 70.3%)**。回檔洗盤完畢後的轉折點，買點安全且不易隔日跳水！(需經週線未走空與破億金額過濾，如強茂、頎邦)。
    * 🔋 **多頭蓄勢 (蓄力中)**：季線之上的量縮內困型態 (母子線，30 分)。
      * **戰術定位**：大漲後的籌碼沉澱洗盤，主力並未出貨。耐心等待帶量突破母K高點再行追擊。
    * ⚠️ **跌破趨勢 (波段停利)**：上升趨勢中，股價收盤實體跌破波段拉回低點連線（上升趨勢線），或跌破前波次低點。
      * **戰術定位**：**林家洋核心停利精髓**。多頭上升慣性（低點墊高）被跌破，代表多頭動能告終，為波段最佳獲利了結點！
    * ⏳ **弱勢反彈 (空頭壓制)**：季線之下的無量反彈或內困 (15 分)，或週線走空下之日線假反彈。
      * **戰術定位**：空頭趨勢逃命波，上方均線下彎反壓沉重，**多單絕對禁止進場**。
    * 💣 **黑K吞噬 (逃命警示)**：長黑K實體完全包覆前日紅K (負分)。
      * **戰術定位**：觸發第一道/第三道風控防線，多頭防守潰敗，部位果斷停損或停利結案。

    ---

    ### 🛡️ 二、 大盤環境與風險控管 (Market Regime & Risk Control)
    * ⚠️ **偏弱震盪／防守提醒**：當台股加權指數 (^TWII) 收盤 **跌破 20MA 月線**，或 **單日長黑重挫 > 1.0%**。
      * **戰術動作**：**提醒提高防禦意識、控管部位成數**。多空行情皆有獲利機會，此時更應注重選股不選市，嚴格落實防守防線與停損停利，避免過度槓桿與盲目追高！
    * 🟢 **多頭順風**：指數穩居月線 (20MA) 與季線 (60MA) 之上，順勢操作，落實雙軌選股。

    ---

    ### 🛡️ 三、 實戰買進前置 6 大濾網 (TOP 5 雙軌決選模型)
    1. 🛑 **短線暴衝與力竭過濾**：近 20 日累積漲幅 > 25.0% 或 短線 5 日急漲 > 20.0% ➔ **一票否決**！
    2. 📏 **季線乖離過濾**：季線正乖離率 > 18.0% ➔ **一票否決** (黃金區間為 +1.0% 至 +12.0%)。
    3. 🌊 **族群共振加權**：同產業有 2 檔以上同時表態者優先 (TOP 1至TOP 3 必備條件)。
    4. 📊 **剛性流動性與破億門檻**：5 日均量需 >= 800 張，當日成交量需 >= 1,200 張，**成交金額必須 >= 1.0 億元台幣 (未破億一票否決)**！
    5. 🚫 **上方 250 天歷史巨量套牢過濾**：過去 1 年內曾自歷史高點大跌逾 28% 且上方存在巨量套牢山頭者 ➔ **一票否決** (排除華經這類妖股)。
    6. 📈 **週K線宏觀趨勢共振過濾**：週 5MA 走平翻揚、打出週線 Higher Low 雙底者優先 (如強茂、頎邦)；週線高檔下彎走空者 ➔ **一票否決** (排除華碩)。
    7. ⏰ **T+1 分級掛單深度**：09:30 後以「限價 ROD 單」掛單，50元以下掛 1/2 處，50至100元掛 2/3至3/4 處，百元以上考慮零股。

    ---

    ### 🛡️ 四、 實戰持股四大防線 (v3.1 有狀態出場與防主力洗盤紀律)
    1. 🛑 **第一道防線：發動點停損 (-3.5% 停損線)** ➔ **以 13:10 尾盤收盤價判定**，實體跌破發動日攻擊K最低點，無條件停損結案。**嚴禁預掛盤中窄幅條件觸價單**，避免被主力開盤 09:05 下影線洗在阿呆谷（鴻準實戰教訓）！
    2. 📉 **第二道防線：季線 MA60 終極防線** ➔ 實體跌破 60MA 季線，波段趨勢破壞，停損結案。
    3. 💰 **第三道防線：雙數張階梯移動保本與上升趨勢線防守** ➔ 第 1 張衝高達 +5%~+8% 獲利落袋，**第 2 張「同時」將防守底線上移至「買進平均成本價（保本）」**，絕不允許賺錢單變賠錢單！
    4. ⏳ **第四道防線：5 日無動能換股 (T+5 硬性執行)** ➔ 納入滿 5 天且損益在 +-2.0% 停滯、分數歸零，**第 5~6 天必須在平盤附近主動換股結案**，嚴禁以未破停損為由盲目續抱，徹底釋放資金！

    ---

    ### 🔥 五、 實戰下單 4 大鐵律
    1. 🌊 **雙軌平衡配置**：結合低乖離轉折（強茂、頎邦）與首日強攻，寧缺毋濫。
    2. ⏰ **避開開盤追高與洗盤**：09:00 至 09:30 觀察隔日沖賣壓消化情況，一律於 09:30 至 10:30 以「限價 ROD 單」掛單。
    3. 🚫 **嚴禁預掛盤中窄幅觸價單**：停損停利一律以 13:10 收盤實體定多空，不被盤中下影線甩轎。
    4. 🛑 **破攻擊K低點必砍**：收盤若實體跌破發動日最低價，無條件停損結案，絕不凹單、絕不把短線變存股！
    ''')

# 【修正1】加上 '成交量': '{:,}' 來啟用千分位分隔符號
# 【防呆容錯格式化函式】：徹底解決包含 '未知' 或字串時拋出 ValueError: Cannot specify ',' with 's' 的問題
def fmt_price(x):
    try:
        if pd.isna(x) or str(x).strip() in ['未知', '-', 'None', '']: return '-'
        return f"{float(x):.2f}"
    except (ValueError, TypeError):
        return str(x)

def fmt_volume(x):
    try:
        if pd.isna(x) or str(x).strip() in ['未知', '-', 'None', '']: return '-'
        return f"{int(float(x)):,}"
    except (ValueError, TypeError):
        return str(x)

def fmt_lots(x):
    try:
        if pd.isna(x) or str(x).strip() in ['未知', '-', 'None', '']: return '-'
        val = int(round(float(x)))
        return "0" if val == 0 else f"{val:+,}"
    except (ValueError, TypeError):
        return str(x)

def fmt_score(x):
    try:
        if pd.isna(x) or str(x).strip() in ['未知', '-', 'None', '']: return '-'
        return f"{int(float(x))} 分"
    except (ValueError, TypeError):
        return str(x)

format_dict = {
    '收盤價': fmt_price,
    '成交量': fmt_volume,
    '外資買賣(張)': fmt_lots,
    '投信買賣(張)': fmt_lots,
    '外資持股(張)': fmt_volume,
    '外資持股率%': lambda x: str(x) if x is not None and not pd.isna(x) and str(x).strip() != '' else '-',
    '投信波段累積鎖碼張數（近 60 日推估）': fmt_lots,
    '季線(MA60)': fmt_price,
    '推薦分數': fmt_score
}

# 【修正2】定義期望的欄位顯示順序 (完整呈現外資買賣、投信買賣、外資持股張數、外資持股率%與投信波段累積鎖碼張數（近 60 日推估）)
desired_cols = ['名稱', '收盤價', '成交量', '最新形態', '推薦分數', '三大法人狀態', '外資買賣(張)', '投信買賣(張)', '外資持股(張)', '外資持股率%', '投信波段累積鎖碼張數（近 60 日推估）', '季線(MA60)', '季線之上', '資料來源']

current_cache = st.session_state.scan_results.get(scan_mode, None)

# 👇 破除黑箱：從各模式專屬的保險箱提取失敗日誌 👇
if current_cache and current_cache.get("failed_logs"):
    with st.expander(f"⚠️ 共有 {len(current_cache['failed_logs'])} 檔股票掃描失敗 (點擊展開查看原因)", expanded=False):
        st.dataframe(pd.DataFrame(current_cache['failed_logs']), width="stretch")

if current_cache:
    scan_time = current_cache.get("date", "未知時間")
    # 新增這行，將 2026-08-10 19:33 轉換為安全的 2026-08-10_1933
    safe_date = scan_time.replace(":", "").replace(" ", "_")
        # --- 2026-09-05 新增：與本次掃描時間 100% 同步之大盤環境燈號 ---
    if current_cache and current_cache.get("market_status"):
        m_stat = current_cache["market_status"]
        if m_stat.get("status") == "BEAR_DEFENSE":
            msg_text = m_stat.get('msg', '')
            # 相容清理舊快取中的極端用語
            msg_text = msg_text.replace("！系統啟動「空頭避險開關」，強烈建議今日 100% 空手觀望，強制關閉 TOP 5 追價買單！", "。提醒提高防禦意識、謹慎控管部位成數並嚴設停損，切忌盲目追高；多空皆有獲利機會，精選抗跌強勢標的！")
            msg_text = msg_text.replace("系統啟動「空頭避險開關」，強烈建議今日 100% 空手觀望，強制關閉 TOP 5 追價買單！", "提醒提高防禦意識、謹慎控管部位成數並嚴設停損，切忌盲目追高；多空皆有獲利機會，精選抗跌強勢標的！")
            st.warning(f"⚠️ **【大盤環境提醒：震盪偏弱・審慎操作】** {msg_text}")
        elif m_stat.get("status") == "BULL_NORMAL":
            st.success(f"🟢 **【大盤環境：多頭順風】** {m_stat.get('msg')}")
        else:
            st.warning(f"🟡 **【大盤環境：震盪整理】** {m_stat.get('msg')}")

    is_fresh = current_cache.get("source") == "fresh"
    market_data_date = current_cache.get("market_data_date", "最新交易日")
    
    if is_fresh:
        st.success(f"✨ **【全新即時掃描完成】** ｜ 執行掃描時間：`{scan_time}` ｜ 市場數據基準日：`{market_data_date} 盤後結算`")
    else:
        st.info(f"📁 **【已自動載入歷史快取】** ｜ 存檔掃描時間：`{scan_time}` ｜ 市場數據基準日：`{market_data_date} 盤後結算`")
    
    if current_cache["type"] == "single":
        st.subheader(f"📋 {scan_mode.split('：')[0]} 掃描結果 (完整列出)")
        st.caption("💡 **法人籌碼數據權威說明**：依主管機關法規，『外資持股張數』與『外資持股率%』每日由證交所/櫃買中心依法公布；『投信』官方每日僅公告買賣超張數，未公開持股庫存總量，本系統特別實裝『投信波段累積鎖碼張數（近 60 日推估）』，以近一季（60 個交易日）波段實質淨買賣超滾動累計，精準量化投信最新季底作帳與認養動能！")
        data_list = current_cache["data"]
        if data_list:
            df_out = pd.DataFrame(data_list).set_index('代碼')
            # 兼容舊快取欄位遷移
            if '投信持股(張)' in df_out.columns and '投信波段累積鎖碼張數（近 60 日推估）' not in df_out.columns:
                df_out['投信波段累積鎖碼張數（近 60 日推估）'] = df_out['投信持股(張)'].replace('官方未公開*', '0')
            # 【修正3】強制套用欄位順序
            df_out = df_out.reindex(columns=desired_cols, fill_value='未知')
            styled_out = df_out.style.format(format_dict).apply(highlight_signals, axis=1)
            st.dataframe(styled_out, width="stretch")

            # 👇 新增以下程式碼：自訂下載按鈕
            csv_data = df_out.to_csv(index=True).encode('utf-8-sig')
            st.download_button(
                label="📥 下載完整清單 (CSV)",
                data=csv_data,
                file_name=f"{scan_mode.split('：')[0]}_完整紀錄_{safe_date}.csv",
                mime="text/csv",
                key="download_single"
            )
            
        else:
            st.info("尚無資料。")
    else:
        col1, col2 = st.columns(2)
        with col1:
            st.subheader(f"🟩 今日強勢買進與蓄勢訊號")
            buy_list = current_cache["buy"]
            if buy_list:
                df_buy = pd.DataFrame(buy_list).set_index('代碼')
                if '投信持股(張)' in df_buy.columns and '投信波段累積鎖碼張數（近 60 日推估）' not in df_buy.columns:
                    df_buy['投信波段累積鎖碼張數（近 60 日推估）'] = df_buy['投信持股(張)'].replace('官方未公開*', '0')
                # 【修正3】強制套用欄位順序
                df_buy = df_buy.reindex(columns=desired_cols, fill_value='未知')
                styled_buy = df_buy.style.format(format_dict).apply(highlight_signals, axis=1)
                st.dataframe(styled_buy, width="stretch")

                # 👇 新增以下程式碼：買進清單專屬下載
                csv_buy = df_buy.to_csv(index=True).encode('utf-8-sig')
                st.download_button(
                    label="🟩 下載強勢買進清單 (CSV)",
                    data=csv_buy,
                    file_name=f"推薦買進_{safe_date}.csv",
                    mime="text/csv",
                    key="download_buy"
                )
            
            else:
                st.info("尚無強勢股票紀錄。")
                
        with col2:
            st.subheader(f"🟥 今日弱勢與警戒清單")
            sell_list = current_cache["sell"]
            if sell_list:
                df_sell = pd.DataFrame(sell_list).set_index('代碼')
                if '投信持股(張)' in df_sell.columns and '投信波段累積鎖碼張數（近 60 日推估）' not in df_sell.columns:
                    df_sell['投信波段累積鎖碼張數（近 60 日推估）'] = df_sell['投信持股(張)'].replace('官方未公開*', '0')
                # 【修正3】強制套用欄位順序
                df_sell = df_sell.reindex(columns=desired_cols, fill_value='未知')
                styled_sell = df_sell.style.format(format_dict).apply(highlight_signals, axis=1)
                st.dataframe(styled_sell, width="stretch")

                # 👇 新增以下程式碼：警戒清單專屬下載
                csv_sell = df_sell.to_csv(index=True).encode('utf-8-sig')
                st.download_button(
                    label="🟥 下載弱勢警戒清單 (CSV)",
                    data=csv_sell,
                    file_name=f"警戒賣出_{safe_date}.csv",
                    mime="text/csv",
                    key="download_sell"
                )
                
else:
    st.warning("⚠️ 目前此模式尚無掃描紀錄，請點擊左側的「🚀 開始批次掃描」按鈕來產生報表。")
