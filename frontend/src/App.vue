<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { api, errorMessage, isCloud, json, objectResult, queuedTask, stringField, waitTask } from './api';
import type { BaseResumeData, Company, InterviewSession, Job, Template, Version, VisualSettings } from './types';
import { normalizeVersion, sidebarModules, versionSummary } from './types';
import BaseResume from './components/BaseResume.vue';
import CompanyTree from './components/CompanyTree.vue';
import InterviewPanel from './components/InterviewPanel.vue';
import ResumePreview from './components/ResumePreview.vue';
import VersionGraph from './components/VersionGraph.vue';

const colors = [
  { value: '#2C5263', name: '墨染蓝' }, { value: '#16766d', name: '青碧' },
  { value: '#647a9e', name: '雾蓝' }, { value: '#707956', name: '苔绿' },
  { value: '#7a4b78', name: '暮紫' }, { value: '#927559', name: '素纸' },
];
const widths = [
  { value: 'narrow', name: '古典雅宋', width: '窄幅' },
  { value: 'standard', name: '简约内敛', width: '标准' },
  { value: 'wide', name: '现代留白', width: '宽幅' },
];
const visualDefaults: VisualSettings = { template_id: 'template-04', color: '#2C5263', page_width: 'standard' };
const snapshot = (version: Version | null) => version ? JSON.stringify({
  resume_content: version.resume_content, module_order: version.module_order,
  visual_settings: version.visual_settings,
}) : '';

const base = ref<BaseResumeData | null>(null);
const companies = ref<Company[]>([]);
const templates = ref<Template[]>([]);
const selectedCompanyId = ref<string | null>(null);
const selectedJobId = ref<string | null>(null);
const selectedVersionId = ref<string | null>(null);
const draft = ref<Version | null>(null);
const savedSnapshot = ref('');
const editing = ref(false);
const savingVersion = ref(false);
const saveFeedback = ref<{ text: string; error: boolean } | null>(null);
const visual = ref<VisualSettings>({ ...visualDefaults });
const activeTab = ref<'versions' | 'interview'>('versions');
const currentInterview = ref<InterviewSession | null>(null);
const companyFormOpen = ref(false);
const companyName = ref('');
const jobFormOpen = ref(false);
const jobTitle = ref('');
const jobText = ref('');
const jobEditing = ref(false);
const editJobTitle = ref('');
const editJobText = ref('');
const uploadState = ref('');
const editUploadState = ref('');
const busy = ref<'generate' | 'export' | 'interview' | null>(null);
const notice = ref<{ text: string; error: boolean } | null>(null);
const cloud = isCloud();

const selectedCompany = computed(() => companies.value.find(item => item.id === selectedCompanyId.value) || null);
const selectedJob = computed(() => selectedCompany.value?.jobs?.find(item => item.id === selectedJobId.value) || null);
const storedVersion = computed(() => selectedJob.value?.versions?.find(item => item.id === selectedVersionId.value) || null);
const currentVersion = computed(() => draft.value || storedVersion.value);
const dirty = computed(() => editing.value && draft.value !== null && snapshot(draft.value) !== savedSnapshot.value);
const hasGenerated = computed(() => Boolean(selectedJob.value?.has_generated || selectedJob.value?.versions?.length));
const currentMeta = computed(() => selectedJob.value
  ? currentVersion.value ? editing.value ? `正在编辑 · 来源 V${currentVersion.value.number}` : versionSummary(selectedJob.value, currentVersion.value)
    : `招聘要求已保存 · ${selectedJob.value.versions?.length || 0} 个简历版本`
  : '从左侧选择公司和职位，查看简历版本。');
let selectionEpoch = 0;
let interviewRequest = 0;
let applicationsRequest = 0;

function invalidateInterview() {
  interviewRequest += 1;
  currentInterview.value = null;
  sessionStorage.removeItem('cv-selected-interview');
}

function selectionMatches(epoch: number, jobId: string, versionId?: string): boolean {
  return selectionEpoch === epoch && selectedJobId.value === jobId &&
    (versionId === undefined || selectedVersionId.value === versionId);
}

function notify(text: string, error = false) {
  notice.value = { text, error };
  window.setTimeout(() => { if (notice.value?.text === text) notice.value = null; }, 5000);
}

async function run(action: () => Promise<void>) {
  try { await action(); } catch (error) { notify(errorMessage(error), true); }
}

async function refreshApplications(): Promise<Company[]> {
  const request = ++applicationsRequest;
  const data = await api<{ companies: Company[] }>('/api/applications');
  if (request !== applicationsRequest) return companies.value;
  companies.value = data.companies || [];
  if (selectedCompanyId.value && !selectedCompany.value) {
    selectedCompanyId.value = null;
    clearSelection();
  } else if (selectedJobId.value && !selectedJob.value) {
    clearSelection();
  } else if (selectedVersionId.value && !storedVersion.value &&
    !(editing.value && selectedJob.value?.resume_draft?.id === selectedVersionId.value)) {
    selectionEpoch += 1;
    selectedVersionId.value = null;
    draft.value = null;
    savedSnapshot.value = '';
    visual.value = { ...visualDefaults };
    editing.value = false;
  }
  return companies.value;
}

async function refreshInterviews(jobId = selectedJobId.value): Promise<InterviewSession | null> {
  const request = ++interviewRequest;
  if (!jobId) { invalidateInterview(); return null; }
  const list = await api<InterviewSession[]>(`/api/interviews?job_id=${encodeURIComponent(jobId)}`);
  if (request !== interviewRequest || selectedJobId.value !== jobId || !selectedJob.value) return null;
  let latest = list[0] ? await api<InterviewSession>(`/api/interviews/${encodeURIComponent(list[0].id)}`) : null;
  if (request !== interviewRequest || selectedJobId.value !== jobId || !selectedJob.value) return null;
  if (request !== interviewRequest || selectedJobId.value !== jobId || !selectedJob.value) return null;
  currentInterview.value = latest;
  if (latest) sessionStorage.setItem('cv-selected-interview', latest.id);
  else sessionStorage.removeItem('cv-selected-interview');
  return latest;
}

let interviewTimer: number | undefined;
onMounted(async () => {
  await window.cvAuth?.ready;
  if (cloud && document.querySelector('main')?.hidden) return;
  try {
    const [loadedBase, loadedTemplates, applications] = await Promise.all([
      api<BaseResumeData | null>('/api/resume'), api<Template[]>('/api/templates'),
      api<{ companies: Company[] }>('/api/applications'),
    ]);
    base.value = loadedBase;
    templates.value = loadedTemplates;
    companies.value = applications.companies || [];
    const jobId = sessionStorage.getItem('cv-selected-job');
    const parent = companies.value.find(company => company.jobs?.some(job => job.id === jobId));
    if (parent && jobId) {
      selectedCompanyId.value = parent.id;
      selectedJobId.value = jobId;
      const epoch = ++selectionEpoch;
      const request = ++interviewRequest;
      const list = await api<InterviewSession[]>(`/api/interviews?job_id=${encodeURIComponent(jobId)}`);
      if (selectionMatches(epoch, jobId) && request === interviewRequest) {
        const saved = sessionStorage.getItem('cv-selected-interview');
        const interview = list.find(item => item.id === saved) || list[0];
        if (interview) {
          const loaded = await api<InterviewSession>(`/api/interviews/${encodeURIComponent(interview.id)}`);
          if (selectionMatches(epoch, jobId) && request === interviewRequest) {
            currentInterview.value = loaded;
            if (saved) activeTab.value = 'interview';
          }
        }
      }
    }
  } catch (error) { notify(errorMessage(error), true); }
  interviewTimer = window.setInterval(() => {
    if (activeTab.value !== 'interview' || !currentInterview.value ||
      !['new', 'question_pending', 'reference_pending', 'feedback_pending'].includes(currentInterview.value.status)) return;
    void run(async () => { await refreshInterviews(); });
  }, 5000);
});
onUnmounted(() => { if (interviewTimer) window.clearInterval(interviewTimer); });

async function saveVersion(asNew: boolean): Promise<boolean> {
  if (savingVersion.value) return false;
  if (!draft.value || !selectedJob.value || !selectedVersionId.value) {
    saveFeedback.value = { text: '当前简历的来源版本不可用，请重新选择职位和版本。', error: true };
    return false;
  }
  const jobId = selectedJob.value.id;
  const versionId = selectedVersionId.value;
  const epoch = selectionEpoch;
  if (!asNew && !storedVersion.value) {
    saveFeedback.value = { text: '来源版本已删除，请使用「另存为新版本」保留当前内容。', error: true };
    return false;
  }
  savingVersion.value = true;
  saveFeedback.value = { text: asNew ? '正在另存为新版本…' : '正在保存…', error: false };
  try {
    const candidate = normalizeVersion({ ...draft.value, visual_settings: visual.value });
    const endpoint = asNew ? '/edits' : '';
    const version = await api<Version>(`/api/jobs/${encodeURIComponent(jobId)}/versions/${encodeURIComponent(versionId)}${endpoint}`,
      json(asNew ? 'POST' : 'PUT', { resume_content: candidate.resume_content, module_order: candidate.module_order, visual_settings: candidate.visual_settings }));
    const next = normalizeVersion(version);
    const job = companies.value.flatMap(company => company.jobs).find(item => item.id === jobId);
    if (job) {
      const index = job.versions.findIndex(item => item.id === next.id);
      if (index >= 0) job.versions[index] = next;
      else job.versions.push(next);
    }
    if (!selectionMatches(epoch, jobId, versionId)) return false;
    selectedVersionId.value = next.id;
    draft.value = next;
    editing.value = false;
    savedSnapshot.value = snapshot(next);
    const message = asNew ? `已另存为 V${next.number}，原版本保留。` : `V${next.number} 已保存。`;
    saveFeedback.value = { text: message, error: false };
    notify(message);
    return true;
  } catch (error) {
    const message = errorMessage(error);
    saveFeedback.value = { text: `保存失败。${message}`, error: true };
    notify(message, true);
    return false;
  }
  finally { savingVersion.value = false; }
}

async function beforeSwitch(): Promise<boolean> {
  if (savingVersion.value) return false;
  if (!dirty.value) return true;
  const choice = prompt('当前版本有未保存修改。输入“保存”覆盖当前版本，输入“放弃”丢弃，或取消以留在当前版本。', '保存');
  if (choice === '保存') return saveVersion(false);
  if (choice === '放弃') { draft.value = storedVersion.value ? normalizeVersion(storedVersion.value) : null; editing.value = false; return true; }
  return false;
}

function clearSelection() {
  saveFeedback.value = null;
  selectionEpoch += 1;
  selectedJobId.value = null;
  selectedVersionId.value = null;
  draft.value = null;
  editing.value = false;
  jobEditing.value = false;
  invalidateInterview();
  sessionStorage.removeItem('cv-selected-job');
}

async function selectCompany(company: Company) {
  if (!await beforeSwitch()) return;
  selectedCompanyId.value = selectedCompanyId.value === company.id ? null : company.id;
  clearSelection();
  jobFormOpen.value = false;
}

async function selectJob(company: Company, job: Job, version?: Version) {
  if (version && selectedJob.value?.id === job.id) return selectVersion(version);
  if (!await beforeSwitch()) return;
  selectedCompanyId.value = company.id;
  selectedJobId.value = job.id;
  saveFeedback.value = null;
  selectionEpoch += 1;
  selectedVersionId.value = null;
  draft.value = null;
  editing.value = false;
  jobEditing.value = false;
  invalidateInterview();
  sessionStorage.setItem('cv-selected-job', job.id);
  if (version) showVersion(version);
  await run(async () => { await refreshInterviews(job.id); });
}

async function selectVersion(version: Version) {
  if (!await beforeSwitch()) return;
  showVersion(version);
}

function showVersion(version: Version) {
  const next = normalizeVersion(version);
  selectionEpoch += 1;
  saveFeedback.value = null;
  selectedVersionId.value = version.id;
  draft.value = next;
  visual.value = { ...visualDefaults, ...next.visual_settings };
  savedSnapshot.value = snapshot(next);
  editing.value = false;
}

async function openDraft() {
  if (!await beforeSwitch()) return;
  const saved = selectedJob.value?.resume_draft;
  if (!saved) return;
  saveFeedback.value = null;
  selectionEpoch += 1;
  selectedVersionId.value = saved.id;
  draft.value = normalizeVersion(saved);
  visual.value = { ...visualDefaults, ...saved.visual_settings };
  savedSnapshot.value = snapshot(draft.value);
  activeTab.value = 'versions';
  editing.value = true;
}

async function createCompany() {
  await run(async () => {
    const company = await api<Company>('/api/companies', json('POST', { name: companyName.value.trim() }));
    await refreshApplications();
    selectedCompanyId.value = company.id;
    clearSelection();
    companyName.value = '';
    companyFormOpen.value = false;
  });
}

async function renameCompany(company: Company) {
  await run(async () => {
    const name = prompt('新的公司名称', company.name);
    if (name === null) return;
    if (!name.trim()) throw Error('请填写公司名称。');
    if (!await beforeSwitch()) return;
    await api(`/api/companies/${encodeURIComponent(company.id)}`, json('PATCH', { name: name.trim() }));
    await refreshApplications();
    notify('公司名称已更新。');
  });
}

async function deleteCompany(company: Company) {
  await run(async () => {
    if (!confirm(`永久删除“${company.name}”及其所有岗位和简历版本？`) || !await beforeSwitch()) return;
    await api(`/api/companies/${encodeURIComponent(company.id)}`, { method: 'DELETE' });
    if (selectedCompanyId.value === company.id) { selectedCompanyId.value = null; clearSelection(); }
    await refreshApplications();
    notify('公司及其岗位和版本已删除。');
  });
}

async function createJob() {
  if (!selectedCompanyId.value) return;
  const companyId = selectedCompanyId.value;
  await run(async () => {
    const job = await api<Job>(`/api/companies/${encodeURIComponent(companyId)}/jobs`,
      json('POST', { title: jobTitle.value.trim(), job_text: jobText.value.trim() }));
    const next = await refreshApplications();
    jobTitle.value = ''; jobText.value = ''; jobFormOpen.value = false; uploadState.value = '';
    const parent = next.find(item => item.id === companyId);
    if (parent) await selectJob(parent, job);
  });
}

async function extractJobFile(file: File | undefined, setText: (text: string) => void, setState: (text: string) => void) {
  if (!file) return;
  setState('正在提取招聘要求…');
  try {
    let result: { text: string };
    if (cloud) {
      const upload = await api<{ url: string; key: string }>('/api/uploads', json('POST', { filename: file.name, size: file.size }));
      const put = await fetch(upload.url, { method: 'PUT', body: file });
      if (!put.ok) throw Error('文件上传失败，请重试。');
      const queued = queuedTask(await api('/api/jobs/extract', json('POST', { key: upload.key })));
      result = { text: await waitTask(queued.task_id, value => stringField(value, 'text')) };
    } else {
      const form = new FormData(); form.append('file', file);
      result = { text: stringField(await api('/api/jobs/extract', { method: 'POST', body: form }), 'text') };
    }
    setText(result.text);
    setState('文字已提取。请检查并修改后保存。');
  } catch (error) { setState(errorMessage(error)); notify(errorMessage(error), true); }
}

function uploadJob(event: Event, editingJob = false) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (editingJob) void extractJobFile(file, text => { editJobText.value = text; }, text => { editUploadState.value = text; });
  else void extractJobFile(file, text => { jobText.value = text; }, text => { uploadState.value = text; });
  input.value = '';
}

async function editJob(company: Company, job: Job) {
  if (!await beforeSwitch()) return;
  selectedCompanyId.value = company.id; selectedJobId.value = job.id;
  selectionEpoch += 1;
  selectedVersionId.value = null; draft.value = null; editing.value = false;
  invalidateInterview();
  sessionStorage.setItem('cv-selected-job', job.id);
  jobEditing.value = true; editJobTitle.value = job.title; editJobText.value = job.job_text || '';
}

async function saveJob() {
  if (!selectedJob.value) return;
  await run(async () => {
    await api(`/api/jobs/${encodeURIComponent(selectedJob.value!.id)}`,
      json('PATCH', { title: editJobTitle.value.trim(), job_text: editJobText.value.trim(), expected_revision: Number(selectedJob.value!.revision || 0) }));
    await refreshApplications();
    jobEditing.value = false; editUploadState.value = '';
    notify('职位和招聘要求已保存；已有版本保持原快照。');
  });
}

async function deleteJob(job: Job) {
  await run(async () => {
    if (!confirm(`永久删除“${job.title}”及其全部简历版本？`) || !await beforeSwitch()) return;
    await api(`/api/jobs/${encodeURIComponent(job.id)}`, { method: 'DELETE' });
    if (selectedJobId.value === job.id) clearSelection();
    await refreshApplications(); notify('岗位及其版本已删除。');
  });
}

async function deleteVersion(job: Job, version: Version) {
  await run(async () => {
    if (!confirm(`永久删除 V${version.number}？其他版本和编号保持不变。`) || !await beforeSwitch()) return;
    await api(`/api/jobs/${encodeURIComponent(job.id)}/versions/${encodeURIComponent(version.id)}`, { method: 'DELETE' });
    if (selectedVersionId.value === version.id) { selectedVersionId.value = null; draft.value = null; editing.value = false; }
    await refreshApplications(); await refreshInterviews(); notify(`V${version.number} 已删除。`);
  });
}

async function createVersion() {
  if (!selectedJob.value || hasGenerated.value || busy.value) return;
  const jobId = selectedJob.value.id;
  const epoch = selectionEpoch;
  busy.value = 'generate';
  try {
    let result: Version;
    if (cloud) {
      const queued = queuedTask(await api(`/api/jobs/${encodeURIComponent(jobId)}/versions`, json('POST', {})));
      result = await waitTask(queued.task_id, value => {
        const version = objectResult(value).version;
        return objectResult(version) as unknown as Version;
      });
    } else {
      result = await api<Version>(`/api/jobs/${encodeURIComponent(jobId)}/versions`, json('POST', {}));
    }
    await refreshApplications();
    if (!selectionMatches(epoch, jobId)) return;
    const next = normalizeVersion(result);
    selectedVersionId.value = next.id; draft.value = next;
    visual.value = { ...visualDefaults, ...next.visual_settings };
    savedSnapshot.value = snapshot(next); editing.value = false;
    notify('已生成 V1，可以编辑、保存或另存为新版本。');
  } catch (error) { notify(errorMessage(error), true); }
  finally { busy.value = null; }
}

function updateVisual(key: keyof VisualSettings, value: string) {
  if (!currentVersion.value) return;
  const next = { ...visual.value, [key]: value };
  visual.value = next;
  draft.value = { ...normalizeVersion(draft.value || currentVersion.value), visual_settings: next };
  editing.value = true;
}

function updateField(path: string, value: string) {
  if (!editing.value || !currentVersion.value) return;
  const next = normalizeVersion(draft.value || currentVersion.value);
  const parts = path.split('.');
  let target: unknown = next.resume_content;
  for (const part of parts.slice(0, -1)) {
    if (!target || typeof target !== 'object') return;
    target = (target as Record<string, unknown>)[part];
  }
  if (target && typeof target === 'object') (target as Record<string, unknown>)[parts.at(-1)!] = value;
  draft.value = next;
}

function moveModule(key: string, direction: -1 | 1) {
  if (!currentVersion.value) return;
  const next = normalizeVersion(draft.value || currentVersion.value);
  const from = next.module_order.indexOf(key);
  let to = from + direction;
  if (visual.value.template_id === 'template-06') {
    const onSidebar = sidebarModules.includes(key);
    while (to >= 0 && to < next.module_order.length && sidebarModules.includes(next.module_order[to]) !== onSidebar) {
      to += direction;
    }
  }
  if (from >= 0 && to >= 0 && to < next.module_order.length) {
    [next.module_order[from], next.module_order[to]] = [next.module_order[to], next.module_order[from]];
  }
  draft.value = next;
}

function cancelVersion() {
  const restored = storedVersion.value ? normalizeVersion(storedVersion.value) : null;
  saveFeedback.value = null;
  draft.value = restored;
  visual.value = { ...visualDefaults, ...(restored?.visual_settings || {}) };
  editing.value = false;
  notify('已放弃本次未保存修改。');
}

async function exportResume(format: 'docx' | 'pdf') {
  if (!currentVersion.value || editing.value || !selectedJobId.value) return;
  busy.value = 'export';
  try {
    if (cloud) {
      const queued = queuedTask(await api('/api/export', json('POST', {
        job_id: selectedJobId.value, version_id: selectedVersionId.value, template_id: visual.value.template_id, format,
      })));
      await waitTask(queued.task_id, objectResult);
      const file = await api<{ url: string; format: string }>(`/api/download/${queued.task_id}`);
      const link = document.createElement('a'); link.href = file.url; link.download = `resume.${file.format}`; link.click();
    } else {
      const response = await api<Response>('/api/export', json('POST', {
        template_id: visual.value.template_id, format,
        resume_content: currentVersion.value.resume_content,
        module_order: currentVersion.value.module_order,
        visual_settings: visual.value,
      }));
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href = url; link.download = `resume.${format}`; link.click();
      URL.revokeObjectURL(url);
    }
    notify('文件已导出。');
  } catch (error) { notify(errorMessage(error), true); }
  finally { busy.value = null; }
}

async function startInterview() {
  if (!currentVersion.value || !selectedJob.value || !selectedVersionId.value || busy.value || !await beforeSwitch()) return;
  const jobId = selectedJob.value?.id;
  const versionId = selectedVersionId.value;
  if (!jobId || !versionId) return;
  const epoch = selectionEpoch;
  await run(async () => {
    const request = interviewRequest + 1;
    const existing = await refreshInterviews(jobId);
    if (!selectionMatches(epoch, jobId, versionId) || request !== interviewRequest) return;
    if (existing && !confirm('开始新面试会删除该职位之前保存的面试记录，确定继续吗？')) return;
    busy.value = 'interview';
    try {
      const url = `/api/jobs/${encodeURIComponent(jobId)}/versions/${encodeURIComponent(versionId)}/interviews`;
      if (cloud) {
        const queued = await api<unknown>(url, json('POST', {}));
        if (!selectionMatches(epoch, jobId, versionId)) return;
        activeTab.value = 'interview';
        const session = await api<InterviewSession>(`/api/interviews/${encodeURIComponent(stringField(queued, 'session_id'))}`);
        if (selectionMatches(epoch, jobId, versionId)) currentInterview.value = session;
        await waitTask(queuedTask(queued).task_id, objectResult);
      } else {
        const session = await api<InterviewSession>(url, json('POST', {}));
        if (!selectionMatches(epoch, jobId, versionId)) return;
        activeTab.value = 'interview';
        currentInterview.value = session;
      }
      if (selectionMatches(epoch, jobId, versionId)) await refreshInterviews(jobId);
    } finally { busy.value = null; }
  });
}

function resetWorkspace() {
  companies.value = []; selectedCompanyId.value = null; clearSelection();
}
function onBaseChange(value: BaseResumeData | null) { base.value = value; }
async function refreshInterviewView() { await refreshInterviews(); }
</script>

<template>
  <header class="atelier-header">
    <div class="atelier-title"><h1>简历工作台</h1><span aria-hidden="true">／</span></div>
    <div class="atelier-status"><span class="local">本地工作台</span><button id="auth-logout" type="button" :hidden="!cloud">退出登录</button></div>
  </header>

  <BaseResume :base="base" :on-base-change="onBaseChange" :api="api" :cloud="cloud"
    :wait-task="waitTask" :notify="notify" :before-reset="beforeSwitch" :on-reset="async () => resetWorkspace()" />

  <div class="workspace" :class="{ 'interview-mode': activeTab === 'interview' }">
    <aside class="card navigation">
      <div class="section-heading"><div><p class="panel-kicker">公司 · 职位 · 版本</p><h2>投递图谱</h2></div><button id="add-company" class="secondary small" type="button" @click="companyFormOpen = !companyFormOpen">＋ 公司</button></div>
      <form v-if="companyFormOpen" id="company-form" class="compact-form" @submit.prevent="createCompany">
        <input id="company-name" v-model="companyName" placeholder="公司名称" aria-label="公司名称" required />
        <button type="submit">添加</button>
      </form>
      <CompanyTree :companies="companies" :selected-company-id="selectedCompanyId" :selected-job-id="selectedJobId"
        :selected-version-id="selectedVersionId" @select-company="selectCompany" @select-job="selectJob"
        @rename-company="renameCompany" @delete-company="deleteCompany"
        @edit-job="editJob" @delete-job="deleteJob" @add-job="jobFormOpen = true" />
      <div v-if="jobFormOpen" id="job-form-wrap"><h3>添加岗位</h3>
        <form id="job-form" class="compact-form" @submit.prevent="createJob">
          <input id="job-title" v-model="jobTitle" placeholder="职位名称" aria-label="职位名称" required />
          <textarea id="job-text" v-model="jobText" rows="12" placeholder="粘贴招聘要求" aria-label="招聘要求" required />
          <label class="button secondary">上传 DOCX / DOC / PDF<input id="job-upload" type="file" accept=".docx,.doc,.pdf" @change="uploadJob($event)" /></label>
          <p id="job-upload-state" class="hint" role="status">{{ uploadState }}</p>
          <button type="submit">检查后保存岗位</button>
          <button id="cancel-job" class="secondary" type="button" @click="jobFormOpen = false; jobTitle = ''; jobText = ''; uploadState = ''">取消</button>
        </form>
      </div>
    </aside>

    <section class="resume-column">
      <div class="section-heading"><div>
        <h2 id="current-title">{{ selectedJob ? `${selectedCompany?.name || ''} · ${selectedJob.title}` : '选择一个岗位' }}</h2>
        <p id="current-meta" class="hint">{{ currentMeta }}</p>
      </div><div class="row tight">
        <button id="create-version" type="button" :disabled="!selectedJob || editing || hasGenerated || !!busy" @click="createVersion">{{ hasGenerated ? 'V1 已生成' : '确认生成简历' }}</button>
        <button v-if="currentVersion && !editing" id="edit-version" class="secondary" type="button" @click="draft = normalizeVersion(currentVersion); editing = true; saveFeedback = null">编辑</button>
        <button v-if="selectedJob?.resume_draft && activeTab === 'versions' && !editing" id="open-resume-draft"
          class="secondary" type="button" @click="openDraft">打开已有草稿</button>
        <template v-if="editing"><button id="save-current-version" type="button" :disabled="savingVersion" @click="saveVersion(false)">保存</button>
          <button id="save-version" class="secondary" type="button" :disabled="savingVersion" @click="saveVersion(true)">另存为新版本</button>
          <button id="cancel-version" class="secondary" type="button" :disabled="savingVersion" @click="cancelVersion">取消</button></template>
      </div></div>
      <p v-if="saveFeedback" id="resume-save-status" class="hint" :class="{ error: saveFeedback.error }"
        role="status">{{ saveFeedback.text }}</p>
      <section v-if="selectedJob && activeTab !== 'interview'" id="job-details" class="job-details">
        <div class="section-heading"><h3>目标职位与招聘要求</h3>
          <button v-if="!jobEditing" id="edit-job" class="secondary small" type="button"
            @click="editJobTitle = selectedJob.title; editJobText = selectedJob.job_text || ''; jobEditing = true">修改职位</button>
        </div>
        <p v-if="!jobEditing" id="job-requirements" class="requirements-text">{{ selectedJob.job_text || '' }}</p>
        <form v-else id="job-edit-form" @submit.prevent="saveJob">
          <label>职位名称<input id="job-edit-title" v-model="editJobTitle" required /></label>
          <label>招聘要求<textarea id="job-edit-text" v-model="editJobText" rows="14" required /></label>
          <label class="button secondary">上传 DOCX / DOC / PDF<input id="job-edit-upload" type="file" accept=".docx,.doc,.pdf" @change="uploadJob($event, true)" /></label>
          <p id="job-edit-upload-state" class="hint" role="status">{{ editUploadState }}</p>
          <div class="row"><button type="submit">检查后保存修改</button>
            <button id="cancel-job-edit" class="secondary" type="button" @click="jobEditing = false; editUploadState = ''">取消</button></div>
        </form>
      </section>

      <div class="workspace-tabs" role="tablist" aria-label="工作区">
        <button id="tab-versions" type="button" role="tab" :aria-selected="activeTab === 'versions'" @click="activeTab = 'versions'">简历版本</button>
        <button id="tab-interview" type="button" role="tab" :aria-selected="activeTab === 'interview'"
          @click="activeTab = 'interview'; run(refreshInterviewView)">模拟面试</button>
      </div>
      <section v-if="activeTab === 'versions'" id="versions-panel" role="tabpanel">
        <VersionGraph v-if="selectedJob?.versions?.length" :job="selectedJob" :selected-id="selectedVersionId"
          @select="selectVersion" @delete="deleteVersion" />
        <p class="notice">AI 生成内容未经事实核验。请核对简历内容后再导出。</p>
        <div v-if="busy === 'generate' || busy === 'export'" id="generation-progress" class="progress-status" role="status" aria-live="polite">
          <span>{{ busy === 'generate' ? '正在根据基础简历和岗位要求生成简历模块…' : '正在导出文件…' }}</span>
          <progress aria-label="正在处理岗位简历" />
        </div>
        <ResumePreview :job="selectedJob" :version="currentVersion" :editing="editing" :visual="visual" :inert="savingVersion || undefined"
          :on-field="updateField" :on-move="moveModule" />
        <section v-if="currentVersion" class="version-requirements">
          <h3>V{{ currentVersion.number }} 使用的招聘要求</h3>
          <p class="requirements-text">{{ currentVersion.job_text || '' }}</p>
        </section>
        <button v-if="currentVersion && !editing" id="start-interview" type="button" :disabled="!!busy" @click="startInterview">用此版本开始面试</button>
      </section>
      <template v-else>
        <section id="interview-workspace" role="tabpanel">
          <div v-if="selectedJob?.versions?.length" class="interview-version-picker" aria-label="选择面试简历版本">
            <strong>选择面试简历版本</strong>
            <div class="interview-version-buttons" role="group" aria-label="面试简历版本">
              <button v-for="version in [...selectedJob.versions].sort((a, b) => a.number - b.number)" :key="version.id"
                type="button" class="secondary" :aria-pressed="selectedVersionId === version.id" :disabled="!!busy"
                @click="selectVersion(version)">V{{ version.number }}</button>
            </div>
            <button v-if="selectedVersionId && !editing" id="start-interview-here" type="button" :disabled="!!busy"
              @click="startInterview">{{ currentInterview ? '用 V' + currentVersion?.number + ' 开始新面试' : '用 V' + currentVersion?.number + ' 开始面试' }}</button>
          </div>
          <InterviewPanel :job-selected="!!selectedJob" :session="currentInterview" :api="api" :cloud="cloud"
            :wait-task="waitTask" :notify="notify" :on-refresh="refreshInterviewView" />
        </section>
      </template>
    </section>

    <aside class="card appearance" :inert="savingVersion || undefined">
      <div class="appearance-title"><p class="panel-kicker">模板 · 配色 · 导出</p><h2>风格工坊</h2></div>
      <section class="appearance-section"><h3>简历模板</h3><div id="template-cards" class="template-cards" role="group" aria-label="简历模板">
        <button v-for="item in templates" :key="item.id" class="template-card" :class="{ selected: visual.template_id === item.id }"
          type="button" :aria-pressed="visual.template_id === item.id" :disabled="!currentVersion" @click="updateVisual('template_id', item.id)">
          <span class="template-miniature" :class="item.id" aria-hidden="true"><i></i><i></i><i></i></span>
          <span>{{ item.name }}</span>
        </button>
      </div></section>
      <section class="appearance-section"><h3>主题配色</h3><div id="color-swatches" class="color-swatches" role="group" aria-label="主题颜色">
        <button v-for="(item, index) in colors" :key="item.value" type="button" class="color-swatch"
          :class="{ selected: visual.color?.toLowerCase() === item.value.toLowerCase() }"
          :style="{ '--swatch-position': `${index * 20}%` }" :aria-label="item.name" :aria-pressed="visual.color?.toLowerCase() === item.value.toLowerCase()"
          :title="item.name" :disabled="!currentVersion || visual.template_id === 'template-04'" @click="updateVisual('color', item.value)"><span class="color-swatch-dot" aria-hidden="true" /><span class="color-swatch-name">{{ item.name }}</span></button>
      </div></section>
      <section class="appearance-section"><h3>排版细节</h3><div id="width-options" class="width-options" role="group" aria-label="页面宽度">
        <button v-for="item in widths" :key="item.value" type="button" :class="{ selected: visual.page_width === item.value }"
          :aria-pressed="visual.page_width === item.value" :title="item.width" :aria-label="`${item.name}（${item.width}）`" :disabled="!currentVersion" @click="updateVisual('page_width', item.value)">{{ item.name }}</button>
      </div></section>
      <section class="appearance-section export-section"><h3 class="visually-hidden">导出简历</h3><div class="export-buttons">
        <button id="export-pdf" type="button" :disabled="!currentVersion || editing || !!busy" @click="exportResume('pdf')">导出 PDF</button>
        <button id="export" class="secondary" type="button" :disabled="!currentVersion || editing || !!busy" @click="exportResume('docx')">导出 Word</button>
      </div></section>
    </aside>
  </div>
  <div id="status" role="status" :class="{ show: !!notice, error: notice?.error }">{{ notice?.text || '' }}</div>
</template>
