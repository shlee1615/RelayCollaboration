import {t, getLanguage, initializeLanguage, onLanguageChange, applyLanguage, retranslate} from './i18n.js';
import {statusLabel, formatBytes, buildFlow, replyDraft, appConnectionView} from './flow_model.js';
import {mountBindings} from './bindings.js';
export {statusLabel, formatBytes, buildFlow, replyDraft, appConnectionView};

const $ = id => document.getElementById(id);
const state = {csrf:null,profileId:null,configDigest:null,pendingProfile:null,pendingDigest:null,selected:null,detail:null,tab:'flow',cursor:null,items:[],expanded:false,polling:false,summary:null,listVersion:0,contexts:[],images:[],pending:null,sending:false};
const names = {codex:'Codex',claude:'Claude',get broker(){return t("協作橋");},get unknown(){return t("未知");}};
const bytes = value => new TextEncoder().encode(value).length;
function element(tag,className,text){const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=String(text);return node;}
function set(id,value){$(id).textContent=value??'—';}
function date(ts,short=false){if(typeof ts!=='number'||!Number.isFinite(ts))return '—';return new Intl.DateTimeFormat(getLanguage(),{timeZone:'Asia/Taipei',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',...(short?{}:{second:'2-digit'}),hour12:false}).format(new Date(ts*1000));}
function badge(node,text,tone='muted'){node.className='badge '+tone;node.textContent=text;}
function taskTone(task){return task.claim_expired||task.status==='failed'||task.result_ok===false?'attention':task.status==='completed'?'good':task.status==='running'?'active':task.status==='pending'?'pending':'muted';}
function showError(id,message){const node=$(id);node.textContent=message||'';if(node.classList.contains('banner'))node.hidden=!message;}
let toastTimer;
let bindingManager;
function toast(message){set('toast',message);$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,4500);}
async function session(){const response=await fetch('/api/session',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(15000)});if(!response.ok)throw new Error(t("無法建立控制台工作階段"));const value=await response.json();state.csrf=value.csrf;state.profileId=value.profile_id;state.configDigest=value.config_sha256;}
async function api(path,body,repeat=true){
 const options={credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(20000)};
 if(body!==undefined){options.method='POST';options.headers={'Content-Type':'application/json','X-CSRF-Token':state.csrf};options.body=JSON.stringify(body);}
 let response;try{response=await fetch(path,options);}catch(error){throw Object.assign(new Error(t("連線中斷或逾時，請先確認現有任務狀態。")),{uncertain:body!==undefined});}
 if(response.status===401&&repeat){await session();if(body===undefined)return api(path,undefined,false);throw Object.assign(new Error(t("控制台工作階段已更新。請使用同一任務識別續接。")),{uncertain:true});}
 const value=await response.json();if(!response.ok)throw Object.assign(new Error(t(value.error?.message)||t("控制台請求失敗")),{status:response.status,code:value.error?.code,uncertain:body!==undefined&&response.status>=500});return value;
}
function renderSummary(value){
 bindingManager?.presence(value.workers?.codex?.app);
 const profile=value.profile||{};
 set('project-label',profile.project||t("專案"));
 set('broker-endpoint',profile.broker_url||t("協作橋"));
 const providerText=profile.provider?.kind==='claude_web'?t("Claude 網頁 · 沿用 Chrome 登入"):profile.provider?.kind==='claude_cli'? 'Claude · '+(profile.provider?.model||t("帳戶預設模型"))+' · '+(profile.provider?.effort||t("預設"))+' effort':t("unavailable · Provider 尚未配置");
 set('provider-label',providerText);
 const recovery=value.submission_recovery;
 showError('recovery-error',recovery?.count>0?String(recovery.count)+t(" 份保存的送出紀錄需要人工確認；原始檔案已保留，相關 UUID 不會重新派送。"):'');
 state.summary=value;composerDirection();set('snapshot-time',date(value.server_time));set('connection',value.broker.ok?t("本機已連線"):t("協作橋無法連線"));$('connection').classList.toggle('offline',!value.broker.ok);
 badge($('broker-badge'),value.broker.ok?t("已連線"):t("無法連線"),value.broker.ok?'good':'attention');set('broker-latency',value.broker.ok?t("查詢 {0} ms", value.broker.latency_ms):t("狀態不可用"));set('leader-generation',value.leader?.generation!=null?t("派送世代 {0}", value.leader.generation):'—');set('leader-title',!value.broker.ok?t("派送狀態未知"):value.leader?.active?t("{0} 持有派送權", names[value.leader.actor]||t("未知")):t("目前無人持有派送權"));
 renderCodexWorker(value);
 const w=value.worker;const label={unavailable:t("unavailable · 尚未配置"),starting:t("啟動中"),idle:t("待命"),executing:t("執行中"),running:t("執行中"),manual_review:t("需人工確認"),auth_blocked:t("需要登入"),transport_error:t("通道異常"),stopped:t("已停止"),unknown:t("狀態未知"),stopping:t("停止中"),attention_required:t("需確認")}[w.state]||w.state||t("狀態未知");
 badge($('worker-badge'),label,w.verified&&w.state==='idle'?'good':w.verified&&['executing','running'].includes(w.state)?'active':['stopped','unknown'].includes(w.state)?'muted':'attention');set('worker-title',w.verified?`${label} · PID ${w.pid}`:label);set('worker-detail',w.verified?t("本程序已執行 {0} 次模型呼叫。{1}", w.executions??'—', w.reason?t("狀態：")+w.reason:t("等待或處理協作橋中的任務。")):(w.state==='unavailable'?t("尚未配置模型 provider；任務只會保存並等候，不會呼叫模型。"):t("尚無可驗證的背景程序狀態。")));set('worker-time',w.checked_at?t("程序查核 {0}", date(w.checked_at,true)):t("尚未查核"));
 const busy=w.control?.state==='running';$('worker-start').disabled=busy||w.verified||w.state==='unavailable';$('worker-stop').disabled=busy||!w.verified;
 if(w.control&&['failed','uncertain'].includes(w.control.state))showError('global-error',t(w.control.message));
 if(profile.provider?.kind==='claude_web'){
  badge($('worker-badge'),t("網頁自動化已停用"),'attention');set('worker-title',t("等待選擇允許的整合方式"));
  set('worker-detail',t("查核 Anthropic 使用條款後，Claude 網頁自動送件已停用。尚未送出真模型請求；可改用官方 API 或人工轉交。"));
  set('worker-time',t("這不是網路連線故障"));$('worker-start').disabled=true;$('worker-stop').disabled=true;
 }
 for(const name of ['total','pending','running','completed','attention'])set('count-'+name,value.counts[name]);
 const rows=value.submissions||[];$('submissions-wrap').hidden=!rows.length;$('submissions').replaceChildren();
 for(const row of rows.slice(0,5)){const line=element('div','submission');line.append(element('span','submission-title',row.title));const status=element('span','badge');badge(status,{dispatching:t("派送中"),queued:t("已送入協作橋"),completed:t("已完成"),failed:t("執行失敗"),uncertain:t("需續接"),manual_review:t("需人工確認")}[row.state]||row.state,['uncertain','manual_review'].includes(row.state)?'attention':row.state==='completed'?'good':'muted');line.append(status);if(row.task_id){const open=element('button','text-button',t("查看"));open.addEventListener('click',()=>selectTask(row.task_id));line.append(open);}if(row.state==='uncertain'&&row.retryable!==false){const retry=element('button','text-button',t("續接原任務"));retry.addEventListener('click',async()=>{retry.disabled=true;try{await api('/api/review/retry',{client_request_id:row.request_id});toast(t("已續接原任務，沒有建立另一份任務。"));await poll(true);}catch(error){toast(error.message);}finally{retry.disabled=false;}});line.append(retry);} $('submissions').append(line);}
}
function composerDirection(){
 const actor=$('review-recipient').value;
 const provider=state.summary?.profile?.providers?.[actor]||(actor==='claude'?state.summary?.profile?.provider:null);
 set('composer-direction',actor==='claude'?'CODEX → CLAUDE':'CLAUDE → CODEX');
 set('submit-review',t("送交 {0} →", names[actor]));
 set('composer-provider',provider&&provider.kind!=='unavailable'?t("{0} · {1} · 提案模式", names[actor], provider.model||t("CLI 預設模型")):t("{0} 尚未配置；任務將保存等待", names[actor]));
 if(provider?.kind==='codex_app')set('composer-provider',t("目前 Codex App · 沿用 App 模型設定 · 提案模式"));
 if(provider?.kind==='claude_web')set('composer-provider',t("Claude 網頁 · 沿用 Chrome 模型設定 · 提案模式"));
}
function renderCodexWorker(value){
 const w=value.workers?.codex||{state:'unavailable'};
 const provider=value.profile?.providers?.codex;
 $('app-connect').hidden=provider?.kind!=='codex_app';
 if(provider?.kind==='codex_app'){
  const view=appConnectionView(w);
  set('codex-provider-label',t("Codex App · 沿用目前登入"));
  badge($('codex-badge'),view.label,view.tone);set('codex-title',view.label);set('codex-detail',view.detail);
  set('codex-phase',w.app?.thread_id?t("任務 {0}…", w.app.thread_id.slice(0,8)):t("等待任務綁定"));
  set('codex-time',w.app?.last_seen_at?t("上次收件 {0}", date(w.app.last_seen_at,true)):t("尚未收件"));
  $('codex-start').disabled=!!w.running;$('codex-stop').disabled=!w.running;
  return;
 }
 set('codex-provider-label',provider?.kind==='codex_cli'?`Codex · ${provider.model||t("CLI 預設模型")}`:t("Provider 尚未配置"));
 const label={unavailable:t("尚未配置"),starting:t("啟動中"),idle:t("待命"),executing:t("執行中"),running:t("執行中"),auth_blocked:t("需要登入"),manual_review:t("需人工確認"),stopped:t("已停止"),stopping:t("停止中"),attention_required:t("需確認"),transport_error:t("通道異常")}[w.state]||t("狀態未知");
 badge($('codex-badge'),label,w.provider_ready?'good':w.verified?'active':'muted');
 set('codex-title',label);
 set('codex-detail',w.verified?t("本程序已執行 {0} 次模型呼叫。{1}", w.executions??'—', w.reason||t("等待或處理送往 Codex 的任務。")):w.state==='unavailable'?t("設定並登入 Codex 後，即可自動接收 Claude → Codex 任務。"):(w.reason||t("啟動後可接收任務。")));
 set('codex-phase',value.codex?.state==='reported'?t("主控台回報：{0}", value.codex.title):t("背景接收者"));
 set('codex-time',w.checked_at?t("查核 {0}", date(w.checked_at,true)):'—');
 const busy=w.control?.state==='running';
 $('codex-start').disabled=busy||w.verified||w.state==='unavailable';
 $('codex-stop').disabled=busy||!w.verified;
}
function handoffReply(){
 if(state.pending){toast(t("請先續接上次尚未確認的送出。"));openComposer();return;}
 const draft=replyDraft(state.detail?.task);if(!draft)return;
 $('review-title').value=draft.title;$('review-prompt').value=draft.prompt;
 $('review-recipient').value=draft.recipient;state.contexts=draft.contexts;state.images=[];
 openComposer();
}
function filters(){return {status:$('status-filter').value,direction:$('direction-filter').value,q:$('search').value.trim()};}
async function loadTasks(append=false){
 const version=++state.listVersion,query=new URLSearchParams(filters());if(append&&state.cursor)query.set('cursor',state.cursor);$('load-more').disabled=true;
 try{const value=await api('/api/tasks?'+query);if(version!==state.listVersion)return;state.items=append?[...state.items,...value.items]:value.items;state.cursor=value.next_cursor;state.expanded=append;set('list-count',`${state.items.length} / ${value.total}`);showError('list-error','');renderTasks();}
 catch(error){if(version===state.listVersion)showError('list-error',t("資料可能已過期：")+error.message);}finally{$('load-more').disabled=false;}
}
function renderTasks(){
 const body=$('task-list');body.replaceChildren();$('list-empty').hidden=state.items.length>0;if(!state.items.length)set('list-empty',t("沒有符合條件的任務。"));$('load-more').hidden=!state.cursor;
 for(const task of state.items){const row=element('tr','task-row'+(task.task_id===state.selected?' selected':''));row.tabIndex=0;row.setAttribute('aria-label',`${task.title}，${statusLabel(task)}`);const name=element('td');name.append(element('div','task-name',task.title));const sub=element('div','task-sub');sub.append(element('span','direction',`${names[task.sender]||t("未知")} → ${names[task.recipient]||t("未知")}`),element('span','short-id',task.task_id.slice(0,8)));name.append(sub);const status=element('td');const pill=element('span');badge(pill,statusLabel(task),taskTone(task));status.append(pill);const stamp=element('td','task-time',date(task.created_at,true).replace(' ','\n'));row.append(name,status,stamp);row.addEventListener('click',()=>selectTask(task.task_id));row.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();selectTask(task.task_id);}});body.append(row);}
 for(const metric of document.querySelectorAll('.metric'))metric.classList.toggle('selected',metric.dataset.status===$('status-filter').value);
}
async function selectTask(id){state.selected=id;state.detail=null;$('detail-empty').hidden=true;$('detail-content').hidden=false;set('detail-title',t("讀取任務…"));showError('detail-error','');renderTasks();await loadDetail(id);}
async function loadDetail(id=state.selected){if(!id)return;try{const value=await api('/api/task/'+encodeURIComponent(id));if(id!==state.selected)return;state.detail=value;renderDetail(value);showError('detail-error','');}catch(error){if(id===state.selected)showError('detail-error',t("資料可能已過期：")+error.message);}}
function renderDetail(value){
 const {task,manifest,events}=value;set('detail-title',task.title);set('detail-direction',`${names[task.sender]||t("未知")} → ${names[task.recipient]||t("未知")}`);badge($('detail-status'),statusLabel(task),taskTone(task));set('detail-mode',task.mode==='proposal'?t("提案模式"):task.mode==='read_only'?t("唯讀模式"):task.mode);set('copy-id',task.task_id+' ⧉');
 $('flow-steps').replaceChildren();buildFlow(task).forEach((step,index)=>{const node=element('div','flow-step '+step.state);node.append(element('span','step-number',`0${index+1} / ${names[step.actor]||t("未知")}`),element('strong','',step.label),element('span','step-state',{done:t("已記錄"),active:t("處理中"),waiting:t("等待中"),attention:t("需確認")}[step.state]||step.state),element('span','',date(step.time,true)));$('flow-steps').append(node);});
 $('event-list').replaceChildren();for(const event of events){const row=element('li');row.append(element('span','',t(event.label)),element('time','',`${date(event.time)} · ${names[event.actor]||t("協作橋")}`));$('event-list').append(row);}
 $('source-list').replaceChildren();for(const source of manifest.sources){const node=element('div','source',source.name);node.append(element('small','',`${formatBytes(source.bytes)} · SHA256 ${source.sha256}`));$('source-list').append(node);}
 renderImages(task);set('input-meta',t("供給內容 {0}\nSHA256 {1}", formatBytes(manifest.prompt.bytes), manifest.prompt.sha256));set('input-text',task.prompt);
 const result=task.result;$('reply-to-peer').hidden=!replyDraft(task);set('result-meta',result?`${result.reported_model||t("模型未回報")} · ${result.requested_effort??t("預設 effort")}${result.execution_kind==='mock_test'||result.execution_kind==='fixture_cli'?t(" · 測試替身，非真模型"):result.execution_kind==='codex_app'?t(" · App 回覆已接收；模型名稱未獨立驗證"):result.model_execution_verified?t(" · CLI 回報，非獨立驗證"):result.cli_execution_verified?t(" · CLI 已完成，模型名稱未驗證"):t(" · 模型執行未驗證")}`:t("回覆尚未產生"));set('output-meta',t("回覆資料 {0}\nSHA256 {1}", formatBytes(manifest.result.bytes), manifest.result.sha256));set('output-text',result?(result.text||JSON.stringify(result,null,2)):t("等待接收者完成後，回覆會顯示在這裡。"));
}
async function poll(force=false){if(state.polling||(!force&&document.hidden))return;state.polling=true;try{const summary=await api('/api/summary');showError('global-error','');renderSummary(summary);if(!summary.broker.ok)showError('global-error',t(summary.broker.error)+t("；下方可能保留先前快照。"));if(!state.expanded||force)await loadTasks();if(state.selected)await loadDetail();}catch(error){set('connection',t("連線中斷"));$('connection').classList.add('offline');showError('global-error',error.message+t(" 畫面保留上次資料。"));}finally{state.polling=false;}}
function composerSize(){const size=bytes($('review-prompt').value)+state.contexts.reduce((sum,item)=>sum+bytes(item.text)+bytes(item.name)+80,0)+180;set('composer-size',t("{0} / 64 KiB（含封裝估計）", formatBytes(size)));}
function renderAttachments(){const area=$('attachments');area.replaceChildren();state.contexts.forEach((item,index)=>{const node=element('div','attachment');node.append(element('span','',item.name),element('span','subtle',formatBytes(bytes(item.text))));const remove=element('button','text-button',t("移除"));remove.type='button';remove.disabled=!!state.pending;remove.addEventListener('click',()=>{state.contexts.splice(index,1);renderAttachments();composerSize();});node.append(remove);area.append(node);});state.images.forEach((item,index)=>{const node=element('div','attachment');const img=element('img');img.src='data:image/png;base64,'+item.data;img.alt=item.name;img.style.cssText='width:90px;height:60px;object-fit:contain';node.append(img,element('span','',item.name));const remove=element('button','text-button',t("移除"));remove.type='button';remove.disabled=!!state.pending||state.sending;remove.onclick=()=>{state.images.splice(index,1);renderAttachments();};node.append(remove);area.append(node);});composerSize();}
function pendingUI(){const pending=!!state.pending;$('uncertain-box').hidden=!pending;$('retry-submit').hidden=!pending;$('submit-review').hidden=pending;for(const id of ['review-title','review-prompt','context-files','image-files','review-recipient'])$(id).disabled=pending||state.sending;$('retry-submit').disabled=state.sending||(pending&&(state.pendingProfile!==state.profileId||state.pendingDigest!==state.configDigest));if(pending&&(state.pendingProfile!==state.profileId||state.pendingDigest!==state.configDigest))showError('composer-error',t("此待確認送出的 runtime 或設定已改變。請恢復原設定再續接；原內容已保留。"));renderAttachments();}
function clearPending(){state.pending=null;state.pendingProfile=null;state.pendingDigest=null;sessionStorage.removeItem('relay.pending');sessionStorage.removeItem('relay.pending.config');sessionStorage.removeItem('relay.pending.profile');pendingUI();}
function openComposer(){showError('composer-error','');if(state.pending){$('review-title').value=state.pending.title;$('review-prompt').value=state.pending.prompt;state.contexts=state.pending.contexts;state.images=state.pending.images||[];$('review-recipient').value=state.pending.recipient||'claude';}composerDirection();pendingUI();$('review-dialog').showModal();if(!state.pending)$('review-title').focus();}
async function sendReview(retry=false){
 if(state.sending)return;showError('composer-error','');let body;
 if(!state.profileId||!state.configDigest||(retry&&(state.pendingProfile!==state.profileId||state.pendingDigest!==state.configDigest))){showError('composer-error',t("Runtime／設定身分不符或尚未確認。請恢復原設定再續接；原內容已保留。"));return;}
 if(retry){if(!state.pending)return;body=state.pending;}else{body={client_request_id:crypto.randomUUID(),recipient:$('review-recipient').value,title:$('review-title').value.trim(),prompt:$('review-prompt').value,contexts:state.contexts.map(item=>({...item})),...(state.images.length?{images:state.images.map(item=>({...item}))}:{})};if(!body.title||!body.prompt.trim()){showError('composer-error',t("請填寫標題與任務內容。"));return;}if(bytes(body.title)>512){showError('composer-error',t("標題超過512 bytes。"));return;}state.pending=body;try{sessionStorage.setItem('relay.pending.profile',state.profileId);state.pendingProfile=state.profileId;sessionStorage.setItem('relay.pending.config',state.configDigest);state.pendingDigest=state.configDigest;sessionStorage.setItem('relay.pending',JSON.stringify(body));}catch(error){state.pending=null;showError('composer-error',t("無法保存重試識別，尚未送出。請縮小附件或檢查瀏覽器儲存設定。"));return;}}
 state.sending=true;pendingUI();$('submit-review').disabled=true;
 try{const result=await api('/api/review',body);clearPending();$('review-dialog').close();$('review-form').reset();state.contexts=[];state.images=[];renderAttachments();toast(result.deduped?t("已找到原任務，沒有重複派送。"):t("任務已保存，等待 {0} 認領。", names[body.recipient||'claude']));await poll(true);if(result.task_id)await selectTask(result.task_id);}
 catch(error){if(error.status>=400&&error.status<500&&error.status!==409&&!error.uncertain)clearPending();showError('composer-error',error.message);}
 finally{state.sending=false;$('submit-review').disabled=false;pendingUI();}
}
async function workerAction(action,actor='claude'){if(action==='stop'&&!confirm(t("停止 {0} 背景 worker？正在執行的任務可能需要人工確認。", names[actor])))return;try{await api('/api/worker',{action,actor});toast(action==='start'?t("正在啟動背景 worker。"):t("已請求停止背景 worker。"));await poll(true);}catch(error){toast(error.message);}}
function renderImages(task){
 let area=$('input-images');if(!area){area=element('div');area.id='input-images';$('input-text').before(area);}area.replaceChildren();
 for(const item of task.images||[]){if(item.media_type!=='image/png')continue;const fig=element('figure');const img=element('img');img.src='data:image/png;base64,'+item.data;img.alt=item.name;img.style.cssText='max-width:100%;max-height:420px;object-fit:contain';const caption=element('figcaption','',item.name+' · SHA256 '+item.sha256);fig.append(img,caption);area.append(fig);}
 const receipt=task.result?.input_images||[];area.append(element('p','subtle',receipt.length?t("Worker 已封裝 ")+receipt.length+t(" 張圖片；實際判讀請見回覆。"):(task.images?.length?t("圖片已保存，等待 worker 回覆。"):t("本任務未附圖片。"))));
}
function renderLanguage(){
 applyLanguage(document);
 for(const id of ['global-error','recovery-error','list-error','detail-error','composer-error','toast'])set(id,retranslate($(id).textContent));
 if(state.summary)renderSummary(state.summary);
 renderTasks();if(state.detail)renderDetail(state.detail);
 composerDirection();renderAttachments();bindingManager?.renderLanguage();
}
async function boot(){
 let languageStorage;try{languageStorage=localStorage;}catch{/* Optional preference storage. */}
 initializeLanguage(languageStorage,navigator.languages||[navigator.language],document);
 onLanguageChange(renderLanguage);
 bindingManager=mountBindings({api,identity:()=>({profileId:state.profileId,configDigest:state.configDigest}),refreshSummary:()=>poll(true)});
 try{const saved=sessionStorage.getItem('relay.pending');if(saved){const value=JSON.parse(saved);if(value&&typeof value.client_request_id==='string'&&typeof value.title==='string'&&typeof value.prompt==='string'&&Array.isArray(value.contexts)){state.pending=value;state.pendingProfile=sessionStorage.getItem('relay.pending.profile');state.pendingDigest=sessionStorage.getItem('relay.pending.config');}}}catch(error){sessionStorage.removeItem('relay.pending');}
 $('new-review').addEventListener('click',openComposer);$('close-dialog').addEventListener('click',()=>$('review-dialog').close());$('cancel-review').addEventListener('click',()=>$('review-dialog').close());$('review-form').addEventListener('submit',event=>{event.preventDefault();sendReview();});$('retry-submit').addEventListener('click',()=>sendReview(true));$('review-prompt').addEventListener('input',composerSize);$('refresh').addEventListener('click',()=>{state.expanded=false;poll(true);});$('worker-start').addEventListener('click',()=>workerAction('start'));$('worker-stop').addEventListener('click',()=>workerAction('stop'));
 $('review-recipient').addEventListener('change',composerDirection);$('codex-start').addEventListener('click',()=>workerAction('start','codex'));$('codex-stop').addEventListener('click',()=>workerAction('stop','codex'));$('reply-to-peer').addEventListener('click',handoffReply);
 $('app-connect').addEventListener('click',async()=>{const prompt=getLanguage()==='en'?state.summary?.app_connect_prompt_en:state.summary?.app_connect_prompt;if(!prompt){toast(t('連線說明語言尚未支援，請先依 docs/AGENT_GUIDE.md 操作。'));return;}try{await navigator.clipboard.writeText(prompt);toast(t("連線說明已複製，請貼到目前 Codex App 任務。"));}catch(error){toast(t("複製失敗；請依 docs/CODEX_APP.md 的連線步驟操作。"));}});
 $('context-files').addEventListener('change',async event=>{try{const files=[...event.target.files];if(state.contexts.length+files.length>8)throw new Error(t("最多8份文字附件。"));const additions=[];for(const file of files){if(file.size>65536)throw new Error(t("單一附件超過64 KiB，請拆分。"));if(!/\.(txt|md|json|csv|py|js|ts|log)$/i.test(file.name))throw new Error(t("請使用支援的文字檔格式。"));if([...state.contexts,...additions].some(item=>item.name===file.name))throw new Error(t("附件名稱重複：")+file.name);const text=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(await file.arrayBuffer());if(text.includes('\0'))throw new Error(t("附件不是可接受的純文字。"));additions.push({name:file.name,text});}state.contexts.push(...additions);renderAttachments();showError('composer-error','');}catch(error){showError('composer-error',t("附件讀取失敗：")+error.message);}event.target.value='';});
 $('image-files').addEventListener('change',async event=>{try{const files=[...event.target.files];if(state.images.length+files.length>8)throw Error(t("最多 8 張 PNG。"));const additions=[];let total=state.images.reduce((sum,item)=>sum+atob(item.data).length,0);for(const file of files){total+=file.size;if(total>1048576)throw Error(t("PNG 原始檔總量最多 1 MiB。"));if(!/\.png$/i.test(file.name))throw Error(t("請使用 PNG。"));if([...state.contexts,...state.images,...additions].some(i=>i.name===file.name))throw Error(t("附件名稱重複。"));const raw=new Uint8Array(await file.arrayBuffer());const sha256=[...new Uint8Array(await crypto.subtle.digest('SHA-256',raw))].map(b=>b.toString(16).padStart(2,'0')).join('');let binary='';for(let i=0;i<raw.length;i+=8192)binary+=String.fromCharCode(...raw.subarray(i,i+8192));additions.push({name:file.name,media_type:'image/png',data:btoa(binary),sha256});}state.images.push(...additions);renderAttachments();showError('composer-error','');}catch(error){showError('composer-error',error.message);}event.target.value='';});
 let searchTimer;const filterChanged=()=>{state.expanded=false;state.cursor=null;loadTasks();};$('search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(filterChanged,300);});$('status-filter').addEventListener('change',filterChanged);$('direction-filter').addEventListener('change',filterChanged);$('load-more').addEventListener('click',()=>loadTasks(true));for(const metric of document.querySelectorAll('.metric'))metric.addEventListener('click',()=>{$('status-filter').value=metric.dataset.status;filterChanged();});
 for(const tab of document.querySelectorAll('[data-tab]'))tab.addEventListener('click',()=>{state.tab=tab.dataset.tab;for(const button of document.querySelectorAll('[data-tab]'))button.setAttribute('aria-selected',String(button===tab));for(const name of ['flow','input','output'])$('tab-'+name).hidden=name!==state.tab;});
 $('copy-id').addEventListener('click',async()=>{if(!state.selected)return;try{await navigator.clipboard.writeText(state.selected);toast(t("任務ID已複製。"));}catch(error){toast(t("任務ID：")+state.selected);}});document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll(true);});
 try{await session();await poll(true);try{await bindingManager.load();if(location.hash==='#connections')await bindingManager.open();}catch(error){set('instance-presence',t("連線管理尚未載入，請確認服務已更新"));}const linkedTask=new URLSearchParams(location.search).get('task');if(linkedTask&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(linkedTask))await selectTask(linkedTask);}catch(error){showError('global-error',error.message);set('connection',t("連線中斷"));}setInterval(()=>poll(),5000);if(state.pending)toast(t("有一筆上次送出尚未確認，請開啟新增任務以續接。"));
}
if(typeof document!=='undefined')boot();
