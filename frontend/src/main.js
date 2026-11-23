import './style.css';
import { getCurrentUser, logout, setupAuthForm } from './auth.js';
import { setupPosts } from './posts.js';

const page = document.body.dataset.page;
const content = document.querySelector('#page-content');
const screen = document.querySelector('#session-screen');
const status = document.querySelector('#session-status');
const retryButton = document.querySelector('#retry-session');
let postsUI = null;
let cleanupAuthForm = null;
let sessionCheck = 0;

function hideContent() {
  content.hidden = true;
  postsUI?.clear();
  postsUI = null;
  cleanupAuthForm?.();
  cleanupAuthForm = null;
  for (const selector of ['#current-username', '#current-email', '#logout-message', '#form-message']) {
    const element = document.querySelector(selector);
    if (element) element.textContent = '';
  }
  document.querySelector('#auth-form')?.reset();
  screen.hidden = false;
  screen.setAttribute('aria-busy', 'true');
  status.textContent = 'Sprawdzanie sesji…';
  retryButton.hidden = true;
}

function redirect(path) {
  sessionCheck += 1;
  hideContent();
  window.location.replace(path);
}

function showContent() {
  screen.hidden = true;
  screen.setAttribute('aria-busy', 'false');
  content.hidden = false;
}

async function checkSession() {
  const check = ++sessionCheck;
  hideContent();
  let user;
  try {
    user = await getCurrentUser();
  } catch (error) {
    if (check !== sessionCheck) return;
    if (error.status !== 401) {
      screen.setAttribute('aria-busy', 'false');
      status.textContent = error.message;
      retryButton.hidden = false;
      return;
    }
    if (page === 'feed') {
      redirect('/login');
      return;
    }
    cleanupAuthForm = setupAuthForm();
    showContent();
    return;
  }
  if (check !== sessionCheck) return;
  if (page !== 'feed') {
    redirect('/feed');
    return;
  }
  document.querySelector('#current-username').textContent = user.username;
  document.querySelector('#current-email').textContent = user.email;
  postsUI = setupPosts({ user, onUnauthorized: () => redirect('/login?expired=1') });
  showContent();
}

retryButton.addEventListener('click', checkSession);
document.querySelector('#logout')?.addEventListener('click', async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await logout();
    redirect('/');
  } catch (error) {
    document.querySelector('#logout-message').textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

window.addEventListener('pagehide', () => {
  sessionCheck += 1;
  hideContent();
});
window.addEventListener('pageshow', (event) => { if (event.persisted) checkSession(); });
checkSession();
