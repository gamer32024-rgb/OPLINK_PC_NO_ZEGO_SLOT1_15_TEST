# GUI_TEST_PC 角色資產與武器欄盤點功能交接

## 使用者目標

在 GUI_TEST_PC 對一個或多個 Slot 播放現有「修裝」模組／腳本時，遊戲會依序切換同一 Slot 內的 5 個角色並打開背包。系統要在背包畫面穩定時，讀取並記錄每個角色的：

1. 魔幣
2. 綁晶
3. 非綁晶
4. 武器欄是否空缺

目前 15 Slot 共 75 個角色。資料模型與介面需預留未來 20 Slot、100 個角色，但不得因此把實驗性的 Slot 16-20 支援部署到現行 1-15 Slot 正式服務。

最終在 GUI_TEST_PC 手機 PWA 的「呼叫模組播放」按鈕上方增加金錢符號按鈕。按下後一次查看各 Slot、各 5 個角色的最新盤點資料。

## 已確認的現況

- 正式專案：`C:\Users\andyb\Documents\New project\GUI_TEST_PC_DEV_20260703`
- 共用 Python：`C:\Users\andyb\Documents\New project\src\starcg_bot`
- 現行模組名稱：`修裝`
- 現行腳本：`GUI_TEST_PC_DEV_20260703\scripts_pc\修裝.....新.pcscript.json`
- `modules_pc.json` 已把 `修裝` 對應到上述腳本。
- 腳本格式：`gui_test_pc_script_v1`
- 錄製 client：`1920x1080`
- 腳本長度：約 `57594 ms`
- 腳本事件：24 個 click
- 腳本內可看見五個左側角色選取座標，約為 `(106,153)`、`(93,352)`、`(92,522)`、`(82,703)`、`(90,891)`。
- 只靠事件座標無法證明哪個時間點背包已完全顯示，不可直接猜測 OCR 檢查點。
- 現有固定 ROI OCR 可參考 `src\starcg_bot\ocr_probe.py`。
- 現有遊戲視窗畫面擷取與狀態偵測可參考 `src\starcg_bot\battle_interrupt_runtime.py`、`src\starcg_bot\battle_interrupt.py` 及 GUI_TEST_PC 的 HWND/Slot 配對。
- PWA server 現有 `/api/status`、`/api/play/module`、`/api/play/module-chain` 及 GUI heartbeat bridge。

## 建議架構

### 1. 不修改原修裝腳本內容

建立獨立 sidecar 設定，例如：

`GUI_TEST_PC_DEV_20260703\config_pc\asset_scan_profile.json`

它以腳本名稱、角色 index、檢查點 event index、等待畫面穩定時間及 ROI 定義盤點位置。不要先把新 event type 寫進 `修裝.....新.pcscript.json`，避免破壞已驗證的播放時間與 Pico 完整性。

檢查點必須先透過 Slot 1 實際畫面確定，並在擷取前驗證「背包已開啟」。如果畫面不符合，重試有限次後記錄 `unknown`，不可把辨識失敗寫成 0。

### 2. 擷取不增加任何 Pico 輸入

修裝腳本仍由現有 GUI_TEST_PC/Pico 播放器執行。盤點 observer 只在指定檢查點從該 Slot 的 HWND 擷取 client frame，不切換前景、不點擊、不改動腳本時間。

優先重用已驗證的 WGC／視窗 client 擷取路線。不可透過 OPLINK 串流畫面反向 OCR，避免串流縮放、編碼與黑邊影響結果。

### 3. 固定 ROI 加保守判定

- 三項資產：對固定 ROI 做二值化／色彩遮罩及遊戲字型專用數字 OCR。
- OCR 結果需通過兩至三幀一致或高信心判定。
- 武器欄：以固定武器 slot ROI 偵測空槽模板。只有空槽模板高信心匹配才回報 `empty=true`；確認 slot 框存在但內容不同才回報 `empty=false`；其餘為 `unknown`。
- 每個欄位保存原始字串、解析整數、confidence、ROI 及證據截圖路徑。
- 數值需支援千分位符號及大數，不可用 32-bit 整數上限假設。

YOLO 不適合直接讀固定位置的數字。只有在背包狀態、遮罩或武器空槽外觀變化很大時，才考慮以小型分類器／偵測器輔助；資產數值仍應使用固定 ROI OCR。

### 4. 獨立持久化

建議使用 SQLite WAL，讓 GUI_TEST_PC 單一 writer、PWA server reader：

`GUI_TEST_PC_DEV_20260703\runtime_pc\asset_inventory\asset_inventory.sqlite3`

最低資料欄位：

- `scan_id`
- `slot`
- `character_index`，1-5
- `character_name`，可空；若未做角色名稱 OCR，不可猜名稱
- `coins`
- `bound_crystals`
- `unbound_crystals`
- `weapon_empty`，true/false/null
- 每個欄位 confidence
- `status`，ok/partial/unknown/error
- `captured_at`
- `script_name`
- `playback_command_id`
- `evidence_path`

保留 scan history，另提供每個 Slot/角色的 latest view。資料列總數及 API 不可硬編碼為 75，但正式服務仍只允許目前 policy 支援的 Slot 1-15。

### 5. GUI_TEST_PC 播放整合

盤點結果必須綁定現有 Slot playback token／command id，確保：

- Slot 3 的 frame 不會寫到 Slot 4。
- 五個角色依實際已確認的角色切換次序寫入 character 1-5。
- 中止或漏步時，未擷取角色保持 `unknown`，不沿用成為本次掃描結果。
- 單一 Slot 的擷取失敗不可干擾其他 Slot 的修裝腳本完整播放。
- OCR 工作不可持有 Pico gesture lock，也不可讓下一個已錄製動作提早或延遲。

建議先完成一個只擷取、不 OCR 的 checkpoint probe，證明 5 張圖確實對應 Slot 1 的角色 1-5，再加入 OCR。

### 6. API 與 PWA

建議新增只讀 API：

- `GET /api/assets/latest`
- `GET /api/assets/latest?slots=1,2,3`
- `GET /api/assets/history?slot=1&character=1&limit=20`

若金錢按鈕只負責查看，按下後不得自動播放修裝模組。PWA 顯示：

- Slot 卡片 1-15，目前正式上限
- 每張 Slot 卡可展開 5 個角色
- 魔幣／綁晶／非綁晶／武器狀態
- 最後更新時間、完整／部分／未知狀態
- 資料過舊提示
- 未辨識顯示 `--` 或 `未知`，不可顯示 0

未來 20 Slot 只需讓資料與排版可擴充；在取得使用者明確 cutover 批准前，不修改 live Slot policy、window layout 或 Pico 支援上限。

## 新對話開始時必須先確認

以下問題不能猜：

1. 金錢符號按鈕是「只顯示最近一次修裝時收集的資料」，還是「按下後立即對選取 Slot 播放修裝並重新盤點」？目前較安全的建議是只顯示，另設明確的更新盤點動作。
2. 角色 1-5 是否固定對應左側由上至下的五個角色？是否要顯示角色名稱，還是只顯示角色 1-5？
3. 盤點應在每次播放 `修裝` 模組時自動執行，還是只有特定「資產盤點」模組／連串才執行？
4. 請提供 Slot 1 背包完全開啟的原始 1920x1080 client 截圖，至少包含：三項資產與武器欄；最好有 5 個角色各一張，並提供畫面上的正確三項數值作 ground truth。
5. 武器欄要檢查哪一格？如角色有多個裝備頁、造型覆蓋或武器耐久為 0，是否仍算「有武器」？
6. PWA 是否需要歷史趨勢／總和，還是只顯示每個角色最新值？

## 開發順序與驗收

1. Read-only 檢查 heartbeat、目前播放工作、launcher 狀態及最新 live source。
2. 建立 timestamped backup 及 `RESTORE.txt`。
3. 在隔離資料夾完成 Slot 1 checkpoint probe，不修改 live config。
4. 使用使用者提供的 ground truth 校準三個 OCR ROI 和武器 ROI。
5. Slot 1 五角色測試：5/5 身分與資產值正確，截圖證據可追溯。
6. Slot 1-2 並行測試：不可串 Slot、不可影響 Pico 播放完整性。
7. 才整合 GUI_TEST_PC heartbeat、只讀 API 及 PWA 金錢按鈕。
8. 15 Slot 驗收：75 個角色均有獨立 row；失敗顯示 unknown，不污染其他資料。
9. 部署前遵守 workspace atomic compatibility set 規則；停止 GUI_TEST_PC 與其 PWA server，但不停止 StarCG，除非使用者明確要求。
10. 部署後驗證 `/api/status`、fresh `gui_heartbeat.json`、`http://127.0.0.1:5111/health` Pico enabled，並確認沒有新 `OPLINK touch ERROR`。

## 禁止事項

- 不修改舊版 GUI_TEST 或舊版 OPLINK。
- 不以串流畫面作正式 OCR 來源。
- 不把 OCR 失敗或背包未開啟當成資產 0。
- 不在 GUI_TEST_PC 運行時修改 `config_pc\window_layout.json`。
- 不把 Slot 16-20 實驗改動部署到正式 1-15 Slot compatibility set。
- 不因 PWA server 重啟就宣稱部署成功；GUI_TEST_PC 才是輸入與 live policy owner。
