# 0.3.0 App mailbox 補充

codex_app 沿用 broker 雙向協定；App 不持有 broker claim token。Relay worker 以自己的 claim 建立私有 delivery，App attach 綁定任務後 inbox 讀取，再用同 delivery UUID 回覆。回覆不可改寫，重試同內容去重；逾時、停止或重綁拒絕舊 delivery。Model identity 不作獨立驗證。

App inbox 同時觀測本次綁定之後的 Claude completed/failed 結果，處理後 ack 才移出通知。task 資料與模型文字不會被當成 shell 執行。收件細節见 [CODEX_APP.md](CODEX_APP.md)。

# 任務協定與持久化

broker 使用 dual-agent/v1，actor 對稱（codex、claude）。0.2.0 提供兩方各自的自動 consumer；GUI／CLI submit 可設定 recipient 為 codex 或 claude，sender 為另一方。這是 Relay 的路由身分，不代表派工文字一定由該模型產生。

HTTP POST /rpc 的 JSON：

~~~json
{
  "protocol": "dual-agent/v1",
  "request_id": "<canonical UUID>",
  "actor": "codex",
  "op": "health",
  "args": {}
}
~~~

HTTP Authorization 使用該新 runtime/broker/credentials 中的 actor bearer；GUI 不會取得 broker token。file transport 使用 broker/requests/<request_id>.json，envelope 有 auth_token，broker 會移到 processing 並將結果放 responses，處理後的 archive 去除認證值。完整 runtime 仍屬私有資料。

請透過 CLI call 避免手動接觸 token。call 必須提供 --request-id；HTTP 和 file 對同一請求有共同去重記錄。CLI call 也會在發送前保存原 operation、args 與 UUID；相同 UUID 變更內容會衝突。高階 submit／controller 另外保存整份原始任務與各步驟 receipt。

| op | 重要 args |
| --- | --- |
| health | {} |
| lead.acquire | project, ttl_seconds（30–900） |
| lead.release | project, leader_token |
| task.submit | project, leader_token, to, title, prompt, mode, allowed_paths |
| task.claim | task_id, ttl_seconds |
| task.complete | task_id, claim_token, result, status |
| task.get | task_id |
| task.list | project, status, to, limit, cursor 等支援的篩選 |

精確欄位與回傳依 relay_collaboration/bridge_core.py 的操作 schema。不要把 leader/claim 能力值放進 prompt、GUI 附件或分享記錄。read_only 與 proposal 模式可用；worker 拒絕 implementation。

## 送出與恢復

1. 原始 GUI／CLI payload 與 request UUID 先落盘。
2. Controller 保存 acquire/submit/release 各 mutation UUID 與參數，才向 broker 發送。
3. broker 以 request UUID＋內容摘要進行 SQLite transaction；相同內容重送取得原 receipt，變更內容回衝突。
4. Worker 在執行模型前提交 execution-start。完成內容和 completion UUID 先存 journal 再報回。
5. completion receipt 丟失可重送同一 completion；已執行結果不再叫模型。
6. 執行期間崩潰、不同 claim generation 或不確定外部執行，需要人工確認，不盲目重跑。

UUID 的去重範圍是同一 broker database；不同 fresh runtime 不共享去重記錄。不要將尚未確認的任務轉送到另一個 broker，即使 UUID 相同。

submit payload 的可選欄位 recipient 省略時為 claude。原始 payload 摘要綁定方向，同 UUID 變更方向會衝突。Controller journal 綁定 sender／recipient，兩方 worker 各自使用私有 journal 與 actor lock。舊版未含 recipient 的已保存任務仍維持原方向。回覆已包含在原任務，不需要額外派送才能讀到；GUI「把回覆交回另一方」則是使用者確認的新任務、新 UUID，附件保留前一輪內容與 ID，不會自動無限接力。

GUI session bootstrap 回傳穩定 runtime profile_id 與啟動設定的 config_sha256。瀏覽器與 CLI 檢查該身分；尚未確認的瀏覽器 payload 綁定原 runtime，避免同 port 重用後送到另一 queue。重啟同一 runtime 不改其身分。

task timeout 必須小於 claim TTL，保留 cancellation／完成回報 margin。TTL 最大 900 秒，預設模型 timeout 600 秒。沒有 claim renew、task cancel 或 exactly-once 外部副作用保證；長工作需拆分。
