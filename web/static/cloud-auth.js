(() => {
  const config = window.CV_CLOUD_CONFIG;
  if (!config) {
    window.cvAuth = { enabled: false, ready: Promise.resolve(), url: path => path, headers: () => ({}) };
    return;
  }
  const panel = document.getElementById('auth-panel');
  const login = document.getElementById('auth-login');
  const logout = document.getElementById('auth-logout');
  const message = document.getElementById('auth-message');
  const issuer = config.issuer.replace(/\/$/, '');
  const domain = config.hostedUiDomain.replace(/\/$/, '');
  const bytes = value => btoa(String.fromCharCode(...value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  async function challenge(verifier) { return bytes(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)))); }
  async function signIn() {
    const verifier = bytes(crypto.getRandomValues(new Uint8Array(32)));
    const state = bytes(crypto.getRandomValues(new Uint8Array(24)));
    sessionStorage.setItem('cv-pkce', JSON.stringify({ verifier, state }));
    const query = new URLSearchParams({ response_type: 'code', client_id: config.clientId, redirect_uri: config.redirectUri, scope: 'openid email', state, code_challenge_method: 'S256', code_challenge: await challenge(verifier) });
    location.assign(`${domain}/oauth2/authorize?${query}`);
  }
  function claims(token) { try { return JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))); } catch { return {}; } }
  function validToken() {
    const token = sessionStorage.getItem('cv-access-token');
    const c = token && claims(token);
    return c?.iss === issuer && c?.client_id === config.clientId && c?.token_use === 'access' && c?.exp > Date.now() / 1000 + 30 ? token : null;
  }
  async function redeemCode() {
    const url = new URL(location.href);
    const code = url.searchParams.get('code');
    if (!code) return;
    const stored = JSON.parse(sessionStorage.getItem('cv-pkce') || 'null');
    sessionStorage.removeItem('cv-pkce');
    if (!stored || stored.state !== url.searchParams.get('state')) throw Error('登录状态不匹配，请重试。');
    const body = new URLSearchParams({ grant_type: 'authorization_code', client_id: config.clientId, redirect_uri: config.redirectUri, code, code_verifier: stored.verifier });
    const response = await fetch(`${domain}/oauth2/token`, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body });
    if (!response.ok) throw Error('登录验证失败，请重试。');
    const tokens = await response.json();
    sessionStorage.setItem('cv-access-token', tokens.access_token);
    history.replaceState(null, '', url.pathname);
  }
  function render() {
    const logged = !!validToken();
    panel.hidden = logged;
    logout.hidden = !logged;
    document.querySelector('main').hidden = !logged;
    return logged;
  }
  login.addEventListener('click', signIn);
  logout.addEventListener('click', () => {
    sessionStorage.removeItem('cv-access-token');
    const query = new URLSearchParams({ client_id: config.clientId, logout_uri: config.redirectUri });
    location.assign(`${domain}/logout?${query}`);
  });
  const ready = redeemCode().catch(error => { message.textContent = error.message; }).then(render);
  window.cvAuth = {
    enabled: true, ready,
    url: path => `${config.apiBase.replace(/\/$/, '')}${path}`,
    headers: () => { const token = validToken(); if (!token) { render(); throw Error('登录已过期，请重新登录。'); } return { Authorization: `Bearer ${token}` }; },
  };
})();
