import { mount } from '@vue/test-utils';
import { expect, test } from 'vitest';
import VersionGraph from './VersionGraph.vue';
import type { Job, Version } from '../types';

const version = (id: string, number: number, sourceVersionId?: string, sourceVersionNumber?: number): Version => ({
  id, number, source_version_id: sourceVersionId, source_version_number: sourceVersionNumber,
  resume_content: {}, module_order: [], visual_settings: {},
});

test('shows real branches and keeps a child visible after its source is deleted', async () => {
  const job: Job = { id: 'fictional-job', title: '虚构测试岗位', job_text: '虚构测试要求', versions: [
    version('fictional-v1', 1), version('fictional-v2', 2, 'fictional-v1', 1),
    version('fictional-v3', 3, 'fictional-v1', 1), version('fictional-v4', 4, 'deleted-v2', 2),
  ] };
  const wrapper = mount(VersionGraph, { props: { job, selectedId: 'fictional-v3' } });
  const root = wrapper.find('.version-branches');
  expect(root.element.children).toHaveLength(2);
  expect(root.element.children[0].querySelectorAll('.version-graph.nested > .version-branches > li')).toHaveLength(2);
  expect(wrapper.text()).toContain('来源 V2 已删除');
  expect(wrapper.find('[aria-label="选择 V3"]').attributes('aria-pressed')).toBe('true');
  await wrapper.find('[aria-label="选择 V2"]').trigger('click');
  expect(wrapper.emitted('select')?.[0]?.[0]).toEqual(job.versions[1]);
  await wrapper.find('[aria-label="删除 V4"]').trigger('click');
  expect(wrapper.emitted('delete')?.[0]).toEqual([job, job.versions[3]]);
});
