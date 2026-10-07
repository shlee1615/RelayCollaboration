# 設定與 provider

## 選擇 Claude 與 Codex 模型

兩端分開選擇，與誰主控無關。Claude 主控派工給 Codex 時，接收端使用 `codex_provider`；Codex 派工給 Claude 時，接收端使用 `provider`。互動中的主控 Claude Code 對話仍在 Claude Code 自己的 `/model` 選擇，不受 Relay worker 設定控制。

使用同一份完整設定檔查詢（不呼叫模型）：

```powershell
python relay.py --config 'D:\RelayData\instances\research-a\relay.local.json' model show
```

`model show --actor claude` 或 `--actor codex` 可只查單方。結果區分 Relay 可設定的 CLI、由 App 選擇的模型及尚未配置的 provider；顯示的是設定值，並非正在執行或已驗證的實際模型。選單是便利預設清單，不是帳號可用模型清單，仍接受完整自訂 ID。

**CLI 模式**：等待既有工作完成，用原設定正常停止該 instance，再選擇並啟動：

```powershell
python relay.py --config 'D:\RelayData\instances\research-a\relay.local.json' stop
python relay.py --config 'D:\RelayData\instances\research-a\relay.local.json' model set --actor claude --model claude-opus-5-5 --effort default
# 下行只適用於已配置 codex_cli 的 instance：
python relay.py --config 'D:\RelayData\instances\research-a\relay.local.json' model set --actor codex --model gpt-6-astra --effort high
python relay.py --config 'D:\RelayData\instances\research-a\relay.local.json' start
```

- `--model default` 清除明確指定，儲存為 JSON `null`，執行時不傳 `--model`，使用該 instance 的 CLI 預設。
- 可選 `claude-opus-5-5`、`fable`、`opus`、`sonnet`、`haiku`，或自訂完整名稱；固定 ID 與會隨供應商變動的 alias 有別。Codex 便利選項包含 `gpt-6-astra`、`gpt-6-sol`、`gpt-6-luna`。
- 省略 `--model` 或 `--effort` 保留該欄原設定。`--effort default` 清除 effort 覆寫；新模型若不支援原 effort，請明確改成 `default` 或支援值。至少提供一個欄位。
- 寫入前驗證設定並保存原始位元組備份；已執行或程序身分不明時拒絕變更。使用與 start/stop 相同的 instance lock。保留另一方 provider、runtime 身分、登入與其他設定。
- 新設定在下次 `start` 生效；尚未認領的排隊任務也會使用新設定，任務不會各自鎖定提交當時的模型。既有歷史 receipt 不會改寫。
- `--instance NAME` 仍放在 `model` 前，且 `--config` 應指向基底設定；完整 instance 設定路徑則不要再加 `--instance`。

**目前的 Codex App 模式**：Relay 不覆寫 App 模型或推理強度，`model set --actor codex` 會明確拒絕並提示到已綁定的 Codex App 任務選擇。使用該任務輸入框下方的模型／推理控制。Claude 作主控也遵循此規則；單純要求選模型不會自動轉成 CLI、建立額外登入或更換綁定。Relay 網頁控制台仍顯示設定，切換入口是本指令與 QuickStart。

2026-09-23 查證：[Anthropic Claude Code 模型配置](https://support.claude.com/en/articles/11940350-claude-code-model-configuration)列出 `claude --model claude-opus-5-5`；[OpenAI Models](https://learn.chatgpt.com/docs/models)說明 App 選擇器與 `codex exec --model`。實際帳號、CLI 版本及模型／effort 支援仍由供應商決定。設定成功及 `doctor` 不代表已完成真模型驗收；執行後檢查 receipt 的 `requested_model`／`reported_model`，沒有回報就保持未驗證，不自動換成其他模型。

## 設定欄位

schema_version 必須是整數 1。未知欄位、重複 JSON keys、非有限數值會被拒絕。請從 config/relay.example.json 複製成自己的 relay.local.json。所有相對路徑以設定檔所在目錄解析，與執行命令時的工作目錄無關。

| 欄位 | 作用 |
| --- | --- |
| project.id | 此 profile 的專案 slug |
| project.root | 明確的專案根；模型不會因此自動讀檔 |
| runtime.root | 新建的私有 runtime；必須與 project.root 互不包含 |
| endpoints.broker | HTTP loopback origin，預設 http://127.0.0.1:9041 |
| endpoints.console | GUI origin，預設 http://127.0.0.1:9042 |
| transport | http 或 file；GUI 觀測仍透過 broker HTTP |
| provider.kind | unavailable 或 claude_cli |
| provider.command | 僅一個原生 Claude executable 的絕對路徑；不用 shell wrapper |
| provider.model | null 保留帳戶預設；明確名稱則驗證 CLI 回報的模型 |
| provider.effort | null 保留預設；顯式 low/medium/high/xhigh/max |
| provider.auth_profile | 僅 subscription |
| codex_provider | 可選；省略等同 unavailable。kind 為 unavailable、codex_cli 或 codex_app；command／model／effort／auth_profile 與 Claude 分開配置 |
| worker | 認領 TTL、timeout、poll、重查與允許路徑／模式 |

端點只接受 127.0.0.1、不同的 1024–65535 port。沒有遠端 broker 或外網發布模式。init 不會接管既有不具 Relay 身分的目錄，不會複製任何其他服務的登入或 SQLite。

範本：
- relay.example.json：HTTP，provider unavailable。
- relay.file.example.json：file 任務通道，provider unavailable。
- claude-default.example.json：Claude 帳戶預設模型／effort。
- claude-fable.example.json：明確 Fable 模型，預設 effort。
- bidirectional.example.json：兩方 CLI，模型／effort 各自使用 CLI 預設。
- codex-app.example.json：目前 Codex App，Claude 尚未配置。
- app-cli.example.json：目前 Codex App ＋官方 Claude Code CLI；填入自己的 Claude 執行檔並完成官方登入。

Claude 範本中的 executable placeholder 必須改成自己安裝的原生 CLI 絕對路徑，不能直接執行未填好的範本。Relay 不會依 task prompt 改變 model、effort、cwd、CLI 指令或工具權限。

## 使用既有 Codex App 登入

`codex_provider: {"kind":"codex_app"}` 沿用 App 自己的登入、模型與 effort，不接受 command 或顯式 model/effort。不啟動 Codex 模型 subprocess，也不讀取 codex-auth。採用 `config/codex-app.example.json`，或使用安裝腳本 `-CodexApp`。執行 `init`、`start` 後，由目前 App 任務 `app attach`。詳見 [App 連線流程](CODEX_APP.md)。`doctor` 的 App preflight 表示有效綁定與最近活動，不是獨立登入檢查。

## CLI 模式的私有登入

先停止自己的 Relay profile，完成設定及 init。對新 runtime/provider-auth 使用官方 Claude CLI 登入；不要複製其他程式的憑證。以下為明確使用自己的新目錄的 PowerShell 範例；請將變數換成自己的路徑：

~~~powershell
$relayCli = '<absolute native claude executable>'
$relayAuth = '<absolute fresh runtime directory>\provider-auth'
$relayRun = '<absolute fresh runtime directory>\provider-run'
$relayPrevious = $env:CLAUDE_CONFIG_DIR
Push-Location -LiteralPath $relayRun
try {
    $env:CLAUDE_CONFIG_DIR = $relayAuth
    & $relayCli auth login
} finally {
    $env:CLAUDE_CONFIG_DIR = $relayPrevious
    Pop-Location
}
~~~

登入後執行 doctor，再 start。doctor 會驗證安裝版本、所需 CLI help flags、官方 auth status；不呼叫模型。缺少 executable、flags 或登入時保持 unavailable/auth_blocked，工作不會被假模型消耗。

每個真模型驗收要保存完整 task UUID、requested/reported model、結果與執行證據。model=null 只表示帳戶預設，不能假定它一定是 Fable；若必須 Fable 請使用明確模型選擇並查看 reported_model。CLI 未回報 effort 時顯示 not_reported，不能宣称其與要求相等。

runtime/provider-run 和 provider-auth 必須沒有 .git 祖先；CLI 子程序有隔離的 PATH 與設定，移除自動工具／hooks／MCP／session persistence，拒絕 implementation 模式。這些是應用層限制，未宣稱完整 OS containment。

不要在服務運行中改設定。程序 receipt 綁定啟動時設定 SHA。先用原設定 stop，確認程序結束，再更改 model/effort 或端點後啟動。更換 project/root/runtime 身分需使用新的 fresh runtime；不要直接改既有 runtime 身分檔。

## 0.2.0 Codex 登入與執行

codex_provider.command 只接受一個自己的原生 codex.exe 絕對路徑。model=null 使用 CLI 預設；effort 可為 null、minimal、low、medium、high、xhigh、max、ultra，實際支援由安裝的 CLI／模型決定，無靜默替換。doctor 檢查版本、exec 旗標、停用能力的實際讀回及 ChatGPT 登入狀態。未通過不認領任務。

以自己的設定執行 init 後，在 PowerShell 登入（路徑依設定替換）：

~~~powershell
$relayCodex = '<absolute native codex.exe>'
$relayCodexAuth = '<absolute runtime directory>\codex-auth'
$relayCodexRun = '<absolute runtime directory>\codex-run'
$relayPreviousCodexHome = $env:CODEX_HOME
Push-Location -LiteralPath $relayCodexRun
try {
    $env:CODEX_HOME = $relayCodexAuth
    & $relayCodex -c 'cli_auth_credentials_store="file"' login
} finally {
    $env:CODEX_HOME = $relayPreviousCodexHome
    Pop-Location
}
~~~

子程序使用自己的 CODEX_HOME；登入在新 runtime/codex-auth，worker journal 在 codex-worker，cwd 在 codex-run。不要複製桌面程式或其他 runtime 的登入。doctor.provider_statuses 分別顯示 claude 與 codex；舊 provider_status 保留為 Claude。

Codex 強制 read-only、ephemeral、忽略使用者設定與規則，停用 shell、hooks、MCP 設定、apps、plugins、瀏覽器、電腦操作、子代理及主機技能探索等能力。解析拒絕工具事件／失敗／重複結束；這些是應用層防護，CLI 管理設定或版本變更需重新驗證，並非完整 OS 隔離保證。

成功 JSONL 可證明 CLI 完成；沒有回報 model／effort 時保持 null／not_reported，不用要求值冒充結果。介接依據：[非互動模式](https://learn.chatgpt.com/docs/non-interactive-mode)、[官方設定參考](https://learn.chatgpt.com/docs/config-file/config-reference)，並核對本機 CLI help。
