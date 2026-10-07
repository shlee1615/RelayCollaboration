// UI copy only. Task bodies, attachments, result text and protocol IDs are never translated.
// Traditional Chinese source strings are stable catalog keys; {0}, {1}, ... are values.
export const english = Object.freeze({
 '協作橋':'Broker', '未知':'Unknown',
 '無法建立控制台工作階段':'Could not establish a console session',
 '連線中斷或逾時，請先確認現有任務狀態。':'Connection interrupted or timed out. Check the existing task status first.',
 '控制台工作階段已更新。請使用同一任務識別續接。':'The console session was renewed. Resume using the same request ID.',
 '控制台請求失敗':'Console request failed', '專案':'Project',
 'Claude 網頁 · 沿用 Chrome 登入':'Claude web · Chrome session',
 '帳戶預設模型':'Account default model', '預設':'Default',
 'unavailable · Provider 尚未配置':'Unavailable · Provider not configured',
 ' 份保存的送出紀錄需要人工確認；原始檔案已保留，相關 UUID 不會重新派送。':' saved submissions need manual review. Original files are preserved; their UUIDs will not be dispatched again.',
 '本機已連線':'Connected locally', '協作橋無法連線':'Broker unreachable',
 '已連線':'Connected', '無法連線':'Unreachable', '查詢 {0} ms':'Query: {0} ms',
 '狀態不可用':'Status unavailable', '派送世代 {0}':'Dispatch generation {0}',
 '派送狀態未知':'Dispatch status unknown', '{0} 持有派送權':'{0} holds the dispatch lease',
 '目前無人持有派送權':'No active dispatch lease',
 'unavailable · 尚未配置':'Unavailable · Not configured',
 '啟動中':'Starting', '待命':'Idle', '執行中':'Running', '需人工確認':'Manual review needed',
 '需要登入':'Sign-in required', '通道異常':'Transport error', '已停止':'Stopped',
 '狀態未知':'Status unknown', '停止中':'Stopping', '需確認':'Needs attention',
 '本程序已執行 {0} 次模型呼叫。{1}':'This process has made {0} model calls. {1}',
 '狀態：':'Status: ', '等待或處理協作橋中的任務。':'Waiting for or processing tasks in the broker.',
 '尚未配置模型 provider；任務只會保存並等候，不會呼叫模型。':'No model provider is configured. Tasks are saved and wait; no model is called.',
 '尚無可驗證的背景程序狀態。':'No verified background process status is available.',
 '程序查核 {0}':'Process checked {0}', '尚未查核':'Not checked yet',
 '網頁自動化已停用':'Web automation disabled', '等待選擇允許的整合方式':'Choose a supported integration',
 '查核 Anthropic 使用條款後，Claude 網頁自動送件已停用。尚未送出真模型請求；可改用官方 API 或人工轉交。':'Automated submission to Claude web is disabled. No real model request was sent. Configure the supported official Claude Code CLI integration.',
 '這不是網路連線故障':'Integration disabled by configuration',
 '派送中':'Dispatching', '已送入協作橋':'Queued in broker', '已完成':'Completed',
 '執行失敗':'Failed', '需續接':'Resume needed', '查看':'View', '續接原任務':'Resume original request',
 '已續接原任務，沒有建立另一份任務。':'Resumed the original request without creating another task.',
 '送交 {0} →':'Send to {0} →', '{0} · {1} · 提案模式':'{0} · {1} · Proposal mode',
 'CLI 預設模型':'CLI default model', '{0} 尚未配置；任務將保存等待':'{0} is not configured; the task will be saved and wait',
 '目前 Codex App · 沿用 App 模型設定 · 提案模式':'Current Codex App · App model settings · Proposal mode',
 'Claude 網頁 · 沿用 Chrome 模型設定 · 提案模式':'Claude web · Chrome model settings · Proposal mode',
 'Codex App · 沿用目前登入':'Codex App · Current sign-in',
 '任務 {0}…':'Task {0}…', '等待任務綁定':'Waiting for task binding',
 '上次收件 {0}':'Last inbox check {0}', '尚未收件':'Inbox not checked yet',
 'Provider 尚未配置':'Provider not configured', '尚未配置':'Not configured',
 '等待或處理送往 Codex 的任務。':'Waiting for or processing tasks addressed to Codex.',
 '設定並登入 Codex 後，即可自動接收 Claude → Codex 任務。':'Configure and sign in to Codex CLI to receive Claude → Codex tasks automatically.',
 '啟動後可接收任務。':'Start the receiver to accept tasks.',
 '主控台回報：{0}':'Console report: {0}', '背景接收者':'Background receiver', '查核 {0}':'Checked {0}',
 '請先續接上次尚未確認的送出。':'Resume the previous unconfirmed submission first.',
 '資料可能已過期：':'Data may be stale: ', '沒有符合條件的任務。':'No matching tasks.',
 '讀取任務…':'Loading task…', '提案模式':'Proposal mode', '唯讀模式':'Read-only mode',
 '已記錄':'Recorded', '處理中':'Processing', '等待中':'Waiting',
 '供給內容 {0}\nSHA256 {1}':'Input {0}\nSHA256 {1}',
 '模型未回報':'Model not reported', '預設 effort':'Default effort',
 ' · 測試替身，非真模型':' · Test fixture; not a real model',
 ' · App 回覆已接收；模型名稱未獨立驗證':' · App reply received; model identity not independently verified',
 ' · CLI 回報，非獨立驗證':' · Reported by CLI; not independently verified',
 ' · CLI 已完成，模型名稱未驗證':' · CLI completed; model identity not verified',
 ' · 模型執行未驗證':' · Model execution not verified',
 '回覆尚未產生':'No reply yet', '回覆資料 {0}\nSHA256 {1}':'Reply {0}\nSHA256 {1}',
 '等待接收者完成後，回覆會顯示在這裡。':'The reply will appear here when the recipient finishes.',
 '；下方可能保留先前快照。':'; the previous snapshot may still be shown below.',
 '連線中斷':'Disconnected', ' 畫面保留上次資料。':' The last available data is still displayed.',
 '{0} / 64 KiB（含封裝估計）':'{0} / 64 KiB (estimated including envelope)',
 '移除':'Remove',
 '此待確認送出的 runtime 或設定已改變。請恢復原設定再續接；原內容已保留。':'The runtime or configuration for this pending request changed. Restore the original configuration to resume; the content is preserved.',
 'Runtime／設定身分不符或尚未確認。請恢復原設定再續接；原內容已保留。':'Runtime/configuration identity is unconfirmed or mismatched. Restore the original configuration to resume; the content is preserved.',
 '請填寫標題與任務內容。':'Enter a title and task instructions.', '標題超過512 bytes。':'The title exceeds 512 bytes.',
 '無法保存重試識別，尚未送出。請縮小附件或檢查瀏覽器儲存設定。':'Could not save the retry identity. Nothing was submitted. Reduce attachments or check browser storage settings.',
 '已找到原任務，沒有重複派送。':'Found the original task; no duplicate dispatch.',
 '任務已保存，等待 {0} 認領。':'Task saved; waiting for {0} to claim it.',
 '停止 {0} 背景 worker？正在執行的任務可能需要人工確認。':'Stop the {0} background worker? Running tasks may need manual review.',
 '正在啟動背景 worker。':'Starting the background worker.', '已請求停止背景 worker。':'Requested background worker shutdown.',
 'Worker 已封裝 ':'The worker packaged ', ' 張圖片；實際判讀請見回覆。':' images; see the reply for the actual interpretation.',
 '圖片已保存，等待 worker 回覆。':'Images saved; waiting for the worker reply.', '本任務未附圖片。':'No images attached to this task.',
 '連線說明已複製，請貼到目前 Codex App 任務。':'Connection instructions copied. Paste them into the intended Codex App task.',
 '複製失敗；請依 docs/CODEX_APP.md 的連線步驟操作。':'Copy failed. Follow the connection steps in docs/AGENT_GUIDE.md.',
 '最多8份文字附件。':'Attach at most 8 text files.', '單一附件超過64 KiB，請拆分。':'A text attachment exceeds 64 KiB. Split it into smaller files.',
 '請使用支援的文字檔格式。':'Use a supported text file format.', '附件名稱重複：':'Duplicate attachment name: ',
 '附件不是可接受的純文字。':'The attachment is not valid plain text.', '附件讀取失敗：':'Could not read attachment: ',
 '最多 8 張 PNG。':'Attach at most 8 PNG images.', 'PNG 原始檔總量最多 1 MiB。':'The combined PNG files must not exceed 1 MiB.',
 '請使用 PNG。':'Use PNG images.', '附件名稱重複。':'Duplicate attachment name.',
 '任務ID已複製。':'Task ID copied.', '任務ID：':'Task ID: ',
 '連線管理尚未載入，請確認服務已更新':'Connection management did not load. Check that the service has been updated.',
 '有一筆上次送出尚未確認，請開啟新增任務以續接。':'A previous submission is unconfirmed. Open New task to resume it.',
 '待認領':'Pending', '回覆失敗':'Reply failed', '送出任務':'Submit task',
 '保存與派送':'Save and dispatch', '接收與處理':'Receive and process', '收到回覆':'Receive reply',
 '續議：':'Follow-up: ',
 '請根據附件的原任務與回覆，提出下一輪審查意見。原任務 ID：{0}':'Review the attached original task and reply, and propose the next steps. Original task ID: {0}',
 '接收已停止':'Receiver stopped', '啟動接收服務後，App 才會取得新任務。':'Start the receiver service so the App can receive new tasks.',
 '收件處理異常：':'Inbox processing issue: ', '等待 App 回覆':'Waiting for App reply', 'App 已連接':'App connected',
 '沿用已登入的 Codex App。自動收件由 App 任務排程執行；此狀態表示最近有收件活動。':'Uses the signed-in Codex App. Automatic inbox checks are scheduled by the App task; this status indicates recent inbox activity.',
 '等候 App 收件':'Waiting for App inbox check',
 'App 最近未收件。請保持 App 開啟並確認任務排程；新任務會留在佇列。':'The App has not checked its inbox recently. Keep it open and check its task schedule. New tasks stay queued.',
 '尚未連接 App':'App not connected',
 '不需要重新登入 Codex。複製連線說明，貼到要參與協作的 App 任務。':'No new Codex sign-in is needed. Copy the connection instructions into the App task that will collaborate.',
 '綁定狀態不可用':'Binding status unavailable', '已解除 · 保留交接名額':'Released · Reserved for handoff',
 '對話已連線':'Task connected', '保留舊綁定 · 等候對話':'Binding retained · Waiting for task',
 '可供新對話連線':'Available for a new task', '設定已變更 · 需要重新綁定':'Configuration changed · Rebind required',
 '綁定狀態未知':'Binding status unknown',
 '上次解除結果尚未確認，請先重試原解除。':'The previous release is unconfirmed. Retry the original release first.',
 '控制台身分已改變，未接受新預覽。':'Console identity changed. The new preview was not accepted.',
 '預覽與目前 instance 或設定不符，請回到原控制台。':'The preview does not match this instance or configuration. Return to the original console.',
 '目前預覽不可解除，請處理阻擋原因後重新檢查。':'This preview cannot be released. Resolve the blockers and check again.',
 '尚無活動':'No activity yet', '重試原解除':'Retry original release', '確認解除此綁定':'Confirm release',
 '這份預覽已被拒絕。請處理原因，再按「檢查解除條件」。':'This preview was rejected. Resolve the cause, then select Check release conditions.',
 '上次解除結果尚未確認。請重試原解除；即使已有新對話連線，也只查回原解除結果。':'The previous release is unconfirmed. Retry the original release; even if another task has connected, only the original release result will be retrieved.',
 '有 {0} 筆派工尚未收尾，目前不可解除。':'{0} deliveries are unfinished. Release is blocked.',
 '此對話最近仍有活動。若確定要解除，勾選下方選項後重新檢查。':'This task was recently active. To release it, select the active-task option and check again.',
 '可以解除。':'Ready to release. ', '本次預覽允許解除仍有活動的對話。':'This preview allows release of a recently active task. ',
 '確認後會開放這份 instance，讓新對話自行連線。原登入與任務資料會保留。':'Confirmation opens this instance for a new task to connect. Sign-in and task data are preserved.',
 '本次檢查：':'Preview ID: ', 'base（預設）':'base (default)',
 '目前 instance 未知':'Current instance unknown', '目前操作：{0} · {1}':'Managing: {0} · {1}',
 '目前控制台':'This console', '服務執行中':'Service running', '服務未啟動':'Service stopped',
 '設定無法讀取':'Could not read configuration', '最近活動：':'Last activity: ',
 '查看綁定識別':'Show binding identity', '前往此控制台管理綁定':'Manage bindings in this console',
 '部分主 instance 尚未登記或設定需檢查；目前控制台仍可操作。':'Some base registration or configuration needs attention. This console is still usable.',
 '解除已確認。請讓要接手的新對話使用此控制台的「複製 App 連線說明」。':'Release confirmed. In this console, use Copy App connection instructions for the new task that will take over.',
 'Relay — Codex／Claude 協作控制台':'Relay — Codex / Claude Collaboration Console',
 'Relay 協作控制台首頁':'Relay console home', '協作控制台':'Collaboration console',
 '專案載入中':'Loading project', '連線中':'Connecting', '重新整理':'Refresh', '刷新':'Refresh',
 '＋ 新增協作任務':'+ New task', '目前 instance':'Current instance', '讀取中…':'Loading…',
 '正在確認綁定':'Checking binding', '管理連線／解除綁定':'Manage connections / Unbind',
 '雙方工作，一處掌握。':'Two collaborators. One workspace.',
 '查看派工、上下文與回覆，依真實紀錄追蹤每一步。':'Track tasks, context and replies through recorded events.',
 '資料快照':'Snapshot', '每 5 秒更新 · Asia/Taipei':'Updates every 5 seconds · Asia/Taipei',
 '協作者與資料通道':'Collaborators and data channels', '尚未回報':'Not reported',
 '讀取背景 worker':'Loading background worker', '讀取 Codex 接收狀態。':'Loading Codex receiver status.',
 '複製 App 連線說明':'Copy App connection instructions', '啟動':'Start', '停止':'Stop',
 '本機協作橋':'Local broker', '檢查中':'Checking', '確認派送權':'Checking dispatch lease',
 '任務保存 → 接收者認領 → 回覆留存':'Task saved → Recipient claims → Reply retained',
 '核對程序身分與最新工作紀錄。':'Checking process identity and recent work records.',
 '任務計數':'Task counts', '全部任務':'All tasks', '需確認／失敗':'Attention / Failed',
 '本控制台送出的任務':'Submitted from this console', '任務紀錄':'Task history',
 '雙向任務共用同一個協作橋':'Both directions use the same broker', '自動更新':'Live updates',
 '搜尋標題或任務 ID':'Search title or task ID', '搜尋任務':'Search tasks',
 '狀態篩選':'Filter by status', '所有狀態':'All statuses', '方向篩選':'Filter by direction',
 '雙向':'Both directions', '任務 / 方向':'Task / Direction', '狀態':'Status', '建立時間':'Created',
 '正在取得任務紀錄…':'Loading task history…', '記錄時間來自協作橋':'Timestamps recorded by the broker',
 '載入更多':'Load more', '任務詳情':'Task details', '選擇一個任務':'Select a task',
 '追蹤資料如何送出、處理與回傳。':'Follow how data is sent, processed and returned.',
 '每個輸入與回覆都保留內容摘要值。':'Inputs and replies retain their content hashes.',
 '複製任務ID':'Copy task ID', '資料流':'Data flow', '輸入':'Input', '回覆':'Reply', '時間線':'Timeline',
 '只呈現已保存的事件；認領時間為最近一次，不代表完整操作歷史。':'Only saved events are shown. The claim time is the latest claim, not a complete activity history.',
 '把回覆交回另一方':'Send reply back to peer', '本機審查與程式提案':'Local reviews and code proposals',
 '目前 App 收件 · 雙向派工 · 回覆接續':'App inbox · Bidirectional tasks · Follow-up replies',
 '新增協作任務':'New collaboration task', '關閉':'Close',
 '選擇接收者，把任務與相關文字、PNG 圖片交給對方。內容會留存，回覆可在任務紀錄中查看。':'Choose a recipient and include instructions, text files or PNG images. Submissions are saved; replies appear in task history.',
 '協作方向':'Direction', '任務標題':'Task title', '例如：審查資料流顯示邏輯':'Example: Review the data flow display',
 '任務內容':'Instructions', '說明目標、已知資訊與希望 Claude 回覆的內容…':'Describe the goal, known facts and what the recipient should return…',
 '＋ 附上文字上下文':'+ Attach text context', 'TXT、Markdown、JSON、CSV、程式碼 · 最多 8 份':'TXT, Markdown, JSON, CSV, code · Up to 8 files',
 '＋ 附上圖片':'+ Attach images', 'PNG · 最多 8 張 · 合計 1 MiB · 圖片直接傳給接收者':'PNG · Up to 8 images, 1 MiB combined · Sent directly to the recipient',
 'Provider 尚未配置 · 提案模式':'Provider not configured · Proposal mode',
 '上次送出結果尚未確認。請保留原內容，使用同一任務識別重試。':'The previous submission is unconfirmed. Preserve its content and retry with the same request ID.',
 '取消':'Cancel', '續接原送出':'Resume original submission', '送交 Claude →':'Send to Claude →',
 '管理連線與綁定':'Manage connections and bindings', '關閉連線管理':'Close connection management',
 '不必知道 instance 名稱或任務 ID。這裡會列出本機設定的連線；要處理另一份綁定，可直接前往它的控制台。':'No instance name or task ID is needed. This list shows configured local connections. Open another instance’s console to manage its binding.',
 '重新讀取所有連線':'Refresh all connections', '目前操作的 instance':'Instance being managed',
 '解除後，這份 instance 會開放給新對話連線。若仍有派工等待回覆，會先阻擋解除。':'Releasing allows a new task to connect to this instance. Unfinished deliveries block release.',
 '允許解除最近仍有活動的對話':'Allow release of a recently active task', '檢查解除條件':'Check release conditions',
 '任務送入協作橋':'Task submitted to broker', '接收者認領（最近一次）':'Recipient claimed (latest)',
 '回覆已保存':'Reply saved', '任務失敗':'Task failed', '認領已逾期，需確認':'Claim expired; needs attention',
 '介面語言':'Interface language',
 '連線說明語言尚未支援，請先依 docs/AGENT_GUIDE.md 操作。':'Connection instructions are unavailable in this language. Follow docs/AGENT_GUIDE.md.',
 '服務重新啟動；請續接同一任務。':'Service restarted. Resume the same request.',
 '此任務的保存紀錄需要人工確認；原檔已保留，禁止覆寫或重新派工。':'The saved record needs manual review. Original files are preserved; do not overwrite or redispatch.',
 '任務狀態未能保存；請保留原始紀錄並人工確認。':'Could not save task status. Preserve the original records for manual review.',
 '任務狀態未能保存；禁止自動續接，請人工確認。':'Could not save task status. Automatic resume is blocked; manual review is needed.',
 '接收者必須是 Codex 或 Claude。':'The recipient must be Codex or Claude.',
 '標題不可空白，最多512 UTF-8 bytes。':'A title is required and must not exceed 512 UTF-8 bytes.',
 '請填寫任務內容。':'Enter task instructions.', '最多附上8個純文字檔案。':'Attach at most 8 plain text files.',
 '文字附件格式不正確。':'Invalid text attachment format.', '附件名稱不可重複或包含路徑。':'Attachment names must be unique and contain no paths.',
 '合併後的任務與附件超過64 KiB，請拆成多個主題。':'The combined instructions and text attachments exceed 64 KiB. Split them into smaller topics.',
 '此任務識別已綁定其他內容，請使用原內容續接。':'This request ID is bound to different content. Resume with the original content.',
 '待處理GUI任務已達20筆，請先處理現有任務。':'There are already 20 pending console requests. Handle existing tasks first.',
 '任務未能完整保存；已保留現有紀錄，請人工確認。':'The task could not be fully saved. Existing records are preserved for manual review.',
 'GUI任務不存在。':'Console request not found.', '模型任務未成功，請查看回覆。':'The model task did not succeed. Check the reply.',
 '派工或等待中斷；請續接同一任務，不要重新新增。':'Dispatch or waiting was interrupted. Resume the same request without creating another.',
 'Broker 已明確拒絕此派工；保留原始紀錄，此任務不可自動重試。':'The broker rejected this dispatch. Preserve the original records; automatic retry is blocked.',
 '任務紀錄、設定或控制器狀態需要人工確認；禁止自動重試。':'Task records, configuration or controller state need manual review. Automatic retry is blocked.',
 '派工狀態無法確認；請人工檢查原任務紀錄。':'Dispatch status is unknown. Inspect the original task records.',
 '任務識別紀錄無法驗證；原始紀錄已保留，請人工確認。':'Request identity could not be verified. Original records are preserved for manual review.',
 '尚未配置 instance。':'No instance is configured.', '不支援的綁定管理操作。':'Unsupported binding operation.',
 '綁定操作欄位不正確。':'Invalid binding operation fields.',
 '預覽後綁定或活動已改變。請重新檢查，再建立新的預覽。':'Binding or activity changed after preview. Check again and prepare a new preview.',
 '此對話最近仍有活動。若要解除，請先勾選允許解除仍有活動的對話。':'This task was recently active. Select Allow release of a recently active task before releasing it.',
 '仍有待處理或待收尾的派工。請先完成回覆或停止該 worker 並等待收尾，再重新檢查。':'There are unfinished deliveries. Finish the reply or stop that worker and wait for it to settle, then check again.',
 '這份預覽屬於其他 instance 或設定。請回到原控制台處理。':'This preview belongs to another instance or configuration. Return to the original console.',
 '解除紀錄與原請求不符，請保留原預覽供檢查。':'The release record does not match the original request. Preserve the preview for inspection.',
 '目前沒有需要解除的綁定或交接預留。':'There is no binding or handoff reservation to release.',
 '無法執行解除，請保留預覽並檢查目前綁定。':'Release failed. Preserve the preview and inspect the current binding.',
 '無法取得即時 broker 狀態':'Could not obtain live broker status',
 '請重新整理控制台工作階段。':'Refresh the console session.',
 '控制台已重啟，請重新取得工作階段。':'The console restarted. Establish a new session.',
 '無法取得資料；請稍後重試。':'Could not retrieve data. Try again later.',
 '請重新整理後再操作。':'Refresh the console before proceeding.',
 '操作結果不確定；請保留同一任務識別並先查詢狀態。':'The outcome is uncertain. Preserve the request ID and check its status first.',
});

let language='zh-Hant';
const listeners=new Set();
const renderedMessages=new Map();
export const getLanguage=()=>language;
export function resolveLanguage(saved, languages=[]) {
 if(saved==='en'||saved==='zh-Hant')return saved;
 for(const value of languages){
  if(/^zh(?:-|$)/i.test(value))return 'zh-Hant';
  if(/^en(?:-|$)/i.test(value))return 'en';
 }
 return 'en';
}
export function t(source,...values) {
 if(typeof source!=='string')return '';
 const text=language==='en'&&Object.hasOwn(english,source)?english[source]:source;
 const result=text.replace(/\{(\d+)\}/g,(match,index)=>index<values.length?String(values[index]):match);
 renderedMessages.set(result,{source,values});
 if(renderedMessages.size>1024)renderedMessages.delete(renderedMessages.keys().next().value);
 return result;
}
// Only called for application-owned notices, never for model or user content.
export function retranslate(message){const prior=renderedMessages.get(message);return prior?t(prior.source,...prior.values):message;}
export function setLanguage(value) {
 if(value!=='en'&&value!=='zh-Hant')return false;
 language=value;
 for(const callback of listeners)callback(value);
 return true;
}
export function onLanguageChange(callback){listeners.add(callback);return ()=>listeners.delete(callback);}
export function applyLanguage(root) {
 for(const node of root.querySelectorAll('[data-i18n]'))node.textContent=t(node.dataset.i18n);
 for(const attr of ['title','placeholder','aria-label']) {
  for(const node of root.querySelectorAll('[data-i18n-'+attr+']'))node.setAttribute(attr,t(node.getAttribute('data-i18n-'+attr)));
 }
 root.documentElement.lang=language;
}
export function initializeLanguage(storage,languages,root) {
 let saved;try{saved=storage?.getItem('relay.language');}catch{/* Storage is optional for language selection. */}
 setLanguage(resolveLanguage(saved,languages));
 applyLanguage(root);
 const select=root.getElementById('language-select');select.value=language;
 select.addEventListener('change',()=>{
  if(!setLanguage(select.value))return;
  try{storage?.setItem('relay.language',language);}catch{/* Keep the selection for this page. */}
 });
}
