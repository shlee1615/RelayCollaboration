[English](README.md) | 繁體中文

**2026-10-07 來源快照：**完整安裝、架構、雙向操作、故障恢復與打包方式見[完整使用說明](docs/USER_MANUAL.zh-TW.md)。開始前請讀[發行狀態與已知 CLI 相容性問題](docs/RELEASE_STATUS.md)。本次發布來源，不包含重新建置的 EXE；專案尚未選定開源授權。

**一般使用從這裡開始：**雙擊 **QuickStart.cmd** → 完成 Claude 官方登入 → 將產生的連線指示貼到 Codex（需要反向派工時也貼到 Claude）。
中英文精靈會處理 Python、連接埠、獨立 instance 與設定檔。詳見[三步快速啟動](docs/QUICKSTART.zh-TW.md)。

**模型選擇：**精靈已加入 Claude Opus 5.5、alias 與自訂名稱。使用 `relay.py --config <完整路徑> model show` 查詢兩方設定；停止的 CLI instance 可用 `model set --actor claude|codex --model <名稱>` 分別切換，再啟動。Codex App 模式請在已綁定任務內選模型，Claude 主控時也相同。網頁控制台顯示設定，選擇入口為精靈或 CLI；詳見[模型選擇與設定](docs/CONFIGURATION.md)。

**新來源功能：英文／繁體中文 GUI 切換與 Agent 啟動指南。** 語言偏好會保存在瀏覽器，切換不會重送任務或改變草稿。雙方 LLM 請先讀 [共用 LLM 操作指南（繁體中文）](docs/AGENT_GUIDE.zh-TW.md)（[English](docs/AGENT_GUIDE.md)），依角色啟動、連線、派工與收件。分享 GitHub 前使用 [乾淨封裝流程](docs/SHARING.md)。既有 0.5.0 EXE 尚未更新。

# Relay Collaboration 0.5.0

**來源更新：獨立多 instance。** 使用 `instances create --name research-a` 建立，之後每次
以 `--instance research-a` 選用；各份擁有獨立 runtime、連接埠、登入、App binding 與任務。
支援 Codex 主／Claude 副和 Claude 主／Codex 副，操作見 [多 instance 雙向派工](docs/INSTANCES.md)。
既有 0.5.0 EXE／ZIP 保留原版，尚未納入這份來源更新。

0.5.0 新增 Windows x64 的 `Relay.exe` 發行包，內含 Python runtime，使用端不必安裝 Python。支援目前 Codex App ＋官方 Claude Code CLI。Claude 網頁自動化維持停用。官方登入、用量與分享範圍見 [整合條款紀錄](docs/SERVICE_TERMS.md)。

**執行檔版：**將 `relay-collaboration-0.5.0-windows-x64.zip` 完整解壓到可寫入目錄，雙擊 **Relay.exe**，會建立乾淨設定、啟動背景服務並開啟瀏覽器控制台。雙擊 **Install.cmd** 可另裝兩端共用技能；它會自動使用 EXE，不建立 venv，也不需要 Python。新電腦的官方 Claude Code CLI 仍須自行配置並登入。

Codex App 輸入 `$relay-codex-app 請啟動 Relay 並連接目前任務`；Claude Code Local 可輸入 `/relay-claude-code 請啟動 Relay`。以下原有 Python 命令適用於來源版；EXE 版把 `python relay.py` 換成 `Relay.exe` 即可。完整步驟、技能位置、升級與通訊架構見 [安裝與 Agent 操作](docs/INSTALLATION.md)。

```powershell
.\Relay.exe --version
.\Relay.exe status
.\Relay.exe stop
```

本機 Codex／Claude 雙向協作工具：選擇接收者、保存任務、送交審查／提案、追蹤輸入與回覆。新增 Codex App 模式：沿用目前 App 登入與任務收件，不再另開 Codex 模型 CLI。Claude 端依所選 provider 配置。

- 有持久化 UUID、同內容重送去重、HTTP 與 file 協定，以及中英文任務資料流 GUI。
- 一個隱藏 supervisor 管理 broker、GUI 與各自獨立的 Claude／Codex consumer。兩方可自動認領寄給自己的任務，分別啟停。
- GUI 可選 Codex → Claude 或 Claude → Codex。完成後按「把回覆交回另一方」，會帶入原文與回覆草稿，確認後送出下一輪。
- 預設兩方 provider 均為 unavailable。可保存任務，配置並登入後才會處理，沒有模擬模型 fallback。
- 模型只收到明確附上的文字與 PNG 圖片，回覆為審查／提案，程式不會自動執行回覆中的命令。

## 使用目前 Codex App（新增）

在解壓後目錄執行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -CodexApp -Initialize
.\.venv\Scripts\python.exe -B -X utf8 .\relay.py --config .\relay.local.json start
```

開啟 [App 模式控制台](http://127.0.0.1:9142)，在 Codex 卡片複製連線說明，貼到目前的 Codex App 任務。App 會綁定目前任務，透過 App 任務排程自動收件。Codex 不需要再次登入。安裝會保留既有設定；若已有 0.2 設定，先依升級文件切换 `codex_provider.kind` 為 `codex_app`。

完整流程與限制見 [Codex App 連線](docs/CODEX_APP.md)。搭配 Claude CLI 可使用 `config/app-cli.example.json`，填入自己安裝的官方執行檔路徑，依 [登入步驟](docs/CONFIGURATION.md) 完成新 CLI 授權。Codex 仍使用目前 App 登入。App 關閉或沒有排程時不能即時收件；Claude 尚未配置時，送往 Claude 的任務仍會等待。

## 原有獨立 CLI 模式

需要 Windows 上可執行的 Python 3.11+。執行以下命令時，工作目錄是解壓縮後包含 relay.py 的目錄。套件執行僅使用 Python 標準函式庫，沒有網路下載步驟。

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Initialize
.\.venv\Scripts\python.exe -B -X utf8 .\relay.py --config .\relay.local.json doctor
.\.venv\Scripts\python.exe -B -X utf8 .\relay.py --config .\relay.local.json start
~~~

使用瀏覽器開啟 [預設本機控制台](http://127.0.0.1:9042)。端點可在設定中更改。start 不會開啟視窗或瀏覽器。

~~~powershell
.\.venv\Scripts\python.exe -B -X utf8 .\relay.py --config .\relay.local.json status
.\.venv\Scripts\python.exe -B -X utf8 .\relay.py --config .\relay.local.json stop
~~~

安裝腳本建立本機 .venv 和乾淨設定；加上 `-InstallSkills both` 可安裝共用技能（Install.cmd 已包含）。也可直接使用既有 Python 執行 relay.py。如需標準 pip 套件安裝，先自行準備 setuptools>=68，執行 python -m pip install --no-build-isolation --no-deps .；這不是離線啟用的必要步驟。

doctor 的配置有效與 provider ready 是不同欄位。預設應顯示 unavailable／auth_ready=false；不表示真模型驗收通過。

## 啟用雙向模型處理

將 config/bidirectional.example.json 複製到套件根作為 relay.local.json，填入自己安裝的兩個原生 CLI 絕對路徑。先 init，分別在 runtime/provider-auth 登入 Claude、runtime/codex-auth 登入 Codex，再執行 doctor／start。完整登入命令與升級步驟見 CONFIGURATION.md 與 MIGRATION.md。

GUI「新增協作任務」可選方向，填寫文字並附上最多 8 份文字檔案。CLI submit 的 JSON 增加 `"recipient": "codex"` 即可送往 Codex；省略時維持送往 Claude。兩個方向共用同樣的 UUID 去重與恢復機制。同 UUID 不能更改方向。

自動處理範圍是審查／提案，沒有自動來回迴圈或自動改檔。只有 codex_cli 模式使用獨立 codex exec 子程序；codex_app 模式由已綁定的 App 任務收件及回覆。既有 activity 回報另行保留。Codex JSONL 可能不回報模型／effort，畫面會如實顯示未回報。

## 文件

- [安裝與 Agent 操作、CLI／HTTP 架構](docs/INSTALLATION.md)
- [連接目前 Codex App](docs/CODEX_APP.md)
- [設定與 provider 登入](docs/CONFIGURATION.md)
- [持久化與 HTTP／file 協定](docs/PROTOCOL.md)
- [GUI 契約](docs/GUI_CONTRACT.md)
- [啟停、失敗恢復與資料保管](docs/OPERATIONS.md)
- [無中斷導入](docs/MIGRATION.md)
- [驗收範圍](docs/VALIDATION.md)
- [版本說明](docs/RELEASE_NOTES.md)

發行 zip 以明確來源清單建立，附各檔大小／SHA-256 manifest；不含私有 runtime、登入、queue、SQLite、工作歷史或第三方模型執行檔。不要將自己的 runtime 加進分享包。來源程式包含的路徑檢查、無工具模型設定與 ACL 是操作防護，並非隔離惡意同使用者程式的 OS sandbox。
