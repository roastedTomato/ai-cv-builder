<script setup lang="ts">
import { computed } from 'vue';
import type { Job, ResumeContent, Version, VisualSettings } from '../types';
import { sections, sidebarModules } from '../types';

const props = defineProps<{
  job: Job | null;
  version: Version | null;
  editing: boolean;
  visual: VisualSettings;
  onField: (path: string, value: string) => void;
  onMove: (key: string, direction: -1 | 1) => void;
}>();

const emptyMessage = computed(() => {
  const job = props.job;
  if (!job) return '选择岗位版本后，这里会显示简历。';
  if (job.has_generated && !job.versions?.length) return '该岗位的版本已全部删除。请新建岗位以重新生成 V1。';
  if (job.versions?.length) return '从上方版本图选择一个简历版本。';
  return '该岗位还没有简历版本。点击“确认生成简历”创建 V1。';
});
interface ResumeEntry {
  id?: string;
  text?: string;
  title?: string;
  organization?: string;
  start_date?: string;
  end_date?: string;
  bullets?: { text?: string }[];
}
const content = computed<ResumeContent>(() => props.version?.resume_content || {});
const personal = computed(() => content.value.personal || {});
const order = computed(() => props.version?.module_order || []);
const moduleColumns = computed(() => {
  if (props.visual.template_id !== 'template-06') return [{ name: 'full', keys: order.value }];
  const sidebar = order.value.filter(key => sidebarModules.includes(key));
  return [
    { name: 'sidebar', keys: sidebar },
    { name: 'main', keys: order.value.filter(key => !sidebar.includes(key)) },
  ];
});
const heading = (key: string) => sections.find(([id]) => id === key)?.[1] || key;
const isClaim = (key: string) => ['summary', 'gaps', 'suggestions'].includes(key);
const entries = (key: string): ResumeEntry[] => {
  const value = content.value[key];
  return Array.isArray(value) ? value.flatMap((item: unknown) => typeof item === 'string'
    ? [{ text: item }] : item !== null && typeof item === 'object' && !Array.isArray(item)
      ? [item as ResumeEntry] : []) : [];
};
const claimText = (item: ResumeEntry) => item.text || '';
const change = (path: string, event: Event) => props.onField(path, (event.target as HTMLInputElement | HTMLTextAreaElement).value);
</script>

<template>
  <div v-if="!version" id="resume-document" class="resume-document empty">{{ emptyMessage }}</div>
  <div v-else id="resume-document" class="resume-document"
    :class="[`template-${visual.template_id || 'template-01'}`, `width-${visual.page_width || 'standard'}`, { editing }]"
    :style="{ '--accent': visual.color || '#2C5263' }">
    <div class="doc-personal"><h1>{{ personal.name || '' }}</h1><p v-if="visual.template_id !== 'template-06'">{{ [personal.email, personal.phone, personal.location].filter(Boolean).join(' · ') }}</p></div>
    <div class="doc-columns">
    <div v-for="column in moduleColumns" :key="column.name" :class="`doc-column-${column.name}`">
    <p v-if="column.name === 'sidebar'" class="doc-sidebar-contact">{{ [personal.email, personal.phone, personal.location].filter(Boolean).join('\n') }}</p>
    <section v-for="key in column.keys" :key="key" class="doc-module" :data-module="key">
      <h2>{{ heading(key) }}<span v-if="editing" class="module-actions">
        <button type="button" title="上移" @click="onMove(key, -1)">↑</button>
        <button type="button" title="下移" @click="onMove(key, 1)">↓</button>
      </span></h2>
      <template v-if="isClaim(key)">
        <div v-for="(item, index) in entries(key)" :key="index" class="claim-row">
          <textarea v-if="editing" rows="2" :value="claimText(item)" @input="change(`${key}.${index}.text`, $event)" />
          <p v-else>{{ claimText(item) }}</p>
        </div>
      </template>
      <template v-else>
        <article v-for="(item, index) in entries(key)" :key="item.id || index" class="resume-item">
          <div class="item-title">
            <template v-if="editing">
              <input :value="item.title || ''" @input="change(`${key}.${index}.title`, $event)" />
              <input :value="item.organization || ''" @input="change(`${key}.${index}.organization`, $event)" />
            </template>
            <template v-else><strong>{{ item.title || '' }}</strong><span>{{ item.organization || '' }}</span></template>
          </div>
          <div class="item-dates">
            <template v-if="editing">
              <input :value="item.start_date || ''" placeholder="开始时间" @input="change(`${key}.${index}.start_date`, $event)" />
              <input :value="item.end_date || ''" placeholder="结束时间" @input="change(`${key}.${index}.end_date`, $event)" />
            </template>
            <template v-else>{{ [item.start_date, item.end_date].filter(Boolean).join(' — ') }}</template>
          </div>
          <ul><li v-for="(bullet, bulletIndex) in item.bullets || []" :key="bulletIndex">
            <textarea v-if="editing" rows="2" :value="bullet.text || ''" @input="change(`${key}.${index}.bullets.${bulletIndex}.text`, $event)" />
            <template v-else>{{ bullet.text || '' }}</template>
          </li></ul>
        </article>
      </template>
    </section>
    </div>
    </div>
  </div>
</template>
