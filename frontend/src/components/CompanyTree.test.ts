import { mount } from '@vue/test-utils';
import { expect, test } from 'vitest';
import CompanyTree from './CompanyTree.vue';
import { versionDate, type Company } from '../types';

const companies: Company[] = [
  { id: 'fictional-company-a', name: '虚构星河科技', jobs: [
    { id: 'fictional-job-a', title: '虚构产品经理', job_text: '虚构要求', versions: [
      { id: 'fictional-v1', number: 1, resume_content: {}, module_order: [], visual_settings: {} },
      { id: 'fictional-v2', number: 2, saved_at: '2025-01-20T08:00:00Z', source_version_id: 'fictional-v1', source_version_number: 1, resume_content: {}, module_order: [], visual_settings: {} },
    ] },
  ] },
  { id: 'fictional-company-b', name: '虚构远山咨询', jobs: [] },
];

test('company contains its jobs and compact version switches in the sidebar', async () => {
  const wrapper = mount(CompanyTree, { props: {
    companies, selectedCompanyId: 'fictional-company-a', selectedJobId: 'fictional-job-a', selectedVersionId: 'fictional-v1',
  } });
  const branches = wrapper.findAll('.company-branch');
  expect(branches).toHaveLength(2);
  expect(branches[0].find('.company-select').text()).toContain('虚构星河科技');
  expect(branches[0].find('.job-select').text()).toBe('虚构产品经理');
  expect(branches[0].findAll('.job-version-bubble')).toHaveLength(2);
  expect(branches[0].find('.job-version-bubble.selected').text()).toBe('V1');
  const job = companies[0].jobs[0];
  expect(branches[0].text()).toContain(versionDate(job.versions[1]));
  await branches[0].get('[aria-label="删除 V2"]').trigger('click');
  expect(wrapper.emitted('delete-version')).toEqual([[job, job.versions[1]]]);
  expect(wrapper.emitted('select-job')).toBeUndefined();
  expect(branches[1].find('.company-children').attributes('hidden')).toBeDefined();
});
