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
  await wrapper.find('.version-chip').trigger('click');
  expect(wrapper.find('#resume-document').text()).toContain('虚构经历');
  expect(wrapper.find('#export').attributes('disabled')).toBeUndefined();
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
  await wrapper.find('.version-chip').trigger('click');
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
