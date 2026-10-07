import {test} from 'node:test';
import assert from 'node:assert/strict';
import {statusLabel, formatBytes, buildFlow, replyDraft, appConnectionView} from '../relay_collaboration/web/app.js';
import {ReleaseFlow, bindingLabel, localConsoleLink} from '../relay_collaboration/web/bindings.js';
import {english, t, getLanguage, setLanguage, resolveLanguage, initializeLanguage, onLanguageChange} from '../relay_collaboration/web/i18n.js';
import fs from 'node:fs';

function releaseFixture(){
 const data=new Map();const storage={getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)};
 const identity=()=>({profileId:'runtime-a',configDigest:'config-a'});
 const preview={can_release:true,payload:{schema:'relay-app-release-request/v2',runtime_id:'runtime-a',config_sha256:'config-a',request_id:'one-saved-request',confirm_release:true,successor_thread_id:null}};
 return {storage,identity,preview};
}
test('lost release response and refresh retry the identical saved payload',async()=>{
 const {storage,identity,preview}=releaseFixture();let calls=[],lost=true;
 const api=async(path,body)=>{calls.push(structuredClone(body));if(body.command==='preview')return preview;if(lost){lost=false;throw Object.assign(Error('lost'),{uncertain:true});}return {state:'released',deduped:true};};
 let flow=new ReleaseFlow(api,storage,identity);await flow.prepare(false);
 await assert.rejects(flow.confirm(),/lost/);assert.equal(flow.canPrepare(),false);
 flow=new ReleaseFlow(api,storage,identity);
 await assert.rejects(flow.prepare(true),/原解除/);
 assert.equal((await flow.confirm()).deduped,true);
 assert.deepEqual(calls[1],calls[2]);assert.equal(calls.filter(c=>c.command==='preview').length,1);
});
test('changed profile and failed durable storage prevent release POST',async()=>{
 const {storage,identity,preview}=releaseFixture();let posts=0;
 const api=async(_,body)=>{if(body.command==='release')posts++;return preview;};
 const flow=new ReleaseFlow(api,storage,identity);await flow.prepare(false);
 flow.identity=()=>({profileId:'other',configDigest:'config-a'});await assert.rejects(flow.confirm(),/不符/);assert.equal(posts,0);
 flow.identity=identity;storage.setItem=()=>{throw Error('storage unavailable');};
 await assert.rejects(flow.confirm(),/storage/);assert.equal(posts,0);
});
test('definitive snapshot rejection allows manual new preview but never automatic release',async()=>{
 const {storage,identity,preview}=releaseFixture();let count=0;
 const flow=new ReleaseFlow(async(_,body)=>{count++;if(body.command==='preview')return preview;throw Object.assign(Error('changed'),{code:'binding_changed'});},storage,identity);
 await flow.prepare(false);await assert.rejects(flow.confirm());assert.equal(flow.canPrepare(),true);assert.equal(count,2);
 await assert.rejects(flow.confirm());assert.equal(count,2);
});
test('binding views distinguish a reservation and only link to local consoles',()=>{
 assert.match(bindingLabel({state:'unbound',successor_thread_id:'x'}),/交接/);
 assert.match(bindingLabel({state:'unbound'}),/新對話/);
 for(const url of ['javascript:alert(1)','https://example.com/','http://evil@127.0.0.1:9242/','http://127.0.0.1:9242/private','http://127.0.0.1:9242/?secret=x'])assert.equal(localConsoleLink(url),null);
 assert.equal(localConsoleLink('http://127.0.0.1:9242'),'http://127.0.0.1:9242/');
});

test('App presence is separate from the relay supervisor and CLI authentication',()=>{
 assert.equal(appConnectionView({running:false,app:{state:'connected'}}).label,'接收已停止');
 assert.equal(appConnectionView({running:true,state:'auth_blocked',app:{state:'unbound'}}).label,'尚未連接 App');
 assert.equal(appConnectionView({running:true,state:'idle',app:{state:'stale'}}).label,'等候 App 收件');
 assert.equal(appConnectionView({running:true,state:'executing',app:{state:'connected'}}).label,'等待 App 回覆');
 assert.equal(appConnectionView({running:true,state:'manual_review',app:{state:'connected'}}).label,'需確認');
});

test('reply handoff reverses both directions and preserves supplied text',()=>{
 for(const sender of ['codex','claude']){
  const task={task_id:'original-id',sender,title:'檢查',prompt:'原任務',status:'completed',result:{ok:true,text:'回覆<script>'}};
  const draft=replyDraft(task);
  assert.equal(draft.recipient,sender);
  assert.ok(draft.prompt.includes('original-id'));
  assert.deepEqual(draft.contexts.map(c=>c.text),['原任務','回覆<script>']);
 }
 for(const task of [null,{status:'pending'},{status:'completed',result:{ok:false,text:'失敗'}}])assert.equal(replyDraft(task),null);
});
test('states distinguish expired claims and unsuccessful replies',()=>{
 assert.equal(statusLabel(null),'未知');
 assert.equal(statusLabel({status:'pending'}),'待認領');
 assert.equal(statusLabel({status:'running'}),'執行中');
 assert.equal(statusLabel({status:'running',claim_expired:true}),'需確認');
 assert.equal(statusLabel({status:'failed'}),'執行失敗');
 assert.equal(statusLabel({status:'completed',result_ok:false}),'回覆失敗');
 assert.equal(statusLabel({status:'completed'}),'已完成');
});
test('byte values reject unavailable values without presenting zero',()=>{
 for(const n of [null,undefined,NaN,Infinity,-1,'12'])assert.equal(formatBytes(n),'—');
 for(const [n,s] of [[0,'0 B'],[23,'23 B'],[1024,'1.0 KiB'],[1536,'1.5 KiB'],[1048576,'1.0 MiB']])assert.equal(formatBytes(n),s);
});
test('reverse data flow uses actual peer direction and recorded times',()=>{
 const steps=buildFlow({sender:'claude',recipient:'codex',status:'completed',created_at:1,claimed_at:2,completed_at:3});
 assert.deepEqual(steps.map(s=>s.actor),['claude','broker','codex','claude']);
 assert.deepEqual(steps.map(s=>s.time),[1,1,2,3]);
 assert.ok(steps.every(s=>s.state==='done'));
});
test('expiry requires attention without inventing returned data',()=>{
 const steps=buildFlow({sender:'codex',recipient:'claude',status:'running',claim_expired:true,created_at:1});
 assert.deepEqual(steps.map(s=>s.state),['done','done','attention','waiting']);
 assert.equal(steps[3].time,null);assert.deepEqual(buildFlow(null),[]);
});
test('failed result and unknown actors stay explicit',()=>{
 const steps=buildFlow({sender:'<script>',recipient:'?',status:'completed',result_ok:false});
 assert.deepEqual(steps.map(s=>s.actor),['unknown','broker','unknown','unknown']);
 assert.equal(steps[2].state,'attention');assert.equal(steps[3].state,'attention');
});

test('language selection honors preference and tolerates unavailable storage',()=>{
 assert.equal(resolveLanguage('en',['zh-TW']),'en');
 assert.equal(resolveLanguage('zh-Hant',['en-US']),'zh-Hant');
 assert.equal(resolveLanguage('invalid',['zh-HK']),'zh-Hant');
 assert.equal(resolveLanguage(null,['en-GB']),'en');
 assert.equal(resolveLanguage(null,['en-US','zh-TW']),'en');
 assert.equal(resolveLanguage(null,['fr-FR','zh-TW']),'zh-Hant');
 assert.equal(resolveLanguage(null,[]),'en');
 const label={dataset:{i18n:'資料流'},textContent:''};
 const field={getAttribute:()=> '搜尋任務',setAttribute:(k,v)=>{field[k]=v;}};
 let change;const select={value:'',addEventListener:(_,fn)=>{change=fn;}};
 const root={documentElement:{lang:''},getElementById:()=>select,querySelectorAll:q=>q==='[data-i18n]'?[label]:q==='[data-i18n-aria-label]'?[field]:[]};
 const storage={getItem:()=>{throw Error('blocked');},setItem:()=>{throw Error('blocked');}};
 try {
  initializeLanguage(storage,['en-US'],root);
  assert.equal(label.textContent,'Data flow');assert.equal(root.documentElement.lang,'en');
  assert.equal(field['aria-label'],'Search tasks');
  select.value='zh-Hant';assert.doesNotThrow(change);assert.equal(getLanguage(),'zh-Hant');
  assert.equal(setLanguage('unsupported'),false);
 } finally {setLanguage('zh-Hant');}
});

test('English flow and reply drafts preserve original content and protocol identity',()=>{
 const task={task_id:'fixed-id',sender:'claude',recipient:'codex',title:'原題 <script>',prompt:'原始內容 中文',status:'completed',result:{ok:true,text:'回覆 <script>unchanged</script>'}};
 const before=JSON.stringify(task);
 try {
  setLanguage('en');assert.equal(statusLabel({status:'running',claim_expired:true}),'Needs attention');
  assert.equal(statusLabel({status:'completed',result_ok:false}),'Reply failed');
  assert.equal(appConnectionView({running:true,app:{state:'stale'}}).label,'Waiting for App inbox check');
  assert.equal(bindingLabel({state:'unbound',successor_thread_id:'reserved'}),'Released · Reserved for handoff');
  assert.deepEqual(buildFlow(task).map(s=>s.label),['Submit task','Save and dispatch','Receive and process','Receive reply']);
  const draft=replyDraft(task);assert.equal(draft.recipient,'claude');assert.match(draft.prompt,/fixed-id/);
  assert.deepEqual(draft.contexts.map(c=>c.text),[task.prompt,task.result.text]);
  assert.equal(JSON.stringify(task),before);
  assert.equal(t('任務已保存，等待 {0} 認領。','<script>{1}</script>'),'Task saved; waiting for <script>{1}</script> to claim it.');
  assert.equal(t('constructor'),'constructor');assert.equal(t('__proto__'),'__proto__');
 } finally {setLanguage('zh-Hant');}
});

test('changing language cannot mutate or redispatch an uncertain release',async()=>{
 const {storage,identity,preview}=releaseFixture();const calls=[];
 const api=async(_,body)=>{calls.push(structuredClone(body));if(body.command==='preview')return preview;throw Object.assign(Error('lost'),{uncertain:true});};
 const flow=new ReleaseFlow(api,storage,identity);await flow.prepare(false);await assert.rejects(flow.confirm());
 const saved=storage.getItem('relay.binding.release');
 try {
  setLanguage('en');assert.equal(storage.getItem('relay.binding.release'),saved);assert.equal(calls.length,2);
  await assert.rejects(flow.prepare(true),/original release/);assert.equal(calls.length,2);
  await assert.rejects(new ReleaseFlow(api,storage,identity).confirm());assert.deepEqual(calls[1],calls[2]);
 } finally {setLanguage('zh-Hant');}
});

test('all marked GUI copy has English translations and matching placeholders',()=>{
 const source=fs.readFileSync(new URL('../relay_collaboration/web/index.html',import.meta.url),'utf8');
 const decode=s=>s.replace(/&quot;/g,'"').replace(/&amp;/g,'&').replace(/&#x27;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>');
 for(const match of source.matchAll(/data-i18n(?:-(?:aria-label|placeholder|title))?="([^"]+)"/g))assert.ok(Object.hasOwn(english,decode(match[1])),match[1]);
 for(const name of ['app.js','flow_model.js','bindings.js']){
  const code=fs.readFileSync(new URL('../relay_collaboration/web/'+name,import.meta.url),'utf8');
  for(const match of code.matchAll(/\bt\(("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/g)) {
   const key=match[1][0]==='"'?JSON.parse(match[1]):match[1].slice(1,-1);
   assert.ok(Object.hasOwn(english,key),name+': '+key);
  }
 }
 for(const [source,target] of Object.entries(english)){
  assert.ok(target.trim());assert.doesNotMatch(target,/[\u3400-\u9fff]/);
  assert.deepEqual([...source.matchAll(/\{\d+\}/g)].map(m=>m[0]).sort(),[...target.matchAll(/\{\d+\}/g)].map(m=>m[0]).sort(),source);
 }
});
