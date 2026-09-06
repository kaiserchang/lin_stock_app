import pandas as pd
import numpy as np
import pandas_ta as ta
import logging

# 設定日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class LinJiaYangEngine:
    """
    林家洋技術分析引擎 (2026-08-24 升級版)
    核心邏輯：力竭原理、K線組合、趨勢位置、攻擊K線、波段漲幅與乖離濾網
    """
    def __init__(self, df):
        """
        df 必須包含 Open, High, Low, Close, Volume 欄位，索引為日期
        """
        self.df = df.copy()
        self._prepare_indicators()

    def _prepare_indicators(self):
        """計算基礎技術指標與實戰前置濾網指標"""
        # 計算季線 (60MA) 作為多空分界
        self.df['MA60'] = self.df['Close'].rolling(window=60, min_periods=1).mean()
        
        # --- 2026-08-24 新增：季線乖離率% ---
        self.df['MA60_Bias'] = ((self.df['Close'] - self.df['MA60']) / self.df['MA60'] * 100).round(2)
        
        # --- 2026-08-24 新增：近20日累積漲幅% (當日收盤價相較於近20日最低價之漲幅) ---
        self.df['Low20'] = self.df['Low'].rolling(window=20, min_periods=1).min()
        self.df['Gain_20D'] = ((self.df['Close'] - self.df['Low20']) / self.df['Low20'] * 100).round(2)
        
        # 計算實體大小與漲跌幅
        self.df['Body'] = self.df['Close'] - self.df['Open']
        self.df['Body_Abs'] = self.df['Body'].abs()
        self.df['Range'] = self.df['High'] - self.df['Low']
        self.df['Pct_Change'] = self.df['Close'].pct_change() * 100

    def is_bullish_engulfing(self, idx):
        """判斷是否為多頭吞噬 (Bullish Engulfing)"""
        if idx < 1: return False
        prev = self.df.iloc[idx-1]
        curr = self.df.iloc[idx]
        
        cond1 = prev['Body'] < 0  # 前一根黑K
        cond2 = curr['Body'] > 0  # 當前紅K
        cond3 = (curr['Open'] <= prev['Close']) and (curr['Close'] >= prev['Open']) # 包覆實體
        return cond1 and cond2 and cond3

    def is_bearish_engulfing(self, idx):
        """判斷是否為黑K吞噬 (Bearish Engulfing)"""
        if idx < 1: return False
        prev = self.df.iloc[idx-1]
        curr = self.df.iloc[idx]
        
        cond1 = prev['Body'] > 0  # 前一根紅K
        cond2 = curr['Body'] < 0  # 當前黑K
        cond3 = (curr['Open'] >= prev['Close']) and (curr['Close'] <= prev['Open']) # 包覆實體
        return cond1 and cond2 and cond3

    def is_harami(self, idx):
        """判斷是否為內困型態 (Harami)"""
        if idx < 1: return False
        prev = self.df.iloc[idx-1]
        curr = self.df.iloc[idx]
        
        cond1 = prev['Body_Abs'] > curr['Body_Abs'] * 2 # 前根實體顯著較大
        cond2 = (curr['High'] <= prev['High']) and (curr['Low'] >= prev['Low']) # 價格範圍在內
        cond3 = curr['Volume'] <= prev['Volume'] # 量能量縮
        return cond1 and cond2 and cond3

    def is_attack_k(self, idx):
        """判斷是否為攻擊K線"""
        if idx < 20: return False
        curr = self.df.iloc[idx]
        vol_ma5 = self.df['Volume'].iloc[idx-5:idx].mean()
        
        cond1 = curr['Pct_Change'] >= 3.0 # 漲幅夠大
        cond2 = curr['Body'] > 0          # 紅K
        cond3 = curr['Volume'] > vol_ma5 * 1.5 # 量增
        
        # 突破近20日高點
        swing_high_20 = self.df['High'].iloc[idx-20:idx].max()
        cond4 = curr['Close'] > swing_high_20
        return cond1 and cond2 and cond3 and cond4

    def calculate_recommendation_score(self, idx):
        """計算推薦指數"""
        if idx < 5: return 0
        curr = self.df.iloc[idx]
        signal = curr.get('Signal', '無')
        
        if signal == '內困型態':
            return 30 if curr['Close'] > curr['MA60'] else 15
            
        buy_signal_strength = {'攻擊K線': 100, '多頭吞噬': 75}.get(signal, 0)
        sell_signal_strength = {'黑K吞噬': -60}.get(signal, 0)
        
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
        return min(int(score), 120) if score > 0 else max(int(score), -120)

    def run_analysis(self):
        """執行全量分析"""
        signals, scores = [], []
        for i in range(len(self.df)):
            sig = "無"
            if self.is_attack_k(i): sig = "攻擊K線"
            elif self.is_bullish_engulfing(i): sig = "多頭吞噬"
            elif self.is_harami(i): sig = "內困型態"
            
            if self.is_bearish_engulfing(i): sig = "黑K吞噬"
            signals.append(sig)
        
        self.df['Signal'] = signals
        for i in range(len(self.df)):
            scores.append(self.calculate_recommendation_score(i))
            
        self.df['RecommendationScore'] = scores
        self.df['Above_MA60'] = (self.df['Close'] > self.df['MA60']).fillna(False)
        return self.df