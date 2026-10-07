# 0.3.0 Codex App 收件

App 模式由目前已登入的 App 任務處理，背景 worker 只保有 broker claim 並等待回覆。自動收件需 App 自己的 heartbeat；詳見 [CODEX_APP.md](CODEX_APP.md)。停止接收服务不會自動停 App 排程；完整斷線請 app detach 並由 App 暫停其匹配的 heartbeat。App 關閉／忙碌導致逾時時，保留原 journal/配送紀錄，禁止自動另建 UUID 重跑。若 supervisor 在模型回覆與 broker completion 之間崩潰，仍採既有保守 manual_review 行為，App 留存的回覆不視為已完成 broker 紀錄。

# 操作與恢復

## App binding 復原來源更新

新版 `app release` v2 提供明確預覽／確認、開放解除、指定交接及預留清除；操作見
[CODEX_APP.md](CODEX_APP.md)。`/api/session` 的 `app-release-v2` capability 表示該服務
已載入這項 API，不能只看磁碟上檔案或版本字串推定服務已更新。每份需分別正常重啟。

v2 pending／receipt 保存原完整 binding 與其 SHA-256，恢復只接受原快照或精確的解除
投影。與 v1 receipt 並存；舊 UUID 只回傳原結果。回滾至只懂 v1 的來源前，必須先用新
程式完成所有 pending 恢復，且評估 v2 receipt 無法再由舊版重試的限制；保留所有 receipt，
不得刪除來迴避版本檢查。正常 owner detach 也會阻擋未收尾的 delivery。

stale 解除與條件、錯誤處理見 [CODEX_APP.md](CODEX_APP.md)。這是來源修正；既有 0.5.0
EXE／ZIP 不會因更新來源自動改變。使用來源服務時，先確認 pending/running 為 0，依相同
設定正常 stop，保留 runtime、auth、設定與全部任務資料，再 start 載入更新。若 stop 不能
確認完成，不強制殺程序或接續啟動第二個 supervisor。

回滾只還原此次變更的公開程式／文件，且須先停止同一 profile、確認
`release-pending.json` 不存在；若仍存在，先以修正版完成同一解除的復原。不要回滾 binding、
delivery、receipt 或 broker SQLite，不重新初始化 runtime。舊版不認得解除中的新操作；
若已解除但仍保留 successor 預約，先以修正版完成預定任務的 attach，才可回滾，因為舊版
不會強制執行這項預約。回滾後繼續使用 owner detach/attach 流程，保留已完成解除的 audit receipt。

具名 instance 的 runtime 無須遷移。回滾到沒有 `--instance` 的舊來源／EXE 時，直接使用
該 instance 的完整 `--config` 路徑來啟停，保持新舊 runtime 各自原位。`receipt` 是新增
端點；只更新 CLI 而未重啟服務時可能得到 HTTP 404，先在該份 instance 閒置時正常重啟。

## 啟停

從套件根執行 scripts/start.ps1、status.ps1、stop.ps1，提供 -Config；或使用相同 Python 的 relay.py。第一次先 init，doctor 不呼叫模型。

start 啟動 CREATE_NO_WINDOW 背景 supervisor，使用固定 Python 與來源入口、明確設定路徑、私有 runtime cwd。它先查設定端點是否被占用，絕不停止占用的程序。GUI 各方啟停按鈕只管理此 supervisor 所擁有的指定 actor thread；停止整個服務時先通知兩方停止，再等待退出。

status 對實際 OS 比對 PID、建立時間、executable、原始 command line，再比對設定摘要。worker 的 thread liveness 與 provider auth readiness 分開。JSON snapshot、端點健康或 PID 存在，單獨都不是模型工作證據。

stop 使用核對過的 instance UUID 寫停止要求。只等待正常結束，不猜 PID、不強制殺程序、不影響其他 profile。超時保留 stopping/manual-review；查看同一份設定的 status 及私有 service.log。仍有執行 callback 時保持 actor lock，避免另一 consumer 重做任務。

## 重試

GUI 新增任務可選兩個方向，限純文字附件（最多 8 個，合併後 64 KiB），不依輸入路徑讀伺服器檔案。CLI payload 同格式，以下送往 Codex：

~~~json
{
  "client_request_id": "<使用者或呼叫端先保存的 canonical UUID>",
  "recipient": "codex",
  "title": "Review example",
  "prompt": "Please review only this supplied context.",
  "contexts": [{"name": "example.py", "text": "def add(a,b): return a+b"}]
}
~~~

~~~powershell
python -B -X utf8 .\relay.py --config .\relay.local.json submit --payload .\review.json
python -B -X utf8 .\relay.py --config .\relay.local.json retry --request-id <same UUID>
~~~

同 UUID 內容不能變更。送出 timeout 後先查 GUI 既有任務，再續接原送出。未配置 provider 時任務可保留 pending；等待中斷可變 uncertain，這不表示未建立。不要新增另一筆代替不確定的任務。

Codex 活動使用 activity 命令明示回報，TTL 15–900 秒；頁面輪詢不會刷新活動 TTL。這是主控台的文字回報，不是 Codex 模型執行證明。

## 私有資料與備份

runtime 全部私有：broker SQLite／credentials、controller mutation journal、worker SQLite、原始 payload／prompt、結果、provider-auth、程序紀錄。只備份自己已停止的 profile，並保持原有路徑／身分和權限；此版本沒有跨 runtime journal 搬遷工具。分享使用 scripts/package.py 的來源 allowlist，不能手動 zip 整個工作目錄。

同使用者程式或管理员可干預檔案與程序，本機 ACL、Origin／CSRF 和 path checks 不隔離這類對手。模型環境為無工具設定，並非任意程式執行 sandbox。第三方 CLI 升級或 managed setting 改變需重新跑 doctor 和真模型驗收。

## 開發與封裝

~~~powershell
python -B -X utf8 -m unittest discover -s tests -t . -v
node --test .\tests\test_frontend.mjs
python -B -X utf8 .\scripts\package.py
~~~

Node 僅供 GUI 純函式測試，執行 Relay GUI 不需要 Node。所有測試生成資料應置於 work；測試替身不能做真模型可用性的驗收。
