declare global {
  interface Window {
    cvAuth?: {
      enabled: boolean;
      ready: Promise<void>;
      url: (path: string) => string;
      headers: () => Record<string, string>;
    };
  }
}

export const isCloud = () => Boolean(window.cvAuth?.enabled);

export type ApiClient = <T = unknown>(url: string, options?: RequestInit) => Promise<T>;

export interface QueuedTask { task_id: string; status: 'queued' }
export type TaskStatus = 'queued' | 'running' | 'succeeded' | 'failed';
export interface TaskResponse {
  status: TaskStatus;
  result: unknown;
  error?: { message?: string } | null;
}

const taskStatuses: TaskStatus[] = ['queued', 'running', 'succeeded', 'failed'];

export function objectResult(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw Error('服务器返回的数据格式不正确，请重试。');
  }
  return value as Record<string, unknown>;
}

export function stringField(value: unknown, key: string): string {
  const field = objectResult(value)[key];
  if (typeof field !== 'string' || !field) throw Error('服务器返回的数据格式不正确，请重试。');
  return field;
}

export function queuedTask(value: unknown): QueuedTask {
  const record = objectResult(value);
  if (record.status !== 'queued') throw Error('服务器返回了未知任务状态，请重试。');
  return { task_id: stringField(record, 'task_id'), status: 'queued' };
}

function taskResponse(value: unknown): TaskResponse {
  const record = objectResult(value);
  if (!taskStatuses.includes(record.status as TaskStatus)) {
    throw Error('服务器返回了未知任务状态，请重试。');
  }
  const error = record.error && typeof record.error === 'object' && !Array.isArray(record.error)
    ? record.error as { message?: string } : null;
  return { status: record.status as TaskStatus, result: record.result, error };
}

export async function api<T = unknown>(url: string, options: RequestInit = {}): Promise<T> {
  const auth = window.cvAuth;
  const response = await fetch(auth?.url(url) || url, {
    ...options,
    headers: { ...(options.headers || {}), ...(auth?.headers() || {}) },
  });
  if (!response.ok) {
    let body: { error?: { message?: string } } = {};
    try { body = await response.json(); } catch { /* Keep the status fallback. */ }
    throw Error(body.error?.message || `请求失败 (${response.status})`);
  }
  return response.headers.get('content-type')?.includes('application/json')
    ? response.json() as Promise<T> : response as T;
}

export const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

export async function waitTask<T>(taskId: string, parseResult: (value: unknown) => T): Promise<T> {
  for (let attempt = 0; attempt < 180; attempt += 1) {
    await new Promise(resolve => setTimeout(resolve, 2000));
    const task = taskResponse(await api(`/api/tasks/${encodeURIComponent(taskId)}`));
    if (task.status === 'succeeded') return parseResult(task.result);
    if (task.status === 'failed') throw Error(task.error?.message || '后台任务失败，请重试。');
  }
  throw Error('任务执行时间过长，请稍后重试。');
}

export const errorMessage = (error: unknown) => error instanceof Error ? error.message : '操作失败，请重试。';
