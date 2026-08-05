import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import plotly.graph_objects as go  # 新增：Plotly 圖表套件

# 匯入您提供的模組
from analysis_engine import LinJiaYangEngine
from scan_orchestrator import TaiwanStockDataFetcher

# 設定網頁標題與寬度佈局
st.set_page_config(page_title="林家洋技術分析系統", layout="wide")

# 1. 注入自訂 CSS
custom_css = """
<style>
    .stApp { background-color: #0B1120; color: #FFFFFF; }
    h1, h2, h3 { color: #FB923C !important; font-weight: bold !important; }
    h1 { font-size: 2.5rem !important; }
    h2 { font-size: 1.8rem !important; }
    section[data-testid="stSidebar"] { background-color: #0f172a; }
    
    
</style>
"""
st.markdown(custom_css, unsafe_allow_html=True)

# 2. 定義動態卡片色彩與 HTML 渲染函式
def get_signal_color(score, signal):
    if score < 0 or signal == '黑K吞噬':
        return '#EF4444'
    elif score > 0 or signal in ['攻擊K線', '多頭吞噬']:
        return '#4ADE80'
    elif signal == '內困型態':
        return '#FDE047'
    else:
        return '#38BDF8'

def render_custom_card(label, value, text_color="#38BDF8"):
    card_html = f"""
    <div style="background-color: #0f172a; padding: 16px; border-radius: 8px; border: 1px solid #1e293b; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.2);">
        <p style="color: #94a3b8; font-size: 0.95rem; margin-bottom: 6px; font-weight: 500;">{label}</p>
        <p style="color: {text_color}; font-size: 1.8rem; font-weight: bold; margin: 0;">{value}</p>
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)

# 3. 定義 DataFrame 的上色邏輯 (已更新為對應中文欄位)
def highlight_signals(row):
    signal = row['最新形態']
    score = row['推薦指數']
    
    if signal == '黑K吞噬' or score < 0:
        return ['color: #EF4444; font-weight: bold'] * len(row)
    elif score > 0 or signal in ['攻擊K線', '多頭吞噬']:
        return ['color: #4ADE80; font-weight: bold'] * len(row)
    elif signal == '內困型態' or row['季線之上'] == True:
        return ['color: #FDE047'] * len(row)
    else:
        return ['color: #FFFFFF'] * len(row)

# 4. 網頁 UI 佈局
st.title("📈 林家洋技術分析引擎")
st.markdown("基於力竭原理、K線組合、趨勢位置與攻擊K線的自動化判讀系統。")

with st.sidebar:
    st.header("參數設定")
    stock_id = st.text_input("股票代號 (如: 2330)", value="2330")
    default_start = datetime.today() - timedelta(days=180)
    start_date = st.date_input("開始日期", value=default_start)
    end_date = st.date_input("結束日期", value=datetime.today())
    sort_option = st.selectbox("歷史資料排序", options=["最新日期在最上 (預設)", "最舊日期在最上"])
    analyze_btn = st.button("🚀 開始分析", use_container_width=True)

# 5. 執行分析邏輯
if analyze_btn:
    with st.spinner("正在呼叫資料源並運算技術指標..."):
        try:
            fetcher = TaiwanStockDataFetcher()
            df = fetcher.get_stock_daily_data(stock_id, start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'))
            
            if df.empty or len(df) < 20:
                st.error("⚠️ 獲取數據失敗，或數據量不足20天，無法計算季線。")
            else:
                engine = LinJiaYangEngine(df)
                result_df = engine.run_analysis()
                
                latest = result_df.iloc[-1]
                latest_date = result_df.index[-1].strftime('%Y-%m-%d')
                
                # --- 區塊 A：最新狀態卡片 ---
                st.subheader(f"📊 最新狀態掃描 ({latest_date})")
                col1, col2, col3 = st.columns(3)
                status_color = get_signal_color(latest['RecommendationScore'], latest['Signal'])
                with col1: render_custom_card("收盤價", f"{latest['Close']:.2f}", "#38BDF8")
                with col2: render_custom_card("最新形態", latest['Signal'], status_color)
                with col3: render_custom_card("推薦指數", f"{latest['RecommendationScore']} 分", status_color)
                
                # --- 區塊 B：動態 K 線與季線圖表 ---
                st.subheader("📈 趨勢與 K 線動態圖表")
                fig = go.Figure()
                fig.add_trace(go.Candlestick(
                    x=result_df.index, open=result_df['Open'], high=result_df['High'],
                    low=result_df['Low'], close=result_df['Close'], name='K線',
                    increasing_line_color='#EF4444', decreasing_line_color='#4ADE80'
                ))
                fig.add_trace(go.Scatter(
                    x=result_df.index, y=result_df['MA60'], mode='lines',
                    line=dict(color='#FDE047', width=1.5), name='MA60 季線'
                ))
                fig.update_layout(
                    template='plotly_dark', plot_bgcolor='#0f172a', paper_bgcolor='#0f172a',
                    margin=dict(l=20, r=20, t=30, b=20), xaxis_rangeslider_visible=False, hovermode='x unified'
                )
                st.plotly_chart(fig, use_container_width=True)
                
                # --- 區塊 C：歷史數據表 ---
                st.subheader("📝 近期訊號歷史")
                display_df = result_df[['Close', 'Volume', 'MA60', 'Signal', 'RecommendationScore', 'Above_MA60']].copy()
                
                # 將英文欄位重新命名為中文
                display_df = display_df.rename(columns={
                    'Close': '收盤價',
                    'Volume': '成交量',
                    'MA60': '季線(MA60)',
                    'Signal': '最新形態',
                    'RecommendationScore': '推薦指數',
                    'Above_MA60': '季線之上'
                })
                display_df.index.name = '日期'
                
                recent_30_df = display_df.tail(30).copy()
                if sort_option == "最新日期在最上 (預設)":
                    recent_30_df = recent_30_df.sort_index(ascending=False)
                else:
                    recent_30_df = recent_30_df.sort_index(ascending=True)
                
                recent_30_df.index = recent_30_df.index.strftime('%Y-%m-%d')
                styled_df = recent_30_df.style.apply(highlight_signals, axis=1)
                st.dataframe(styled_df, use_container_width=True, height=400)
                
                st.success("分析完成！")
        except Exception as e:
            st.error(f"系統發生錯誤: {e}")