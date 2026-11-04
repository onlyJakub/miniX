export async function checkHealth() {
  let response;

  try {
    response = await fetch('/api/health', {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
      signal: AbortSignal.timeout(10000),
    });
  } catch {
    throw new Error('Nie można połączyć się z API. Sprawdź, czy frontend i backend są uruchomione.');
  }

  let data;

  try {
    data = await response.json();
  } catch {
    throw new Error('API nie zwróciło poprawnej odpowiedzi JSON. Sprawdź, czy FastAPI działa na porcie 8000.');
  }

  if (!response.ok) {
    throw new Error(data.message || `Błąd API (HTTP ${response.status}).`);
  }

  return data;
}
