# 0.3.0 App 模式顯示

codex_app 卡片使用 worker.app 的 binding/last_seen 狀態；不以 supervisor 的 PID 推論 App 已連線，也不要求 Codex 再登入。接收服务停止、App 未綁定、最近無收件、等待回覆與處理異常分別顯示。連線說明按鈕只複製指定本機設定與技能路徑，不能從網頁直接改 App 排程。結果為 codex_app 時標示「App 回覆已接收；模型名稱未獨立驗證」。

# Standalone GUI contract — schema v1

Origin 是 config.endpoints.console；只接受相同的 127.0.0.1 origin。無 CDN／外部資源。所有 user/model 文字以 textContent 或 textarea 呈現，不能以 innerHTML 注入。0.2.0 cookie 名稱包含 runtime UUID，避免不同 localhost port 的控制台互相覆蓋工作階段。

GET /api/session 建立 HttpOnly SameSite=Strict cookie，回傳 csrf、server_time、profile_id（穩定 runtime UUID）與 config_sha256（此程序的啟動設定摘要）。POST 必須同 origin、cookie 與 X-CSRF-Token；Codex activity 使用獨立本機 bearer。瀏覽器與 CLI 都核對 profile，CLI 額外與本地設定比對摘要；瀏覽器未確認送出的 metadata 保存原 profile＋摘要，變更後不能續送。

| Endpoint | 回應／操作 |
| --- | --- |
| GET /health | service=relay-console，僅為端點健康 |
| GET /api/summary | profile, server_time, broker, leader, counts, worker, codex, submissions, submission_recovery |
| GET /api/tasks | status、direction、q、cursor 篩選，items／next_cursor／total |
| GET /api/task/{uuid} | task、manifest、events |
| POST /api/review | 保存原 payload 並派送；202 |
| POST /api/review/retry | client_request_id，續接原任務 |
| POST /api/worker | action=start 或 stop，actor=claude 或 codex（省略為 claude）；只管理該 supervisor 的指定 worker |
| POST /api/codex/activity | 只有明示主控台 CLI／API 可用；瀏覽器不呼叫 |

review body 必填 client_request_id、title、prompt、contexts，可選 recipient（claude 或 codex；省略為 claude）。contexts 最多 8 個 {name,text}，名稱不能帶路徑／控制字元；合併後 prompt 上限 64 KiB。相同 UUID＋相同內容回 deduped；不同內容或方向 409。完整 payload 在發送前落盤。瀏覽器不確定送出時保存方向、內容與身分，重新整理不會自動重播 POST。

submissions.state 可以是 dispatching、queued、completed、failed、uncertain、manual_review。retryable=false 或 manual_review 不顯示重試按鈕。永久 broker 拒絕標成 failed；傳輸／等待中斷可續接；journal 身分、內容或格式不一致需人工確認。submission_recovery 呈現保留但無法讀取的紀錄數，壞檔不會使其他任務或 GUI 全部停止，同 UUID 亦不能被覆寫重新送出。

workers.claude 與 workers.codex 分別回報 state、verified、provider_ready、model、effort；舊 worker 欄位維持 Claude 別名。verified 是自有 thread 存活查核，不是模型成功。unavailable 為未配置；auth_blocked 為登入／前置查核不可用，均無 mock fallback。codex 保留外部活動 TTL；背景 Codex 以 workers.codex 顯示。profile.codex_daemon 表示配置能力，workers.codex.codex_daemon 才表示當下 thread 存活。

detail manifest 記錄 prompt/result byte count＋SHA-256，sources 有原附文名／bytes／SHA。顯示的 prompt/result 經既有輸出淨化規則，內容摘要應按畫面所示資料解讀；來源附件 SHA 是送出時原始文字。timeline 只含已保存的送出、最近認領與完成／逾期事实，不捏造完整重試史或模型token进度。

回覆顯示 requested/reported model／effort與 execution_kind。mock_test／fixture_cli 明確標為測試替身；real_cli 的 model_execution_verified 只表示 adapter 成功解析 CLI 回報，並非獨立服務端模型身分證明。

輪詢每 5 秒，不重疊請求，頁面隱藏時暫停。離線保留最後畫面並顯示過期／錯誤，未知計數為 null，不轉成零。搜尋 debounce，翻頁使用 cursor。狹窄 viewport 會垂直堆疊面板。

## English / Traditional Chinese UI (source update)

The header language selector supports `en` and `zh-Hant`, stores `relay.language`
in optional browser localStorage, and falls back to the first supported browser
language or English. Document language, accessibility labels, placeholders,
status/flow labels, composer messages, binding controls and connection-copy text
change together. Dates use the selected locale with the fixed, visible
Asia/Taipei time zone.

`web/i18n.js` owns UI copy; Traditional Chinese source strings are catalog keys
and numbered placeholders are interpolated as text. Static markup opts in with
`data-i18n` / `data-i18n-aria-label` / `data-i18n-placeholder` / `data-i18n-title`.
Do not translate arbitrary DOM subtrees, user text, model output, protocol IDs,
paths, hashes or model identifiers. Dynamic views rerender from retained state;
language changes cause no network call, form reset, dispatch or new request UUID.
The binding catalog rerenders from its last observation without preparing a new
release. Pending release/request payloads and their runtime identity remain fixed.

Backend summaries preserve `app_connect_prompt` for existing clients and add
`app_connect_prompt_en` with the exact selected config and shared Agent Guide.
The static HTTP allowlist and source package both include `i18n.js`. Restart a
running old service before refreshing updated static files. English UI does not
choose an LLM reply language; specify that in the task instructions.

Tests cover localization completeness, placeholder integrity, browser-preference
selection/storage failures, preserved task data and uncertain-release retries
across language changes. The shared agent guide is available in [English](AGENT_GUIDE.md) and [繁體中文](AGENT_GUIDE.zh-TW.md).
