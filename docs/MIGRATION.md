# 升級到 0.3.0 的 App 模式

保留舊版封裝。先停止自己 profile 的 Relay supervisor，將新的來源放在新的目錄，使用新的 profile/runtime 或依原有身分規則原地更新。不要搬移 live SQLite 或登入資料。

把自己的 codex_provider 設為 `{"kind":"codex_app"}`，移除該欄的 command/model/effort，保留其他 provider 設定；執行 init 補上 app-mailbox 目錄，再 start。在目前 App 任務按照 [CODEX_APP.md](CODEX_APP.md) attach 並設定自動收件。新的工作只用原 App 登入；舊 codex-auth 會保留、不被讀取。

如原 runtime 已有 Codex 執行中／manual_review 的 CLI 任務，先處理原任務，不可透過切換 provider 強迫重跑。改動 profile digest 後，未確認的 GUI／CLI 送出仍須依原版恢復規則續接。

# 無中斷導入與回復

來源套件可解壓到新位置。配置使用自己的 project 根、fresh runtime 及尚未使用的 broker/console port。這不是將原資料夾改名：套件 imports、設定驗證、啟停 receipt 與來源 allowlist 均獨立於原環境。

1. 保持既有服務與 queue 運行。新 profile 選不同端點與全新的 runtime，先 init、doctor，驗證 unavailable 或自己的登入狀態。
2. 啟動新服務，確認程序完整身分、GUI profile_id、project 與端點。以新建 request UUID 做一筆小型驗收，等待保存的 receipt，重送同一 payload 確認沒有第二個 task。
3. 若使用真模型，先完成新 provider-auth 的官方登入，確認 requested/reported model、結果與執行證據。不能用舊服務的成功結果替新 provider 驗收。
4. 明確記錄切換時間，從該時刻開始只將「全新任務」送新端點。既有 pending/running/uncertain 任務與 UUID 留在舊 broker 完成或人工處置。
5. 觀察兩邊各自的任務與結果；跨 broker 不做自動 replay，不複製 live SQLite、登入或活動歷史。
6. 舊 queue 清空且原任務擁有者確認不再需要後，再另行安排舊服務停止。本套件不提供停止外部舊服務的命令。

回復時先停止向新端點新增任務，新 runtime 中既有工作保留其原 UUID 處理。後續全新任務可改回舊端點，避免將同一個不確定任務送到兩邊。保留所有原 receipt/journal；不以刪除資料來消除狀態。

更換程式版本也先停止自己的新 profile，保持 runtime 路徑與資料，驗證來源 manifest 後更換程式。執行中不覆蓋 source 或 config。0.1.0 尚未提供自動 DB schema downgrade 或跨主機私有 runtime 遷移。

## 0.1.0 升級 0.2.0

先以原設定 stop，確認程序已結束，再換 0.2.0 來源。設定 schema 維持 1，舊 provider 保留為 Claude，省略 codex_provider 時 Codex 未配置。舊 payload／UUID／controller／worker journal 不需改寫，省略方向仍送往 Claude。

如需 Codex，加入 codex_provider 後執行 init；它會核對既有 runtime 身分、確認服務停止，新增 codex-worker／codex-run／codex-auth 三個私人目錄，保留原登入與任務。分別登入、doctor 後再 start。尚未確認的瀏覽器送出綁定舊 config SHA，請在改設定前先完成續接；不要改寫其 sessionStorage 來跨設定重送。

不要用 0.1.0 開啟已新增反向任務的 runtime；舊版不理解其方向語意。回復時保留 0.2.0 runtime，由 0.2.0 完成或人工處理既有任務；以新的 profile 處理後續工作。仍未提供 DB downgrade、跨路徑搬遷或接管其他服務。
