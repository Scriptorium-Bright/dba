export async function apiRequest(path, actor, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set('X-User-Id', String(actor.userId));
  headers.set('X-Demo-Role', actor.role);
  if (options.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  let response;
  try {
    response = await fetch(`/api${path}`, { ...options, headers });
  } catch {
    throw new Error('API에 연결하지 못했습니다. DB와 서버 실행 상태를 확인해 주세요.');
  }

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(payload?.message || `요청을 처리하지 못했습니다. (${response.status})`);
  }
  return payload;
}
