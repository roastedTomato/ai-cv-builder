import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, expect, test, vi } from 'vitest';
import App from './App.vue';

afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear(); });

const response = (body: unknown) => ({
  ok: true, headers: { get: () => 'application/json' }, json: async () => body,
});

const fictionalVersion = (id: string) => ({
  id, number: 1, resume_content: { personal: { name: '虚构姓名' }, summary: [{ text: '虚构经历' }] },
  module_order: ['summary'], visual_settings: { template_id: 'template-01', color: '#2C5263', page_width: 'standard' },
});

test('loads the Vue workspace and selects company, job and resume version', async () => {
  window.cvAuth = { enabled: false, ready: Promise.resolve(), url: path => path, headers: () => ({}) };
  const company = { id: 'fictional-company', name: '虚构公司', jobs: [{
    id: 'fictional-job', title: '虚构岗位', job_text: '虚构招聘要求', has_generated: true,
    versions: [{ id: 'fictional-version', number: 1,
      resume_content: { personal: { name: '虚构姓名' }, summary: [{ text: '虚构经历' }] },
      module_order: ['summary'], visual_settings: { template_id: 'template-01', color: '#2C5263', page_width: 'standard' } }],
  }] };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({
    ok: true, headers: { get: () => 'application/json' },
    json: async () => url === '/api/resume' ? null : url === '/api/templates' ? [{ id: 'template-01', name: '简洁蓝' }]
      : url === '/api/applications' ? { companies: [company] } : [],
  })));
  const wrapper = mount(App);
  await flushPromises();
  expect(wrapper.text()).toContain('AI 生成内容未经事实核验');
  await wrapper.find('.company-select').trigger('click');
  await wrapper.find('.job-select').trigger('click');
  await flushPromises();
  await wrapper.find('.version-bubble').trigger('click');
  expect(wrapper.find('#resume-document').text()).toContain('虚构经历');
  expect(wrapper.find('#export').attributes('disabled')).toBeUndefined();
  wrapper.unmount();
});

test('starts an interview from a version selected in the sidebar while viewing the interview tab', async () => {
  window.cvAuth = { enabled: false, ready: Promise.resolve(), url: path => path, headers: () => ({}) };
  const company = { id: 'fictional-company', name: '虚构公司', jobs: [{
    id: 'fictional-job', title: '虚构岗位', job_text: '虚构招聘要求', has_generated: true,
    versions: [{ ...fictionalVersion('fictional-v1'), number: 1 }, { ...fictionalVersion('fictional-v2'), number: 2 }],
  }] };
  let started = false;
  const session = { id: 'fictional-session', job_title: '虚构岗位', version_number: 2, status: 'awaiting_answer', turns: [] };
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/resume') return response(null);
    if (url === '/api/templates') return response([]);
    if (url === '/api/applications') return response({ companies: [company] });
    if (url === '/api/interviews?job_id=fictional-job') return response(started ? [{ id: session.id }] : []);
    if (url === '/api/interviews/fictional-session') return response(session);
    if (url === '/api/jobs/fictional-job/versions/fictional-v2/interviews') {
      started = true;
      return response(session);
    }
    throw Error(`Unexpected test URL: ${url}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  const wrapper = mount(App);
  await flushPromises();
  await wrapper.find('.company-select').trigger('click');
  await wrapper.find('.job-select').trigger('click');
  await wrapper.find('#tab-interview').trigger('click');
  await flushPromises();
  expect(wrapper.find('#start-interview-here').exists()).toBe(false);
  expect(wrapper.find('#interview-workspace .job-version-bubble').exists()).toBe(false);
  await wrapper.findAll('.navigation .job-version-bubble')[1].trigger('click');
  expect(wrapper.findAll('.navigation .job-version-bubble')[1].attributes('aria-pressed')).toBe('true');
  await wrapper.find('#start-interview-here').trigger('click');
  await flushPromises();
  expect(fetchMock).toHaveBeenCalledWith('/api/jobs/fictional-job/versions/fictional-v2/interviews', expect.objectContaining({ method: 'POST' }));
  expect(wrapper.find('#interview-session').text()).toContain('虚构岗位 · V2');
  wrapper.unmount();
});

test('ignores an old interview response after selecting another fictional job', async () => {
  window.cvAuth = { enabled: false, ready: Promise.resolve(), url: path => path, headers: () => ({}) };
  const companies = ['a', 'b'].map(id => ({
    id: `fictional-company-${id}`, name: `虚构公司 ${id}`,
    jobs: [{ id: `fictional-job-${id}`, title: `虚构岗位 ${id}`, job_text: '虚构招聘要求', versions: [] }],
  }));
  let resolveOld!: (value: ReturnType<typeof response>) => void;
  const oldDetail = new Promise<ReturnType<typeof response>>(resolve => { resolveOld = resolve; });
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/resume') return response(null);
    if (url === '/api/templates') return response([]);
    if (url === '/api/applications') return response({ companies });
    if (url === '/api/interviews?job_id=fictional-job-a') return response([{ id: 'fictional-session-a' }]);
    if (url === '/api/interviews/fictional-session-a') return oldDetail;
    if (url === '/api/interviews?job_id=fictional-job-b') return response([{ id: 'fictional-session-b' }]);
    if (url === '/api/interviews/fictional-session-b') return response({ id: 'fictional-session-b', job_title: '虚构岗位 b', status: 'ended', turns: [] });
    throw Error(`Unexpected test URL: ${url}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  const wrapper = mount(App);
  await flushPromises();
  await wrapper.findAll('.company-select')[0].trigger('click');
  await wrapper.find('.job-select').trigger('click');
  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/interviews/fictional-session-a', expect.anything()));
  await wrapper.findAll('.company-select')[1].trigger('click');
  await wrapper.findAll('.company-branch')[1].find('.job-select').trigger('click');
  await flushPromises();
  resolveOld(response({ id: 'fictional-session-a', job_title: '虚构岗位 a', status: 'ended', turns: [] }));
  await flushPromises();
  expect(sessionStorage.getItem('cv-selected-interview')).toBe('fictional-session-b');
  await wrapper.find('#tab-interview').trigger('click');
  expect(wrapper.find('#interview-session').text()).toContain('虚构岗位 b');
  expect(wrapper.find('#interview-session').text()).not.toContain('虚构岗位 a');
  wrapper.unmount();
});

test('clears a selected fictional version removed by a refreshed application list', async () => {
  window.cvAuth = { enabled: false, ready: Promise.resolve(), url: path => path, headers: () => ({}) };
  const company = { id: 'fictional-company', name: '虚构公司', jobs: [{
    id: 'fictional-job', title: '虚构岗位', job_text: '虚构招聘要求', versions: [fictionalVersion('fictional-version')],
  }] };
  let applicationReads = 0;
  vi.stubGlobal('prompt', vi.fn(() => '虚构公司新名称'));
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url === '/api/resume') return response(null);
    if (url === '/api/templates') return response([]);
    if (url === '/api/applications') {
      applicationReads += 1;
      return response({ companies: applicationReads === 1 ? [company] : [{ ...company, jobs: [{ ...company.jobs[0], versions: [] }] }] });
    }
    if (url === '/api/interviews?job_id=fictional-job') return response([]);
    if (url === '/api/companies/fictional-company') return response({ ...company, name: '虚构公司新名称' });
    throw Error(`Unexpected test URL: ${url}`);
  }));
  const wrapper = mount(App);
  await flushPromises();
  await wrapper.find('.company-select').trigger('click');
  await wrapper.find('.job-select').trigger('click');
  await wrapper.find('.version-bubble').trigger('click');
  expect(wrapper.find('#resume-document').text()).toContain('虚构经历');
  await wrapper.find('[aria-label="修改虚构公司名称"]').trigger('click');
  await flushPromises();
  expect(wrapper.find('#resume-document').text()).not.toContain('虚构经历');
  expect(wrapper.find('#export').attributes('disabled')).toBeDefined();
  wrapper.unmount();
});

test('does not select a generated fictional version after switching jobs', async () => {
  window.cvAuth = { enabled: false, ready: Promise.resolve(), url: path => path, headers: () => ({}) };
  const jobs = ['a', 'b'].map(id => ({
    id: `fictional-job-${id}`, title: `虚构岗位 ${id}`, job_text: '虚构招聘要求', versions: [] as ReturnType<typeof fictionalVersion>[],
  }));
  const company = { id: 'fictional-company', name: '虚构公司', jobs };
  let resolveGeneration!: (value: ReturnType<typeof response>) => void;
  const generation = new Promise<ReturnType<typeof response>>(resolve => { resolveGeneration = resolve; });
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/resume') return response(null);
    if (url === '/api/templates') return response([]);
    if (url === '/api/applications') return response({ companies: [company] });
    if (url === '/api/interviews?job_id=fictional-job-a' || url === '/api/interviews?job_id=fictional-job-b') return response([]);
    if (url === '/api/jobs/fictional-job-a/versions') return generation;
    throw Error(`Unexpected test URL: ${url}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  const wrapper = mount(App);
  await flushPromises();
  await wrapper.find('.company-select').trigger('click');
  await wrapper.findAll('.job-select')[0].trigger('click');
  await wrapper.find('#create-version').trigger('click');
  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/jobs/fictional-job-a/versions', expect.anything()));
  await wrapper.findAll('.job-select')[1].trigger('click');
  resolveGeneration(response(fictionalVersion('fictional-version-a')));
  await flushPromises();
  expect(wrapper.find('#current-title').text()).toContain('虚构岗位 b');
  expect(wrapper.find('#resume-document').text()).not.toContain('虚构经历');
  expect(wrapper.find('#export').attributes('disabled')).toBeDefined();
  wrapper.unmount();
});

test('shows the saved resume even when the application list cannot be reloaded', async () => {
  const original = fictionalVersion('fictional-v1');
  const savedText = '修改后的虚构经历';
  const saved = {
    ...original, id: 'fictional-v2', number: 2, source_version_id: original.id, source_version_number: 1,
    resume_content: { ...original.resume_content, summary: [{ text: savedText }] },
  };
  const company = { id: 'fictional-company', name: '虚构公司', jobs: [{
    id: 'fictional-job', title: '虚构岗位', job_text: '虚构招聘要求', versions: [original],
  }] };
  let savedToServer = false;
  const editsUrl = '/api/jobs/fictional-job/versions/fictional-v1/edits';
  const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/resume') return response(null);
    if (url === '/api/templates' || url === '/api/interviews?job_id=fictional-job') return response([]);
    if (url === '/api/applications' && !savedToServer) return response({ companies: [company] });
    if (url === editsUrl) {
      expect(JSON.parse(String(options?.body)).resume_content).toEqual(saved.resume_content);
      savedToServer = true;
      return response(saved);
    }
    throw Error('测试中的列表请求失败');
  });
  vi.stubGlobal('fetch', fetchMock);
  const wrapper = mount(App);
  try {
    await flushPromises();
    await wrapper.get('.company-select').trigger('click');
    await wrapper.get('.job-version-bubble').trigger('click');
    expect(wrapper.get('#current-title').text()).toContain('虚构岗位');
    await wrapper.get('#edit-version').trigger('click');
    await wrapper.get('#resume-document textarea').setValue(savedText);
    await wrapper.get('#save-version').trigger('click');
    await flushPromises();
    expect(wrapper.get('#status').text()).toContain('已另存为 V2');
    expect(wrapper.get('#resume-document').text()).toContain(savedText);
    expect(wrapper.findAll('.version-bubble').map(bubble => bubble.text())).toEqual(['V1', 'V2']);
    await wrapper.findAll('.version-bubble')[0].trigger('click');
    expect(wrapper.get('#resume-document').text()).toContain('虚构经历');
    expect(wrapper.get('#resume-document').text()).not.toContain(savedText);
  } finally {
    wrapper.unmount();
  }
});

test('overwrites the version from an existing draft, then saves another edit as a new version', async () => {
  const original = fictionalVersion('fictional-v1');
  const draftText = '虚构测试草稿';
  const legacyDraft = {
    ...original,
    resume_content: { ...original.resume_content, summary: [{ text: draftText }] },
    visual_settings: { ...original.visual_settings, template_id: 'template-06' },
  };
  const job = {
    id: 'fictional-job', title: '虚构岗位', job_text: '虚构招聘要求',
    versions: [original], resume_draft: legacyDraft,
  };
  const company = { id: 'fictional-company', name: '虚构公司', jobs: [job] };
  const versionUrl = '/api/jobs/fictional-job/versions/fictional-v1';
  let failFirstSave = true;
  const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/applications') return response({ companies: JSON.parse(JSON.stringify([company])) });
    if (url === '/api/resume') return response(null);
    if (url === '/api/templates' || url === '/api/interviews?job_id=fictional-job') return response([]);
    if (url === versionUrl) {
      expect(options?.method).toBe('PUT');
      if (failFirstSave) {
        failFirstSave = false;
        throw Error('虚构测试：保存暂时不可用');
      }
      job.versions[0] = { ...original, ...JSON.parse(String(options?.body)) };
      return response(job.versions[0]);
    }
    if (url === `${versionUrl}/edits`) {
      expect(options?.method).toBe('POST');
      const version = {
        ...original, ...JSON.parse(String(options?.body)), id: 'fictional-v2', number: 2,
        source_version_id: original.id, source_version_number: 1,
      };
      job.versions.push(version);
      return response(version);
    }
    throw Error(`Unexpected test URL: ${url}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  let wrapper = mount(App);
  try {
    await flushPromises();
    await wrapper.get('.company-select').trigger('click');
    await wrapper.get('.job-version-bubble').trigger('click');
    await wrapper.get('#open-resume-draft').trigger('click');
    expect(wrapper.get('#resume-document').classes()).toContain('template-template-06');
    expect(wrapper.get('#resume-document').attributes('inert')).toBeUndefined();
    expect(wrapper.get<HTMLTextAreaElement>('#resume-document textarea').element.value).toBe(draftText);
    const updatedText = '虚构测试覆盖 V1';
    await wrapper.get('#resume-document textarea').setValue(updatedText);
    await wrapper.get('#save-current-version').trigger('click');
    await flushPromises();
    expect(wrapper.get('#resume-save-status').text()).toContain('保存失败');
    expect(wrapper.get<HTMLTextAreaElement>('#resume-document textarea').element.value).toBe(updatedText);
    await wrapper.get('#save-current-version').trigger('click');
    await flushPromises();
    expect(wrapper.get('#resume-save-status').text()).toContain('V1 已保存');
    expect(wrapper.findAll('.version-bubble')).toHaveLength(1);
    expect(fetchMock).not.toHaveBeenCalledWith(`${versionUrl}/edits`, expect.anything());

    wrapper.unmount();
    wrapper = mount(App);
    await flushPromises();
    await wrapper.get('.job-version-bubble').trigger('click');
    expect(wrapper.get('#resume-document').text()).toContain(updatedText);
    await wrapper.get('#edit-version').trigger('click');
    await wrapper.get('#resume-document textarea').setValue('虚构测试另存 V2');
    await wrapper.get('#save-version').trigger('click');
    await flushPromises();
    expect(wrapper.get('#resume-save-status').text()).toContain('已另存为 V2');
    expect(wrapper.findAll('.version-bubble')).toHaveLength(2);
    await wrapper.findAll('.version-bubble')[0].trigger('click');
    expect(wrapper.get('#resume-document').text()).toContain(updatedText);
  } finally {
    wrapper.unmount();
  }
});
