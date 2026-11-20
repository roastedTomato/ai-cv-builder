// Fictional records verify the company > job > version navigation hierarchy.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'static', 'app.js'), 'utf8');
const start = source.indexOf('function renderTree(){');
const end = source.indexOf('async function refreshApplications()', start);
assert.ok(start >= 0 && end > start);
const renderer = source.slice(start, end);

function render(selectedCompanyId, selectedJobId, selectedVersionId) {
  const host = { innerHTML: '' };
  const companies = [
    { id: 'fictional-company-a', name: '虚构星河科技', jobs: [
      { id: 'fictional-job-a', title: '虚构产品经理', versions: [
        { id: 'fictional-version-1', number: 1 },
        { id: 'fictional-version-2', number: 2, source_version_id: 'fictional-version-1', source_version_number: 1 },
      ] },
      { id: 'fictional-job-b', title: '虚构产品运营', versions: [] },
    ] },
    { id: 'fictional-company-b', name: '虚构远山咨询', jobs: [
      { id: 'fictional-job-c', title: '虚构研究职位', versions: [] },
    ] },
  ];
  const context = {
    $: selector => selector === '#company-list' ? host : null,
    companies, selectedCompanyId, selectedJobId, selectedVersionId,
    esc: value => String(value ?? ''),
    versionDate: () => '测试日期',
  };
  vm.runInNewContext(`${renderer}\nrenderTree();`, context);
  return host.innerHTML;
}

test('jobs sit inside their company, and versions sit below the selected job', () => {
  const html = render('fictional-company-a', 'fictional-job-a', 'fictional-version-2');
  assert.match(html, /公司.*虚构星河科技|虚构星河科技/);
  assert.ok(html.indexOf('虚构星河科技') < html.indexOf('company-children'));
  assert.ok(html.indexOf('company-children') < html.indexOf('虚构产品经理'));
  assert.ok(html.indexOf('虚构产品经理') < html.indexOf('version-chips'));
  assert.ok(html.indexOf('version-chips') < html.indexOf('V1'));
  assert.match(html, /class="version-chip selected"[^>]+aria-pressed="true"[^>]*>V2/);
  assert.match(html, /id="company-jobs-fictional-company-b" hidden/);
  assert.doesNotMatch(html, /version-record|version-graph/);
});

test('a company can expand without showing job versions before a job is chosen', () => {
  const html = render('fictional-company-a', null, null);
  assert.match(html, /id="company-jobs-fictional-company-a"\s*>/);
  assert.match(html, /虚构产品经理/);
  assert.doesNotMatch(html, /class="version-chips"/);
  assert.match(html, /id="add-job"/);
  assert.equal((html.match(/id="add-job"/g) || []).length, 1);
});
