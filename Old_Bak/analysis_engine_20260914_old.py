import pandas as pd
import numpy as np
try:
    import pandas_ta as ta
except ImportError:
    ta = None
import logging

# 設定日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class LinJiaYangEngine:
    """
    林家洋技術分析引擎 (2026-09-10 v8.1 旗艦大底破繭版)
    核心邏輯：
    1. 力竭原理與K線組合 (多頭吞噬、黑K吞噬、內困型態)
    2. 【新增】40~60日長期整理出底突破 (Base Consolidation Breakout) 👑
    3. 下降慣性扭轉突破 (Downtrend Structure Break / CHoCH) ⚡
    4. 上升趨勢線跌破波段停利 (Upward Trendline Break) ⚠️
    5. 【新增】上方套牢巨量反壓濾網 (Overhead Supply Filter) 🚫
    6. 雙軌選股、首日強攻限制、季線乖離與波段漲幅硬性阻斷
    """
    def __init__(self, df):
        """
        df 必須包含 Open, High, Low, Close, Volume 欄位，索引為日期
        """
        self.df = df.copy()
        self._prepare_indicators()

    def _prepare_indicators(self):
        """計算基礎技術指標與實戰前置濾網指標"""
        # 計算均線群：月線 (MA20)、季線 (MA60)
        self.df['MA20'] = self.df['Close'].rolling(window=20, min_periods=1).mean()
        self.df['MA60'] = self.df['Close'].rolling(window=60, min_periods=1).mean()
        
        # --- 季線乖離率% ---
        self.df['MA60_Bias'] = ((self.df['Close'] - self.df['MA60']) / self.df['MA60'] * 100).round(2)
        
        # --- 均線糾結度% (月線與季線差距比例) ---
        self.df['MA_Diff_Pct'] = (abs(self.df['MA20'] - self.df['MA60']) / self.df['MA60'] * 100).round(2)
        
        # --- 近20日累積漲幅% (當日收盤價相較於近20日最低價之漲幅) ---
        self.df['Low20'] = self.df['Low'].rolling(window=20, min_periods=1).min()
        self.df['Gain_20D'] = ((self.df['Close'] - self.df['Low20']) / self.df['Low20'] * 100).round(2)
        
        # --- 5日均量 (Vol_MA5)、爆量比與成交金額 ---
        self.df['Vol_MA5'] = self.df['Volume'].rolling(window=5, min_periods=1).mean()
        self.df['Vol_MA5_Ratio'] = (self.df['Volume'] / self.df['Vol_MA5'].replace(0, 1)).round(2)
        self.df['Amount_100M'] = ((self.df['Close'] * self.df['Volume']) / 100000000).round(2)
        
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

    def is_base_consolidation_breakout(self, idx):
        """
        👑 【林家洋核心大底起漲理論】：40~60日長期整理破繭第一根 (Base Consolidation Breakout)
        前輩心法：「最好的股票還是要有長期整理，然後剛剛開始突破的股票」
        特徵：
        1. 時間維度：過去 40~60 個交易日處於打底整理狀態 (基期極低)。
        2. 空間維度：過去 40 日的高低點振幅狹窄 (箱型震盪 <= 22%)，波動充分收斂。
        3. 均線糾結：月線 (MA20) 與季線 (MA60) 差距 <= 4.0%，均線走平糾結纏繞。
        4. 出底第一根：當日實體紅K (>= 2.5%)，成交量放大至 5 日均量 1.8 倍以上 (且 >= 1,200 張)，
           收盤價實體帶量長紅突破過去 40 日的箱型整理平台最高點！
        """
        if idx < 40: return False
        curr = self.df.iloc[idx]
        vol_ma5 = self.df['Volume'].iloc[idx-5:idx].mean()

        # 基本流動性門檻 (>= 800張均量, >= 1200張當日量)
        vol_threshold_ma5 = 800 * 1000 if vol_ma5 > 100000 else 800
        vol_threshold_curr = 1200 * 1000 if curr['Volume'] > 100000 else 1200
        if vol_ma5 < vol_threshold_ma5 or curr['Volume'] < vol_threshold_curr:
            return False

        # 過去 40 天數據 (idx-40:idx)
        base_period = self.df.iloc[idx-40:idx]
        high_40 = base_period['High'].max()
        low_40 = base_period['Low'].min()

        # 1. 箱型振幅收斂檢驗 (振幅 <= 22%)
        box_amplitude = ((high_40 - low_40) / low_40 * 100) if low_40 > 0 else 999
        if box_amplitude > 22.0:
            return False

        # 2. 均線糾結檢驗 (月線與季線差距 <= 4.0%)
        ma_diff = curr['MA_Diff_Pct']
        if pd.isna(ma_diff) or ma_diff > 4.0:
            return False

        # 3. 當日突破條件：實體紅K、漲幅 >= 2.5%、成交量 >= 1.8倍均量、實體收過 40 日箱頂
        cond1 = curr['Pct_Change'] >= 2.5
        cond2 = curr['Body'] > 0
        cond3 = curr['Volume'] >= vol_ma5 * 1.8
        cond4 = curr['Close'] > high_40

        return cond1 and cond2 and cond3 and cond4

    def has_overhead_supply_wall(self, idx):
        """
        🚫 【上方套牢巨量反壓濾網 (Overhead Supply Wall)】
        防範像定穎投控這類：前波曾遭遇巨量崩跌，上方短期內存在龐大套牢密集區，反彈極易撞牆跳水。
        """
        if idx < 40: return False
        curr = self.df.iloc[idx]
        lookback = self.df.iloc[max(0, idx-60):idx]
        max_h = lookback['High'].max()
        min_l = lookback['Low'].min()
        
        # 檢驗前波是否曾有崩跌 > 22%
        total_drop = (max_h - min_l) / max_h * 100 if max_h > 0 else 0
        if total_drop > 22.0:
            half_idx = len(lookback) // 2
            early_high = lookback.iloc[:half_idx]['High'].max()
            # 若當前股價仍深陷在早期崩跌平台下方 10%~25%，且近期從最低點急拉超過 12%
            # 代表這是大跌後的深水區反彈，正逼近上方套牢密集區
            if curr['Close'] < early_high * 0.90 and curr['Gain_20D'] > 12.0:
                return True
        return False

    def is_downtrend_reversal_attack(self, idx):
        """
        ⚡ 【林家洋核心理論】：下降趨勢反轉進場點 (破底翻起漲先鋒)
        定義：趨勢向下，高點與低點皆傾向越來越低 (Lower Highs & Lower Lows)；
              此時伴隨高成交量實體突破前波反彈高點，打破空方慣性，為絕佳多方進場時機。
        """
        if idx < 20: return False
        curr = self.df.iloc[idx]
        vol_ma5 = self.df['Volume'].iloc[idx-5:idx].mean()

        # 基本流動性門檻 (>= 800張均量, >= 1200張當日量)
        vol_threshold_ma5 = 800 * 1000 if vol_ma5 > 100000 else 800
        vol_threshold_curr = 1200 * 1000 if curr['Volume'] > 100000 else 1200
        if vol_ma5 < vol_threshold_ma5 or curr['Volume'] < vol_threshold_curr:
            return False

        # 將過去20日切分為前半段 (idx-20:idx-6) 與 後半段 (idx-6:idx)
        part1 = self.df.iloc[idx-20:idx-6]
        part2 = self.df.iloc[idx-6:idx]

        h1, h2 = part1['High'].max(), part2['High'].max()
        l1, l2 = part1['Low'].min(), part2['Low'].min()

        # 判定下降趨勢慣性：高點越過越低 (Lower High) 且 低點破低 (Lower Low)
        is_downtrend = (h1 > h2) and (l1 > l2)
        if not is_downtrend:
            return False

        # 排除連漲力竭出貨K
        past_5d = self.df.iloc[idx-5:idx]
        recent_surges = (past_5d['Pct_Change'] >= 3.0).sum()
        min_5d = past_5d['Low'].min()
        gain_5d = ((curr['Close'] - min_5d) / min_5d * 100) if min_5d > 0 else 0
        if recent_surges >= 2 or gain_5d > 20.0:
            return False

        # 當日發動條件：實體紅K、漲幅 >= 2.5%、伴隨高成交量 (放量1.5倍以上)、實體收盤價突破後段反彈高點 h2
        cond1 = curr['Pct_Change'] >= 2.5
        cond2 = curr['Body'] > 0
        cond3 = curr['Volume'] > vol_ma5 * 1.5
        cond4 = curr['Close'] > h2

        return cond1 and cond2 and cond3 and cond4

    def is_attack_k(self, idx):
        """判斷是否為攻擊K線 (2026-09-10 升級：納入上方套牢反壓過濾)"""
        if idx < 20: return False
        curr = self.df.iloc[idx]
        vol_ma5 = self.df['Volume'].iloc[idx-5:idx].mean()
        
        # --- 雜魚與一日遊爆量硬性過濾 (Anti-Junk / Anti-Spike) ---
        vol_threshold_ma5 = 800 * 1000 if vol_ma5 > 100000 else 800
        vol_threshold_curr = 1200 * 1000 if curr['Volume'] > 100000 else 1200
        if vol_ma5 < vol_threshold_ma5 or curr['Volume'] < vol_threshold_curr:
            return False
            
        # 門檻2：當日成交金額需 >= 6000萬台幣
        turnover = (curr['Close'] * curr['Volume']) if curr['Volume'] > 100000 else (curr['Close'] * curr['Volume'] * 1000)
        if turnover < 60000000:
            return False
            
        # 門檻3：排除突兀暴衝一日遊 (單日暴增4倍但平時無量)
        vol_spike_limit = 1500 * 1000 if vol_ma5 > 100000 else 1500
        if curr['Volume'] > vol_ma5 * 4.0 and vol_ma5 < vol_spike_limit:
            return False
            
        # --- 首日突破限制 (排除連漲力竭出貨K) ---
        past_5d = self.df.iloc[idx-5:idx]
        recent_surges = (past_5d['Pct_Change'] >= 3.0).sum()
        min_5d = past_5d['Low'].min()
        gain_5d = ((curr['Close'] - min_5d) / min_5d * 100) if min_5d > 0 else 0
        if recent_surges >= 2 or gain_5d > 20.0:
            return False

        # --- 【新增】：排除上方沉重套牢巨峰 (防範弱勢反彈撞牆假突破) ---
        if self.has_overhead_supply_wall(idx):
            return False

        cond1 = curr['Pct_Change'] >= 3.0 # 漲幅夠大
        cond2 = curr['Body'] > 0          # 紅K
        cond3 = curr['Volume'] > vol_ma5 * 1.5 # 溫和放量
        
        # 突破近20日高點
        swing_high_20 = self.df['High'].iloc[idx-20:idx].max()
        cond4 = curr['Close'] > swing_high_20
        return cond1 and cond2 and cond3 and cond4

    def is_uptrend_line_break(self, idx):
        """
        ⚠️ 【林家洋核心理論】：上升趨勢波段停利點
        定義：上升趨勢之低點連成線（上升趨勢線）被跌破，或跌破前波次低點，多方墊高慣性終結
        """
        if idx < 20: return False
        curr = self.df.iloc[idx]
        prev = self.df.iloc[idx-1]

        part1 = self.df.iloc[idx-20:idx-6]
        part2 = self.df.iloc[idx-6:idx]

        l1 = part1['Low'].min()
        t1 = part1['Low'].idxmin()
        l2 = part2['Low'].min()
        t2 = part2['Low'].idxmin()

        idx_t1 = self.df.index.get_loc(t1)
        idx_t2 = self.df.index.get_loc(t2)

        # 判定是否具備上升趨勢特徵：低點越來越高 (Higher Lows)
        if l2 <= l1 or idx_t2 <= idx_t1:
            return False

        slope = (l2 - l1) / (idx_t2 - idx_t1)
        trendline_val = l2 + slope * (idx - idx_t2)

        # 當日收盤實體跌破上升趨勢線，或收盤實體跌破前波次低點 l2 (階梯防守點)
        break_line = (curr['Close'] < trendline_val) and (prev['Close'] >= (trendline_val - slope))
        break_prev_low = (curr['Close'] < l2) and (prev['Close'] >= l2)
        is_weak = (curr['Body'] < 0) or (curr['Pct_Change'] < 0)

        return (break_line or break_prev_low) and is_weak

    def calculate_recommendation_score(self, idx):
        """計算推薦指數 (v8.1 大底突破 120 分頂級權重)"""
        if idx < 5: return 0
        curr = self.df.iloc[idx]
        signal = curr.get('Signal', '無')
        
        if signal == '內困型態':
            return 30 if curr['Close'] > curr['MA60'] else 15
            
        buy_signal_strength = {
            '大底破繭 (長期整理突破)': 120, # 👑 最高評分
            '攻擊K線 (扭轉突破)': 100,
            '攻擊K線': 100, 
            '多頭吞噬': 75
        }.get(signal, 0)
        sell_signal_strength = {
            '黑K吞噬': -60,
            '跌破上升趨勢線': -60
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
        return min(int(score), 120) if score > 0 else max(int(score), -120)

    def run_analysis(self):
        """執行全量分析"""
        signals, scores = [], []
        for i in range(len(self.df)):
            sig = "無"
            # 多方進場訊號優先檢核 (大底破繭最高優先)
            if self.is_base_consolidation_breakout(i):
                sig = "大底破繭 (長期整理突破)"
            elif self.is_downtrend_reversal_attack(i):
                sig = "攻擊K線 (扭轉突破)"
            elif self.is_attack_k(i):
                sig = "攻擊K線"
            elif self.is_bullish_engulfing(i):
                sig = "多頭吞噬"
            elif self.is_harami(i):
                sig = "內困型態"
            
            # 風控與出場賣訊優先權最高 (若同時出現，以出場避險為第一優先)
            if self.is_bearish_engulfing(i):
                sig = "黑K吞噬"
            elif self.is_uptrend_line_break(i) and sig != "黑K吞噬":
                sig = "跌破上升趨勢線"

            signals.append(sig)
        
        self.df['Signal'] = signals
        for i in range(len(self.df)):
            scores.append(self.calculate_recommendation_score(i))
            
        self.df['RecommendationScore'] = scores
        self.df['Above_MA60'] = (self.df['Close'] > self.df['MA60']).fillna(False)
        return self.df
