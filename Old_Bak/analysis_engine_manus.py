import pandas as pd
import numpy as np
import pandas_ta as ta
import logging

# 設定日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class LinJiaYangEngine:
    """
    林家洋技術分析引擎
    核心邏輯：力竭原理、K線組合、趨勢位置、攻擊K線
    """
    def __init__(self, df):
        """
        df 必須包含 Open, High, Low, Close, Volume 欄位，索引為日期
        """
        self.df = df.copy()
        self._prepare_indicators()

    def _prepare_indicators(self):
        """計算基礎技術指標"""
        # 計算季線 (60MA) 作程多空分界
        self.df['MA60'] = self.df['Close'].rolling(window=60, min_periods=1).mean()
        # 計算實體大小與漲跌幅
        self.df['Body'] = self.df['Close'] - self.df['Open']
        self.df['Body_Abs'] = self.df['Body'].abs()
        self.df['Range'] = self.df['High'] - self.df['Low']
        self.df['Pct_Change'] = self.df['Close'].pct_change() * 100

    def is_bullish_engulfing(self, idx):
        """
        判斷是否為多頭吞噬 (Bullish Engulfing)
        條件：前一根是黑K，當前是紅K，且紅K實體完全包覆黑K實體
        """
        if idx < 1: return False
        prev = self.df.iloc[idx-1]
        curr = self.df.iloc[idx]
        
        cond1 = prev['Body'] < 0  # 前一根黑K
        cond2 = curr['Body'] > 0  # 當前紅K
        cond3 = (curr['Open'] <= prev['Close']) and (curr['Close'] >= prev['Open']) # 包覆實體
        return cond1 and cond2 and cond3

    def is_bearish_engulfing(self, idx):
        """
        判斷是否為黑K吞噬 (Bearish Engulfing)
        條件：前一根是紅K，當前是黑K，且黑K實體完全包覆紅K實體
        """
        if idx < 1: return False
        prev = self.df.iloc[idx-1]
        curr = self.df.iloc[idx]
        
        cond1 = prev['Body'] > 0  # 前一根紅K
        cond2 = curr['Body'] < 0  # 當前黑K
        cond3 = (curr['Open'] >= prev['Close']) and (curr['Close'] <= prev['Open']) # 包覆實體
        return cond1 and cond2 and cond3

    def is_harami(self, idx):
        """
        判斷是否為內困型態 (Harami)
        條件：前一根是大K，當前是小K且被包覆在前一根實體內，且成交量必須量縮
        """
        if idx < 1: return False
        prev = self.df.iloc[idx-1]
        curr = self.df.iloc[idx]
        
        cond1 = prev['Body_Abs'] > curr['Body_Abs'] * 2 # 前根實體顯著較大
        cond2 = (curr['High'] <= prev['High']) and (curr['Low'] >= prev['Low']) # 價格範圍在內
        
        # 新增：量能濾網 (內困必須伴隨量縮，否則為無效訊號)
        cond3 = curr['Volume'] <= prev['Volume'] 
        
        return cond1 and cond2 and cond3

    def is_attack_k(self, idx):
        """
        判斷是否為攻擊K線
        條件：長紅K (漲幅 > 3%) 且成交量顯著放大 (大於 5 日均量 1.5 倍)
        """
        if idx < 5: return False
        curr = self.df.iloc[idx]
        vol_ma5 = self.df['Volume'].iloc[idx-5:idx].mean()
        
        cond1 = curr['Pct_Change'] >= 3.0 # 漲幅夠大
        cond2 = curr['Body'] > 0          # 紅K
        cond3 = curr['Volume'] > vol_ma5 * 1.5 # 量增
        return cond1 and cond2 and cond3

    def calculate_recommendation_score(self, idx):
        """計算推薦指數"""
        if idx < 5:
            return 0
        
        curr = self.df.iloc[idx]
        signal = curr.get('Signal', '無')
        
        # --- 新增：內困型態的天花板鎖碼邏輯 ---
        # 如果是內困型態，直接切斷後續的乘法運算，強制壓低分數
        if signal == '內困型態':
            # 在季線之上給 30 分，季線之下給 15 分
            return 30 if curr['Close'] > curr['MA60'] else 15
            
        # 買進訊號強度
        buy_signal_strength = {
            '攻擊K線': 100,
            '多頭吞噬': 75,
        }.get(signal, 0)
        
        # 賣出訊號強度
        sell_signal_strength = {
            '黑K吞噬': -60,
        }.get(signal, 0)
        
        if buy_signal_strength == 0 and sell_signal_strength == 0:
            return 0
        
        if buy_signal_strength > 0:
            signal_strength = buy_signal_strength
            ma60_coefficient = 1.0 if curr['Close'] > curr['MA60'] else 0.5
        else:
            signal_strength = sell_signal_strength
            ma60_coefficient = 1.0 if curr['Close'] < curr['MA60'] else 0.5
        
        vol_ma5 = self.df['Volume'].iloc[idx-5:idx].mean()
        if curr['Volume'] > vol_ma5 * 2:
            vol_coefficient = 1.2
        elif curr['Volume'] > vol_ma5 * 1.5:
            vol_coefficient = 1.0
        else:
            vol_coefficient = 0.8
        
        score = signal_strength * ma60_coefficient * vol_coefficient
        
        if score > 0:
            return min(int(score), 120)
        else:
            return max(int(score), -120)

    def run_analysis(self):
        """執行全量分析"""
        signals = []
        scores = []
        
        for i in range(len(self.df)):
            sig = "無"
            if self.is_attack_k(i):
                sig = "攻擊K線"
            elif self.is_bullish_engulfing(i):
                sig = "多頭吞噬"
            elif self.is_harami(i):
                sig = "內困型態"
            
            if self.is_bearish_engulfing(i):
                sig = "黑K吞噬"
                
            signals.append(sig)
        
        self.df['Signal'] = signals
        
        for i in range(len(self.df)):
            score = self.calculate_recommendation_score(i)
            scores.append(score)
        
        self.df['RecommendationScore'] = scores
        self.df['Above_MA60'] = (self.df['Close'] > self.df['MA60']).fillna(False)
        return self.df

if __name__ == "__main__":
    try:
        data = pd.read_csv("2330_daily_data.csv", index_col='Date', parse_dates=True)
        engine = LinJiaYangEngine(data)
        result = engine.run_analysis()
        
        active_signals = result[result['Signal'] != "無"][['Close', 'Signal', 'Above_MA60']]
        print("\n--- 林家洋理論分析結果 (近期訊號) ---")
        print(active_signals.tail(10))
        
        result.to_csv("analysis_result_2330.csv")
        print("\n完整分析結果已儲存至 analysis_result_2330.csv")
    except FileNotFoundError:
        print("請先執行第一階段 data_integration.py 以產生測試數據。")