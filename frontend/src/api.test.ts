import { afterEach, expect, test, vi } from 'vitest';
import { objectResult, queuedTask, stringField, waitTask } from './api';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

test('requires a queued task id before polling', () => {
  expect(() => queuedTask({ status: 'queued' })).toThrow('服务器返回的数据格式不正确');
});

test('rejects malformed task results before a caller reads their fields', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true,
    headers: { get: () => 'application/json' },
    json: async () => ({ status: 'succeeded', result: { wrong_field: 'fictional test value' } }),
  })));
  const pending = waitTask('fictional-task', value => stringField(value, 'text'));
  const rejection = expect(pending).rejects.toThrow('服务器返回的数据格式不正确');
  await vi.runAllTimersAsync();
  await rejection;
});

test('rejects an unknown task status instead of polling forever', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true,
    headers: { get: () => 'application/json' },
    json: async () => ({ status: 'fictional-unknown', result: null }),
  })));
  const pending = waitTask('fictional-task', objectResult);
  const rejection = expect(pending).rejects.toThrow('未知任务状态');
  await vi.runAllTimersAsync();
  await rejection;
});
