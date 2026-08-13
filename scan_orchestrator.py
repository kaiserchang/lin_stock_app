import pandas as pd
import numpy as np
import pandas_ta as ta
import logging
import sys
import json
import time
import requests
from datetime import datetime, timedelta
import yfinance as yf
import re

# 設定日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
# 假設 LinJiaYangEngine 已經在同一個目錄下
from analysis_engine import LinJiaYangEngine

class TaiwanStockDataFetcher:
    def __init__(self):
        pass
    
    def get_taiwan_stock_list(self):
        """優先從公開市場資料來源組建台股清單，無法取得時才使用備用清單。"""
        try:
            stock_list = self._get_listed_stock_list()
            if stock_list is not None and not stock_list.empty:
                logger.info(f"Loaded {len(stock_list)} listed stocks from public market data")
                return stock_list
        except Exception as e:
            logger.warning(f"Failed to load public market stock list: {e}")
        return self._get_fallback_stock_list()

    def _get_listed_stock_list(self):
        """從 TWSE / TPEX 公開行情頁面抓取上市與上櫃股票代號與名稱。"""
        records = []
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        }

        for url in [
            'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&type=ALLBUT008',
            'https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&d=%s&stkno=' % datetime.now().strftime('%Y%m%d'),
        ]:
            try:
                response = requests.get(url, headers=headers, timeout=20)
                if response.status_code != 200:
                    continue
                text = response.text
                if 'tpex.org.tw' in url:
                    data = response.json()
                    tables = data.get('tables', [])
                    if not tables:
                        continue
                    rows = tables[0].get('data', []) if tables and isinstance(tables[0], dict) else []
                    for row in rows:
                        if not row or len(row) < 2:
                            continue
                        code = str(row[0]).strip()
                        name = str(row[1]).strip()
                        if re.fullmatch(r'\d{4,5}', code) and name:
                            records.append({'stock_id': code, 'stock_name': name, 'industry_category': '公開市場'})
                else:
                    try:
                        data = response.json()
                    except Exception:
                        continue
                    tables = data.get('tables', [])
                    if not tables:
                        continue
                    for table in tables:
                        if not isinstance(table, dict):
                            continue
                        rows = table.get('data', [])
                        for row in rows:
                            if not row or len(row) < 2:
                                continue
                            code = str(row[0]).strip()
                            name = str(row[1]).strip()
                            if re.fullmatch(r'\d{4,5}', code) and name:
                                records.append({'stock_id': code, 'stock_name': name, 'industry_category': '公開市場'})
            except Exception as e:
                logger.warning(f"Failed to fetch stock list from {url}: {e}")

        if records:
            df = pd.DataFrame(records)
            df = df.drop_duplicates(subset=['stock_id'])
            return df
        return None
    
    def get_stock_daily_data_twse(self, stock_id, start_date, end_date):
        """使用台灣證交所 API 取得台股日線數據，作為 yfinance 的備援"""
        try:
            def parse_roc_date(date_str):
                year, month, day = date_str.strip().split('/')
                year = int(year) + 1911
                return datetime(year, int(month), int(day))

            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')
            session = requests.Session()
            session.trust_env = False
            session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            })

            records = []
            current = datetime(start_dt.year, start_dt.month, 1)
            while current <= end_dt:
                query_date = current.strftime('%Y%m%d')
                url = f'https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={query_date}&stockNo={stock_id}'
                response = session.get(url, timeout=15)
                logger.info(f'TWSE fallback URL={url} status={response.status_code}')
                if response.status_code != 200:
                    logger.warning(f'TWSE request failed for {stock_id} {query_date}: {response.status_code}')
                    current = (current + timedelta(days=32)).replace(day=1)
                    continue

                data = response.json()
                logger.info(f'TWSE data stat={data.get("stat")} rows={len(data.get("data", []))} for {stock_id} {query_date}')
                if data.get('stat') != 'OK':
                    logger.warning(f'TWSE returned stat={data.get("stat")} for {stock_id} {query_date}')
                    current = (current + timedelta(days=32)).replace(day=1)
                    continue

                for row in data.get('data', []):
                    logger.debug(f'TWSE row for {stock_id}: {row}')
                    try:
                        row_date = parse_roc_date(row[0].replace(' ', ''))
                    except Exception:
                        continue
                    if row_date < start_dt or row_date > end_dt:
                        continue

                    open_price = pd.to_numeric(str(row[3]).replace(',', ''), errors='coerce')
                    high_price = pd.to_numeric(str(row[4]).replace(',', ''), errors='coerce')
                    low_price = pd.to_numeric(str(row[5]).replace(',', ''), errors='coerce')
                    close_price = pd.to_numeric(str(row[6]).replace(',', ''), errors='coerce')
                    volume = pd.to_numeric(str(row[1]).replace(',', ''), errors='coerce')
                    if any(pd.isna(val) for val in [open_price, high_price, low_price, close_price, volume]):
                        continue
                    records.append({
                        'Date': row_date,
                        'Open': open_price,
                        'High': high_price,
                        'Low': low_price,
                        'Close': close_price,
                        'Volume': volume,
                    })

                current = (current + timedelta(days=32)).replace(day=1)
            df = pd.DataFrame(records)
            df = df.dropna(subset=['Open', 'High', 'Low', 'Close', 'Volume'])
            if df.empty:
                logger.warning(f'All TWSE data rows for {stock_id} contained invalid OHLCV values')
                return pd.DataFrame()

            df = df.sort_values('Date').set_index('Date')
            logger.info(f'Successfully fetched {len(df)} days of data for {stock_id} from TWSE')
            return df[['Open', 'High', 'Low', 'Close', 'Volume']]
        except Exception as e:
            logger.error(f'Failed to fetch data for {stock_id} from TWSE: {e}')
            import traceback
            logger.error(traceback.format_exc())
            return pd.DataFrame()

    def _get_fallback_stock_list(self):
        """返回常見台股清單"""
        fallback_stocks = [
            {'stock_id': '2330', 'stock_name': '台積電', 'industry_category': '半導體'},
            {'stock_id': '2454', 'stock_name': '聯發科', 'industry_category': '半導體'},
            {'stock_id': '3008', 'stock_name': '大立光', 'industry_category': '光學'},
            {'stock_id': '2317', 'stock_name': '鴻海', 'industry_category': '電子'},
            {'stock_id': '2412', 'stock_name': '中華電', 'industry_category': '電信'},
            {'stock_id': '1101', 'stock_name': '台泥', 'industry_category': '水泥'},
            {'stock_id': '1102', 'stock_name': '亞泥', 'industry_category': '水泥'},
            {'stock_id': '1216', 'stock_name': '統一', 'industry_category': '食品'},
            {'stock_id': '1301', 'stock_name': '台塑', 'industry_category': '化工'},
            {'stock_id': '1303', 'stock_name': '南亞', 'industry_category': '化工'},
            {'stock_id': '2882', 'stock_name': '國泰金', 'industry_category': '金融'},
            {'stock_id': '2891', 'stock_name': '中信金', 'industry_category': '金融'},
            {'stock_id': '2886', 'stock_name': '兆豐金', 'industry_category': '金融'},
            {'stock_id': '1590', 'stock_name': '亞德客', 'industry_category': '機械'},
            {'stock_id': '2308', 'stock_name': '台達電', 'industry_category': '電機'},
            {'stock_id': '3231', 'stock_name': '緯創', 'industry_category': '電子'},
            {'stock_id': '2357', 'stock_name': '華碩', 'industry_category': '電子'},
            {'stock_id': '2379', 'stock_name': '瑞昱', 'industry_category': '半導體'},
            {'stock_id': '2395', 'stock_name': '力成', 'industry_category': '半導體'},
            {'stock_id': '2408', 'stock_name': '南亞科', 'industry_category': '半導體'},
            {'stock_id': '2409', 'stock_name': '友達', 'industry_category': '電子'},
            {'stock_id': '2448', 'stock_name': '晶電', 'industry_category': '半導體'},
            {'stock_id': '2498', 'stock_name': '宏達電', 'industry_category': '電子'},
            {'stock_id': '2618', 'stock_name': '浩鼎', 'industry_category': '生技'},
            {'stock_id': '2633', 'stock_name': '台灣高鐵', 'industry_category': '運輸'},
            {'stock_id': '2801', 'stock_name': '彰銀', 'industry_category': '金融'},
            {'stock_id': '2809', 'stock_name': '京城銀', 'industry_category': '金融'},
            {'stock_id': '2880', 'stock_name': '華南金', 'industry_category': '金融'},
            {'stock_id': '3034', 'stock_name': '聯詠', 'industry_category': '半導體'},
            {'stock_id': '3045', 'stock_name': '普萊德', 'industry_category': '電子'},
            {'stock_id': '3481', 'stock_name': '群創', 'industry_category': '電子'},
            {'stock_id': '3711', 'stock_name': '日月光', 'industry_category': '半導體'},
            {'stock_id': '4904', 'stock_name': '遠傳', 'industry_category': '電信'},
            {'stock_id': '5880', 'stock_name': '合庫金', 'industry_category': '金融'},
            {'stock_id': '6505', 'stock_name': '台塑化', 'industry_category': '化工'},
            {'stock_id': '8046', 'stock_name': '南電', 'industry_category': '電子'},
            {'stock_id': '9910', 'stock_name': '豐泰', 'industry_category': '紡織'},
            {'stock_id': '9945', 'stock_name': '潤泰全', 'industry_category': '貿易'},
            {'stock_id': '0050', 'stock_name': '元大台灣50', 'industry_category': 'ETF'},
            {'stock_id': '0056', 'stock_name': '元大高股息', 'industry_category': 'ETF'},
        ]
        logger.info(f"Using stock list with {len(fallback_stocks)} stocks")
        return pd.DataFrame(fallback_stocks)

    def get_stock_daily_data(self, stock_id, start_date, end_date):
            """獲取股票日線數據，支援動態後綴與來源標籤"""
            try:
                # 1. 切除中文，確保只有數字代碼 (過濾 '4931 新盛力' -> '4931')
                stock_id = str(stock_id).split()[0]
                logger.info(f"Fetching data for {stock_id} from {start_date} to {end_date}")
                
                session = requests.Session()
                session.trust_env = False
                session.headers.update({
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                })
                
                df = pd.DataFrame()
                data_source = "Yahoo" # 預設來源標籤
                
                # 2. 雙棲嘗試：先上市(.TW)，後上櫃(.TWO)
                for suffix in [".TW", ".TWO"]:
                    ticker = f"{stock_id}{suffix}"
                    for attempt in range(1, 4):
                        try:
                            df = yf.download(ticker, start=start_date, end=end_date, progress=False, threads=False, session=session)
                            if not df.empty and len(df) > 0:
                                break
                        except Exception:
                            time.sleep(1)
                    if not df.empty and len(df) > 0:
                        break
    
                # 3. 備援機制：如果 Yahoo 徹底失敗，改叫 TWSE
                if df.empty or len(df) == 0:
                    logger.warning(f"Yahoo failed for {stock_id}, switching to TWSE fallback.")
                    df = self.get_stock_daily_data_twse(stock_id, start_date, end_date)
                    data_source = "TWSE" # 更改來源標籤為證交所
                    
                    if df.empty or len(df) == 0:
                        return pd.DataFrame()
    
                # 4. 欄位清洗與標籤注入
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                if 'Adj Close' in df.columns:
                    df = df.drop('Adj Close', axis=1)
                    
                # 🌟 將來源標籤寫入 DataFrame 中
                df['Data_Source'] = data_source
                
                df = df[['Open', 'High', 'Low', 'Close', 'Volume', 'Data_Source']]
                df = df.dropna(subset=['Open', 'High', 'Low', 'Close', 'Volume'])
                
                if len(df) < 20:
                    return pd.DataFrame()
                    
                return df
                
            except Exception as e:
                logger.error(f"Failed to fetch data for {stock_id}: {e}")
                return pd.DataFrame()

class ScanLogWriter:
    """用於寫入掃描日誌的類別"""
    def __init__(self, session_id, api_url="http://localhost:3000/api/trpc"):
        self.session_id = session_id
        self.api_url = api_url
        self.logs = []
        self.log_file = "data/scan_logs.json"
    
    def write_log(self, stock_id, stock_name, status, signal_type=None, message=None):
        """寫入單筆日誌到文件"""
        try:
            # 構建日誌數據
            log_data = {
                "sessionId": self.session_id,
                "stockId": stock_id,
                "stockName": stock_name,
                "status": status,
                "signalType": signal_type,
                "message": message,
                "timestamp": datetime.now().isoformat()
            }
            # 本地緩存日誌（避免頻繁的文件 I/O）
            self.logs.append(log_data)
            # 每 5 筆日誌或特定狀態時才寫入文件
            if len(self.logs) >= 5 or status in ["completed", "failed"]:
                self._flush_logs()
        except Exception as e:
            logger.error(f"Error writing log for {stock_id}: {e}")
    
    def _flush_logs(self):
        """批量寫入緩存的日誌到文件"""
        if not self.logs:
            return
        try:
            # 讀取現有日誌
            import os
            if os.path.exists(self.log_file):
                with open(self.log_file, 'r', encoding='utf-8') as f:
                    existing_logs = json.load(f)
            else:
                existing_logs = []
            
            # 合併新日誌
            existing_logs.extend(self.logs)
            
            # 寫入文件
            os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
            with open(self.log_file, 'w', encoding='utf-8') as f:
                json.dump(existing_logs, f, ensure_ascii=False, indent=2)
            
            logger.info(f"Flushed {len(self.logs)} logs to {self.log_file}")
            self.logs = []
        except Exception as e:
            logger.error(f"Error flushing logs: {e}")
    
    def flush_all(self):
        """確保所有日誌都被寫入"""
        self._flush_logs()

def parse_csv_data(csv_file_path, stock_id=None):
    """從 CSV 文件解析股票數據"""
    try:
        logger.info(f"Parsing CSV data from {csv_file_path}")
        
        # 讀取 CSV 文件
        df = pd.read_csv(csv_file_path)
        
        # 標準化列名（支援多種格式）
        df.columns = df.columns.str.strip().str.lower()
        
        # 嘗試找到日期列
        date_col = None
        for col in ['date', 'time', '日期', '時間']:
            if col in df.columns:
                date_col = col
                break
        
        if date_col is None:
            logger.error("CSV file must contain a 'date' column")
            return pd.DataFrame()
        
        # 標準化 OHLCV 列
        required_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in required_cols:
            if col not in df.columns:
                # 嘗試找到相似的列名
                for c in df.columns:
                    if col[0] in c.lower():
                        df[col] = df[c]
                        break
        
        # 檢查是否有所有必要的列
        if not all(col in df.columns for col in required_cols):
            logger.error(f"CSV file must contain columns: {', '.join(required_cols)}")
            return pd.DataFrame()
        
        # 選擇必要的列
        df = df[[date_col, 'open', 'high', 'low', 'close', 'volume']]
        df.columns = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']
        
        # 轉換日期和數值
        df['Date'] = pd.to_datetime(df['Date'])
        df['Open'] = pd.to_numeric(df['Open'], errors='coerce')
        df['High'] = pd.to_numeric(df['High'], errors='coerce')
        df['Low'] = pd.to_numeric(df['Low'], errors='coerce')
        df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
        df['Volume'] = pd.to_numeric(df['Volume'], errors='coerce')
        
        # 移除 NaN 值
        df = df.dropna()
        
        # 排序並設置日期為索引
        df = df.sort_values('Date').reset_index(drop=True)
        df = df.set_index('Date')
        
        if len(df) < 30:
            logger.warning(f"Insufficient data in CSV: {len(df)} rows (need 30)")
            return pd.DataFrame()
        
        logger.info(f"Successfully parsed {len(df)} days of data from CSV")
        return df
    except Exception as e:
        logger.error(f"Failed to parse CSV data: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return pd.DataFrame()

def run_market_scan(params):
        fetcher = TaiwanStockDataFetcher()
        stock_list_df = fetcher.get_taiwan_stock_list()
        
        if stock_list_df.empty:
            return {"status": "error", "message": "Failed to get stock list."}
            
        scan_limit = params.get("scan_limit")
        if scan_limit:
            stock_list_df = stock_list_df.head(scan_limit)
            
        today = datetime.now()
        start_date_str = params.get("start_date_str", (today - timedelta(days=120)).strftime('%Y-%m-%d'))
        end_date_str = params.get("end_date_str", today.strftime('%Y-%m-%d'))
        signal_filter = params.get("signal_filter", [])
        session_id = params.get("session_id")
        
        log_writer = ScanLogWriter(session_id) if session_id else None
        recommendations = []
        total_scanned = 0
        failed_count = 0
        
        for i, row in stock_list_df.iterrows():
            stock_id = row.get("stock_id") or row.get("code")
            stock_name = row.get("stock_name") or row.get("name")
            industry = row.get("industry_category") or row.get("industry") or "未分類"
            
            try:
                if log_writer:
                    log_writer.write_log(stock_id, stock_name, "scanning", message="正在獲取數據...")
                    
                df_daily = fetcher.get_stock_daily_data(stock_id, start_date_str, end_date_str)
                
                if df_daily.empty or len(df_daily) < 20: 
                    if log_writer:
                        log_writer.write_log(stock_id, stock_name, "failed", message="數據不足（少於20天）")
                    failed_count += 1
                    continue
                    
                # 🌟 取出我們剛剛在 DataFrame 埋入的來源標籤 
                source_label = df_daily['Data_Source'].iloc[-1] if 'Data_Source' in df_daily.columns else "未知"
                
                engine = LinJiaYangEngine(df_daily)
                analysis_result = engine.run_analysis()
                
                latest_signal = analysis_result.iloc[-1]
                signal_type = latest_signal["Signal"]
                above_ma60 = bool(latest_signal["Above_MA60"])
                recommendation_score = int(latest_signal.get("RecommendationScore", 0))
                
                if signal_type != "無":
                    if not signal_filter or signal_type in signal_filter:
                        recommendations.append({
                            "stockId": stock_id,
                            "stockName": stock_name,
                            "industry": industry,
                            "closePrice": float(latest_signal["Close"]),
                            "signalType": signal_type,
                            "aboveMa60": above_ma60,
                            "recommendationScore": recommendation_score,
                            "scanDate": latest_signal.name.strftime('%Y-%m-%d'),
                            "dataSource": source_label  # 👈 新增欄位：將來源擴充並傳遞給前端網頁
                        })
                        if log_writer:
                            log_writer.write_log(stock_id, stock_name, "completed", signal_type=signal_type, message=f"發現訊號：{signal_type}")
                else:
                    if log_writer:
                        log_writer.write_log(stock_id, stock_name, "completed", message="無技術訊號")
                        
                total_scanned += 1
                
            except Exception as e:
                logger.error(f"Error analyzing {stock_id}: {e}")
                if log_writer:
                    log_writer.write_log(stock_id, stock_name, "failed", message=f"分析失敗：{str(e)}")
                failed_count += 1
                
            progress = int(((i + 1) / len(stock_list_df)) * 100)
            print(f"PROGRESS:{progress}")  
            sys.stdout.flush() 
            
        if log_writer:
            log_writer.flush_all()
            
        return {
            "status": "success",
            "totalScannedStocks": total_scanned,
            "recommendationCount": len(recommendations),
            "failedCount": failed_count,
            "recommendations": recommendations
        }

def get_stock_kline_data(stock_id, start_date_str, end_date_str):
    fetcher = TaiwanStockDataFetcher()
    df_daily = fetcher.get_stock_daily_data(stock_id, start_date_str, end_date_str)

    if df_daily.empty:
        return {"status": "error", "message": f"Failed to get kline data for {stock_id}."}

    engine = LinJiaYangEngine(df_daily)
    analysis_result = engine.run_analysis()

    # 將 DataFrame 轉換為適合前端的 JSON 格式
    kline_data = []
    for index, row in analysis_result.iterrows():
        kline_data.append({
            "Date": index.strftime('%Y-%m-%d'),
            "Open": float(row["Open"]),
            "High": float(row["High"]),
            "Low": float(row["Low"]),
            "Close": float(row["Close"]),
            "Volume": float(row["Volume"]),
            "Signal": row["Signal"],
            "Above_MA60": bool(row["Above_MA60"])
        })
    
    return {"status": "success", "klines": kline_data}

def analyze_csv_data(csv_file_path, stock_id=None, signal_filter=None):
    """分析上傳的 CSV 數據"""
    df_daily = parse_csv_data(csv_file_path, stock_id)
    
    if df_daily.empty:
        return {"status": "error", "message": "Failed to parse CSV data."}
    
    engine = LinJiaYangEngine(df_daily)
    analysis_result = engine.run_analysis()
    
    # 提取最新訊號
    latest_signal = analysis_result.iloc[-1]
    signal_type = latest_signal["Signal"]
    above_ma60 = bool(latest_signal["Above_MA60"])
    
    # 生成 K 線數據
    kline_data = []
    for index, row in analysis_result.iterrows():
        kline_data.append({
            "Date": index.strftime('%Y-%m-%d'),
            "Open": float(row["Open"]),
            "High": float(row["High"]),
            "Low": float(row["Low"]),
            "Close": float(row["Close"]),
            "Volume": float(row["Volume"]),
            "Signal": row["Signal"],
            "Above_MA60": bool(row["Above_MA60"])
        })
    
    return {
        "status": "success",
        "stockId": stock_id or "uploaded",
        "signalType": signal_type,
        "aboveMa60": above_ma60,
        "closePrice": float(latest_signal["Close"]),
        "klines": kline_data
    }

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "No command provided."}), file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    if command == "run_market_scan":
        params_str = sys.argv[2]
        params = json.loads(params_str)
        result = run_market_scan(params)
        print(json.dumps(result, default=str))
    elif command == "get_stock_kline_data":
        stock_id = sys.argv[2]
        start_date_str = sys.argv[3]
        end_date_str = sys.argv[4]
        result = get_stock_kline_data(stock_id, start_date_str, end_date_str)
        print(json.dumps(result, default=str))
    elif command == "analyze_csv":
        csv_file_path = sys.argv[2]
        stock_id = sys.argv[3] if len(sys.argv) > 3 else None
        signal_filter = json.loads(sys.argv[4]) if len(sys.argv) > 4 else None
        result = analyze_csv_data(csv_file_path, stock_id, signal_filter)
        print(json.dumps(result, default=str))
    else:
        print(json.dumps({"status": "error", "message": f"Unknown command: {command}"}), file=sys.stderr)
        sys.exit(1)
