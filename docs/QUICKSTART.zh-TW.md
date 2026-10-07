# 三步開始雙向協作

[English](QUICKSTART.md) | 繁體中文

一般使用者從這裡開始即可。快速啟動精靈適用於目前的 Windows 來源碼套件，需要已安裝 Python 3.11 以上、Codex App 與官方 Claude Code CLI。

## 1. 雙擊 QuickStart.cmd

解壓縮套件後，雙擊根目錄的 **QuickStart.cmd**。選擇中文或英文，接著選擇資料目錄與 instance 名稱；一般可直接按 Enter 使用預設值。

精靈會準備本機 Python 環境、自動分配可用連接埠、建立獨立 instance，並尋找具有有效 Anthropic 發行者簽章的原生 Claude CLI。無須手動編輯 JSON 或設定登入目錄。

資料預設放在套件內的 `local-data`。如果來源碼位於 Git 儲存庫，資料目錄請改填儲存庫外的位置，例如 `D:\RelayData`。

## 2. 依提示完成 Claude 登入

首次使用時，精靈會詢問是否開啟這個 instance 的官方 Claude 登入。照畫面完成登入即可。若尚未安裝或找不到 Claude，精靈會讓你填入官方 `claude.exe` 路徑，也可以稍後再設定。

CLI 設定完成且 instance 已停止時，精靈會先顯示模型選單：CLI 預設、Opus 5.5、Fable／Opus／Sonnet／Haiku alias，也可輸入完整模型 ID。Enter 保留目前設定；選擇後可一併填 effort，`default` 恢復預設。執行中的 instance 會保留原模型，請正常停止後再選。Codex App 的模型則在已綁定的 App 任務中選擇；由 Claude 主控時也相同。

每個獨立 instance 使用自己的登入資料；相同資料目錄與 instance 名稱會沿用原環境。新增另一個名稱，就能建立另一組獨立協作。相同帳號的用量限制仍共用。

## 3. 貼上連線指示

精靈會開啟控制台，並顯示兩段已填好路徑的連線指示：

- **Codex 段落**：貼到要參與協作的 Codex App 任務。
- **Claude 段落**：需要 Claude 主動派工給 Codex 時，貼到 Claude Code 對話。

同一份內容會保存在該 instance 的 `CONNECT.zh-TW.txt`，之後可再次開啟複製。兩邊必須使用指示中的同一份設定檔。

控制台啟動後，仍需等 Claude 顯示就緒、實際的 Codex 任務完成連接，才能進行完整雙向派工。精靈不會冒充 Codex 綁定，也不會自動解除其他任務的綁定。若有衝突，請用控制台的「管理連線／解除綁定」檢查，或使用另一個獨立 instance。

## 之後如何使用

再次雙擊 **QuickStart.cmd**，選同一個資料目錄及名稱，就會沿用原 instance。已設定的 CLI、指定模型、登入資料與綁定都會保留。精靈不會自動切換既有 CLI；若 Claude 更新導致原路徑失效，先正常停止該 instance，再使用下方 `-Claude` 參數指定新官方路徑。

自動收件仍由 Codex App 任務的 heartbeat 負責。精靈產生的指示會請該任務在工具可用時設定五分鐘收件；沒有自動化工具時，LLM 應明確回報只能手動收件。

## 給 LLM：一個啟動指令

在使用者授權的資料目錄建立或恢復 instance，無須手動串接 install、create、init、doctor 與 start：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File 'D:\Tools\Relay\scripts\quickstart.ps1' -NoWizard -DataDir 'D:\RelayData' -Name research-a -Language zh-TW -Start
```

請替換套件與資料路徑。此入口回傳 JSON；以 `config` 與 `console_url` 確認實際選擇，再將 `connection_instructions` 交給預定的 LLM。`ok=true` 只表示環境操作成功，完整雙向就緒要看 `ready_for_both_directions`，並分別判讀 `claude` 與 `codex`。

可加上 `-Claude 'C:\實際安裝位置\claude.exe'` 明確設定路徑；只有所選 instance 已停止時才允許修改。原始設定會備份，模型與 effort 保留。若原官方 CLI 已設定，單純重跑精靈不會自動換成另一個版本。

只有使用者已授權、且可以進行互動式登入時，才加入 `-Login`。登入會使用該 instance 自己的目錄，透過官方 `auth login` 進行；不複製登入資料、不呼叫模型。正在執行的 instance 必須先正常停止，再變更 CLI 或登入。

省略 `-Start` 可只準備環境與檢查。`-NoDetect` 可停用自動尋找 CLI。精靈不安裝全域 skills；兩端直接讀取套件內的 skill 範本，因此不會改動既有全域安裝綁定。

停止的 instance 可加入 `-Model claude-opus-5-5 -Effort default` 明確選擇 Claude；省略就保留原值。對既有完整設定檔使用 `relay.py --config <完整路徑> model show`，CLI 雙向模式可用 `model set --actor claude|codex` 各自設定。詳見[模型選擇與設定](CONFIGURATION.md)。

若使用者指定的是既有設定檔絕對路徑，請直接沿用原本 `relay.py --config <完整路徑>` 的入口，勿擅自改成精靈的預設資料目錄。進階操作與疑難排解見[完整 LLM 操作指南](AGENT_GUIDE.zh-TW.md)。
