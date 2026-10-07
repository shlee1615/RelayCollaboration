# Relay Collaboration 0.5.0

2026-09-07。新增 Windows x64 的 Relay.exe，使用端不必安裝 Python；原 Python 來源版仍保留。

- 同一 EXE 提供完整 JSON CLI；無參數雙擊時執行 launch，建立首次設定、啟動隱藏服務並開啟控制台。install 子命令直接初始化及安裝共用技能，Install.cmd 會辨識 EXE。沒有自動設定 Claude 登入或模型。
- 分離穩定的 EXE 安裝路徑與 onefile 暫存資源路徑。背景 supervisor 呼叫自己的 EXE，重設 PyInstaller 執行環境以獨立持有暫存檔；CLI 結束後 supervisor 可繼續運行。
- Windows 啟動後載入必要 runtime 相依並恢復標準 DLL 搜尋目錄，避免官方外部 CLI 繼承 PyInstaller 私有 DLL 路徑。沿用程序身分核對、私有 ACL、設定摘要與 graceful stop。
- 共用技能 binding 支援 executable／python，已管理的舊版技能仍可更新；程式與憑證不從 skill 目錄載入。新增由公開來源清單編譯、PE x64／ZIP SHA 驗證、build info 與 runtime 授權文件。

# Relay Collaboration 0.4.0

2026-09-07。新增 Windows 目前使用者共用的 Codex App／Claude Code 技能，以及可分享 ZIP 內的 Install.cmd 雙擊安裝入口。每台電腦重新建立路徑綁定、venv 與私人 runtime；不帶入另一台電腦的登入與歷史。

- `install.ps1 -InstallSkills both|codex|claude` 安裝對應技能，支援自訂搜尋目錄；每個技能的 `run-relay.ps1` 依本機 binding 從任意工作目錄呼叫 Relay CLI。
- 安裝前檢查所有目標，重複安裝不重寫相同檔案；保留未受管理或已人工修改的技能，跨安裝切換須明確 `-RebindSkills`。偵測寫入失敗時還原原檔；若程序被強制中斷，後續安裝會拒絕不一致的清單，需先處理。
- 新增 Claude Code Local 啟停／檢查／送件技能；Codex App 接收技能也能啟停服務。技能不授予額外 OS 權限，也不把 Claude Chat 對話變成 worker。
- 附安裝、升級、技能範例與通訊架構說明。通訊仍為 CLI＋本機 HTTP／JSON，Claude worker 使用官方 CLI；沒有新增 MCP、WebSocket 或網頁自動化。

# Relay Collaboration 0.3.0

2026-09-07。新增 codex_app provider，讓已開啟的 Codex App 任務直接收取 Claude 請求與完成結果，寫回分析／提案。此模式不需要另外登入 Codex，不使用額外 Codex 模型 CLI。

- 新增 app attach/inbox/reply/ack/status/detach/send，綁定 runtime 與目前任務，保留重送、回覆衝突、期限與斷線保護。
- 沿用既有 worker 的 broker claim／journal／completion，HTTP 与 file 均支援；回覆標記 codex_app，保留模型未獨立驗證。
- 控制台呈現 App 最近收件狀態，提供連線說明；附 App 連線技能、操作文件與安裝腳本 -CodexApp。
- 自動收件透過 App 的既有任務排程；不修改 App 私有資料庫、不複製登入、不提供外部即時推播。App 須保持執行，Claude 端仍須有效 provider。
- 新增 App ＋官方 Claude Code CLI 設定範本；Claude 網頁自動化停用，網頁範本不納入發行包。

# Relay Collaboration 0.2.0

2026-09-07。新增 Codex → Claude／Claude → Codex 的完整派工、接收與回覆路徑。GUI 可選方向，完成回覆可帶入另一方的新任務草稿；確認後送出，沒有自動無限迴圈。

- 新增 Codex CLI adapter 與独立的 codex-auth／codex-run／codex-worker；兩方各自啟停、預檢與記錄。
- Controller、GUI、CLI submit 的方向與原始 payload／UUID 綁定，重試不能改方向；保留既有 Claude 任務與 journal。
- Codex 使用唯讀提案模式，檢查實際停用能力的讀回，解析拒絕工具／失敗／重複完成事件。CLI 未回報的模型與 effort 保留未回報。
- 新增雙向設定範本、登入／升級文件，以及依 runtime 分離的 localhost session cookie。
- 修正 Windows PATH 同時含多個 Python 執行檔時的安裝／啟停命令解析，離線安裝在中文及空格路徑驗證。

驗收涵蓋 HTTP／file 真 broker 的雙向任務、各 worker 執行一次、重送／重啟恢復與單方停止。自動測試的模型回覆均為明示測試替身。新 runtime 尚未登入，兩個真模型的端到端驗收未執行；本機 Codex CLI 0.153.4 的前置檢查已驗證。此版不支援自動改檔、目前桌面對話的自動接手、跨 runtime 搬遷或外網發布。

# Relay Collaboration 0.1.0（歷史紀錄）

首個可分享的本機來源版本，Windows 優先。

本版新增 versioned profile、套件 imports、標準 CLI、離線安裝腳本、隱藏 supervisor 與 OS 程序身分核對、fresh runtime 初始化、GUI runtime 身分綁定、可配置 project/model/effort/endpoints、來源 zip 與 SHA manifest。移除既有機器／專案路徑及固定來源 SHA 的運行依賴。

保留 durable request UUID、雙向 broker、HTTP/file、Claude 無工具提案 adapter、完整純文字 context、結果資料流與來源摘要。worker/core 可有額外可選來源 pin，但預設沒有機器特定 pin，也不從 runtime 載入程式。

已知邊界：沒有 resident Codex executor；只有 Claude consumer 可自動認領。第三方 CLI 與登入須由使用者提供，CLI help/auth preflight 只證明前置條件。真模型執行需獨立 receipt。沒有強制停止、取消／renew task、live DB 搬遷或外網發布功能。跨 Windows 目錄重定位與本機測試不等同已驗證所有 OS／所有 Claude CLI 版本。

## 2026-09-14 source snapshot — English GUI and shared agent onboarding

- Added English / Traditional Chinese GUI with remembered browser preference,
  localized labels/errors/binding management and English App connection prompts.
  Switching language preserves draft contents, selected task and durable retry IDs.
- Added English README, Traditional Chinese README entry, shared AGENT_GUIDE.md,
  role-specific source skill guidance and SHARING.md. The guide covers fresh
  Windows installation outside Git runtime ancestry, per-instance CLI login,
  both dispatch directions, read-only receipts, scheduling and owner recovery.
- Extended the public source allowlist and added .gitignore for future clean
  repository preparation. No Git repository or remote publication is created.
- Verification: 287 Python tests (285 passed, 2 existing platform/model skips),
  15 frontend tests, actual browser language/draft/release checks and one official
  Claude Fable documentation review. These are not second-machine acceptance.
- This is an update to the 0.5.0 source line. The original 0.5.0 executable archives
  remain unchanged; a new executable version requires a separate build/acceptance.
