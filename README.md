# 📈 林家洋技術分析 - 全市場掃描雷達

這是一個基於 Python 與 Streamlit 開發的輕量化台股技術分析儀表板。系統將技術分析名師林家洋的核心理論（力竭原理、K線組合、趨勢位置、攻擊K線）進行量化，並提供高反差的深色主題介面，讓投資者能快速掃描全市場或自訂關注清單，捕捉買賣訊號。

## ✨ 核心功能 (Features)

*   **四大掃描模式**：支援「自訂關注清單」、「台灣50成分股」、「全市場掃描」以及「自訂 CSV 檔案上傳」。
*   **技術型態自動判讀**：
    *   🚀 順勢強攻 (攻擊K線)
    *   🔥 多頭反轉 (多頭吞噬)
    *   🔋 多頭蓄勢 (內困型態)
    *   💣 逃命警示 (黑K吞噬)
*   **推薦分數系統**：綜合 K線型態、季線 (MA60) 位置與成交量爆發力，計算 -120 至 120 的量化分數。
*   **高反差 Dark Mode UI**：專為長時間盯盤設計的深色主題與視覺化警告標籤。
*   **非同步快取機制**：內建防封鎖冷卻與自動存檔功能，避免頻繁呼叫 API。

## 🛠️ 環境建置與安裝 (Installation)

1. **複製專案 (Clone the repository)**
   `git clone https://github.com/您的帳號/您的專案名稱.git`
   `cd 您的專案名稱`

2. **建立並啟動虛擬環境**
   `python3 -m venv .venv`
   `source .venv/bin/activate`

3. **安裝依賴套件**
   `pip install streamlit pandas pandas-ta yfinance requests`

## 🚀 執行應用程式 (Usage)

在虛擬環境啟動的狀態下，於終端機輸入以下指令：
`streamlit run app3.py`
系統會自動開啟瀏覽器並連線至 http://localhost:8501。

## 📁 專案架構 (Project Structure)
*   `app3.py`：Streamlit 網頁主程式與 UI 渲染。
*   `analysis_engine.py`：林家洋技術分析核心邏輯與計分引擎。
*   `scan_orchestrator.py`：股票資料抓取器（優先使用 yfinance，並具備 TWSE 備援機制）。

## ⚠️ 免責聲明 (Disclaimer)
本專案提供的技術分析訊號與分數僅供學術研究與程式開發參考，**不構成任何投資建議**。實際交易請自行承擔風險。
