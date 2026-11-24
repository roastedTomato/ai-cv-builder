<script setup lang="ts">
import { computed, ref, shallowRef, watch } from 'vue';
import { objectResult, queuedTask, stringField } from '../api';
import type { ApiClient } from '../api';
import type { BaseItem, BaseResumeData } from '../types';

type Section = 'education' | 'work' | 'projects' | 'skills' | 'certificates';
type Props = {
  base: BaseResumeData | null;
  onBaseChange: (base: BaseResumeData | null) => void;
  api: ApiClient;
  cloud: boolean;
  waitTask: <T>(id: string, parseResult: (value: unknown) => T) => Promise<T>;
  notify: (message: string, error?: boolean) => void;
  beforeReset: () => Promise<boolean>;
  onReset: () => Promise<void>;
};
const props = defineProps<Props>();

const sections: [Section, string][] = [
  ['education', '教育经历'], ['work', '工作经历'], ['projects', '项目经历'],
  ['skills', '技能'], ['certificates', '证书'],
];
const required: Record<Section, string[]> = {
  education: ['school', 'degree', 'major'], work: ['employer', 'title'],
  projects: ['name', 'role'], skills: ['name'], certificates: ['name'],
};
const fields: Record<Section, [string, string][]> = {
  education: [['school', '学校'], ['degree', '学历'], ['major', '专业'], ['start_date', '开始时间'], ['end_date', '结束时间'], ['details', '补充信息']],
  work: [['employer', '公司'], ['title', '职位'], ['start_date', '开始时间'], ['end_date', '结束时间'], ['description', '工作描述'], ['achievements', '成果']],
  projects: [['name', '项目名称'], ['role', '角色'], ['start_date', '开始时间'], ['end_date', '结束时间'], ['description', '项目描述'], ['achievements', '成果'], ['technologies', '技术']],
  skills: [['name', '技能'], ['level', '熟练度']],
  certificates: [['name', '证书'], ['issuer', '颁发机构'], ['date', '日期']],
};
const arrayFields = new Set(['education.details', 'work.achievements', 'projects.achievements', 'projects.technologies']);
const personalFields: [string, string][] = [['name', '姓名'], ['email', '邮箱'], ['phone', '电话'], ['location', '所在地']];

function blankResume(): BaseResumeData {
  return {
    resume_id: crypto.randomUUID(), version: '1',
    personal: { name: '', email: '', phone: '', location: '' },
    education: [], work: [], projects: [], skills: [], certificates: [],
  };
}

// Resume data is JSON from the API; JSON cloning also accepts Vue reactive proxies.
function cloneResume(value: BaseResumeData): BaseResumeData {
  return JSON.parse(JSON.stringify(value)) as BaseResumeData;
}

function importResult(value: unknown): { draft_id: string; draft: BaseResumeData } {
  const result = objectResult(value);
  const draft = objectResult(result.draft);
  return { draft_id: stringField(result, 'draft_id'), draft: draft as unknown as BaseResumeData };
}

function hasRequiredFields(resume: BaseResumeData | null): boolean {
  return !!resume
    && ['name', 'email', 'phone'].every(key => String(resume.personal?.[key] || '').trim())
    && sections.every(([section]) => (resume[section] || []).every(item => required[section].every(key => String(item[key] || '').trim())));
}

function newItem(section: Section): BaseItem {
  const item: BaseItem = { id: crypto.randomUUID() };
  fields[section].forEach(([key]) => { item[key] = arrayFields.has(`${section}.${key}`) ? [] : ''; });
  return item;
}

const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});
const errorMessage = (error: unknown): string => error instanceof Error ? error.message : '操作失败，请重试。';

// Only confirmed saves are sent to the parent; an imported draft stays local.
const form = shallowRef<BaseResumeData>(props.base ? cloneResume(props.base) : blankResume());
const editing = ref(!hasRequiredFields(props.base));
const draftId = ref<string | null>(null);
const uploading = ref(false);
const status = computed(() => draftId.value ? '导入草稿待确认，尚未保存。'
  : props.base ? editing.value ? '正在编辑基础简历。' : '基础简历已保存。'
    : '尚未保存基础简历。');

watch(() => props.base, value => {
  if (draftId.value) return;
  form.value = value ? cloneResume(value) : blankResume();
  editing.value = !hasRequiredFields(value);
});

function inputValue(event: Event): string {
  return (event.target as HTMLInputElement | HTMLTextAreaElement).value;
}

function updatePersonal(key: string, event: Event) {
  form.value = { ...form.value, personal: { ...form.value.personal, [key]: inputValue(event) } };
}

function updateItem(section: Section, index: number, key: string, event: Event) {
  const raw = inputValue(event);
  form.value = {
    ...form.value,
    [section]: form.value[section].map((item, itemIndex) => itemIndex === index
      ? { ...item, [key]: raw }
      : item),
  };
}

function itemValue(item: BaseItem, section: Section, key: string): string {
  const value = item[key];
  return arrayFields.has(`${section}.${key}`) && Array.isArray(value) ? value.join('\n') : String(value || '');
}

function addItem(section: Section) {
  form.value = { ...form.value, [section]: [...form.value[section], newItem(section)] };
}

function removeItem(section: Section, index: number) {
  form.value = { ...form.value, [section]: form.value[section].filter((_, itemIndex) => itemIndex !== index) };
}

function payload(): BaseResumeData {
  const resume = cloneResume(form.value);
  delete resume.fixture_note;
  resume.personal = { name: '', email: '', phone: '', location: '', ...resume.personal };
  for (const [section] of sections) {
    resume[section] = resume[section].map(item => {
      const converted = { ...item };
      for (const [key] of fields[section]) {
        if (arrayFields.has(`${section}.${key}`) && typeof converted[key] === 'string') {
          converted[key] = converted[key].split('\n').map(part => part.trim()).filter(Boolean);
        }
      }
      return converted;
    });
  }
  return resume;
}

async function save() {
  if (draftId.value) return;
  const resume = payload();
  if (!hasRequiredFields(resume)) { props.notify('请填写基础简历中的必填字段。', true); return; }
  try {
    const saved = await props.api<BaseResumeData>('/api/resume', json('PUT', resume));
    form.value = cloneResume(saved);
    editing.value = false;
    props.onBaseChange(saved);
    props.notify('基础简历已保存。');
  } catch (error) { props.notify(errorMessage(error), true); }
}

async function upload(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  uploading.value = true;
  try {
    let result: { draft_id: string; draft: BaseResumeData };
    if (props.cloud) {
      const target = await props.api<{ url: string; key: string }>('/api/uploads', json('POST', { filename: file.name, size: file.size }));
      const put = await fetch(target.url, { method: 'PUT', body: file });
      if (!put.ok) throw new Error('文件上传失败，请重试。');
      const queued = queuedTask(await props.api('/api/import', json('POST', { key: target.key })));
      result = await props.waitTask(queued.task_id, importResult);
    } else {
      const data = new FormData();
      data.append('file', file);
      result = importResult(await props.api('/api/import', { method: 'POST', body: data }));
    }
    draftId.value = result.draft_id;
    form.value = cloneResume(result.draft);
    editing.value = true;
  } catch (error) { props.notify(errorMessage(error), true); }
  finally { uploading.value = false; input.value = ''; }
}

async function confirmImport() {
  if (!draftId.value) return;
  try {
    const saved = await props.api<BaseResumeData>(`/api/import/${encodeURIComponent(draftId.value)}/confirm`, json('POST', payload()));
    draftId.value = null;
    form.value = cloneResume(saved);
    editing.value = false;
    props.onBaseChange(saved);
    props.notify('导入草稿已确认并保存。');
  } catch (error) { props.notify(errorMessage(error), true); }
}

async function deleteBase() {
  if (!confirm('确定删除基础简历？')) return;
  try {
    await props.api('/api/resume', { method: 'DELETE' });
    draftId.value = null;
    form.value = blankResume();
    editing.value = true;
    props.onBaseChange(null);
    props.notify('基础简历已删除。');
  } catch (error) { props.notify(errorMessage(error), true); }
}

async function reset() {
  if (!confirm('确定重置全部应用数据？') || !await props.beforeReset()) return;
  try {
    await props.api('/api/reset', { method: 'POST' });
    draftId.value = null;
    form.value = blankResume();
    editing.value = true;
    props.onBaseChange(null);
    await props.onReset();
    props.notify('数据已重置。');
  } catch (error) { props.notify(errorMessage(error), true); }
}
</script>

<template>
  <section class="card base-card">
    <div class="row">
      <label v-if="editing" id="upload-control" class="button secondary">导入 DOCX / DOC / PDF
        <input id="upload" type="file" accept=".docx,.doc,.pdf" :disabled="uploading" @change="upload">
      </label>
      <button v-if="editing && !draftId" id="save" type="button" @click="save">保存基础简历</button>
      <button v-if="!editing && base" id="edit" type="button" @click="editing = true">编辑基础简历</button>
      <button id="delete" class="danger" type="button" @click="deleteBase">删除基础简历</button>
      <button id="reset" class="danger secondary" type="button" @click="reset">重置全部数据</button>
      <span id="resume-state" class="hint" role="status">{{ status }}</span>
    </div>
    <div v-if="uploading" id="base-progress" class="progress-status" role="status" aria-live="polite">
      <span>正在上传文件、提取文字并整理基础简历…</span><progress aria-label="正在解析基础简历" />
    </div>
    <form id="resume-form" @submit.prevent="draftId ? confirmImport() : save()">
      <label v-for="[key, label] in personalFields" :key="key">{{ label }}{{ ['name', 'email', 'phone'].includes(key) ? ' *' : '' }}
        <input :data-key="key" :required="['name', 'email', 'phone'].includes(key)" :readonly="!editing"
          :value="form.personal?.[key] || ''" @input="updatePersonal(key, $event)">
      </label>
      <div v-for="[section, title] in sections" :key="section" class="full">
        <h3>{{ title }}</h3>
        <div v-for="(item, index) in form[section]" :key="item.id || index" class="item-editor">
          <div class="item-fields">
            <label v-for="[key, label] in fields[section]" :key="key"
              :class="{ 'resume-long-field': key === 'achievements' || key === 'details' || (key === 'description') }">
              {{ label }}{{ required[section].includes(key) ? ' *' : '' }}
              <textarea v-if="arrayFields.has(`${section}.${key}`) || (key === 'description')" rows="2" :data-item="section"
                :data-index="index" :data-field="key" :required="required[section].includes(key)" :readonly="!editing"
                :value="itemValue(item, section, key)" @input="updateItem(section, index, key, $event)" />
              <input v-else :data-item="section" :data-index="index" :data-field="key" :required="required[section].includes(key)" :readonly="!editing"
                :value="itemValue(item, section, key)" @input="updateItem(section, index, key, $event)">
            </label>
          </div>
          <button v-if="editing" type="button" class="remove" :data-section="section" :data-index="index"
            @click="removeItem(section, index)">移除</button>
        </div>
        <button v-if="editing" type="button" class="add" :data-section="section" @click="addItem(section)">添加{{ title }}</button>
      </div>
    </form>
    <div v-if="draftId" id="draft-area">
      <div class="notice">已提取为待确认草稿，请逐项检查。</div>
      <button id="confirm" type="button" @click="confirmImport">检查后确认保存</button>
    </div>
  </section>
</template>
