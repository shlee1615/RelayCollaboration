import {t, getLanguage} from './i18n.js';
// flow_model.js — 純函式模組，供 Codex 端 GUI 整合使用。
// 無 DOM、無網路、無外部依賴。ES module，三個 export。

const VALID_ACTORS = new Set(['codex', 'claude']);

function normActor(a) {
  return VALID_ACTORS.has(a) ? a : 'unknown';
}

/**
 * statusLabel(task) → 繁中狀態字串。
 * 規則：pending→待認領；running→執行中；running+claim_expired→需確認；
 * failed→執行失敗；completed且result_ok===false→回覆失敗；completed→已完成；其餘→未知。
 * 不計算本機時間，僅依欄位判斷。
 */
export function statusLabel(task) {
  if (!task || typeof task !== 'object') return t("未知");
  const s = task.status;
  if (s === 'pending') return t("待認領");
  if (s === 'running') return task.claim_expired === true ? t("需確認") : t("執行中");
  if (s === 'failed') return t("執行失敗");
  if (s === 'completed') return task.result_ok === false ? t("回覆失敗") : t("已完成");
  return t("未知");
}

/**
 * formatBytes(n) → 人類可讀字串。
 * null/undefined/NaN/負數/非number/Infinity → '—'；0 → '0 B'；
 * <1024 → 整數 + ' B'；之後依 1024 倍率 KiB/MiB/GiB，保留 1 位小數（上限 GiB，不再升位）。
 */
export function formatBytes(n) {
  if (typeof n !== 'number' || !Number.isFinite(n) || n < 0) return '—';
  if (n === 0) return '0 B';
  if (n < 1024) return Math.floor(n) + ' B';
  const units = ['KiB', 'MiB', 'GiB'];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return v.toFixed(1) + ' ' + units[i];
}

/**
 * buildFlow(task) → 恰好 4 個 step 或 []（task 為 null/非物件時）。
 * step = { actor, label, state, time }
 * actor 依序：task.sender, 'broker', task.recipient, task.sender（僅接受 codex/claude，否則 'unknown'）。
 * label 依序：'送出任務','保存與派送','接收與處理','收到回覆'。
 * state ∈ {'done','active','waiting','attention'}。
 * time 依序：created_at, created_at, claimed_at||null, completed_at||null。不捏造其他事件時間。
 */
export function buildFlow(task) {
  if (!task || typeof task !== 'object') return [];

  const sender = normActor(task.sender);
  const recipient = normActor(task.recipient);
  const status = task.status;
  const expired = task.claim_expired === true;
  const resultFailed = status === 'completed' && task.result_ok === false;
  const completedOk = status === 'completed' && task.result_ok !== false;

  // 第 3 步（接收與處理）
  let s3 = 'waiting';
  if (status === 'pending') {
    s3 = 'waiting';
  } else if (status === 'running') {
    s3 = expired ? 'attention' : 'active';
  } else if (status === 'failed') {
    s3 = 'attention';
  } else if (status === 'completed') {
    s3 = resultFailed ? 'attention' : 'done';
  }

  // 第 4 步（收到回覆）
  let s4 = 'waiting';
  if (completedOk) {
    s4 = 'done';
  } else if (status === 'failed' || resultFailed) {
    s4 = 'attention';
  }

  const created = task.created_at != null ? task.created_at : null;

  return [
    { actor: sender, label: t("送出任務"), state: 'done', time: created },
    { actor: 'broker', label: t("保存與派送"), state: 'done', time: created },
    { actor: recipient, label: t("接收與處理"), state: s3, time: task.claimed_at != null ? task.claimed_at : null },
    { actor: sender, label: t("收到回覆"), state: s4, time: task.completed_at != null ? task.completed_at : null }
  ];
}
// Build a review draft from the selected terminal reply. The user still submits it.
export function replyDraft(task) {
  if (!task || task.status !== 'completed' || task.result?.ok !== true ||
      typeof task.result.text !== 'string' || !task.result.text.trim() ||
      !['codex', 'claude'].includes(task.sender)) return null;
  return {
    recipient: task.sender,
    title: (t("續議：") + task.title).slice(0, 100),
    prompt: t("請根據附件的原任務與回覆，提出下一輪審查意見。原任務 ID：{0}", task.task_id),
    contexts: [
      {name: 'previous-task.txt', text: task.prompt},
      {name: 'previous-reply.txt', text: task.result.text}
    ]
  };
}
export function appConnectionView(worker){
 const app=worker?.app||{};
 if(!worker?.running)return {label:t("接收已停止"),detail:t("啟動接收服務後，App 才會取得新任務。"),tone:'muted'};
 if(['manual_review','attention_required','transport_error'].includes(worker.state))return {label:t("需確認"),detail:t("收件處理異常：")+(worker.reason||worker.state),tone:'attention'};
 if(app.state==='connected')return {label:worker.state==='executing'?t("等待 App 回覆"):t("App 已連接"),detail:t("沿用已登入的 Codex App。自動收件由 App 任務排程執行；此狀態表示最近有收件活動。"),tone:'good'};
 if(app.state==='stale')return {label:t("等候 App 收件"),detail:t("App 最近未收件。請保持 App 開啟並確認任務排程；新任務會留在佇列。"),tone:'attention'};
 return {label:t("尚未連接 App"),detail:t("不需要重新登入 Codex。複製連線說明，貼到要參與協作的 App 任務。"),tone:'muted'};
}
