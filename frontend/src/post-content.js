const POST_LIMIT = 280;

export const dateFormat = new Intl.DateTimeFormat('pl-PL', { dateStyle: 'medium', timeStyle: 'short' });
export const deleteIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16m-10 4v6m4-6v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>';

export function remainingCharacters(text) {
  const trimmed = text.replace(/^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu, '');
  return POST_LIMIT - [...trimmed].length;
}

export function updateCharacterCounter(input, counter) {
  const remaining = remainingCharacters(input.value);
  counter.textContent = String(remaining);
  counter.dataset.kind = remaining < 20 ? 'error' : '';
  input.setAttribute('aria-invalid', String(remaining < 0));
  return remaining;
}

export function canPublish(remaining) {
  return remaining >= 0 && remaining < POST_LIMIT;
}

export function showMessage(element, text, kind = '') {
  element.textContent = text;
  element.dataset.kind = kind;
}