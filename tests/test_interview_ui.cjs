// Fictional test sessions exercise the actual interview renderer without a browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'static', 'app.js'), 'utf8');
const start = source.indexOf('function renderInterview(){');
const end = source.indexOf('async function refreshCurrentInterview()', start);
assert.ok(start >= 0 && end > start);
const renderer = source.slice(start, end);

function render(session) {
  const elements = new Map([
    ['#interview-session', { innerHTML: '' }],
    ['#interview-context', { hidden: true }],
    ['#interview-requirements', { textContent: '' }],
  ]);
  const context = {
    $: selector => elements.get(selector),
    selectedJob: () => ({ id: 'fictional-job' }),
    currentInterview: session,
    esc: value => String(value ?? ''),
  };
  vm.runInNewContext(`${renderer}\nrenderInterview();`, context);
  return elements.get('#interview-session').innerHTML;
}

test('failed question shows a readable message', () => {
  const html = render({ status: 'failed', job_title: '虚构测试岗位', turns: [] });
  assert.match(html, /问题与参考答案生成失败，请重试。/);
  assert.doesNotMatch(html, /\$\{esc\(/);
});

test('failed feedback uses the saved error message', () => {
  const html = render({ status: 'failed', job_title: '虚构测试岗位',
    error: { message: '回答反馈生成失败，回答已保存，请重试。' },
    turns: [{ id: 'fictional-turn', question: '虚构测试问题', answer: '虚构测试回答' }] });
  assert.match(html, /回答反馈生成失败，回答已保存，请重试。/);
  assert.doesNotMatch(html, /\$\{esc\(/);
});
