# 使用目前 Codex App

LLM 操作入口：[繁體中文完整指南](AGENT_GUIDE.zh-TW.md) | [English agent guide](AGENT_GUIDE.md)。
GUI 支援 English／繁體中文；於頁首切換，保留草稿與原請求識別。

這個模式讓你已登入的 Codex App 任務直接收件、分析、回覆。Relay 不會另外啟動 Codex 模型 CLI、不更改 CODEX_HOME，也不讀取或複製 App 登入資料。

## 第一次接上

1. 使用 `config/codex-app.example.json` 建立自己的本機設定。先執行 `init`、`start`。
2. 在要參與協作的 **現有 Codex App 任務**，請 Codex 讀取封裝內 `skills/relay-codex-app/SKILL.md`，指定本機設定檔並接上、自動收件。控制台 Codex 卡片的「複製 App 連線說明」會產生含正確路徑的提示詞。
3. App 會綁定目前任務，並透過 App 自己的任務排程每五分鐘收件。你可以繼續在同一個 App 任務交談；不用再次登入 Codex。

此技能随封裝提供，可由 App 直接讀取；不會自動安裝到全域技能或修改全域設定。需要可探索的技能時，可把此技能放入你自行選定的技能目錄，並保留封裝路徑參照。

Claude 端沿用原有 `provider` 設定。範例預設 unavailable：尚未設定 Claude 時，送往 Claude 的任務會保存等待，不能稱為已完成真模型雙向驗收。要自動呼叫 Claude，仍需有效的 Claude provider。兩家的帳號互不替代。

## App 內部使用的命令

從封裝目錄執行，將設定檔換成自己的絕對路徑：

```powershell
python -B -X utf8 relay.py --config 'D:\Relay\relay.local.json' app attach
python -B -X utf8 relay.py --config 'D:\Relay\relay.local.json' app inbox
python -B -X utf8 relay.py --config 'D:\Relay\relay.local.json' app reply --delivery-id '<原 delivery UUID>' --file 'D:\Relay\answer.txt'
python -B -X utf8 relay.py --config 'D:\Relay\relay.local.json' app ack --task-id '<Claude 回覆的 task UUID>'
python -B -X utf8 relay.py --config 'D:\Relay\relay.local.json' app status
python -B -X utf8 relay.py --config 'D:\Relay\relay.local.json' app detach
```

App 會提供目前 CODEX_THREAD_ID。從一般終端操作時，可在 `app` 後、子命令前指定 `--thread-id <App 任務 UUID>`；這是本機路由資訊，不是 App 身分的密碼或簽章。有目前任務環境變數時，不可指定另一個任務。

`app inbox` 有兩類資料：`requests` 是 Claude → Codex 的待回覆任務；`replies` 是本次綁定後完成的 Codex → Claude 結果，含失敗結果。先處理內容，再 `ack`；未 ack 的結果會再次出現。`next_cursor` 非空時可用 `app inbox --cursor <原值>` 繼續。收件不會自行派送下一輪。

App → Claude 使用 `app send --file <UTF-8 JSON file>`，接收者填 `claude`。檔案欄位為 `client_request_id`、`recipient`、`title`、`prompt`、`contexts`。UUID 與原始 payload 須先保存，重試沿用原件。這種送出只交付你指定的任務與附件，不擷取整個 App 對話。App 命令透過有 session／CSRF 保護的本機 HTTP API 交付，私有檔案由 Relay 服務寫入。

## 搭配官方 Claude Code CLI

`config/app-cli.example.json` 提供 Codex App ＋ Claude CLI 的設定。將範本複製到套件根目錄，再把 `provider.command` 換成自己安裝的官方原生 `claude.exe` 絕對路徑。Claude 使用自己的帳號與新 runtime/provider-auth；登入步驟見 [CONFIGURATION.md](CONFIGURATION.md)。原有設定請先停止服務再修改，重新啟動後由原 App 任務重新綁定。

Claude 網頁已登入不等於新 CLI 目錄已授權。官方 `auth login` 可開啟瀏覽器使用同一帳號完成授權；Relay 不收集密碼、不複製瀏覽器 cookie 或其他工具的登入。Claude 設定為 `model: null` 時使用 CLI 帳戶預設，成功回覆會顯示 CLI 實際回報的模型。

## 狀態與限制

- 「App 已連接」表示目前綁定有效且最近十五分鐘有收件活動；不代表 App 排程永遠正常或有即時推播。
- App 排程每五分鐘喚醒同一任務，須保持電腦與 App 執行。排程功能不可用時仍可在 App 任務中主動收件，工具不能自行喚醒 App。
- Relay 只在 App 最近有活動時認領新任務。已認領任務預設有十分鐘回覆期限；App 忙碌、關閉或超時會進入需人工確認，保留紀錄，不自動重跑模型。
- 同一 runtime 一次綁定一個 App 任務。重綁前先由原任務 detach；更改設定檔後也應重新綁定。停止 Relay 不會自動停止 App 排程；要完整斷線，請在 App 要求斷線並停止自動收件。
- 接收與回覆採本機私有 ACL、runtime 身分、綁定世代與不可重複的 delivery ID。App 與同一 Windows 使用者下其他程式的身分無法獨立密碼學驗證。輸出誠實標記 `execution_kind=codex_app`、`model_execution_verified=false`。
- Relay 不會自動執行模型輸出的命令或套用程式修改。App 接收流程只處理分析與提案；真正改程式仍在使用者授權範圍內另行進行。

實作採用 App 支援的[在既有對話中排程任務](https://learn.chatgpt.com/zh-Hant/docs/automations)與[本機技能](https://learn.chatgpt.com/zh-Hant/docs/build-skills)。這不是供外部程式任意控制 App 對話的公開推播 API。

## 解除綁定與衝突處理（建議入口）

### 不知道 instance 名稱時：直接用控制台

頁面上方現在顯示「目前 instance」與埠號。按 **管理連線／解除綁定**，會列出這份
設定目錄內登記的 base 與具名 instance、各自控制台網址、服務與綁定狀態、最近活動。
不必輸入 instance 名稱、任務 UUID 或錯誤訊息；「查看綁定識別」僅供需要時核對。

在目前控制台按 **檢查解除條件**，核對預覽後按 **確認解除此綁定**。最近仍有活動的
對話預設不可解除；使用者確定要接管時，先勾選對應選項再檢查。未收尾的 delivery 仍會
阻擋。需要處理另一份 instance 時，按它的 **前往此控制台管理綁定**；每份控制台只
能解除自己的綁定。已停止的 instance 不會因開啟清單而自動啟動。

瀏覽器會先保存原預覽再送出確認。若回應遺失，按 **重試原解除**；重新整理後仍使用
相同請求，不自動取得另一個 owner 的新預覽。瀏覽器操作記錄為 `local_console_operator`，
不冒充 Codex App 任務；由同一 runtime 的本機控制台 session／CSRF 權限執行。
解除會開放新連線，不會自動替任何任務 attach，也不會複製或重設登入與工作紀錄。

### 從 App 任務操作

一個 instance 仍只綁定一個 Codex App 任務。不同任務要各自獨立運作，請各自選用
`--instance NAME`；不帶 selector 會選原 base，與目前工作目錄無關。先看 `app status`
回傳的 `selection`、`binding_relation`、`thread_id`、`successor_thread_id` 和 `release_blockers`。

原任務還可操作時，在**原任務**執行 `app detach`，再讓新任務執行 `app attach`。
兩邊必須選同一 instance。detach 遇到未完成 delivery 會回 `release_blocked`；先完成回覆並
等待 worker 收尾，或透過既有 worker 停止流程處理取消，再重試，不刪除紀錄。

原任務無法操作、綁定給錯任務，或上次解除留下錯誤的交接預留時，使用新 `app release`。
以下命令在已取得使用者解除／交接授權的**操作任務**執行，不冒用舊任務身分：

```powershell
$relayLauncher = 'C:\Users\<使用者>\.codex\skills\relay-codex-app\run-relay.ps1'
& $relayLauncher --instance research-a app status
& $relayLauncher --instance research-a app release --file 'D:\Relay\release.json' --reason '使用者要求解除無法操作的舊任務'
# 核對預覽的 current_binding、release_blockers、can_release 及 payload，再執行原檔案：
& $relayLauncher --instance research-a app release --file 'D:\Relay\release.json' --confirm
# 接著由真正要接手的新任務執行：
& $relayLauncher --instance research-a app attach
```

預覽會建立新 UTF-8 檔案，但不解除綁定、不覆寫已存在的檔案。確認與重試只用原 `--file`
和 `--confirm`，不可混入新的 reason、target 或 allow-active。預設解除後開放重新綁定，
不會預留給執行維護的任務。若要防止其他 heartbeat 搶先 attach，請在**預覽**時加入
`--successor-thread-id '<真正接手的任務 UUID>'`，再交由該任務自行 attach。
這個參數是交接目標，不是執行者身分；不能用 `--thread-id` 取代。

仍有最近十五分鐘心跳的 owner，預設不允許解除。使用者明確要求接管仍活躍的綁定時，
可在預覽加入 `--allow-active`；它只略過心跳逾期條件，仍會檢查完整綁定快照與 delivery。
`stale` 本身不等於使用者已授權接管。既有預留可再用同一流程清除或轉交，不需要等十五分鐘。

回覆 `binding_changed` 表示預覽後 owner 活動、世代或預留已改變：重新檢查並建立新的預覽
檔案，不自動改條件重試。`release_blocked` 表示仍有 waiting／delivered／answered delivery，
即使期限已過也不跳過。owner 的 inbox、send、reply、ack 都會更新活動時間。

回覆 `service_upgrade_required` 表示**正在執行的服務**尚未載入新解除 API。確認該 instance
沒有 pending/running 工作後，使用更新後的來源版對同一 selector 正常 `stop` 再 `start`；
保留設定、runtime、登入和全部任務。只有更新檔案而沒有重新啟動，舊程序仍然是舊程式。
舊 0.5.0 EXE 也不會因修改 Python 原始碼而更新。新指令在此錯誤下不送出解除操作。

每次解除會保存 runtime/config、原綁定 SHA-256、操作者、原因、目標與固定 UUID receipt。
當機恢復和舊 UUID 重試不會解除後來的新 binding；跨 instance 的解除檔案會拒絕。
不會重設 Claude 登入、broker 任務或舊回覆。舊 delivery 不移交給新世代；原 Claude 結果
仍可用原 request ID 的 `receipt`／控制台查閱，不能另開新 UUID 當成重試。

若原任務有自動收件 heartbeat，須透過 App 排程工具暫停匹配的排程；解除不會自行修改
App 排程。開放綁定前應先處理該排程，或選擇指定 successor。另一份 instance 的
`auth_ready=false` 則是該份 Claude CLI 尚未登入，解除 App binding 不會解決這項問題。

## 舊 release-stale 檔案的相容流程

`stale` 表示十五分鐘未收件，並未放棄擁有權。普通 `attach` 不會接管另一個任務。
舊版 CLI 將 HTTP 409 一律顯示為「retain the same UUID and payload」，實際可能是
`different_app_task`。修正版只顯示白名單內的錯誤代碼與固定建議，不反射伺服器任意文字。

原任務可操作時，優先請它執行 `app detach`，再由新任務執行 `app attach`。
不要在新任務覆寫 `CODEX_THREAD_ID` 或以 `--thread-id` 冒充原任務。

若原任務無法操作，使用者可明確授權在目前任務執行 `release-stale`。此命令是本機
操作者復原，並非原任務自行 detach 的證明，也不是獨立的 App 身分驗證。

1. 確认服務和設定相符；執行 `app status`，保存 `binding_id`、`last_seen_at`，檢查
   `release_blockers` 為空。若仍有 `waiting`、`delivered` 或 `answered` delivery，即使
   deadline 已過也會拒絕解除，須先讓原 worker 完成收尾或保留紀錄人工排查，不能刪檔跳過。
2. 在授權專案範圍建立 UTF-8 JSON，例如 `release.json`：

```json
{
  "request_id": "<為此次解除新建並保存的 canonical UUID>",
  "binding_id": "<app status 回報的完整 binding UUID>",
  "last_seen_at": 1788803981.0476182,
  "reason": "使用者授權解除無法操作的 stale 任務，讓目前任務重新連線",
  "confirm_release": true
}
```

`last_seen_at` 必須逐值使用狀態回報；範例數字不可直接套用。

3. 從新任務執行，沿用同一設定：

```powershell
python -B -X utf8 relay.py --config '<設定絕對路徑>' app release-stale --file '<release.json 絕對路徑>'
python -B -X utf8 relay.py --config '<設定絕對路徑>' app attach
python -B -X utf8 relay.py --config '<設定絕對路徑>' app inbox
```

已安裝的 `run-relay.ps1` 同樣可轉送以上子命令。只重試原檔案、原解除 UUID、原發起任務。
回報 `binding_changed` 表示世代或活動時間已變，須重新檢查，不能自動刷新條件強制接管。
`binding_not_stale` 要由仍活躍的原任務 detach。新 binding 僅保留給發起復原的目前任務，
防止原 heartbeat 在解除與 attach 間搶回擁有權。完成重新綁定後，重送舊解除請求只取得
原 receipt，不會解除新 binding。

解除在 mailbox lock 內比較世代、活動時間、TTL 與未完成 delivery。原子寫入
`release-pending.json` 是提交點；binding 投影和 `releases/<request_id>.json` receipt
若因當機未完成，下次 mailbox 操作會先續接同一個解除。保留這些檔案，禁止手動刪除 pending
以繞過復原檢查。解除不改動 broker、provider auth、既有 delivery、通知或 worker journal；
舊 delivery 仍屬原世代，不能由新任務回覆或重跑。

`app send` 現在也經 `/api/app` 核對 owner，再交給原本的 review/controller UUID 去重流程。
原任務在解除後不能繼續用 `app send`。共享 Windows 使用者仍可自行操作通用控制台 API；
這項路由檢查不是對同一作業系統使用者的安全隔離。

原任務若有 heartbeat，應由 App 排程工具暫停它。解除不會讀取或修改 App 排程私有檔案，
也不會自動替新任務建立 heartbeat。舊版只以 attach 時间掃描 Claude 回覆，重新綁定前
已完成但未讀的回覆仍留在原任務紀錄／控制台，不會假稱已移交或自動 ack。
