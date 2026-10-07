# 2026-09-07：整合方式與官方條款查核

此為實作決策紀錄，不是法律保證。使用者已選擇「目前 Codex App ＋未修改的官方 Claude Code CLI」供本人在本機使用；Claude 網頁自動化維持停用。

## Claude 網頁

查閱 [Anthropic Consumer Terms](https://www.anthropic.com/legal/consumer-terms) 第 3 節：除透過 Anthropic API Key 或另有明確許可，禁止以自動或非人工方式存取；同節也限制擷取服務資料。條文適用 Claude.ai 等個人服務。

因此，「沿用 Chrome 的 Claude 登入，由程式自動送出提示並讀取回覆」不能僅因使用者已有訂閱／登入就視為獲准。本專案沒有 Anthropic 對這種整合的另行許可，已停止並解除剛建立的網頁收件綁定；未送出任何 Claude 網頁模型請求。實驗性協定的 production attach/preflight/execution 均停用，網頁設定範本不納入分享包。CLI 的官方瀏覽器授權流程與自動操作消費者聊天網頁是不同用途。

## Claude Code／API

[Claude Code 官方法律與合規文件](https://code.claude.com/docs/en/legal-and-compliance) 區分使用者登入未修改的官方 Claude Code 與第三方產品整合。文件要求第三方開發者使用適用的 API／雲端認證，並限制中介 Claude.ai 憑證或代表使用者路由訂閱流量；同時列出官方 Claude Code 原樣執行與使用者本人登入的條件。

官方另提供 [`claude -p` 非互動介面](https://code.claude.com/docs/en/headless)。本機使用現有、未修改的官方 CLI，使用者透過 Anthropic 官方流程授權，由 CLI 自己保存與使用新登入。Relay 不代理登入、不取得密碼／OAuth token、不共用帳號，也不轉售模型用量。未新增 API 付費連線或購買額度。

可分享的本機來源碼包不包含 Claude 二進位檔、runtime 或憑證。這不等於取得對外提供 Claude 服務的許可；若改為商業產品、託管服務或替其他使用者路由訂閱流量，需依官方商業與認證條件另行評估，不宣稱本版對所有用途均合規。

[官方用量說明](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan) 頁首 2026-06-15 更新明確暫緩先前的獨立 Agent SDK 月額度方案，現行 `claude -p` 仍計入訂閱用量。頁面下方舊方案僅為歷史參考，不應當成已生效。帳戶限制與額外用量設定仍適用；CLI 回報的 `total_cost_usd` 不是已刷卡扣款的證明。

## Codex App

[官方排程文件](https://learn.chatgpt.com/zh-Hant/docs/automations) 明確支援在既有 App 任務中按分鐘週期執行工作、使用技能及處理本機專案。本專案 Codex App 路徑使用這些 App 原生功能與本機 Relay 命令，沒有讀取 App 登入、私有對話資料庫或模擬 ChatGPT 網頁送件。

[OpenAI 非歐洲地區使用條款](https://openai.com/policies/row-terms-of-use/) 同時禁止程式化擷取資料／輸出及繞過限制，第三方服務另受自身條款約束。據此，App 官方功能可作為受支援的實作介面，但不能保證任何自動化用途都合規，也不會替 Claude.ai 的網頁自動化授權。

驗收結果與實機證據範圍見 [VALIDATION.md](VALIDATION.md)。成功結果只證明該次呼叫與傳遞；CLI 模型名稱來自 CLI 回報，App 模型身分沒有另外獨立驗證，不延伸為服務條款保證。
