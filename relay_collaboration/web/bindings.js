import {t, getLanguage, retranslate} from './i18n.js';
export function bindingLabel(app) {
  if (!app) return t("綁定狀態不可用");
  if (app.successor_thread_id) return t("已解除 · 保留交接名額");
  return {connected:t("對話已連線"), stale:t("保留舊綁定 · 等候對話"), unbound:t("可供新對話連線"),
    configuration_changed:t("設定已變更 · 需要重新綁定")}[app.state] || t("綁定狀態未知");
}

export function matchesRelease(payload, identity) {
  return !!payload && payload.schema === 'relay-app-release-request/v2' &&
    payload.runtime_id === identity.profileId && payload.config_sha256 === identity.configDigest &&
    typeof payload.request_id === 'string' && payload.confirm_release === true &&
    payload.successor_thread_id === null;
}

export function localConsoleLink(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' && url.hostname === '127.0.0.1' && !!url.port &&
      !url.username && !url.password && url.pathname === '/' && !url.search && !url.hash ? url.href : null;
  } catch { return null; }
}

// Store before POST. A lost response, refresh or supervisor restart reuses the
// same payload and operator receipt; it never prepares another release for a new owner.
export class ReleaseFlow {
  constructor(api, storage, identity) {
    this.api=api; this.storage=storage; this.identity=identity; this.pending=null;
    const saved=storage.getItem('relay.binding.release');
    if(saved) this.pending=JSON.parse(saved);
  }
  save(value) {
    this.storage.setItem('relay.binding.release',JSON.stringify(value));
    this.pending=value;
  }
  canPrepare() { return !this.pending || !this.pending.attempted || this.pending.rejected; }
  async prepare(allowActive) {
    if(!this.canPrepare()) throw Error(t("上次解除結果尚未確認，請先重試原解除。"));
    const result=await this.api('/api/binding',{command:'preview',allow_active:allowActive});
    if(!matchesRelease(result.payload,this.identity())) throw Error(t("控制台身分已改變，未接受新預覽。"));
    this.save({preview:result,attempted:false,rejected:false});
    return result;
  }
  async confirm() {
    const value=this.pending;
    if(!matchesRelease(value?.preview?.payload,this.identity())) throw Error(t("預覽與目前 instance 或設定不符，請回到原控制台。"));
    if(!value.preview.can_release || value.rejected) throw Error(t("目前預覽不可解除，請處理阻擋原因後重新檢查。"));
    this.save({...value,attempted:true});
    try {
      const result=await this.api('/api/binding',{command:'release',payload:value.preview.payload});
      // Clear only after a definitive receipt. Failure to clear keeps a safe deduped retry.
      this.storage.removeItem('relay.binding.release');this.pending=null;
      return result;
    } catch(error) {
      if(!error.uncertain && ['binding_changed','binding_not_stale','release_blocked','app_not_attached'].includes(error.code))
        this.save({...this.pending,rejected:true});
      throw error;
    }
  }
}

export function mountBindings({api,identity,refreshSummary}) {
  const $=id=>document.getElementById(id);
  const node=(tag,text,className='')=>{const e=document.createElement(tag);e.textContent=text;e.className=className;return e;};
  let current=null,flow=null,busy=false,catalog=null;
  const error=text=>{$('binding-error').textContent=text||'';$('binding-error').hidden=!text;};
  const time=stamp=>typeof stamp==='number'?new Date(stamp*1000).toLocaleString(getLanguage(),{timeZone:'Asia/Taipei',hour12:false}):t("尚無活動");
  function controls() {
    const pending=flow?.pending;
    $('binding-prepare').disabled=busy||!flow||!current?.app||(!current.app.thread_id&&!current.app.successor_thread_id)||!flow.canPrepare();
    $('binding-active').disabled=busy||!!(pending?.attempted&&!pending?.rejected);
    $('binding-confirm').hidden=!pending;
    $('binding-confirm').disabled=busy||!pending?.preview?.can_release||!!pending?.rejected||!matchesRelease(pending?.preview?.payload,identity());
    $('binding-confirm').textContent=pending?.attempted&&!pending?.rejected?t("重試原解除"):t("確認解除此綁定");
    $('binding-preview').hidden=!pending;
    if(pending?.preview) {
      const view=pending.preview;
      const count=Object.values(view.release_blockers||{}).reduce((a,b)=>a+b,0);
      $('binding-preview-text').textContent=pending.rejected?t("這份預覽已被拒絕。請處理原因，再按「檢查解除條件」。"):
        pending.attempted?t("上次解除結果尚未確認。請重試原解除；即使已有新對話連線，也只查回原解除結果。"):
        count?t("有 {0} 筆派工尚未收尾，目前不可解除。", count):
        view.requires_allow_active?t("此對話最近仍有活動。若確定要解除，勾選下方選項後重新檢查。"):
        t("可以解除。")+(view.payload.allow_active?t("本次預覽允許解除仍有活動的對話。"):'')+t("確認後會開放這份 instance，讓新對話自行連線。原登入與任務資料會保留。");
      $('binding-preview-id').textContent=t("本次檢查：")+view.payload.request_id;
    }
  }
  function renderCatalog(value) {
    current=value.instances.find(item=>item.current)||null;
    $('instance-name').textContent=current?`${current.name==='base'?t("base（預設）"):current.name} · ${new URL(current.console_url).port}`:t("目前 instance 未知");
    $('instance-presence').textContent=bindingLabel(current?.app);
    $('binding-current').textContent=current?t("目前操作：{0} · {1}", current.name, current.console_url):t("目前 instance 未知");
    const list=$('instance-list');list.replaceChildren();
    for(const item of value.instances) {
      const card=node('article','','instance-row'+(item.current?' current':''));
      const head=node('div','','instance-row-head');head.append(node('strong',item.name==='base'?t("base（預設）"):item.name));
      head.append(node('span',item.current?t("目前控制台"):item.running===true?t("服務執行中"):item.running===false?t("服務未啟動"):t("狀態不可用"),'badge '+(item.current?'good':'muted')));
      card.append(head,node('p',bindingLabel(item.app),'instance-state'));
      card.append(node('p',item.console_url||t("設定無法讀取"),'subtle'));
      if(item.app?.last_seen_at)card.append(node('p',t("最近活動：")+time(item.app.last_seen_at),'subtle'));
      if(item.app?.thread_id||item.app?.successor_thread_id) {
        const detail=node('details','','instance-identity');detail.append(node('summary',t("查看綁定識別")));
        detail.append(node('code',item.app.thread_id||item.app.successor_thread_id));card.append(detail);
      }
      const link=localConsoleLink(item.console_url);
      if(!item.current&&link&&item.running===true){const a=node('a',t("前往此控制台管理綁定"),'text-button');a.href=link+'#connections';card.append(a);}
      list.append(card);
    }
    $('catalog-note').textContent=value.catalog_error?t("部分主 instance 尚未登記或設定需檢查；目前控制台仍可操作。"):'';
    controls();
  }
  async function load(){catalog=await api('/api/instances');renderCatalog(catalog);}
  function renderLanguage(){
    for(const id of ['binding-error','binding-result'])$(id).textContent=retranslate($(id).textContent);
    if(catalog)renderCatalog(catalog);else controls();
  }
  async function operate(callback) {
    if(busy)return;busy=true;error('');controls();
    try {await callback();}catch(e){error(e.message);}finally{busy=false;controls();}
  }
  async function open() {
    if(!$('binding-dialog').open)$('binding-dialog').showModal();
    await operate(async()=>{if(!flow){flow=new ReleaseFlow(api,sessionStorage,identity);if(flow.pending)$('binding-active').checked=flow.pending.preview?.payload?.allow_active===true;}await load();});
  }
  $('manage-bindings').addEventListener('click',open);
  $('close-bindings').addEventListener('click',()=>$('binding-dialog').close());
  $('binding-refresh').addEventListener('click',()=>operate(load));
  $('binding-prepare').addEventListener('click',()=>operate(async()=>{await flow.prepare($('binding-active').checked);}));
  $('binding-confirm').addEventListener('click',()=>operate(async()=>{
    const result=await flow.confirm();$('binding-result').textContent=t("解除已確認。請讓要接手的新對話使用此控制台的「複製 App 連線說明」。");
    $('binding-result').hidden=false;await load();await refreshSummary();
  }));
  // Checkbox changes require a new preview; they can never alter a saved request.
  $('binding-active').addEventListener('change',()=>{
    if(flow?.pending&&!flow.pending.attempted){flow.storage.removeItem('relay.binding.release');flow.pending=null;controls();}
  });
  return {load,open,renderLanguage,presence:app=>{$('instance-presence').textContent=bindingLabel(app);}};
}
