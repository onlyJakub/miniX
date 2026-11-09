export async function apiRequest(path, { method = 'GET', body } = {}) {
  let response;

  try {
    response = await fetch(`/api${path}`, {
      method,
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      cache: 'no-store',
      signal: AbortSignal.timeout(15000),
    });
  } catch {
    throw new Error('Nie można połączyć się z API. Sprawdź, czy frontend i backend są uruchomione.');
  }

  if (response.status === 204) return null;

  let data;

  try {
    data = await response.json();
  } catch {
    throw new Error('API nie zwróciło poprawnej odpowiedzi JSON. Sprawdź, czy FastAPI działa na porcie 8000.');
  }

  if (!response.ok) {
    const details = Array.isArray(data.errors)
      ? [...new Set(data.errors.map((error) => error.message))].join(' ')
      : '';
    const error = new Error([data.message || `Błąd API (HTTP ${response.status}).`, details].filter(Boolean).join(' '));
    error.status = response.status;
    throw error;
  }

  return data;
}

export function checkHealth() {
  return apiRequest('/health');
}