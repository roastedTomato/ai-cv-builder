import { afterEach, expect, test, vi } from 'vitest';
import { createApp, nextTick } from 'vue';
import InterviewPanel from './InterviewPanel.vue';
import type { InterviewSession } from '../types';

let unmount: (() => void) | undefined;
afterEach(() => {
  unmount?.();
  unmount = undefined;
  document.body.innerHTML = '';
});

function mount(session: InterviewSession, api = vi.fn(async (_url: string, _options?: RequestInit) => ({}))) {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const onRefresh = vi.fn(async () => {});
  const app = createApp(InterviewPanel, {
    jobSelected: true,
    session,
    api,
    cloud: false,
    waitTask: vi.fn(async () => ({})),
    notify: vi.fn(),
    onRefresh,
  });
  app.mount(host);
  unmount = () => app.unmount();
  return { host, api, onRefresh };
}

test('shows a fictional saved answer and the actual failure state', () => {
  const { host } = mount({
    id: 'fictional-session', job_title: '虚构测试岗位', version_number: 2,
    status: 'failed', turns: [{ id: 'fictional-turn', question: '虚构测试问题', answer: '虚构测试回答' }],
  });
  expect(host.textContent).toContain('虚构测试回答');
  expect(host.textContent).toContain('回答反馈生成失败，回答已保存，请重试。');
  expect(host.querySelector('#next-question')?.textContent).toBe('重试');
});

test('submits an answer only for the selected fictional session', async () => {
  const { host, api, onRefresh } = mount({
    id: 'fictional-session', job_title: '虚构测试岗位', version_number: 1,
    status: 'awaiting_answer', turns: [{ id: 'fictional-turn', question: '虚构测试问题', reference_answer: '虚构参考答案' }],
  });
  expect(host.querySelector('.reference-answer')?.textContent).toContain('虚构参考答案');
  const textarea = host.querySelector<HTMLTextAreaElement>('#interview-answer');
  expect(textarea).not.toBeNull();
  textarea!.value = '虚构测试回答';
  textarea!.dispatchEvent(new Event('input', { bubbles: true }));
  host.querySelector<HTMLFormElement>('#answer-form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  await nextTick();
  expect(api).toHaveBeenCalledWith('/api/interviews/fictional-session/answers', expect.objectContaining({
    method: 'POST', body: JSON.stringify({ answer: '虚构测试回答' }),
  }));
  await vi.waitFor(() => expect(onRefresh).toHaveBeenCalled());
});
