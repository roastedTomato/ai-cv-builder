<script setup lang="ts">
import { computed } from 'vue';
import type { Job, Version } from '../types';
import { versionDate } from '../types';

interface Branch { version: Version; children: Branch[]; missingParent: boolean }
const props = defineProps<{ job: Job; selectedId: string | null; branches?: Branch[] }>();
const emit = defineEmits<{
  (event: 'select', version: Version): void;
  (event: 'delete', job: Job, version: Version): void;
}>();

const tree = computed(() => {
  if (props.branches) return props.branches;
  const versions = [...props.job.versions].sort((a, b) => a.number - b.number);
  const byId = new Map(versions.map(version => [version.id, { version, children: [], missingParent: false } as Branch]));
  const roots: Branch[] = [];
  for (const version of versions) {
    const branch = byId.get(version.id)!;
    const parent = version.source_version_id && byId.get(version.source_version_id);
    if (parent && parent !== branch) parent.children.push(branch);
    else { branch.missingParent = Boolean(version.source_version_id); roots.push(branch); }
  }
  return roots;
});
</script>

<template>
  <div class="version-graph" :class="{ nested: branches !== undefined }" aria-label="简历版本来源图">
    <ul class="version-branches">
      <li v-for="branch in tree" :key="branch.version.id">
        <div class="version-graph-node">
          <button class="version-bubble" :class="{ selected: branch.version.id === selectedId }" type="button"
            :aria-label="`选择 V${branch.version.number}`" :aria-pressed="branch.version.id === selectedId"
            @click="emit('select', branch.version)">V{{ branch.version.number }}</button>
          <span class="version-graph-caption">{{ versionDate(branch.version) }}
            <span v-if="branch.missingParent">来源 V{{ branch.version.source_version_number || '?' }} 已删除</span>
          </span>
          <button class="version-graph-delete" type="button" :aria-label="`删除 V${branch.version.number}`"
            @click="emit('delete', job, branch.version)">×</button>
        </div>
        <VersionGraph v-if="branch.children.length" :job="job" :selected-id="selectedId" :branches="branch.children"
          @select="emit('select', $event)" @delete="(item, version) => emit('delete', item, version)" />
      </li>
    </ul>
  </div>
</template>
