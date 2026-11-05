import './style.css';
import { checkHealth } from './api.js';
import { getCurrentUser, logout, setupAuthForm } from './auth.js';

setupAuthForm();

const checkButton = document.querySelector('#check-health');
const statusMessage = document.querySelector('#health-status');

checkButton?.addEventListener('click', async () => {
  checkButton.disabled = true;
  checkButton.textContent = 'Sprawdzanie…';
  statusMessage.textContent = 'Łączenie z API i bazą danych…';

  try {
    const data = await checkHealth();
    statusMessage.textContent = data.message;
  } catch (error) {
    statusMessage.textContent = error.message;
  } finally {
    checkButton.disabled = false;
    checkButton.textContent = 'Sprawdź połączenie';
  }
});

const guestPanel = document.querySelector('#guest-panel');
const userPanel = document.querySelector('#user-panel');
const sessionMessage = document.querySelector('#session-message');

function showGuest() {
  guestPanel.hidden = false;
  userPanel.hidden = true;
  document.querySelector('#current-username').textContent = '';
  document.querySelector('#current-email').textContent = '';
}

async function loadSession() {
  try {
    const user = await getCurrentUser();
    guestPanel.hidden = true;
    userPanel.hidden = false;
    document.querySelector('#current-username').textContent = user.username;
    document.querySelector('#current-email').textContent = user.email;
    sessionMessage.textContent = '';
  } catch (error) {
    if (error.status === 401) {
      showGuest();
      sessionMessage.textContent = 'Zaloguj się lub utwórz konto.';
    } else {
      sessionMessage.dataset.kind = 'error';
      sessionMessage.textContent = error.message;
    }
  }
}

if (sessionMessage) {
  loadSession();
  document.querySelector('#logout').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const result = await logout();
      showGuest();
      sessionMessage.dataset.kind = 'success';
      sessionMessage.textContent = result.message;
    } catch (error) {
      sessionMessage.dataset.kind = 'error';
      sessionMessage.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
}