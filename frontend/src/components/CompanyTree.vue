<script setup lang="ts">
import type { Company, Job } from '../types';

defineProps<{
  companies: Company[];
  selectedCompanyId: string | null;
  selectedJobId: string | null;
}>();

const emit = defineEmits<{
  (event: 'select-company', company: Company): void;
  (event: 'select-job', company: Company, job: Job): void;
  (event: 'rename-company', company: Company): void;
  (event: 'delete-company', company: Company): void;
  (event: 'edit-job', company: Company, job: Job): void;
  (event: 'delete-job', job: Job): void;
  (event: 'add-job'): void;
}>();
</script>

<template>
  <div id="company-list" class="company-tree">
    <p v-if="!companies.length" class="hint">还没有公司记录。点击“＋ 公司”开始。</p>
    <section v-for="company in companies" :key="company.id" class="company-branch">
      <div class="record-row company-row" :class="{ selected: company.id === selectedCompanyId }">
        <button class="record-select company-select" type="button"
          :aria-expanded="company.id === selectedCompanyId" :aria-controls="`company-jobs-${company.id}`"
          @click="emit('select-company', company)"><span class="tree-chevron" aria-hidden="true">›</span>{{ company.name }}</button>
        <button class="tree-action" type="button" title="修改公司名称" :aria-label="`修改${company.name}名称`" @click="emit('rename-company', company)">改</button>
        <button class="tree-action delete-action" type="button" title="删除公司及其职位和版本" :aria-label="`删除${company.name}及其职位和版本`" @click="emit('delete-company', company)">删</button>
      </div>
      <div :id="`company-jobs-${company.id}`" class="company-children" :hidden="company.id !== selectedCompanyId">
        <p v-if="!company.jobs?.length" class="hint">还没有职位。</p>
        <div v-for="job in company.jobs || []" :key="job.id" class="job-branch">
          <div class="record-row job-row" :class="{ selected: job.id === selectedJobId }">
            <button class="record-select job-select" type="button" @click="emit('select-job', company, job)">{{ job.title }}</button>
            <button class="tree-action" type="button" title="修改职位" :aria-label="`修改${job.title}`" @click="emit('edit-job', company, job)">改</button>
            <button class="tree-action delete-action" type="button" title="删除职位及其版本" :aria-label="`删除${job.title}及其版本`" @click="emit('delete-job', job)">删</button>
          </div>
        </div>
        <button v-if="company.id === selectedCompanyId" id="add-job" class="add-job-inline" type="button" @click="emit('add-job')">＋ 添加职位</button>
      </div>
    </section>
  </div>
</template>
