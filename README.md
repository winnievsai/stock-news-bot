# FinMind 台股日K 增量抓取工具

## 這個資料夾裡有什麼

- `update_finmind.py`　主程式，執行後會抓「股價」資料並更新 CSV
- `update_news.py`　抓「個股新聞」的程式，用法跟 `update_finmind.py` 一樣（見下方「抓取個股新聞」）
- `update_us_stock.py`　抓「美股股價」的程式（見下方「抓取美股股價」）
- `update_us_news.py`　抓「美股新聞」的程式（見下方「抓取美股新聞」）
- `update_market_news.py`　抓「台股/美股大盤（市場整體）新聞」的程式，不綁定特定股票（見下方「抓取大盤新聞」）
- `validate_prices.py`　拿 Yahoo Finance 的股價交叉核對本地資料（見下方「股價交叉比對」）
- `send_news_email.py`　把當天新抓到的新聞寄成一封 email（見下方「每日新聞 Email」）
- `stock_list.txt`　你要追蹤的**台股**代碼清單，一行一個（`update_finmind.py`、`update_news.py` 共用）
- `us_stock_list.txt`　你要追蹤的**美股**代碼清單，一行一個（`update_us_stock.py` 用，代碼要大寫）
- `.env.local`　放你的 FinMind API token（**不要分享或上傳到公開地方**）
- `data/`　每支台股一個股價 CSV，例如 `data/2330.csv`
- `news/`　每支台股一個新聞 CSV，例如 `news/2330.csv`（欄位：日期時間、股票代碼、標題、來源、連結）
- `us_data/`　每支美股一個股價 CSV，例如 `us_data/AAPL.csv`
- `us_news/`　每支美股一個新聞 CSV，例如 `us_news/AAPL.csv`（資料來源：Google News RSS）
- `market_news/`　`tw.csv`（台股大盤新聞）、`us.csv`（美股大盤新聞），不綁定特定股票代碼
- `requirements.txt`　Python 套件需求（只需要 `requests`）

## 第一次使用

1. 安裝套件：
   ```
   pip3 install -r requirements.txt
   ```
2. 編輯 `stock_list.txt`，把要追蹤的股票代碼填進去（一行一個，`#` 開頭當註解）
3. 編輯 `.env.local`，填入你的 FinMind token：
   ```
   FINMIND_TOKEN=你的token
   ```
4. 執行：
   ```
   python3 update_finmind.py
   ```

第一次執行時，每支股票都沒有 CSV，所以會從 `BACKFILL_START_DATE`（預設 `2024-01-01`）開始回補到今天。

## 之後的增量更新

直接再執行一次 `python3 update_finmind.py` 就好。程式會讀取每支股票 CSV 裡最後一個日期，
只抓「那天之後到今天」的資料，已經是最新的股票會直接略過、不會浪費 API 次數。

## 設定項目（都在 `.env.local`）

| 變數 | 預設值 | 說明 |
|---|---|---|
| `FINMIND_TOKEN` | （必填） | 你的 FinMind API token |
| `BACKFILL_START_DATE` | `2024-01-01` | 股票第一次建立 CSV 時，回補資料的起始日 |
| `OUTPUT_DIR` | `data` | CSV 輸出資料夾 |
| `STOCK_LIST_FILE` | `stock_list.txt` | 股票清單檔案路徑 |
| `REQUEST_INTERVAL_SEC` | `6.5` | 每次 API 呼叫間的間隔秒數（避免超過流量限制）|

## 抓取個股新聞

`update_news.py` 跟 `update_finmind.py` 用法完全一樣：

```
python3 update_news.py
```

會讀同一份 `stock_list.txt`、同一組 `FINMIND_TOKEN`，把每支股票的新聞存到
`news/<股票代碼>.csv`。跟股價不同的是，FinMind 的新聞 API 一次只能查「一天」，
所以：

- **第一次執行**：只會回補「今天往前 7 天」的新聞（可用 `NEWS_BACKFILL_DAYS` 調整），
  不會像股價一樣回補到 2024 年——新聞筆數太多，逐日查詢的話會很快用掉流量額度。
- **之後每次執行**：從 CSV 裡最後一筆新聞的日期開始，逐日補到今天，已收錄過的新聞
  （用連結去重）不會重複寫入。
- 這支程式跟 `update_finmind.py` 共用同一組 token 的每小時額度上限，兩支程式如果
  排在同一時段一起跑，要注意額度會加總計算。`run.sh` 已經把 `update_news.py` 接在
  `update_finmind.py` 後面，所以每天 18:30 的排程會依序自動更新「股價」和「新聞」，
  不需要另外手動執行。日常增量更新每支股票只需查 1 天（15 檔股票 = 15 次請求），
  跟股價抓取加起來仍遠低於每小時額度上限。

## 抓取美股股價

`update_us_stock.py` 跟 `update_finmind.py` 用法完全一樣：

```
python3 update_us_stock.py
```

會讀 `us_stock_list.txt`、同一組 `FINMIND_TOKEN`，把每支美股的股價存到
`us_data/<代碼>.csv`，增量續抓、CSV新到舊排序，跟台股股價邏輯完全相同。

**已知限制：**

- 美股代碼**要用大寫**（例如 `AAPL` 不能寫成 `aapl`），FinMind 對小寫代碼一律回傳
  0 筆、不會報錯，容易誤判成「這檔沒資料」
- `us_stock_list.txt` 裡的 `BRK.B`（波克夏B股）目前透過 FinMind 查不到股價（試過
  `BRK.B`、`BRK-B`、`BRKB`、`BRK/B` 都不行），保留在清單裡但每次執行只會顯示
  「無新資料」，不影響其他股票
- 這支程式跟其他三支共用同一組 token 的每小時額度上限，第一次回補到 2024-01-01
  可能要跑好幾次才能補完全部 25 檔（遇到 402 會自動停止並印出提示，之後再執行
  一次即可）

## 抓取美股新聞

`update_us_news.py`：

```
python3 update_us_news.py
```

會讀 `us_stock_list.txt`，用 **Google News RSS**（不需要申請任何 API key）查每支美股
最近的新聞，存到 `us_news/<代碼>.csv`（欄位：日期時間、股票代碼、標題、來源、連結）。

**跟台股新聞（FinMind）不一樣的地方：**

- FinMind 沒有美股新聞資料集，所以改用 Google News RSS 這個免申請、免key的來源
- 這個來源**沒有真正的歷史區間查詢**，每次只能查「最近幾天」，沒辦法像台股新聞一樣
  回補到很久以前；靠每天執行、用新聞連結去重，慢慢累積出歷史記錄
- `link` 欄位是 Google News 的轉址連結，不是新聞原始網址，點開會先經過 Google News
  頁面再跳轉到原文，這是正常行為
- 沒有 API 額度限制，但程式會在每檔之間禮貌性地暫停幾秒（`US_NEWS_REQUEST_INTERVAL_SEC`，
  預設 2 秒），避免對 Google 發太密集的請求
- **相關性過濾**：Google News 對「代碼+stock」查詢的比對很鬆散，常常混進完全不相關
  的新聞（例如查 `SKHY` 曾經混進馬拉松、足球轉會等新聞）。程式會先用 FinMind
  `USStockInfo` 查出每檔股票的公司全名，抽出顯著關鍵字（去掉 Inc、Corp、Class A、
  Common Stock 之類的泛用字），只保留標題有出現股票代碼或公司關鍵字的新聞，不相關
  的新聞不會被存進 CSV。這是關鍵字比對的心探法，不是完美的語意判斷——如果某檔股票
  之後常見沒被抓到的相關新聞，可能是自動抽取的關鍵字太少，需要再調整

`run.sh` 已經把這支接在排程裡（`update_us_stock.py` 之後、`send_news_email.py`
之前），每天 8:00 會自動更新。`send_news_email.py` 寄出的信也會多一段「美股新聞」，
跟「台股新聞」在同一封信裡。

## 抓取大盤新聞

`update_market_news.py`：

```
python3 update_market_news.py
```

跟 `update_us_news.py` 一樣用 **Google News RSS**（不需要API key），差別是**不綁定
任何股票代碼**，查的是「台股大盤」「美股大盤」整體市場新聞，存到 `market_news/tw.csv`、
`market_news/us.csv`。適合想看大盤走勢、總經事件（例如聯準會決策、台股加權指數）這種
不屬於單一個股的新聞。

- 跟 `update_us_news.py` 一樣，每次只抓「最近1天」，沒有歷史區間查詢，靠每天執行、
  用連結去重累積歷史記錄
- `send_news_email.py` / `send_news_email_resend.py` 寄信時會把大盤新聞放在信件
  最前面（「台股大盤新聞」「美股大盤新聞」兩段），一樣套用可信來源白名單、標題統合、
  美股標題翻譯的規則，跟個股新聞的處理邏輯一致
- `run.sh`、GitHub Actions 的 `daily.yml` 都已經接好這一步

## 股價交叉比對

`validate_prices.py`：

```
python3 validate_prices.py
```

把 `data/`（台股）、`us_data/`（美股）裡每支股票**最新一筆**收盤價，拿去跟
**Yahoo Finance**（不需要API key）同一天的收盤價核對，抓出兩邊落差異常的股票，
分兩種情況：

- **價格異常**：同一天的收盤價，本地跟 Yahoo 差距超過門檻（預設 1.5%，可用
  `.env.local` 的 `PRICE_DIFF_THRESHOLD_PCT` 調整）——可能是 FinMind 資料有誤，
  也可能除權息等因素造成短暫落差，建議人工確認，不代表哪邊一定是對的
- **資料不同步**：兩邊「最新一筆」的日期對不上（例如本地還停在上週五，Yahoo 已經
  有這週一的資料）——通常是本地資料還沒更新到最新，不是價格錯誤

有發現異常時會寫進 `price_validation_report.txt`，`send_news_email.py` /
`send_news_email_resend.py` 寄信時會自動讀取，把警示放在信件最前面；沒有異常
的話這個檔案會被自動刪除、信件也不會有這段。`run.sh`、GitHub Actions 的
`daily.yml` 都已經接好這一步，會在寄信前自動執行。

新聞的交叉比對則是靠現有的「標題相似新聞統合」機制：同一件事被多家媒體報導時，
信件裡會顯示「共幾篇報導、來源有哪些」，本身就是一種多方交叉驗證的呈現方式，
不另外疊加標記。

## 每日新聞 Email

`send_news_email.py` 會把 `news/` 資料夾裡「今天」新增的新聞整理成一封信，透過 Mac
內建的「郵件」App（Mail.app）寄出。`run.sh` 已經接在 `update_news.py` 後面，所以每天
18:30 的排程跑完抓資料後，會自動寄一封「股票新聞日報」給你。

**設定步驟（第一次使用要做）：**

1. 「系統設定」→ 搜尋「網際網路帳號」→「+」新增帳號 → 選「Google」→ 登入寄件用的
   Gmail 帳號（一般帳號密碼登入即可，不需要 App Password），確認「郵件」項目是打開的
2. 在 `.env.local` 加入兩行（範例是自己寄給自己）：
   ```
   EMAIL_FROM=hsunweiai@gmail.com
   EMAIL_TO=hsunweiai@gmail.com
   ```
   `EMAIL_FROM` 要跟步驟 1 加入「郵件」App 的帳號一致
3. 手動測試一次：
   ```
   python3 send_news_email.py
   ```
   第一次執行時，macOS 可能會跳出「是否允許『終端機/Python』控制『郵件』」的視窗，
   要按「允許」，之後就不會再問。成功的話會印出「已寄出。」，並收到一封標題為
   「股票新聞日報 YYYY-MM-DD」的信。

如果某支股票當天沒有新新聞，信裡該股票下面會顯示「（今日無新聞）」，不會漏掉任何一檔。

**美股新聞自動翻譯：** 美股新聞標題會用 MyMemory 免費翻譯 API（不需要申請key）自動
翻成繁體中文才放進信裡，台股新聞本來就是中文不會另外翻譯。是機器翻譯僅供快速瀏覽，
連結還是連到英文原文，準確度不像人工翻譯那麼精確。

**信件精簡處理：**

- **只列有公信力來源的新聞**：信件只會列出白名單裡的新聞來源（[send_news_email.py](send_news_email.py)
  開頭的 `TW_CREDIBLE_SOURCES` / `US_CREDIBLE_SOURCES`），例如 UDN、經濟日報、工商
  時報、Reuters、Bloomberg、CNBC、Yahoo Finance 等主流媒體；投資論壇貼文（CMoney
  股市爆料同學會）、個人部落格、券商業配文（sinotrade richclub）、自動產生的機構
  持股公告（MarketBeat）、加密貨幣網站等來源不會列入信件——但 CSV 裡的原始資料不
  受影響，只是不會寄進信裡。覺得白名單漏掉哪個可信來源，直接跟我說或自己編輯
  `send_news_email.py` 開頭那兩個名單調整即可
- 標題相似的新聞（同一事件被不同媒體轉載）會自動統合成一則，並列出「共幾篇報導、
  來源有哪些」，不會每篇都各佔兩行
- 每檔股票在信裡最多只列 `MAX_NEWS_PER_STOCK`（預設 8）則統合後的新聞，超過的部分
  會顯示「還有 N 則較舊的新聞未列出」，但完整記錄還是都保存在 `news/`、`us_news/`
  的 CSV 裡，只是不會全部塞進信件

## 關於 FinMind 流量限制

FinMind 的 API 有請求次數上限（有 token 大約每小時 600 次，超過會回傳 402）。程式若遇到
402 會直接停止並印出提示，過一段時間（約一小時）後再執行一次即可補齊剩下的股票。

## 設定每日自動排程（macOS launchd）

這個資料夾裡另外有 `run.sh` 和 `com.winnie.finmind-update.plist` 兩個檔案，是給 macOS
內建排程系統（launchd）用的，會直接在你的 Mac 上定時執行，**不需要 Claude 桌面版同時開著**，
只要 Mac 有開機、有網路就會照時間執行。預設時間是每天 **18:30**（FinMind 官方資料約
17:30 更新完當天資料）。

第一次設定，打開「終端機」（Terminal），貼上以下指令並執行：

```bash
cd ~/Desktop/搜集股票資訊
chmod +x run.sh
mkdir -p ~/Library/LaunchAgents
cp com.winnie.finmind-update.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.winnie.finmind-update.plist 2>/dev/null
launchctl load -w ~/Library/LaunchAgents/com.winnie.finmind-update.plist
```

設定完成後就會每天自動執行。你可以：

- **手動立刻測試一次**：`./run.sh`（會直接印出結果，也是你填好 token 後第一次回補歷史資料要跑的指令）
- **看排程執行紀錄**：`logs/launchd.out.log`（正常輸出）、`logs/launchd.err.log`（錯誤訊息）
- **修改執行時間**：編輯 `com.winnie.finmind-update.plist` 裡的 `Hour` / `Minute`，改完後重新執行上面
  最後兩行（先 `unload` 再 `load`）讓設定生效
- **暫停排程**：`launchctl unload ~/Library/LaunchAgents/com.winnie.finmind-update.plist`

因為程式是照「每支股票 CSV 裡最後一個日期」往後補資料，就算某一天 Mac 沒開機、排程沒跑到，
也完全不會漏資料——下次執行時會自動把中間漏掉的交易日一起補齊。
