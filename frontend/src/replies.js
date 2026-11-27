import { apiRequest } from './api.js';
import { canPublish, dateFormat, deleteIcon, remainingCharacters, showMessage, updateCharacterCounter } from './post-content.js';

const PAGE_SIZE = 20;
const replyIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a7 7 0 0 1-7 7H9l-5 3v-5a7 7 0 0 1 5-12h4a7 7 0 0 1 7 7Z"/></svg>';

export function createReplyThread(post, isActive, onUnauthorized) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'reply-toggle';
  button.innerHTML = `${replyIcon}<span aria-hidden="true"></span>`;
  button.setAttribute('aria-expanded', 'false');

  const panel = document.createElement('section');
  panel.className = 'reply-thread';
  panel.id = `replies-${post.id}`;
  panel.hidden = true;
  panel.setAttribute('aria-label', 'Odpowiedzi');
  button.setAttribute('aria-controls', panel.id);
  let open;

  function updateTotal(total) {
    post.replies_count = total;
    button.querySelector('span').textContent = String(total);
    button.setAttribute('aria-label', `Odpowiedzi: ${total}`);
  }

  updateTotal(post.replies_count ?? 0);
  button.addEventListener('click', () => {
    panel.hidden = !panel.hidden;
    button.setAttribute('aria-expanded', String(!panel.hidden));
    if (panel.hidden) return;
    open ??= setupReplyPanel(panel, post, updateTotal, isActive, onUnauthorized);
    open();
  });
  return { button, panel };
}

function setupReplyPanel(panel, post, updateTotal, isActive, onUnauthorized) {
  panel.append(document.querySelector('#reply-thread-template').content.cloneNode(true));
  const list = panel.querySelector('.reply-list');
  const listMessage = panel.querySelector('.reply-list-message');
  const navigation = panel.querySelector('.reply-pages');
  const previous = panel.querySelector('.reply-previous');
  const next = panel.querySelector('.reply-next');
  const retry = panel.querySelector('.reply-retry');
  const form = panel.querySelector('.reply-form');
  const field = form.querySelector('textarea');
  const counter = form.querySelector('.reply-counter');
  const publish = form.querySelector('button');
  const formMessage = form.querySelector('.message');
  const path = `/posts/${post.id}/replies`;
  counter.parentElement.id = `reply-counter-${post.id}`;
  field.setAttribute('aria-describedby', counter.parentElement.id);
  let offset = 0;
  let requestedOffset = 0;
  let loading = false;
  let saving = false;
  let loaded = false;

  function updateControls() {
    const remaining = updateCharacterCounter(field, counter);
    const busy = loading || saving;
    field.disabled = saving;
    publish.disabled = busy || !canPublish(remaining);
    navigation.hidden = post.replies_count <= PAGE_SIZE;
    previous.disabled = busy || offset === 0;
    next.disabled = busy || offset + PAGE_SIZE >= post.replies_count;
    retry.disabled = busy;
    list.setAttribute('aria-busy', String(loading));
    for (const button of list.querySelectorAll('button')) button.disabled = busy;
  }

  function displayError(element, error) {
    if (!isActive()) return;
    if (error.status === 401) onUnauthorized?.();
    else showMessage(element, error.message, 'error');
  }

  function renderReply(reply) {
    const item = document.createElement('article');
    item.className = 'reply-item';
    item.dataset.replyId = String(reply.id);
    const avatar = document.createElement('span');
    avatar.className = 'avatar reply-avatar';
    avatar.setAttribute('aria-hidden', 'true');
    avatar.textContent = reply.author.username.charAt(0).toUpperCase();
    const body = document.createElement('div');
    body.className = 'reply-body';
    const meta = document.createElement('header');
    meta.className = 'reply-meta';
    const author = document.createElement('strong');
    author.textContent = `@${reply.author.username}`;
    const time = document.createElement('time');
    time.dateTime = reply.created_at;
    time.textContent = dateFormat.format(new Date(reply.created_at));
    meta.append(author, time);
    const content = document.createElement('p');
    content.className = 'reply-content';
    content.textContent = reply.content;
    body.append(meta, content);
    item.append(avatar, body);
    if (reply.is_owner) {
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'feed-icon-button delete-button';
      remove.setAttribute('aria-label', 'Usuń odpowiedź');
      remove.title = 'Usuń odpowiedź';
      remove.innerHTML = deleteIcon;
      remove.addEventListener('click', () => deleteReply(reply.id));
      item.append(remove);
    }
    return item;
  }

  async function loadPage(pageOffset) {
    requestedOffset = pageOffset;
    loading = true;
    retry.hidden = true;
    showMessage(listMessage, 'Ładowanie odpowiedzi…');
    updateControls();
    try {
      const read = (start) => apiRequest(`${path}?limit=${PAGE_SIZE}&offset=${start}`);
      let result = await read(pageOffset);
      if (!isActive()) return;
      if (!result.items.length && pageOffset > 0) {
        pageOffset = Math.max(0, Math.floor((result.total - 1) / PAGE_SIZE) * PAGE_SIZE);
        if (result.total) result = await read(pageOffset);
      }
      if (!isActive()) return;
      offset = pageOffset;
      loaded = true;
      updateTotal(result.total);
      list.replaceChildren(...result.items.map(renderReply));
      showMessage(listMessage, result.total ? '' : 'Brak odpowiedzi.');
    } catch (error) {
      displayError(listMessage, error);
      retry.hidden = error.status === 401;
    } finally {
      loading = false;
      if (isActive()) updateControls();
    }
  }

  async function deleteReply(replyId) {
    if (loading || saving) return;
    saving = true;
    updateControls();
    try {
      await apiRequest(`${path}/${replyId}`, { method: 'DELETE' });
      if (!isActive()) return;
      list.querySelector(`[data-reply-id="${replyId}"]`)?.remove();
      updateTotal(Math.max(0, post.replies_count - 1));
      await loadPage(offset);
    } catch (error) {
      displayError(listMessage, error);
    } finally {
      saving = false;
      if (isActive()) updateControls();
    }
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (loading || saving || !canPublish(remainingCharacters(field.value))) return;
    saving = true;
    updateControls();
    showMessage(formMessage, 'Sprawdzanie treści...');
    try {
      await apiRequest(path, { method: 'POST', body: { content: field.value }, timeout: 30000 });
      if (!isActive()) return;
      field.value = '';
      const lastPage = Math.floor(post.replies_count / PAGE_SIZE) * PAGE_SIZE;
      updateTotal(post.replies_count + 1);
      showMessage(formMessage, 'Odpowiedź opublikowana.', 'success');
      await loadPage(lastPage);
    } catch (error) {
      displayError(formMessage, error);
    } finally {
      saving = false;
      if (isActive()) updateControls();
    }
  });

  field.addEventListener('input', () => {
    showMessage(formMessage, '');
    updateControls();
  });
  previous.addEventListener('click', () => loadPage(offset - PAGE_SIZE));
  next.addEventListener('click', () => loadPage(offset + PAGE_SIZE));
  retry.addEventListener('click', () => loadPage(requestedOffset));
  return () => { if (!loaded && !loading) loadPage(0); };
}