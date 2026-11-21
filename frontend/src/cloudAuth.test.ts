import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test } from 'vitest';

test('cloud login initializes before Vue mounts its logout button', async () => {
  document.body.innerHTML = '<section id="auth-panel" hidden><button id="auth-login">登录</button><p id="auth-message"></p></section><main><div id="root"></div></main>';
  sessionStorage.clear();
  (window as any).CV_CLOUD_CONFIG = {
    issuer: 'https://fictional.example.test/pool', hostedUiDomain: 'https://fictional.example.test',
    clientId: 'fictional-client', redirectUri: 'https://fictional.example.test/', apiBase: 'https://fictional.example.test/api',
  };
  const source = readFileSync(resolve(process.cwd(), '../web/static/cloud-auth.js'), 'utf8');
  new Function(source)();
  await window.cvAuth?.ready;
  expect(window.cvAuth?.enabled).toBe(true);
  expect(document.querySelector<HTMLElement>('main')?.hidden).toBe(true);
  expect(document.getElementById('auth-panel')?.hidden).toBe(false);
});
