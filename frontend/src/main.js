import { checkHealth } from './api.js';

const checkButton = document.querySelector('#check-health');
const statusMessage = document.querySelector('#health-status');

checkButton.addEventListener('click', async () => {
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
