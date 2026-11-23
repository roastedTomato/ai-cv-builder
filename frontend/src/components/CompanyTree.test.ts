import { mount } from '@vue/test-utils';
import { expect, test } from 'vitest';
import CompanyTree from './CompanyTree.vue';
import type { Company } from '../types';

const companies: Company[] = [
  { id: 'fictional-company-a', name: '虚构星河科技', jobs: [
    { id: 'fictional-job-a', title: '虚构产品经理', job_text: '虚构要求', versions: [
      { id: 'fictional-v1', number: 1, resume_content: {}, module_order: [], visual_settings: {} },
      { id: 'fictional-v2', number: 2, source_version_id: 'fictional-v1', source_version_number: 1, resume_content: {}, module_order: [], visual_settings: {} },
    ] },
  ] },
  { id: 'fictional-company-b', name: '虚构远山咨询', jobs: [] },
];

test('company contains its jobs without version controls in the sidebar', async () => {
  const wrapper = mount(CompanyTree, { props: {
    companies, selectedCompanyId: 'fictional-company-a', selectedJobId: 'fictional-job-a',
  } });
  const branches = wrapper.findAll('.company-branch');
  expect(branches).toHaveLength(2);
  expect(branches[0].find('.company-select').text()).toContain('虚构星河科技');
  expect(branches[0].find('.job-select').text()).toBe('虚构产品经理');
  expect(branches[0].findAll('.version-chip')).toHaveLength(0);
  expect(branches[1].find('.company-children').attributes('hidden')).toBeDefined();
});
