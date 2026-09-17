import io
import csv
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
        self.institutional_cache = {}
        self.trust_60d_cache = {}
    
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
            if df.empty:
                return pd.DataFrame()
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

    def get_stock_daily_data_tpex(self, stock_id, start_date, end_date):
            """使用櫃買中心 (TPEx) API 取得上櫃日線數據，作為終極備援"""
            try:
                def parse_roc_date(date_str):
                    # 將民國年 (如 113/08/01) 轉為西元年
                    parts = date_str.strip().split('/')
                    return datetime(int(parts[0]) + 1911, int(parts[1]), int(parts[2]))
                
                start_dt = datetime.strptime(start_date, '%Y-%m-%d')
                end_dt = datetime.strptime(end_date, '%Y-%m-%d')
                session = requests.Session()
                session.trust_env = False
                session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
                
                records = []
                current = datetime(start_dt.year, start_dt.month, 1)
                
                while current <= end_dt:
                    # 櫃買中心 API 格式需為 民國年/月份 (例如 113/08)
                    query_date = f"{current.year - 1911}/{current.month:02d}"
                    url = f'https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_info/st43_result.php?l=zh-tw&d={query_date}&stkno={stock_id}'
                    
                    try:
                        res = session.get(url, timeout=15)
                        if res.status_code == 200:
                            data = res.json()
                            for row in data.get('aaData', []):
                                try:
                                    row_date = parse_roc_date(row[0].replace(' ', ''))
                                    if start_dt <= row_date <= end_dt:
                                        open_p = pd.to_numeric(str(row[3]).replace(',', ''), errors='coerce')
                                        high_p = pd.to_numeric(str(row[4]).replace(',', ''), errors='coerce')
                                        low_p = pd.to_numeric(str(row[5]).replace(',', ''), errors='coerce')
                                        close_p = pd.to_numeric(str(row[6]).replace(',', ''), errors='coerce')
                                        # TPEx 的成交量單位是「千股」，必須乘 1000 才能與 TWSE/Yahoo 統一
                                        vol = pd.to_numeric(str(row[1]).replace(',', ''), errors='coerce') * 1000
                                        
                                        if not any(pd.isna(v) for v in [open_p, high_p, low_p, close_p, vol]):
                                            records.append({'Date': row_date, 'Open': open_p, 'High': high_p, 'Low': low_p, 'Close': close_p, 'Volume': vol})
                                except Exception:
                                    continue
                    except Exception:
                        pass
                        
                    current = (current + timedelta(days=32)).replace(day=1)
                    
                df = pd.DataFrame(records)
                if df.empty:
                    return pd.DataFrame()
                df = df.sort_values('Date').set_index('Date')
                logger.info(f'Successfully fetched {len(df)} days of data for {stock_id} from TPEx')
                return df[['Open', 'High', 'Low', 'Close', 'Volume']]
                
            except Exception as e:
                logger.error(f'TPEx error for {stock_id}: {e}')
                return pd.DataFrame()

    def get_stock_daily_data(self, stock_id, start_date, end_date):
        """獲取股票日線數據，支援動態後綴與三層來源標籤 (Yahoo -> TWSE -> TPEx)"""
        try:
            stock_id = str(stock_id).split()[0]
            logger.info(f"Fetching data for {stock_id} from {start_date} to {end_date}")
            
            session = requests.Session()
            session.trust_env = False
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            })
            
            df = pd.DataFrame()
            data_source = "Yahoo" 
            
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

            # 🌟 啟動多層級備援機制 🌟
            if df.empty or len(df) == 0:
                logger.warning(f"Yahoo failed for {stock_id}, switching to TWSE fallback.")
                df = self.get_stock_daily_data_twse(stock_id, start_date, end_date)
                data_source = "TWSE"
                
                # 如果證交所也找不到 (代表是上櫃股)，啟用 TPEx 終極備援
                if df.empty or len(df) == 0:
                    logger.warning(f"TWSE failed for {stock_id}, switching to TPEx fallback.")
                    df = self.get_stock_daily_data_tpex(stock_id, start_date, end_date)
                    data_source = "TPEx"

                if df.empty or len(df) == 0:
                    return pd.DataFrame()

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if 'Adj Close' in df.columns:
                df = df.drop('Adj Close', axis=1)
                
            df['Data_Source'] = data_source
            df = df[['Open', 'High', 'Low', 'Close', 'Volume', 'Data_Source']]
            df = df.dropna(subset=['Open', 'High', 'Low', 'Close', 'Volume'])
            
            if len(df) < 20:
                return pd.DataFrame()

            # 🌟 整合三大法人每日買賣超與外資持股總量數據 🌟
            try:
                target_date_str = df.index[-1].strftime('%Y%m%d') if len(df) > 0 else None
                inst_map = self.get_institutional_data_for_date(target_date_str)
                stock_inst = inst_map.get(stock_id, {})
                df['Foreign_Buy'] = 0
                df['Trust_Buy'] = 0
                df['Dealer_Buy'] = 0
                df['Foreign_Hold_Shares'] = None
                df['Foreign_Hold_Pct'] = None
                df['Trust_Hold_Shares'] = None
                df['Trust_60D_Accum'] = None
                if not df.empty and stock_inst:
                    df.iloc[-1, df.columns.get_loc('Foreign_Buy')] = stock_inst.get('Foreign_Buy', 0)
                    df.iloc[-1, df.columns.get_loc('Trust_Buy')] = stock_inst.get('Trust_Buy', 0)
                    df.iloc[-1, df.columns.get_loc('Dealer_Buy')] = stock_inst.get('Dealer_Buy', 0)
                    df.iloc[-1, df.columns.get_loc('Foreign_Hold_Shares')] = stock_inst.get('Foreign_Hold_Shares', None)
                    df.iloc[-1, df.columns.get_loc('Foreign_Hold_Pct')] = stock_inst.get('Foreign_Hold_Pct', None)
                    df.iloc[-1, df.columns.get_loc('Trust_Hold_Shares')] = stock_inst.get('Trust_Hold_Shares', None)

                # 🌟 方案 A：計算投信波段累積鎖碼張數 (近 60 日推估) 🌟
                trust_60d_lots = self.get_trust_60d_accum(stock_id, df)
                if trust_60d_lots is not None:
                    df.iloc[-1, df.columns.get_loc('Trust_60D_Accum')] = trust_60d_lots
            except Exception as e:
                logger.debug(f"整合三大法人數據至 {stock_id} 失敗: {e}")

            return df
            
        except Exception as e:
            logger.error(f"Failed to fetch data for {stock_id}: {e}")
            return pd.DataFrame()


    def _parse_twse_qfii(self, text):
        """解析台灣證交所 MI_QFIIS (支援 JSON 與 CSV 雙重結構)"""
        records = {}
        # 1. 嘗試以 JSON 解析 (相容 tables、data、aaData 結構)
        try:
            data = json.loads(text)
            rows, fields = [], []
            if isinstance(data, dict):
                if 'tables' in data and isinstance(data['tables'], list):
                    for tbl in data['tables']:
                        if isinstance(tbl, dict) and 'data' in tbl and len(tbl['data']) > 0:
                            rows = tbl['data']
                            fields = tbl.get('fields', [])
                            break
                elif 'data' in data and isinstance(data['data'], list):
                    rows = data['data']
                    fields = data.get('fields', [])
                elif 'aaData' in data and isinstance(data['aaData'], list):
                    rows = data['aaData']
                    fields = data.get('fields', [])

            if rows:
                sid_idx, shs_idx, pct_idx = 0, 5, 7
                for idx, fld in enumerate(fields):
                    f_str = str(fld).strip()
                    if '代號' in f_str or 'Code' in f_str:
                        sid_idx = idx
                    elif '全體外資及陸資持有股數' in f_str or '持有股數' in f_str or '持有單位數' in f_str:
                        shs_idx = idx
                    elif '全體外資及陸資持股比率' in f_str or '持股比率' in f_str or '持股比例' in f_str:
                        pct_idx = idx

                for row in rows:
                    if len(row) > max(sid_idx, shs_idx, pct_idx):
                        sid = str(row[sid_idx]).strip()
                        try: shs = int(str(row[shs_idx]).replace(',', '').replace(' ', ''))
                        except: shs = None
                        try: pct = float(str(row[pct_idx]).replace(',', '').replace('%', '').strip())
                        except: pct = None
                        if sid and (sid.isdigit() or len(sid) >= 4):
                            records[sid] = {'shares': shs, 'pct': pct}
                if records:
                    return records
        except Exception:
            pass

        # 2. 嘗試以 CSV 解析
        try:
            reader = csv.reader(io.StringIO(text))
            header_found = False
            sid_idx, shs_idx, pct_idx = 0, 5, 7
            for row in reader:
                if not row: continue
                row_str = ' '.join(row)
                if '證券代號' in row_str and ('持有股數' in row_str or '持股比率' in row_str):
                    for idx, col in enumerate(row):
                        c = col.strip()
                        if '代號' in c: sid_idx = idx
                        elif '全體外資及陸資持有股數' in c or '持有股數' in c: shs_idx = idx
                        elif '全體外資及陸資持股比率' in c or '持股比率' in c: pct_idx = idx
                    header_found = True
                    continue
                if header_found and len(row) > max(sid_idx, shs_idx, pct_idx):
                    sid = row[sid_idx].strip().replace('=', '').replace('"', '')
                    if sid and (sid.isdigit() or len(sid) >= 4):
                        try: shs = int(row[shs_idx].replace(',', '').strip())
                        except: shs = None
                        try: pct = float(row[pct_idx].replace(',', '').replace('%', '').strip())
                        except: pct = None
                        records[sid] = {'shares': shs, 'pct': pct}
            if records:
                return records
        except Exception:
            pass

        return records

    def _parse_tpex_qfii(self, text):
        """解析櫃買中心 QFII 外資持股 (支援 JSON 與 CSV 雙重結構)"""
        records = {}
        # 1. 嘗試以 JSON 解析
        try:
            data = json.loads(text)
            rows, fields = [], []
            if isinstance(data, dict):
                if 'tables' in data and isinstance(data['tables'], list) and len(data['tables']) > 0:
                    tbl = data['tables'][0]
                    rows = tbl.get('data', [])
                    fields = tbl.get('fields', [])
                elif 'aaData' in data and isinstance(data['aaData'], list):
                    rows = data['aaData']
                    fields = data.get('fields', [])
                elif 'data' in data and isinstance(data['data'], list):
                    rows = data['data']
                    fields = data.get('fields', [])

            if rows:
                sid_idx, shs_idx, pct_idx = None, None, None
                for idx, fld in enumerate(fields):
                    f_str = str(fld).strip()
                    if '代號' in f_str: sid_idx = idx
                    elif '持有股數' in f_str or '持有單位數' in f_str: shs_idx = idx
                    elif '持股比率' in f_str or '持股比例' in f_str: pct_idx = idx

                for row in rows:
                    sid = None
                    if sid_idx is not None and len(row) > sid_idx:
                        sid = str(row[sid_idx]).strip()
                    else:
                        for item in row[:3]:
                            s = str(item).strip()
                            if re.fullmatch(r'\d{4,5}', s):
                                sid = s
                                break
                    if not sid: continue

                    shs, pct = None, None
                    if shs_idx is not None and len(row) > shs_idx:
                        try: shs = int(str(row[shs_idx]).replace(',', '').strip())
                        except: pass
                    elif len(row) >= 7:
                        try: shs = int(str(row[5]).replace(',', '').strip())
                        except: pass

                    if pct_idx is not None and len(row) > pct_idx:
                        try: pct = float(str(row[pct_idx]).replace(',', '').replace('%', '').strip())
                        except: pass
                    elif len(row) >= 9:
                        try: pct = float(str(row[8]).replace(',', '').replace('%', '').strip())
                        except:
                            try: pct = float(str(row[6]).replace(',', '').replace('%', '').strip())
                            except: pass

                    records[sid] = {'shares': shs, 'pct': pct}
                if records:
                    return records
        except Exception:
            pass

        # 2. 嘗試以 CSV 解析
        try:
            reader = csv.reader(io.StringIO(text))
            header_found = False
            sid_idx, shs_idx, pct_idx = None, None, None
            for row in reader:
                if not row: continue
                row_str = ' '.join(row)
                if '代號' in row_str and ('持有股數' in row_str or '持股比率' in row_str):
                    for idx, col in enumerate(row):
                        c = col.strip()
                        if '代號' in c: sid_idx = idx
                        elif '持有股數' in c: shs_idx = idx
                        elif '持股比率' in c or '持股比例' in c: pct_idx = idx
                    header_found = True
                    continue
                if header_found:
                    sid = row[sid_idx].strip().replace('=', '').replace('"', '') if sid_idx is not None and len(row) > sid_idx else None
                    if not sid:
                        for item in row[:3]:
                            s = str(item).strip().replace('=', '').replace('"', '')
                            if re.fullmatch(r'\d{4,5}', s):
                                sid = s
                                break
                    if not sid: continue
                    shs, pct = None, None
                    if shs_idx is not None and len(row) > shs_idx:
                        try: shs = int(row[shs_idx].replace(',', '').strip())
                        except: pass
                    if pct_idx is not None and len(row) > pct_idx:
                        try: pct = float(row[pct_idx].replace(',', '').replace('%', '').strip())
                        except: pass
                    records[sid] = {'shares': shs, 'pct': pct}
            if records:
                return records
        except Exception:
            pass

        return records

    def _fetch_foreign_holding_matrix(self, session, headers, query_date_str, prev_date_str):
        """全方位多重備援抓取上市與上櫃全體外資持股總數與持股比率"""
        foreign_holdings = {}
        ts = int(time.time() * 1000)

        # 整理日期嘗試候選池 (T, T-1, T-2)
        dates_to_try = [d for d in [query_date_str, prev_date_str] if d]
        try:
            cur_dt = datetime.strptime(query_date_str, '%Y%m%d') if query_date_str else datetime.now()
            p2_dt = cur_dt - timedelta(days=2)
            while p2_dt.weekday() >= 5:
                p2_dt -= timedelta(days=1)
            dates_to_try.append(p2_dt.strftime('%Y%m%d'))
        except Exception:
            pass

        # --- A. 上市外資持股 (TWSE MI_QFIIS) ---
        twse_headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, text/csv, */*',
            'Referer': 'https://www.twse.com.tw/zh/trading/foreign/mi-qfiis.html',
            'X-Requested-With': 'XMLHttpRequest',
        }

        twse_candidates = []
        # 1. 優先嘗試各交易日的標準 ALLBUT0999 (證交所標準全部不含權證)
        for d in dates_to_try:
            twse_candidates.append(f'https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?response=json&date={d}&selectType=ALLBUT0999&_={ts}')
            twse_candidates.append(f'https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?response=json&date={d}&_={ts}')
            twse_candidates.append(f'https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?response=csv&date={d}&selectType=ALLBUT0999')
            twse_candidates.append(f'https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?response=csv&date={d}')
            twse_candidates.append(f'https://www.twse.com.tw/fund/MI_QFIIS?response=json&date={d}&selectType=ALLBUT0999')
            twse_candidates.append(f'https://www.twse.com.tw/fund/MI_QFIIS?response=json&date={d}')
        
        # 2. 備援嘗試不帶日期參數之即時派發端點
        twse_candidates.extend([
            f'https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?response=json&selectType=ALLBUT0999&_={ts}',
            f'https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?response=json&_={ts}',
            'https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?response=csv&selectType=ALLBUT0999',
            'https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?response=csv',
        ])

        for url in twse_candidates:
            if not url: continue
            try:
                res = session.get(url, headers=twse_headers, timeout=12)
                if res.status_code == 200:
                    text = res.text.strip()
                    parsed = self._parse_twse_qfii(text)
                    if parsed and len(parsed) > 0:
                        foreign_holdings.update(parsed)
                        logger.info(f"TWSE 外資持股成功取得 {len(parsed)} 檔 (via {url[:65]}...)")
                        break
            except Exception as e:
                logger.debug(f"TWSE QFIIS 端點嘗試失敗 ({url[:50]}...): {e}")

        # --- B. 上櫃外資持股 (TPEx QFII) ---
        tpex_headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, text/csv, */*',
            'Referer': 'https://www.tpex.org.tw/web/stock/3insti/qfii/qfii.php?l=zh-tw',
        }

        tpex_candidates = []
        for d in dates_to_try:
            try:
                year = int(d[:4]) - 1911
                roc_date = f"{year}/{d[4:6]}/{d[6:8]}"
                tpex_candidates.append(f'https://www.tpex.org.tw/web/stock/3insti/qfii/qfii_result.php?l=zh-tw&o=json&d={roc_date}')
                tpex_candidates.append(f'https://www.tpex.org.tw/web/stock/3insti/qfii/qfii_result.php?l=zh-tw&o=csv&d={roc_date}')
            except Exception:
                pass

        tpex_candidates.extend([
            'https://www.tpex.org.tw/web/stock/3insti/qfii/qfii_result.php?l=zh-tw&o=json',
            'https://www.tpex.org.tw/web/stock/3insti/qfii/qfii_result.php?l=zh-tw&o=csv',
        ])

        for url in tpex_candidates:
            if not url: continue
            try:
                res = session.get(url, headers=tpex_headers, timeout=12)
                if res.status_code == 200:
                    text = res.text.strip()
                    parsed = self._parse_tpex_qfii(text)
                    if parsed and len(parsed) > 0:
                        foreign_holdings.update(parsed)
                        logger.info(f"TPEx 外資持股成功取得 {len(parsed)} 檔")
                        break
            except Exception as e:
                logger.debug(f"TPEx QFII 端點嘗試失敗: {e}")

        return foreign_holdings

    def get_institutional_data_for_date(self, query_date_str=None):
        """
        從台灣證交所 (TWSE) 與櫃買中心 (TPEx) 取得三大法人買賣超與外資持股總數/持股比率
        """
        import zoneinfo
        tz_taipei = zoneinfo.ZoneInfo("Asia/Taipei")
        now_tpe = datetime.now(tz_taipei)

        if not query_date_str:
            q_dt = now_tpe
            if q_dt.hour < 15:
                q_dt = q_dt - timedelta(days=1)
            while q_dt.weekday() >= 5:
                q_dt = q_dt - timedelta(days=1)
            query_date_str = q_dt.strftime('%Y%m%d')
        else:
            query_date_str = str(query_date_str).replace('-', '').replace('/', '').strip()

        # 計算前一交易日 (T-1)
        try:
            cur_dt = datetime.strptime(query_date_str, '%Y%m%d')
            prev_dt = cur_dt - timedelta(days=1)
            while prev_dt.weekday() >= 5:
                prev_dt = prev_dt - timedelta(days=1)
            prev_date_str = prev_dt.strftime('%Y%m%d')
        except Exception:
            prev_date_str = None

        if query_date_str in self.institutional_cache:
            return self.institutional_cache[query_date_str]

        inst_dict = {}
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
        }
        session = requests.Session()
        session.trust_env = False

        # --- 1. 抓取三大法人買賣超 (TWSE T86 上市) ---
        for d_str in [query_date_str, prev_date_str]:
            if not d_str: continue
            try:
                url_twse_t86 = f'https://www.twse.com.tw/rwd/zh/fund/T86?response=json&date={d_str}&selectType=ALLBUT0999'
                res = session.get(url_twse_t86, headers=headers, timeout=15)
                if res.status_code == 200:
                    data = res.json()
                    rows, fields = [], []
                    if isinstance(data, dict):
                        if 'tables' in data and isinstance(data['tables'], list) and len(data['tables']) > 0:
                            rows = data['tables'][0].get('data', [])
                        elif 'data' in data:
                            rows = data.get('data', [])
                        elif 'aaData' in data:
                            rows = data.get('aaData', [])

                    if rows:
                        for row in rows:
                            if len(row) >= 12:
                                stock_id = str(row[0]).strip()
                                try:
                                    foreign_net = int(str(row[4]).replace(',', '').replace(' ', ''))
                                    trust_net = int(str(row[10]).replace(',', '').replace(' ', ''))
                                    dealer_net = int(str(row[11]).replace(',', '').replace(' ', ''))
                                except (ValueError, TypeError):
                                    foreign_net, trust_net, dealer_net = 0, 0, 0
                                inst_dict[stock_id] = {
                                    'Foreign_Buy': foreign_net,
                                    'Trust_Buy': trust_net,
                                    'Dealer_Buy': dealer_net,
                                    'Foreign_Hold_Shares': None,
                                    'Foreign_Hold_Pct': None,
                                    'Trust_Hold_Shares': None
                                }
                        logger.info(f"TWSE 三大法人買賣超取得成功 ({d_str})，共 {len(inst_dict)} 檔")
                        break
            except Exception as e:
                logger.warning(f"TWSE T86 取得失敗 ({d_str}): {e}")

        # --- 2. 抓取三大法人買賣超 (TPEx 上櫃) ---
        for d_str in [query_date_str, prev_date_str]:
            if not d_str: continue
            try:
                year = int(d_str[:4]) - 1911
                roc_date = f"{year}/{d_str[4:6]}/{d_str[6:8]}"
                url_tpex_trade = f'https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=json&se=EW&t=D&d={roc_date}'
                res = session.get(url_tpex_trade, headers=headers, timeout=15)
                if res.status_code == 200:
                    data = res.json()
                    tables = data.get('tables', [])
                    rows = tables[0].get('data', []) if tables and isinstance(tables[0], dict) else data.get('aaData', [])
                    if rows and len(rows) > 0:
                        for row in rows:
                            if len(row) >= 11:
                                stock_id = str(row[0]).strip()
                                try:
                                    foreign_net = int(str(row[4]).replace(',', '').replace(' ', ''))
                                    trust_net = int(str(row[7]).replace(',', '').replace(' ', ''))
                                    dealer_net = int(str(row[10]).replace(',', '').replace(' ', '')) if len(row) > 10 else 0
                                except (ValueError, TypeError, IndexError):
                                    foreign_net, trust_net, dealer_net = 0, 0, 0
                                inst_dict[stock_id] = {
                                    'Foreign_Buy': foreign_net,
                                    'Trust_Buy': trust_net,
                                    'Dealer_Buy': dealer_net,
                                    'Foreign_Hold_Shares': None,
                                    'Foreign_Hold_Pct': None,
                                    'Trust_Hold_Shares': None
                                }
                        logger.info(f"TPEx 買賣超取得成功 ({d_str})")
                        break
            except Exception as e:
                logger.warning(f"TPEx 買賣超取得失敗 ({d_str}): {e}")

        # --- 3 & 4. 抓取外資持股總數與持股比率 (全體上市櫃) ---
        foreign_map = self._fetch_foreign_holding_matrix(session, headers, query_date_str, prev_date_str)
        if foreign_map:
            for sid, h_data in foreign_map.items():
                if sid in inst_dict:
                    inst_dict[sid]['Foreign_Hold_Shares'] = h_data.get('shares')
                    inst_dict[sid]['Foreign_Hold_Pct'] = h_data.get('pct')
                else:
                    inst_dict[sid] = {
                        'Foreign_Buy': 0, 'Trust_Buy': 0, 'Dealer_Buy': 0,
                        'Foreign_Hold_Shares': h_data.get('shares'),
                        'Foreign_Hold_Pct': h_data.get('pct'),
                        'Trust_Hold_Shares': None
                    }

        self.institutional_cache[query_date_str] = inst_dict
        return inst_dict

    def get_trust_60d_accum(self, stock_id, df=None):
        """
        方案 A：計算投信波段累積鎖碼張數 (近 60 日推估)
        先嘗試從 FinMind 取得近 60 個交易日投信買賣超數據累加；
        若 API 無法連線或無資料，則檢查 df 內部是否有歷史 Trust_Buy 進行累加，
        若僅有當日資料則回傳當日買賣超作為基礎推估。
        """
        stock_clean = str(stock_id).split()[0].replace('.TW', '').replace('.TWO', '')
        if hasattr(self, 'trust_60d_cache') and stock_clean in self.trust_60d_cache:
            return self.trust_60d_cache[stock_clean]

        if not hasattr(self, 'trust_60d_cache'):
            self.trust_60d_cache = {}

        accum_lots = None

        # 1. 嘗試由 FinMind 取得近 60 個交易日投信買賣超
        try:
            start_date = (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d')
            url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={stock_clean}&start_date={start_date}"
            session = requests.Session()
            session.trust_env = False
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'application/json'
            }
            res = session.get(url, headers=headers, timeout=4)
            if res.status_code == 200:
                data = res.json().get('data', [])
                trust_records = [r for r in data if r.get('name') == 'Investment_Trust']
                if trust_records:
                    trust_records = sorted(trust_records, key=lambda x: x.get('date', ''))
                    trust_60 = trust_records[-60:]
                    net_shares = sum(r.get('buy', 0) - r.get('sell', 0) for r in trust_60)
                    accum_lots = int(round(net_shares / 1000.0))
                    
                    # 同步將歷史 Trust_Buy 填入 df (若日期吻合)
                    if df is not None and not df.empty:
                        date_to_buy = {r['date']: (r.get('buy', 0) - r.get('sell', 0)) for r in trust_records}
                        for dt in df.index:
                            dt_str = dt.strftime('%Y-%m-%d') if hasattr(dt, 'strftime') else str(dt)[:10]
                            if dt_str in date_to_buy:
                                df.loc[dt, 'Trust_Buy'] = date_to_buy[dt_str]
        except Exception as e:
            logger.debug(f"FinMind 投信 60 日資料取得失敗 ({stock_clean}): {e}")

        # 2. 備援：若 df 內已有超過 5 天的歷史 Trust_Buy 資料，直接做 rolling sum
        if accum_lots is None and df is not None and 'Trust_Buy' in df.columns:
            try:
                trust_series = df['Trust_Buy'].dropna()
                non_zero_count = (trust_series != 0).sum()
                if non_zero_count >= 5:
                    accum_shares = trust_series.tail(60).sum()
                    accum_lots = int(round(accum_shares / 1000.0))
                elif len(trust_series) > 0 and trust_series.iloc[-1] != 0:
                    accum_lots = int(round(trust_series.iloc[-1] / 1000.0))
            except Exception:
                pass

        if accum_lots is not None:
            self.trust_60d_cache[stock_clean] = accum_lots
            return accum_lots

        return None

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
        end_date_str = params.get("end_date_str", (today + timedelta(days=1)).strftime('%Y-%m-%d'))
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
