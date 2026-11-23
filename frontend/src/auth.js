import { apiRequest } from './api.js';

export function getCurrentUser() {
  return apiRequest('/auth/me');
}

export function logout() {
  return apiRequest('/auth/logout', { method: 'POST' });
}

export function setupAuthForm() {
  const form = document.querySelector('#auth-form');
  if (!form) return;

  const isRegistration = form.dataset.action === 'register';
  const message = document.querySelector('#form-message');
  const button = form.querySelector('button[type="submit"]');
  const passwordInput = form.elements.namedItem('password');
  const events = new AbortController();
  let active = true;

  if (!isRegistration && new URLSearchParams(window.location.search).get('registered') === '1') {
    message.dataset.kind = 'success';
    message.textContent = 'Konto zostało utworzone. Zaloguj się, aby kontynuować.';
  } else if (!isRegistration && new URLSearchParams(window.location.search).get('expired') === '1') {
    message.dataset.kind = 'error';
    message.textContent = 'Sesja wygasła. Zaloguj się ponownie.';
  }

  passwordInput.addEventListener('input', () => passwordInput.setCustomValidity(''), { signal: events.signal });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (isRegistration && !passwordInput.value.trim()) {
      passwordInput.setCustomValidity('Hasło nie może składać się wyłącznie z białych znaków.');
    }
    if (!form.reportValidity()) return;

    const fields = new FormData(form);
    const data = isRegistration
      ? { username: fields.get('username').trim(), email: fields.get('email').trim(), password: fields.get('password') }
      : { identifier: fields.get('identifier').trim(), password: fields.get('password') };
    button.disabled = true;
    message.dataset.kind = '';
    message.textContent = isRegistration ? 'Tworzenie konta…' : 'Logowanie…';

    try {
      await apiRequest(`/auth/${isRegistration ? 'register' : 'login'}`, { method: 'POST', body: data });
      if (!active) return;
      form.reset();
      window.location.replace(isRegistration ? '/login?registered=1' : '/feed');
    } catch (error) {
      if (!active) return;
      message.dataset.kind = 'error';
      message.textContent = error.message;
      passwordInput.value = '';
      passwordInput.focus();
    } finally {
      if (active) button.disabled = false;
    }
  }, { signal: events.signal });
  return () => {
    active = false;
    events.abort();
    form.reset();
    passwordInput.setCustomValidity('');
    button.disabled = false;
  };
}