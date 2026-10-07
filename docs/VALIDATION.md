# 0.5.0 驗收紀錄

日期：2026-09-07，本機 Windows x64／Python 3.13／Node。

- 完整來源回歸：241 項 Python，239 通過、2 明確跳過（108.992 秒）；7 項前端測試通過。原有兩項跳過仍是原生 POSIX 與需獨立授權的真模型自動驗收。
- 新增 8 項 EXE 行為測試：frozen supervisor 正確使用 EXE／不帶 Python 參數、獨立 PyInstaller 環境、來源模式保留、設定不覆寫、啟動失敗不開瀏覽器、no-browser 選項、EXE 技能綁定／嵌入資源，以及 EXE 預設設定不受 cwd 影響。
- Windows binary builder 只從公開來源清單建立中性 staging，嵌入必要網頁／技能／設定資源；發行檔附 PE x64 驗證、每檔 SHA-256、BUILD_INFO 與 runtime 授權。

來源／fixture 測試不等於實際執行檔驗收；正式 EXE 的離線路徑、啟停、App mailbox、清理與限制另記錄於隨發行提供的交付說明與本機 work 驗收證據。Windows 打包不是模型 CLI 或登入打包，不代表已驗證第二台實體電腦或所有 Windows 環境。

# 0.4.0 驗收紀錄

日期：2026-09-07，本機 Windows／Python 3.13／Node。

- 完整回歸：233 項 Python，231 通過、2 明確跳過（106.249 秒）；7 項 Node 前端測試通過。跳過項目仍為原生 POSIX 權限／鎖整合與需要獨立登入的真模型自動驗收。
- 新增 9 項技能安裝測試：中文空格路徑、per-machine binding、重複安裝不重寫、第二目標衝突前不寫第一目標、人工修改保留、明確 rebind、來源模板保護、hardlink 拒絕、部分寫入失敗還原，以及實際 PowerShell 啟動器從無關工作目錄呼叫所綁定 profile、保留 CLI 退出碼與參數。
- Codex App／Claude Code 兩份 SKILL.md 均通過 skill-creator validator。共用技能安裝不讀取或複製 model auth，不初始化服務、不提交模型任務；runtime 初始化仍由安裝選項或 Agent 明確命令完成。
- 安裝與封裝使用明確公開檔案清單；新增 Install.cmd、兩端技能與本機 launcher。每台電腦生成的 installation.json／管理清單、runtime、工作目錄與登入不納入 ZIP。

本輪變更集中在安裝、技能與文件，沒有修改模型 adapter／任務傳輸核心。9 項技能測試包含多個驗證分支；不把分支數當成測試案例數。驗證使用同一台 Windows 的隔離目錄，並不代表已在第二台實體電腦、所有 OS 或 Claude Desktop GUI 驗收。技能命令入口可執行，也不代表本次既有 App 任務的技能清單已熱更新；需要時刷新或重開工作階段。

# 0.3.0 驗收紀錄

2026-09-07，Windows 本機。

- 完整回歸：224 項 Python，222 通過、2 明確跳過（113.777 秒）；7 項 Node 前端測試全通過。自動測試仍跳過需人工登入的真模型測試與原生 POSIX 整合；另行完成下述本機實機驗收。
- 目前 Codex App 收取測試題並回覆，不另外啟動 Codex 模型 CLI，codex-auth 目錄保持空白。
- 官方 Claude Code CLI 2.1.260 使用本工具新登入目錄，透過 Anthropic 自己的授權流程登入；版本、必要旗標、auth status 均通過。以 CLI 帳戶預設執行一筆提案，CLI 回報 `claude-opus-5`、退出碼 0、完成成功。模型名稱為 CLI 回報，effort 未回報。
- Claude 真回覆進入 App 收件匣，再將完整回覆交回目前 Codex App；Codex 回答設計驗證問題並透過原 delivery ID 回覆。兩個方向均 completed、claim_attempt=1；Claude worker 本次僅執行一次，沒有自動模型迴圈。
- 已驗證 App 綁定世代、期限、重送／衝突、斷線、資料完整性、本機 session／Origin／CSRF、HTTP/file 真 broker 與替身模型回覆。Claude 網頁自動化的 production attach／preflight／execution 均停用，沒有送出網頁模型請求。
- 36/36 移轉參考檔 SHA-256 保持一致；只核對新專案內的 migration_input。

實機證據保存在本機 `work/cli-app-live-evidence-v030.json`，不隨分享包散布。自動回歸與這次真模型傳遞不是整份程式碼的獨立 Claude 審查，也不是使用條款保證。App 仍需保持執行並定期收件；Windows 官方 CLI 更新後需重新驗證。

# 0.2.0 驗收紀錄（歷史）

日期：2026-09-07，本機 Windows／Python 3.13／Node。

- 完整回歸：211 項 Python，209 通過、2 明確跳過（83.410 秒）；6 項 Node 前端測試全通過。跳過項目仍為新登入真模型驗收與原生 POSIX 整合。
- 新增真 HTTP console／controller／SQLite broker 的兩個方向往返，涵蓋 HTTP 和 file transport；兩個 worker 每任務各執行一次、重送去重、重啟不重跑、停止 Codex 不影響 Claude。模型 callback 清楚標為 mock_test。
- Codex 子程序測試涵蓋 UTF-8、auth 未就緒、能力讀回失敗、deadline、implementation 拒絕、重複／工具事件、模型不符及未回報 model／effort。使用 fixture_cli，沒有真模型呼叫。
- 本機 Codex CLI 0.153.4，在新建空 codex-auth 目錄確認版本、exec 旗標、能力停用讀回成功；auth_ready=false。沒有挪用桌面登入，沒有模型執行。
- 真實瀏覽器從 Claude 的明示測試回覆建立 Codex 草稿，確認方向、兩份附件與原任務 ID，送出後保存一筆反向 pending 任務。工作階段更新時續接同 UUID 成功；中文及 script 文字安全顯示，方向篩選與資料流正確。

其後針對 localhost cookie 互相覆蓋的風險補上 runtime 名稱隔離與跨 port 共用 CookieJar 測試，另檢查 Codex runtime 路徑。直接相關回歸 46 項，45 通過、1 真模型項目跳過（37.712 秒）。發行包以外部 manifest 校驗實際 SHA；逐檔雜湊隨包提供。

首次候選 zip 的解壓安裝測試發現 PATH 含多個 Python application 時的命令解析錯誤，已修復 install/start/status/stop 腳本。新增具兩個同名候選的 Windows 安裝回歸測試通過（2.256 秒），包括中文空格路径、離線 venv 與 Codex 私人目錄初始化。首次候選保留於開發目錄，不作最終交付。

未執行兩方新登入真模型往返，亦未進行本次 Claude 外部程式複核。以下 0.1.0 的 Claude 審查只屬歷史證據，不延伸成對 0.2.0 的審查。

# 0.1.0 驗收紀錄（歷史）

日期：2026-09-07。實測環境：本機 Windows／Python 3.13。

## 自動測試

- scripts/test.ps1 -RequireFrontend：202 項 Python 測試，200 通過、2 明確跳過，耗時 67.519 秒；5 項 Node GUI 資料流測試全部通過。
- 完整測試後修正「續接已知 task 時仍顯示派送中」的狀態標籤，保留 queued 與原 task UUID；對相關 console／recovery／portability 再跑 49 項測試，48 通過、1 真模型驗收跳過（15.951 秒）。
- 兩個跳過項目：新 provider 的真模型驗收未配置；POSIX 原生 flock／permission 整合需原生 POSIX 主機。Windows 注入式測試僅驗證 POSIX 程式分支，不冒充 Linux 實機驗收。

涵蓋 broker 雙向 actor／HTTP/file／UUID 衝突與去重、worker durable journal／失聯完成回報／claim generation、CLI deadline／cancellation／輸出限制／模型一致性，以及 fresh runtime ACL／身分／設定摘要／跨程序鎖／Windows 正常啟停。

恢復故障測試包含：壞 JSON／不完整提交／folder UUID 不符／payload 與 prompt hash 不符，保留原檔並封鎖相關 UUID；永久拒絕、可續接 timeout 與 manual_review 分流；狀態寫入失敗不冒充已保存；activity 編碼膨脹不能損壞讀回。

## 重定位及瀏覽器

候選來源包的 17 項重新解壓檢查全部通過：中文及空格路徑、離線 .venv without-pip、fresh runtime UUID、動態獨立端點、隱藏程序身分、同 UUID 重送僅一筆 pending／claim 0、原 payload bytes、正常停止及 OS 證實程序退出。解壓位置以自己的 Python 再跑 23 項 portability／lifecycle 測試，22 通過、1 真模型項目跳過。

實際瀏覽器建立含中文字與 <script> 文字的本機驗收任務，確認內容安全呈現、task data flow、unavailable provider、重啟後保留原 task UUID，續接不新增任務。測試任務沒有模型執行。驗收 supervisor 已正常停止。

最終來源 zip 另須以同名 external manifest 執行 verify-package.py，並在獨立解壓目錄做一次 final artifact smoke；實際 zip SHA 與逐檔摘要在隨包 manifest，不把 archive 自身 SHA 寫進其內容形成循環。

## Claude 複核的範圍

對 controller／console backend 的指定來源快照，已透過經使用者明確授權的既有受控服務取得 Claude Fable 審查：reported_model=claude-fable-5、requested_model=fable、requested_effort=null、reported_effort=null／not_reported。這是來源程式碼複核；提出的恢復、鎖與尺寸缺陷已修復並回歸測試。

它不是新 standalone provider 的模型驗收，也不是對修正後所有程式碼的再次獨立審查。此獨立 runtime 未配置模型登入，provider unavailable／model_acceptance not_run。Codex 仍是外部主控台，沒有常駐模型 executor。

## 封裝與隔離

36/36 移轉來源 SHA 核對符合，migration_input 保持原樣。公開來源無原機器根／舊專案執行路徑或固定來源 SHA 耦合。zip 以指定 module／web／scripts／docs／config／test allowlist 建立；不含 migration_input、bootstrap 清單、agent log、work、私人設定、認證、歷史、SQLite 或第三方 CLI。

Windows 目錄重定位已驗證；Linux 整合、其他 Python／第三方 CLI 版本及新模型登入需要對各自環境重新驗收。runtime 和 journal 的跨主機／跨路徑搬遷不在此版本範圍，操作方式見 MIGRATION.md。
