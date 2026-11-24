<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue';
import type { ApiClient } from '../api';
import type { InterviewSession } from '../types';

interface Props {
  jobSelected: boolean;
  session: InterviewSession | null;
  api: ApiClient;
  cloud: boolean;
  waitTask: <T>(id: string, parseResult: (value: unknown) => T) => Promise<T>;
  notify: (message: string, error?: boolean) => void;
  onRefresh: () => Promise<void>;
}
const props = defineProps<Props>();
const answers = ref<Record<string, string>>({});
const busy = ref(false);
const turns = computed(() => props.session?.turns || []);
const processing = new Set(['new', 'question_pending', 'reference_pending', 'feedback_pending']);
const dirty = new Set<string>();
let timer: number | undefined;
let saveQueue = Promise.resolve();
watch(() => props.session?.id, (_, oldId) => {
  if (oldId && dirty.size) void saveDrafts(oldId).catch(() => {});
  if (timer) window.clearTimeout(timer);
  dirty.clear();
  answers.value = Object.fromEntries((props.session?.turns || []).map(turn => [turn.id, turn.answer || '']));
}, { immediate: true });
watch(() => props.session?.turns, saved => {
  for (const turn of saved || []) if (!dirty.has(turn.id)) answers.value[turn.id] = turn.answer || '';
});
onUnmounted(() => { if (dirty.size) void saveDrafts().catch(() => {}); });
const allFilled = computed(() => turns.value.length === 12 && turns.value.every(turn => !!answers.value[turn.id]?.trim()));
const hasUnevaluated = computed(() => turns.value.some(turn => !turn.feedback));
function changed(id: string) {
  dirty.add(id);
  if (timer) window.clearTimeout(timer);
  timer = window.setTimeout(() => { void saveDrafts().catch(() => {}); }, 500);
}
async function saveDrafts(sessionId = props.session?.id) {
  if (timer) window.clearTimeout(timer);
  timer = undefined;
  if (!sessionId) return;
  const pending = [...dirty].map(id => [id, answers.value[id] || ''] as const);
  dirty.clear();
  saveQueue = saveQueue.catch(() => {}).then(async () => {
    for (const [index, [turnId, answer]] of pending.entries()) {
      try {
        await props.api('/api/interviews/' + encodeURIComponent(sessionId) + '/drafts', {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ turn_id: turnId, answer }),
        });
      } catch (error) {
        for (const [id] of pending.slice(index)) dirty.add(id);
        props.notify(error instanceof Error ? error.message : '草稿保存失败，请重试。', true);
        throw error;
      }
    }
  });
  await saveQueue;
}
async function run(action: 'answer' | 'all' | 'next' | 'end', turnId?: string) {
  const session = props.session;
  if (!session || busy.value) return;
  if (action === 'answer' && !answers.value[turnId || '']?.trim()) return;
  if (action === 'all' && !allFilled.value) return;
  busy.value = true;
  try {
    if (action === 'all' || action === 'answer') await saveDrafts();
    const path = action === 'next' ? 'next' : action === 'end' ? 'end' : 'answers';
    const body = action === 'answer' ? { turn_id: turnId, answer: answers.value[turnId || ''].trim() }
      : action === 'all' ? { turn_ids: turns.value.map(turn => turn.id) } : undefined;
    const result = await props.api<{ task_id?: string }>('/api/interviews/' + encodeURIComponent(session.id) + '/' + path, {
      method: 'POST', ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
    });
    if (props.cloud && result.task_id) {
      await props.onRefresh();
      try { await props.waitTask(result.task_id, value => value); }
      catch (error) { props.notify(error instanceof Error ? error.message : '后台任务失败，请重试。', true); }
    }
    await props.onRefresh();
  } catch (error) {
    props.notify(error instanceof Error ? error.message : '面试操作失败，请重试。', true);
    try { await props.onRefresh(); } catch { /* Keep the original error visible. */ }
  } finally { busy.value = false; }
}
</script>

<template>
  <section id="interview-panel" class="interview-panel">
    <details v-if="session" id="interview-context">
      <summary>查看本场招聘要求</summary>
      <p id="interview-requirements">{{ session.job_text || '' }}</p>
    </details>
    <div id="interview-session">
      <p v-if="!jobSelected" class="hint">请先选择目标职位和简历版本。</p>
      <p v-else-if="!session" class="hint">选择一个已保存的简历版本后开始面试。</p>
      <template v-else>
        <div class="interview-toolbar">
          <strong>{{ session.job_title || '目标职位' }} · V{{ session.version_number }}{{ session.version_deleted ? '（关联版本已删除）' : '' }} · {{ turns.length }} 题{{ session.status === 'ended' ? ' · 已结束' : '' }}</strong>
          <button v-if="session.status !== 'ended'" id="end-interview" class="secondary" type="button" :disabled="busy" @click="run('end')">结束本次面试</button>
        </div>
        <article v-for="(turn, index) in turns" :key="turn.id" class="interview-turn">
          <h3>第 {{ index + 1 }} 题 · {{ turn.category || '面试问题' }}</h3>
          <p>{{ turn.question || '' }}</p>
          <details v-if="turn.reference_answer" class="reference-answer">
            <summary>参考回答要点</summary><p>{{ turn.reference_answer }}</p>
          </details>
          <label class="interview-answer">你的回答
            <textarea v-model="answers[turn.id]" :aria-label="'第 ' + (index + 1) + ' 题回答'" rows="6" maxlength="4000"
              :disabled="busy || !!turn.answered_at || session.status === 'ended' || processing.has(session.status)"
              @input="changed(turn.id)" @blur="saveDrafts().catch(() => {})" />
          </label>
          <button v-if="!turn.feedback && session.status !== 'ended'" type="button" :disabled="busy || !answers[turn.id]?.trim() || processing.has(session.status)"
            @click="run('answer', turn.id)">{{ turn.answered_at ? '重试评价' : '提交本题评价' }}</button>
          <div v-if="turn.feedback" class="interview-feedback">
            <strong>评价</strong><p>{{ turn.feedback.evaluation || '' }}</p>
            <strong>回答要点</strong><p>{{ turn.feedback.answer_points || '' }}</p>
          </div>
        </article>
        <button v-if="hasUnevaluated && session.status !== 'ended' && turns.length === 12" id="submit-all-answers" type="button"
          :disabled="busy || !allFilled || processing.has(session.status)" @click="run('all')">全部回答后统一提交评价</button>
        <p v-if="processing.has(session.status)" class="hint" role="status">AI 正在处理，请稍候…</p>
        <p v-if="session.status === 'failed'" class="error" role="status">{{ session.error?.message || (turns.length ? '回答反馈生成失败，回答已保存，请重试。' : '题目与参考回答要点生成失败，请重试。') }}</p>
        <button v-if="session.status === 'failed' && !turns.length" id="retry-questions" type="button" :disabled="busy" @click="run('next')">重试生成题目</button>
      </template>
    </div>
  </section>
</template>
