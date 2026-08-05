import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import json
import os
import time

# 匯入您的技術分析模組
from analysis_engine import LinJiaYangEngine
from scan_orchestrator import TaiwanStockDataFetcher

# ==========================================
# 1. 系統設定、台灣 50 精確名單與自動存檔
# ==========================================
st.set_page_config(page_title="林家洋 - 批次掃描雷達 (階段一)", layout="wide")

CONFIG_FILE = "user_settings.json"

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

# 新增：全新的行動圖示評級系統
def get_signal_icon(score, signal):
    if signal == '黑K吞噬' or score < 0: return "💣"
    elif score >= 100: return "🚀"
    elif score >= 75: return "🔥"
    elif signal == '內困型態' or score > 0: return "⏳"
    else: return "➖"

# ==========================================
# 2. 注入自訂 CSS 與 4 色上色邏輯
# ==========================================
custom_css = """
<style>
    .stApp { background-color: #0B1120; color: #FFFFFF; }
    h1, h2, h3 { color: #FB923C !important; font-weight: bold !important; }
    h1 { font-size: 2.2rem !important; }
    section[data-testid="stSidebar"] { background-color: #0f172a; }

/* 放大資料表右上角的懸停工具列 (包含搜尋、全螢幕、下載) */
    [data-testid="stElementToolbar"] {
        transform: scale(1.85); /* 修改為 1.85 倍 */
        transform-origin: right top; /* 確保放大時向左下展開 */
        margin-top: -25px !important; /* 強制向上提拉 25 像素 (可依需求修改此數值) */
        padding-bottom: 5px; /* 增加底部緩衝，避免圖示邊緣被裁切 */
        z-index: 9999; /* 確保圖示放大後永遠在最上層，不被表格擋住 */
    }
    
    /* 稍微調整工具列按鈕的間距，避免放大後擠在一起 */
    [data-testid="stElementToolbar"] button {
        margin-left: 5px;
    }
</style>
"""
st.markdown(custom_css, unsafe_allow_html=True)

def highlight_signals(row):
    signal = row['最新形態']
    try:
        score = int(str(row['推薦指數']).split(' ')[0])
    except:
        score = 0
        
    if signal == '攻擊K線': color = '#22D3EE'
    elif signal == '多頭吞噬': color = '#4ADE80'
    elif signal == '內困型態': color = '#FDE047'
    elif signal == '黑K吞噬' or score < 0: color = '#EF4444'
    elif score > 0: color = '#A7F3D0'
    else: color = '#FFFFFF'
        
    return [f'color: {color}; font-weight: bold'] * len(row)

# ==========================================
# 3. 網頁 UI 與側邊欄 (支援名稱顯示)
# ==========================================
st.title("📡 林家洋技術分析 - 批次掃描雷達")

tw50_list = list(TW50_MAPPING.keys())
saved_stocks = load_saved_stocks()

with st.sidebar:
    st.header("⚙️ 掃描模式設定")
    scan_mode = st.radio(
        "請選擇掃描範圍：",
        ("模式 A：自訂關注清單", "模式 B：台灣50成分股掃描")
    )
    
    target_stocks = []
    if scan_mode == "模式 A：自訂關注清單":
        target_stocks = st.multiselect(
            "🔍 請選擇或輸入股票代碼 (自動記憶)",
            options=list(set(tw50_list + saved_stocks)),
            default=saved_stocks,
            format_func=lambda x: f"{x} {TW50_MAPPING.get(x, '')}".strip()
        )
        if target_stocks != saved_stocks:
            save_stocks(target_stocks)
    else:
        st.info(f"系統將自動掃描台灣 50 大權值股 (共 {len(tw50_list)} 檔)。")
        target_stocks = tw50_list
        
    st.divider()
    analyze_btn = st.button("🚀 開始批次掃描", use_container_width=True)

# ==========================================
# 4. 批次運算核心迴圈
# ==========================================
if analyze_btn:
    if not target_stocks:
        st.warning("請至少選擇一檔股票！")
    else:
        mode_a_results = [] 
        buy_signals = []
        sell_signals = []
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        end_date_str = datetime.today().strftime('%Y-%m-%d')
        start_date_str = (datetime.today() - timedelta(days=120)).strftime('%Y-%m-%d')
        fetcher = TaiwanStockDataFetcher()
        
        for i, stock_id in enumerate(target_stocks):
            stock_name = TW50_MAPPING.get(stock_id, "未知名稱")
            status_text.text(f"正在掃描 ({i+1}/{len(target_stocks)}): {stock_id} {stock_name} ...")
            
            try:
                time.sleep(0.2)
                df = fetcher.get_stock_daily_data(stock_id, start_date_str, end_date_str)
                if not df.empty and len(df) >= 20:
                    engine = LinJiaYangEngine(df)
                    result_df = engine.run_analysis()
                    
                    latest = result_df.iloc[-1]
                    score = latest['RecommendationScore']
                    raw_signal = latest['Signal']
                    signal = raw_signal if str(raw_signal).strip() and str(raw_signal) != 'nan' else '無訊號'
                    
                    # 使用新的圖示評級系統
                    score_with_icon = f"{score} 分 {get_signal_icon(score, signal)}"
                    
                    stock_info = {
                        '代碼': stock_id,
                        '名稱': stock_name,
                        '收盤價': latest['Close'],
                        '成交量': int(latest['Volume']),
                        '季線(MA60)': latest['MA60'],
                        '最新形態': signal,
                        '推薦指數': score_with_icon,
                        '季線之上': latest['Above_MA60']
                    }
                    
                    if scan_mode == "模式 A：自訂關注清單":
                        mode_a_results.append(stock_info)
                    else:
                        if score > 0 or signal in ['攻擊K線', '多頭吞噬']:
                            buy_signals.append(stock_info)
                        elif score < 0 or signal == '黑K吞噬':
                            sell_signals.append(stock_info)
                            
            except Exception as e:
                pass
            
            progress_bar.progress((i + 1) / len(target_stocks))
            
        status_text.text("掃描完成！")
        
        # ==========================================
        # 5. 說明折疊面板與報表呈現
        # ==========================================
        # 新增：說明折疊面板 (放置於報表正上方)
        with st.expander("📖 林家洋技術分析型態與圖示說明", expanded=True):
            st.markdown("""
            - 🚀 **順勢強攻 (100分以上)**：季線之上的攻擊K線，主力表態帶量上漲。
            - 🔥 **多頭反轉 (75~99分)**：季線之上的多頭吞噬，買盤強勢吃掉昨日賣壓。
            - ⏳ **內困與逆勢反彈 (15~74分)**：包含量縮內困(15-30分)，以及季線之下的弱勢攻擊/吞噬(37-60分)。趨勢尚未明朗或遭逢空頭壓制，需耐心觀望。
            - ➖ **無明顯型態 (0分)**：毫無動靜，持續觀望。
            - 💣 **逃命警示 (負分)**：出現黑K吞噬，絕對的逃命與警戒訊號。
            *註：基礎分數會依據「股價是否在季線(MA60)之上」與「成交量放大倍數」進行動態權重加減。*
            """)

        format_dict = {'收盤價': '{:.2f}', '季線(MA60)': '{:.2f}'}

        if scan_mode == "模式 A：自訂關注清單":
            st.subheader("📋 自訂清單掃描結果 (完整列出)")
            if mode_a_results:
                df_a = pd.DataFrame(mode_a_results).set_index('代碼')
                styled_df_a = df_a.style.format(format_dict).apply(highlight_signals, axis=1)
                st.dataframe(styled_df_a, use_container_width=True)
            else:
                st.info("無成功獲取的股票數據。")
                
        else:
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("🟩 今日強勢買進訊號 (台灣50)")
                if buy_signals:
                    df_buy = pd.DataFrame(buy_signals).set_index('代碼')
                    styled_buy = df_buy.style.format(format_dict).apply(highlight_signals, axis=1)
                    st.dataframe(styled_buy, use_container_width=True)
                else:
                    st.info("今日無強勢股票。")
                    
            with col2:
                st.subheader("🟥 今日弱勢警示清單 (台灣50)")
                if sell_signals:
                    df_sell = pd.DataFrame(sell_signals).set_index('代碼')
                    styled_sell = df_sell.style.format(format_dict).apply(highlight_signals, axis=1)
                    st.dataframe(styled_sell, use_container_width=True)
                else:
                    st.info("今日無弱勢股票。")