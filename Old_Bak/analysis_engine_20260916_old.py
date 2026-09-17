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
    林家洋技術分析引擎 (2026-09-14 v8.3 實戰週線宏觀與破億防護艦隊版)
    核心邏輯：
    1. 力竭原理與K線組合 (多頭吞噬、黑K吞噬、內困型態)
    2. 【嚴選】40~60日長期整理出底突破 (Base Consolidation Breakout) 👑 (需破億+無上方套牢+週線走平翻揚)
    3. 下降慣性扭轉突破 (Downtrend Structure Break / CHoCH) ⚡ (破億門檻)
    4. 上升趨勢線跌破波段停利 (Upward Trendline Break) ⚠️
    5. 【升級】上方套牢巨量反壓濾網 (Overhead Supply Filter 250D) 🚫 (擴大回溯至1年，抓出歷史炒作妖股)
    6. 【新增】週K線多週期宏觀趨勢濾網 (Weekly Trend Filter) 📈 (排除週線空方段高檔死貓跳)
    7. 剛性破億門檻 (單日成交金額 >= 1.0 億元台幣，杜絕邊緣投機股)
    """
    def __init__(self, df):
        """
        df 必須包含 Open, High, Low, Close, Volume 欄位，索引為日期
        """
        self.df = df.copy()
        self._prepare_indicators()
        self._prepare_weekly_indicators()

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
        
        # 兼容股數 (Shares) 與張數 (Lots)：若最大成交量 > 10萬，視為股數；否則視為張數 (乘以1000)
        max_vol = self.df['Volume'].max() if len(self.df) > 0 else 0
        vol_multiplier = 1.0 if max_vol > 100000 else 1000.0
        self.df['Amount_100M'] = ((self.df['Close'] * self.df['Volume'] * vol_multiplier) / 100000000.0).round(2)
        
        # 計算實體大小與漲跌幅
        self.df['Body'] = self.df['Close'] - self.df['Open']
        self.df['Body_Abs'] = self.df['Body'].abs()
        self.df['Range'] = self.df['High'] - self.df['Low']
        self.df['Pct_Change'] = self.df['Close'].pct_change() * 100

    def _prepare_weekly_indicators(self):
        """
        【新增】：合成真實週K線數據並計算週線均線與趨勢
        """
        try:
            df_temp = self.df.copy()
            if not isinstance(df_temp.index, pd.DatetimeIndex):
                df_temp.index = pd.to_datetime(df_temp.index)
            
            # 以每週五為基準重採樣為週K線
            self.weekly_df = df_temp.resample('W-FRI').agg({
                'Open': 'first',
                'High': 'max',
                'Low': 'min',
                'Close': 'last',
                'Volume': 'sum'
            }).dropna()

            if len(self.weekly_df) >= 5:
                self.weekly_df['W_MA5'] = self.weekly_df['Close'].rolling(window=5, min_periods=1).mean()
                self.weekly_df['W_MA10'] = self.weekly_df['Close'].rolling(window=10, min_periods=1).mean()
                self.weekly_df['W_MA20'] = self.weekly_df['Close'].rolling(window=20, min_periods=1).mean()
                # 計算週 5MA 斜率 (本週相較前週)
                self.weekly_df['W_MA5_Slope'] = self.weekly_df['W_MA5'] - self.weekly_df['W_MA5'].shift(1)
            else:
                self.weekly_df['W_MA5'] = self.weekly_df['Close']
                self.weekly_df['W_MA10'] = self.weekly_df['Close']
                self.weekly_df['W_MA20'] = self.weekly_df['Close']
                self.weekly_df['W_MA5_Slope'] = 0.0
        except Exception as e:
            logger.warning(f"週K線重採樣失敗，改用預設值: {e}")
            self.weekly_df = pd.DataFrame()

    def is_weekly_downtrend(self, idx):
        """
        📈 【週K線宏觀趨勢過濾 (Weekly Trend Filter)】
        判定原則：
        1. 若最新週K線收盤價跌破週 5MA，且週 5MA 正在向下彎曲 (Slope < 0)；
        2. 或在近 4 週內曾自波段最高點急跌拉回超過 8.0% 且處於下彎均線下方 (如華碩 1030 見頂下殺)；
        滿足上述條件者判定為「週線空方修正段」，日線出現的任何吞噬均視為弱勢反彈，一票否決！
        """
        if self.weekly_df.empty or len(self.weekly_df) < 5:
            return False
            
        try:
            curr_date = self.df.index[idx]
            # 取得截至當日為止的最新週K資料
            w_sub = self.weekly_df[self.weekly_df.index <= curr_date]
            if len(w_sub) < 5:
                return False
                
            w_curr = w_sub.iloc[-1]
            w_ma5 = w_curr['W_MA5']
            w_slope = w_curr['W_MA5_Slope']
            
            # 條件1：收在週5MA之下且週5MA下彎
            cond_below_falling_ma5 = (w_curr['Close'] < w_ma5) and (w_slope < -0.05)
            
            # 條件2：高檔波段見頂下挫 (近4週高點回落超過 8% 且週K為黑K)
            recent_4w_high = w_sub.iloc[-4:]['High'].max()
            pullback_from_high = ((recent_4w_high - w_curr['Close']) / recent_4w_high * 100) if recent_4w_high > 0 else 0
            cond_high_altitude_top = (pullback_from_high > 8.0) and (w_curr['Close'] < w_ma5)
            
            return bool(cond_below_falling_ma5 or cond_high_altitude_top)
        except Exception as e:
            logger.debug(f"is_weekly_downtrend 計算錯誤: {e}")
            return False

    def has_overhead_supply_wall(self, idx):
        """
        🚫 【上方套牢巨量反壓濾網 (Overhead Supply Wall 250D)】
        防範像 華經 (2025年自 86.20 崩跌至 31.25) 或 定穎投控 這類：
        過去 1 年 (250個交易日) 內曾自歷史高點崩跌超過 28%，且上方存在密集的歷史大量套牢密集區。
        """
        if idx < 40: return False
        curr = self.df.iloc[idx]
        
        # 擴大回溯視野至最多 250 個交易日 (1 整年)
        lookback = self.df.iloc[max(0, idx-250):idx]
        max_h = lookback['High'].max()
        min_l = lookback['Low'].min()
        
        # 檢驗過去1年內是否曾遭遇深幅崩跌 > 28% (如華經自 86 跌至 31)
        total_drop = (max_h - min_l) / max_h * 100 if max_h > 0 else 0
        if total_drop > 28.0:
            peak_date = lookback['High'].idxmax()
            peak_idx = lookback.index.get_loc(peak_date)
            peak_window = lookback.iloc[max(0, peak_idx-10):min(len(lookback), peak_idx+10)]
            peak_avg_vol = peak_window['Volume'].mean() if len(peak_window) > 0 else 0
            curr_vol_ma5 = curr['Vol_MA5']
            
            # 若歷史高點成交量異常龐大 (為當前5日均量的 1.8 倍以上)，且當前股價仍在歷史高點的 0.85 倍以下
            # 代表上方 20%~80% 空間全是歷史巨量解套賣壓山頭 (妖股/主力出貨型態)
            if peak_avg_vol > curr_vol_ma5 * 1.8 and curr['Close'] < max_h * 0.85:
                return True
                
        # 兼顧近 60 天短期反彈撞牆 (原定穎投控型態)
        lookback_60 = self.df.iloc[max(0, idx-60):idx]
        max_h60 = lookback_60['High'].max()
        min_l60 = lookback_60['Low'].min()
        drop_60 = (max_h60 - min_l60) / max_h60 * 100 if max_h60 > 0 else 0
        if drop_60 > 22.0:
            half_idx = len(lookback_60) // 2
            early_high = lookback_60.iloc[:half_idx]['High'].max()
            if curr['Close'] < early_high * 0.90 and curr['Gain_20D'] > 12.0:
                return True
                
        return False

    def is_bullish_engulfing(self, idx):
        """判斷是否為多頭吞噬 (Bullish Engulfing)"""
        if idx < 1: return False
        prev = self.df.iloc[idx-1]
        curr = self.df.iloc[idx]
        
        cond1 = prev['Body'] < 0  # 前一根黑K
        cond2 = curr['Body'] > 0  # 當前紅K
        cond3 = (curr['Open'] <= prev['Close']) and (curr['Close'] >= prev['Open']) # 包覆實體
        return bool(cond1 and cond2 and cond3)

    def is_bearish_engulfing(self, idx):
        """判斷是否為黑K吞噬 (Bearish Engulfing)"""
        if idx < 1: return False
        prev = self.df.iloc[idx-1]
        curr = self.df.iloc[idx]
        
        cond1 = prev['Body'] > 0  # 前一根紅K
        cond2 = curr['Body'] < 0  # 當前黑K
        cond3 = (curr['Open'] >= prev['Close']) and (curr['Close'] <= prev['Open']) # 包覆實體
        return bool(cond1 and cond2 and cond3)

    def is_harami(self, idx):
        """判斷是否為內困型態 (Harami)"""
        if idx < 1: return False
        prev = self.df.iloc[idx-1]
        curr = self.df.iloc[idx]
        
        cond1 = prev['Body_Abs'] > curr['Body_Abs'] * 2 # 前根實體顯著較大
        cond2 = (curr['High'] <= prev['High']) and (curr['Low'] >= prev['Low']) # 價格範圍在內
        cond3 = curr['Volume'] <= prev['Volume'] # 量能量縮
        return bool(cond1 and cond2 and cond3)

    def is_base_consolidation_breakout(self, idx):
        """
        👑 【林家洋核心大底起漲理論】：40~60日長期整理破繭第一根 (Base Consolidation Breakout)
        前輩心法：「最好的股票還是要有長期整理，然後剛剛開始突破的股票」
        升級防呆：
        1. 成交金額剛性門檻：當日成交金額必須 >= 1.0 億元台幣！
        2. 排除上方歷史巨量套牢山頭 (has_overhead_supply_wall)！
        3. 排除週線空方段 (is_weekly_downtrend)！
        """
        if idx < 40: return False
        curr = self.df.iloc[idx]
        vol_ma5 = self.df['Volume'].iloc[idx-5:idx].mean()

        # 基本流動性門檻 (>= 800張均量, >= 1200張當日量, 且成交金額 >= 1.0 億元)
        vol_threshold_ma5 = 800 * 1000 if vol_ma5 > 100000 else 800
        vol_threshold_curr = 1200 * 1000 if curr['Volume'] > 100000 else 1200
        if vol_ma5 < vol_threshold_ma5 or curr['Volume'] < vol_threshold_curr:
            return False
            
        # 🌟 剛性破億門檻 (徹底排除華經 0.86 億這類邊緣投機股)
        if curr['Amount_100M'] < 1.0:
            return False

        # 🌟 阻斷濾網：上方巨量反壓與週線走空一票否決
        if self.has_overhead_supply_wall(idx) or self.is_weekly_downtrend(idx):
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

        return bool(cond1 and cond2 and cond3 and cond4)

    def is_downtrend_reversal_attack(self, idx):
        """
        ⚡ 【林家洋核心理論】：下降趨勢反轉進場點 (破底翻起漲先鋒)
        """
        if idx < 20: return False
        curr = self.df.iloc[idx]
        vol_ma5 = self.df['Volume'].iloc[idx-5:idx].mean()

        # 基本流動性門檻 (>= 800張均量, >= 1200張當日量, 且成交金額 >= 1.0 億元)
        vol_threshold_ma5 = 800 * 1000 if vol_ma5 > 100000 else 800
        vol_threshold_curr = 1200 * 1000 if curr['Volume'] > 100000 else 1200
        if vol_ma5 < vol_threshold_ma5 or curr['Volume'] < vol_threshold_curr:
            return False
            
        if curr['Amount_100M'] < 1.0:
            return False

        # 阻斷濾網：上方巨量反壓與週線走空一票否決
        if self.has_overhead_supply_wall(idx) or self.is_weekly_downtrend(idx):
            return False

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

        cond1 = curr['Pct_Change'] >= 2.5
        cond2 = curr['Body'] > 0
        cond3 = curr['Volume'] > vol_ma5 * 1.5
        cond4 = curr['Close'] > h2

        return bool(cond1 and cond2 and cond3 and cond4)

    def is_attack_k(self, idx):
        """判斷是否為攻擊K線 (v8.3：升級破億門檻、250日反壓與週線走勢)"""
        if idx < 20: return False
        curr = self.df.iloc[idx]
        vol_ma5 = self.df['Volume'].iloc[idx-5:idx].mean()
        
        # 基本流動性門檻
        vol_threshold_ma5 = 800 * 1000 if vol_ma5 > 100000 else 800
        vol_threshold_curr = 1200 * 1000 if curr['Volume'] > 100000 else 1200
        if vol_ma5 < vol_threshold_ma5 or curr['Volume'] < vol_threshold_curr:
            return False
            
        # 🌟 門檻2：當日成交金額需 >= 1.0 億元台幣 (嚴格提升至破億標準)
        if curr['Amount_100M'] < 1.0:
            return False
            
        # 門檻3：排除突兀暴衝一日遊 (單日暴增4倍但平時無量)
        vol_spike_limit = 1500 * 1000 if vol_ma5 > 100000 else 1500
        if curr['Volume'] > vol_ma5 * 4.0 and vol_ma5 < vol_spike_limit:
            return False
            
        # 首日突破限制 (排除連漲力竭出貨K)
        past_5d = self.df.iloc[idx-5:idx]
        recent_surges = (past_5d['Pct_Change'] >= 3.0).sum()
        min_5d = past_5d['Low'].min()
        gain_5d = ((curr['Close'] - min_5d) / min_5d * 100) if min_5d > 0 else 0
        if recent_surges >= 2 or gain_5d > 20.0:
            return False

        # 🌟 阻斷濾網：排除上方沉重套牢巨峰與週線空方段
        if self.has_overhead_supply_wall(idx) or self.is_weekly_downtrend(idx):
            return False

        cond1 = curr['Pct_Change'] >= 3.0 # 漲幅夠大
        cond2 = curr['Body'] > 0          # 紅K
        cond3 = curr['Volume'] > vol_ma5 * 1.5 # 溫和放量
        
        # 突破近20日高點
        swing_high_20 = self.df['High'].iloc[idx-20:idx].max()
        cond4 = curr['Close'] > swing_high_20
        return bool(cond1 and cond2 and cond3 and cond4)

    def is_uptrend_line_break(self, idx):
        """
        ⚠️ 【林家洋核心理論】：上升趨勢波段停利點
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

        if l2 <= l1 or idx_t2 <= idx_t1:
            return False

        slope = (l2 - l1) / (idx_t2 - idx_t1)
        trendline_val = l2 + slope * (idx - idx_t2)

        break_line = (curr['Close'] < trendline_val) and (prev['Close'] >= (trendline_val - slope))
        break_prev_low = (curr['Close'] < l2) and (prev['Close'] >= l2)
        is_weak = (curr['Body'] < 0) or (curr['Pct_Change'] < 0)

        return bool((break_line or break_prev_low) and is_weak)

    def calculate_recommendation_score(self, idx):
        """計算推薦指數 (v8.3 多週期雙軌嚴選版)"""
        if idx < 5: return 0
        curr = self.df.iloc[idx]
        signal = curr.get('Signal', '無')
        
        if signal == '內困型態':
            return 30 if curr['Close'] > curr['MA60'] else 15
            
        buy_signal_strength = {
            '大底破繭 (長期整理突破)': 120,
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
        """執行全量分析 (加入週線空方與反壓一票否決過濾)"""
        signals, scores = [], []
        for i in range(len(self.df)):
            sig = "無"
            curr = self.df.iloc[i]
            
            # 多方進場訊號優先檢核 (大底破繭最高優先)
            if self.is_base_consolidation_breakout(i):
                sig = "大底破繭 (長期整理突破)"
            elif self.is_downtrend_reversal_attack(i):
                sig = "攻擊K線 (扭轉突破)"
            elif self.is_attack_k(i):
                sig = "攻擊K線"
            elif self.is_bullish_engulfing(i):
                # 🌟 多頭吞噬過濾：若成交金額未破億、或上方有反壓、或週線下彎走空，一律降為弱勢反彈，不給買進訊號！
                if curr['Amount_100M'] >= 1.0 and not self.has_overhead_supply_wall(i) and not self.is_weekly_downtrend(i):
                    sig = "多頭吞噬"
                else:
                    sig = "弱勢反彈 (空頭壓制)"
            elif self.is_harami(i):
                sig = "內困型態"
            
            # 風控與出場賣訊優先權最高
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
