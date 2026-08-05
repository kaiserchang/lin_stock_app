import pandas as pd
import requests

def get_all_taiwan_stocks():
    print("⏳ 正在連線台灣證交所獲取 [上市] 股票清單...")
    # 呼叫上市 OpenAPI
    twse_url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    twse_res = requests.get(twse_url)
    twse_df = pd.DataFrame(twse_res.json())
    # 提取所需欄位並統一命名
    twse_df = twse_df[['Code', 'Name']].rename(columns={'Code': '代碼', 'Name': '名稱'})
    
    print("⏳ 正在連線櫃買中心獲取 [上櫃] 股票清單...")
    # 呼叫上櫃 OpenAPI
    tpex_url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
    tpex_res = requests.get(tpex_url)
    tpex_df = pd.DataFrame(tpex_res.json())
    # 提取所需欄位並統一命名
    tpex_df = tpex_df[['SecuritiesCompanyCode', 'CompanyName']].rename(columns={'SecuritiesCompanyCode': '代碼', 'CompanyName': '名稱'})
    
    print("⚙️ 合併清單並執行正則表達式過濾 (排除權證/牛熊證)...")
    # 將上市與上櫃的資料表上下合併
    all_stocks = pd.concat([twse_df, tpex_df], ignore_index=True)
    
    # 核心濾網：只保留 4 到 6 碼的純數字股票代號 (包含普通股如 2330, ETF如 0050)
    all_stocks = all_stocks[all_stocks['代碼'].str.match(r'^\d{4,6}$')]
    
    # 存檔 (使用 utf-8-sig 確保在 Mac 或 Windows 使用 Excel 打開時不會變成亂碼)
    file_name = "all_stocks.csv"
    all_stocks.to_csv(file_name, index=False, encoding='utf-8-sig')
    
    print(f"✅ 成功獲取 {len(all_stocks)} 檔股票代碼！")
    print(f"📁 檔案已儲存至目前目錄：{file_name}")

if __name__ == "__main__":
    get_all_taiwan_stocks()