import { mount, flushPromises } from '@vue/test-utils';
import { reactive } from 'vue';
import { describe, expect, it, vi } from 'vitest';
import BaseResume from './BaseResume.vue';
import type { ApiClient } from '../api';
import type { BaseResumeData } from '../types';

const fictionalBase = (): BaseResumeData => ({
  resume_id: 'fictional-resume', version: '1',
  personal: { name: '虚构测试者', email: 'fictional@example.com', phone: '000', location: '' },
  education: [], work: [], projects: [], skills: [], certificates: [],
});

function props(api: ApiClient, base: BaseResumeData | null = null) {
  return {
    base,
    onBaseChange: vi.fn(),
    api,
    cloud: false,
    waitTask: async <T>(_id: string, parseResult: (value: unknown) => T): Promise<T> => parseResult(null),
    notify: vi.fn(),
    beforeReset: vi.fn(async () => true),
    onReset: vi.fn(async () => {}),
  };
}

describe('BaseResume', () => {
  it('edits and saves a reactive resume prop without mutating the confirmed source', async () => {
    const confirmed = reactive(fictionalBase());
    const api = async <T = unknown>(_url: string, options?: RequestInit): Promise<T> => JSON.parse(String(options?.body)) as T;
    const input = props(api, confirmed);
    const wrapper = mount(BaseResume, { props: input });
    await wrapper.find('#edit').trigger('click');
    await wrapper.find('input[data-key="name"]').setValue('虚构修改者');
    expect(confirmed.personal.name).toBe('虚构测试者');
    await wrapper.find('#save').trigger('click');
    await flushPromises();
    expect(input.onBaseChange).toHaveBeenCalledWith(expect.objectContaining({ personal: expect.objectContaining({ name: '虚构修改者' }) }));
  });

  it('sends the required empty location field when saving a new resume', async () => {
    const calls: Array<{ url: string; options?: RequestInit }> = [];
    const api = async <T = unknown>(url: string, options?: RequestInit): Promise<T> => {
      calls.push({ url, options });
      return JSON.parse(String(options?.body)) as T;
    };
    const input = props(api);
    const wrapper = mount(BaseResume, { props: input });
    await wrapper.find('input[data-key="name"]').setValue('虚构测试者');
    await wrapper.find('input[data-key="email"]').setValue('fictional@example.com');
    await wrapper.find('input[data-key="phone"]').setValue('000');
    await wrapper.find('#save').trigger('click');
    await flushPromises();
    expect(calls[0].url).toBe('/api/resume');
    expect(JSON.parse(String(calls[0].options?.body)).personal.location).toBe('');
    expect(input.onBaseChange).toHaveBeenCalledOnce();
  });

  it('keeps an imported draft unconfirmed until the confirmation request succeeds', async () => {
    const draft = { ...fictionalBase(), personal: { ...fictionalBase().personal, name: '虚构导入者' } };
    const api = async <T = unknown>(url: string): Promise<T> => {
      if (url === '/api/import') return { draft_id: 'fictional-draft', draft } as T;
      if (url === '/api/import/fictional-draft/confirm') return draft as T;
      throw new Error('Unexpected test request');
    };
    const input = props(api, fictionalBase());
    const wrapper = mount(BaseResume, { props: input });
    await wrapper.find('#edit').trigger('click');
    const upload = wrapper.find('#upload');
    Object.defineProperty(upload.element, 'files', { configurable: true, value: [new File(['fictional content'], 'fictional.docx')] });
    await upload.trigger('change');
    await flushPromises();
    expect(wrapper.find('#resume-state').text()).toContain('待确认');
    expect(input.onBaseChange).not.toHaveBeenCalled();
    await wrapper.find('#confirm').trigger('click');
    await flushPromises();
    expect(input.onBaseChange).toHaveBeenCalledOnce();
    expect(input.onBaseChange).toHaveBeenCalledWith(draft);
  });
});
