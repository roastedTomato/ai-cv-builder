<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { ApiClient } from '../api';
import type { InterviewSession } from '../types';

type Action = 'reference' | 'next' | 'end' | 'answer';

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
const answer = ref('');
const busy = ref(false);
const turns = computed(() => props.session?.turns || []);
const processingStatuses = new Set(['new', 'question_pending', 'reference_pending', 'feedback_pending']);

watch([() => props.session?.id, () => props.session?.turns?.at(-1)?.id], () => {
  answer.value = '';
});

const failureMessage = computed(() => {
  const last = turns.value.at(-1);
  return props.session?.error?.message || (last?.answer && !last.feedback
    ? '回答反馈生成失败，回答已保存，请重试。'
    : '问题与参考答案生成失败，请重试。');
});

async function run(action: Action) {
  const session = props.session;
  if (!session || busy.value) return;
  const submittedAnswer = action === 'answer' ? answer.value.trim() : '';
  if (action === 'answer' && !submittedAnswer) return;
  busy.value = true;
  try {
    const path = action === 'reference' ? 'reference'
      : action === 'next' ? 'next' : action === 'end' ? 'end' : 'answers';
    const result = await props.api<{ task_id?: string }>(`/api/interviews/${encodeURIComponent(session.id)}/${path}`, {
      method: 'POST',
      ...(action === 'answer' ? {
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ answer: submittedAnswer }),
      } : {}),
    });
    if (action === 'answer') answer.value = '';
    if (props.cloud && result.task_id) {
      await props.onRefresh();
      try {
        await props.waitTask(result.task_id, value => value);
      } catch (error) {
        props.notify(error instanceof Error ? error.message : '后台任务失败，请重试。', true);
      }
    }
    await props.onRefresh();
  } catch (error) {
    props.notify(error instanceof Error ? error.message : '面试操作失败，请重试。', true);
    try { await props.onRefresh(); } catch { /* Keep the original error visible. */ }
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section id="interview-panel" class="interview-panel" role="tabpanel">
    <details v-if="session" id="interview-context">
      <summary>查看本场招聘要求</summary>
      <p id="interview-requirements">{{ session.job_text || '' }}</p>
    </details>
    <div id="interview-session">
      <p v-if="!jobSelected" class="hint">请先选择目标职位和简历版本。</p>
      <p v-else-if="!session" class="hint">选择一个已保存版本，点击“用此版本开始面试”。</p>
      <template v-else>
        <div class="interview-toolbar">
          <strong>{{ session.job_title || '目标职位' }} · V{{ session.version_number }}{{ session.version_deleted ? '（关联版本已删除）' : '' }} · 第 {{ turns.length }} 题{{ session.status === 'ended' ? ' · 已结束' : '' }}</strong>
          <button v-if="session.status !== 'ended'" id="end-interview" class="secondary" type="button" :disabled="busy" @click="run('end')">结束本次面试</button>
        </div>
        <article v-for="(turn, index) in turns" :key="turn.id" class="interview-turn">
          <h3>第 {{ index + 1 }} 题 · {{ turn.category || '面试问题' }}</h3>
          <p>{{ turn.question || '' }}</p>
          <details v-if="turn.reference_answer || turn.feedback?.reference_answer" class="reference-answer">
            <summary>查看参考答案</summary>
            <p>{{ turn.reference_answer || turn.feedback?.reference_answer }}</p>
          </details>
          <button v-else-if="!turn.answer && index === turns.length - 1 && session.status === 'awaiting_answer'" type="button" class="secondary" :disabled="busy" @click="run('reference')">为旧题生成参考答案</button>
          <div v-if="turn.answer" class="interview-answer"><strong>你的回答</strong><p>{{ turn.answer }}</p></div>
          <div v-if="turn.feedback" class="interview-feedback">
            <strong>评价</strong><p>{{ turn.feedback.evaluation || '' }}</p>
            <strong>改进建议</strong><p>{{ turn.feedback.suggestions || '' }}</p>
            <strong>回答要点</strong><p>{{ turn.feedback.answer_points || '' }}</p>
            <template v-if="turn.follow_up"><strong>追问</strong><p>{{ turn.follow_up }}</p></template>
            <template v-if="turn.feedback.confirmed_facts?.length"><strong>已确认事实</strong><p>{{ turn.feedback.confirmed_facts.join('；') }}</p></template>
            <template v-if="turn.feedback.user_claims?.length"><strong>用户自述</strong><p>{{ turn.feedback.user_claims.join('；') }}</p></template>
            <template v-if="turn.feedback.unconfirmed_suggestions?.length"><strong>待确认建议</strong><p>{{ turn.feedback.unconfirmed_suggestions.join('；') }}</p></template>
          </div>
        </article>
        <form v-if="session.status === 'awaiting_answer'" id="answer-form" @submit.prevent="run('answer')">
          <label>你的回答<textarea id="interview-answer" v-model="answer" rows="8" maxlength="4000" required :disabled="busy" /></label>
          <button type="submit" :disabled="busy">提交回答</button>
        </form>
        <p v-if="processingStatuses.has(session.status)" class="hint" role="status">AI 正在处理，请稍候…</p>
        <p v-if="session.status === 'failed'" class="error" role="status">{{ failureMessage }}</p>
        <button v-if="['feedback_ready', 'failed'].includes(session.status) && turns.length < 12" id="next-question" type="button" :disabled="busy" @click="run('next')">{{ session.status === 'failed' ? '重试' : '下一题' }}</button>
        <p v-if="turns.length >= 12 && session.status === 'feedback_ready'" class="hint">已达到本场面试的 12 题上限。请结束本次面试。</p>
      </template>
    </div>
  </section>
</template>
