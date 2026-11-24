export interface VisualSettings {
  template_id?: string;
  color?: string;
  page_width?: string;
}

export interface ResumeContent {
  personal?: { name?: string; email?: string; phone?: string; location?: string };
  [section: string]: unknown;
}

export interface Version {
  id: string;
  number: number;
  created_at?: string;
  saved_at?: string;
  source_version_id?: string;
  source_version_number?: number;
  job_text?: string;
  resume_content: ResumeContent;
  module_order: string[];
  visual_settings: VisualSettings;
}

export interface Job {
  id: string;
  title: string;
  job_text: string;
  revision?: number;
  has_generated?: boolean;
  versions: Version[];
}

export interface Company { id: string; name: string; jobs: Job[] }
export interface Template { id: string; name: string }

export interface BaseItem { id?: string; [field: string]: string | string[] | undefined }
export interface BaseResumeData {
  resume_id: string;
  version: string;
  personal: Record<string, string>;
  education: BaseItem[];
  work: BaseItem[];
  projects: BaseItem[];
  skills: BaseItem[];
  certificates: BaseItem[];
  fixture_note?: string;
  [field: string]: unknown;
}

export interface InterviewFeedback {
  evaluation?: string;
  suggestions?: string;
  answer_points?: string;
  reference_answer?: string;
  confirmed_facts?: string[];
  user_claims?: string[];
  unconfirmed_suggestions?: string[];
}
export interface InterviewTurn {
  id: string;
  category?: string;
  question?: string;
  reference_answer?: string;
  answer?: string | null;
  answered_at?: string | null;
  feedback?: InterviewFeedback | null;
  follow_up?: string;
}
export interface InterviewSession {
  id: string;
  job_title?: string;
  job_text?: string;
  version_number?: number;
  version_deleted?: boolean;
  status: 'new' | 'question_pending' | 'reference_pending' | 'awaiting_answer' | 'feedback_pending' | 'feedback_ready' | 'failed' | 'ended';
  turns?: InterviewTurn[];
  error?: { message?: string } | null;
}

export const sections = [
  ['summary', '个人简介'], ['education', '教育经历'], ['work', '工作经历'],
  ['projects', '项目经历'], ['skills', '技能'], ['certificates', '证书'],
] as const;

export function versionDate(version: Version): string {
  const raw = version.created_at || version.saved_at;
  if (!raw) return '';
  const date = new Date(raw);
  return Number.isNaN(date.getTime()) ? raw : new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(date);
}

export function versionSummary(job: Job, version: Version): string {
  const source = version.source_version_id
    ? `来源 V${version.source_version_number || '?'}${job.versions.some(item => item.id === version.source_version_id) ? '' : ' 已删除'}`
    : '初始版本';
  return [`V${version.number}`, versionDate(version), source].filter(Boolean).join(' · ');
}

export function normalizeVersion(version: Version): Version {
  // API versions are JSON data; this also unwraps Vue's reactive proxies.
  const value = JSON.parse(JSON.stringify(version)) as Version;
  const hasModule = (module: unknown) => Array.isArray(module) ? module.length > 0 : Boolean(module);
  const order = (value.module_order || []).filter(key => sections.some(([id]) => id === key) && hasModule(value.resume_content?.[key]));
  sections.forEach(([key]) => { if (hasModule(value.resume_content?.[key]) && !order.includes(key)) order.push(key); });
  value.module_order = order;
  value.visual_settings ||= {};
  return value;
}
