// API client & Auth state with full safety guards
export function getToken() {
  try {
    return localStorage.getItem('ats_token') || '';
  } catch (e) {
    return '';
  }
}

export function getUser() {
  try {
    const raw = localStorage.getItem('ats_user');
    if (raw && raw !== 'undefined') {
      return JSON.parse(raw);
    }
  } catch (e) {
    console.warn('Invalid ats_user in localStorage:', e);
  }
  return null;
}

export function setSession(token, user) {
  try {
    if (token) localStorage.setItem('ats_token', token);
    else localStorage.removeItem('ats_token');
    if (user) localStorage.setItem('ats_user', JSON.stringify(user));
    else localStorage.removeItem('ats_user');
  } catch (e) {
    console.error('Failed to set localStorage session:', e);
  }
}

export async function api(path, options = {}) {
  const method = options.method || (options.body ? 'POST' : 'GET');
  const headers = { ...options.headers };
  const token = getToken();
  
  if (token) {
    headers['X-Ats-Token'] = token;
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
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('ats_unauthorized'));
      }
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
