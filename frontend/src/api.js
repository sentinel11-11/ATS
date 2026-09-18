// API client & Auth state
let authToken = localStorage.getItem('ats_token') || '';
let currentUser = JSON.parse(localStorage.getItem('ats_user') || 'null');

export function getToken() {
  return authToken;
}

export function getUser() {
  return currentUser;
}

export function setSession(token, user) {
  authToken = token || '';
  currentUser = user || null;
  if (token) localStorage.setItem('ats_token', token);
  else localStorage.removeItem('ats_token');
  if (user) localStorage.setItem('ats_user', JSON.stringify(user));
  else localStorage.removeItem('ats_user');
}

export async function api(path, options = {}) {
  const method = options.method || (options.body ? 'POST' : 'GET');
  const headers = { ...options.headers };
  
  if (authToken) {
    headers['X-Ats-Token'] = authToken;
  }
  
  let body = options.body;
  if (body && typeof body === 'object' && !(body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(body);
  }

  const url = path.startsWith('/api/') ? path : `/api/v2${path.startsWith('/') ? path : '/' + path}`;
  
  try {
    const res = await fetch(url, { method, headers, body });
    if (res.status === 401) {
      setSession('', null);
      window.dispatchEvent(new CustomEvent('ats_unauthorized'));
      return { ok: false, error: 'unauthorized' };
    }
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      return await res.json();
    }
    return { ok: res.ok, status: res.status };
  } catch (err) {
    console.error('API Error:', err);
    return { ok: false, error: err.message || 'Network error' };
  }
}
