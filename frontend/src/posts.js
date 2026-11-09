import { apiRequest } from './api.js';

const PAGE_SIZE = 20;
const dateFormat = new Intl.DateTimeFormat('pl-PL', { dateStyle: 'medium', timeStyle: 'short' });

export function setupPosts({ user, onUnauthorized } = {}) {
  const feed = document.querySelector('#feed');
  if (!feed || !user) return null;

  const list = document.querySelector('#post-list');
  const feedMessage = document.querySelector('#feed-message');
  const actionMessage = document.querySelector('#post-action-message');
  const composer = document.querySelector('#post-composer');
  const form = document.querySelector('#post-form');
  const content = document.querySelector('#post-content');
  const counter = document.querySelector('#post-counter');
  const publishButton = document.querySelector('#publish-post');
  const previousButton = document.querySelector('#previous-page');
  const nextButton = document.querySelector('#next-page');
  const refreshButton = document.querySelector('#refresh-posts');
  const pageLabel = document.querySelector('#page-label');

  let currentUser = user;
  let posts = [];
  let offset = 0;
  let hasNext = false;
  let busy = false;
  let disposed = false;
  const events = new AbortController();

  function showMessage(element, text, kind = '') {
    element.textContent = text;
    element.dataset.kind = kind;
  }

  function updateControls() {
    const length = [...content.value.trim()].length;
    counter.textContent = `${length}/280`;
    counter.dataset.kind = length > 280 ? 'error' : '';
    publishButton.disabled = busy || !currentUser || length === 0 || length > 280;
    content.disabled = busy;
    previousButton.disabled = busy || offset === 0;
    nextButton.disabled = busy || !hasNext;
    refreshButton.disabled = busy;
    pageLabel.textContent = `Strona ${Math.floor(offset / PAGE_SIZE) + 1}`;
    list.setAttribute('aria-busy', String(busy));
    for (const button of list.querySelectorAll('button')) button.disabled = busy;
  }

  function setBusy(value) {
    busy = value;
    updateControls();
  }

  function renderPosts() {
    const fragment = document.createDocumentFragment();
    for (const post of posts) {
      const article = document.createElement('article');
      article.className = 'post-card';
      article.dataset.postId = String(post.id);

      const header = document.createElement('header');
      header.className = 'flex flex-wrap items-start justify-between gap-3';
      const author = document.createElement('h3');
      author.className = 'min-w-0 font-semibold text-sky-200 [overflow-wrap:anywhere]';
      author.textContent = `@${post.author.username}`;
      const date = document.createElement('time');
      date.className = 'text-xs leading-6 text-slate-400';
      date.dateTime = post.created_at;
      date.textContent = dateFormat.format(new Date(post.created_at));
      header.append(author, date);

      const text = document.createElement('p');
      text.className = 'post-text';
      text.textContent = post.content;
      article.append(header, text);

      if (currentUser && currentUser.id === post.author.id) {
        const deleteButton = document.createElement('button');
        deleteButton.type = 'button';
        deleteButton.className = 'button-secondary mt-4 text-sm';
        deleteButton.textContent = 'Usuń post';
        deleteButton.disabled = busy;
        deleteButton.addEventListener('click', () => removePost(post.id, deleteButton));
        article.append(deleteButton);
      }
      fragment.append(article);
    }
    list.replaceChildren(fragment);
  }

  async function loadPage(newOffset = offset) {
    if (disposed) return;
    setBusy(true);
    showMessage(feedMessage, 'Ładowanie postów…');
    try {
      const result = await apiRequest(`/posts?limit=${PAGE_SIZE + 1}&offset=${newOffset}`);
      if (disposed) return;
      posts = result.slice(0, PAGE_SIZE);
      hasNext = result.length > PAGE_SIZE;
      offset = newOffset;
      renderPosts();
      showMessage(feedMessage, posts.length ? '' : (offset === 0 ? 'Nie ma jeszcze postów. Opublikuj pierwszy wpis!' : 'Nie ma kolejnych postów. Wróć na poprzednią stronę.'));
    } catch (error) {
      if (disposed) return;
      if (error.status === 401) { onUnauthorized?.(); return; }
      showMessage(feedMessage, error.message, 'error');
    } finally {
      if (!disposed) setBusy(false);
    }
  }

  async function removePost(postId, button) {
    if (disposed || busy || !currentUser) return;
    setBusy(true);
    button.textContent = 'Usuwanie…';
    showMessage(actionMessage, 'Usuwanie posta…');
    try {
      await apiRequest(`/posts/${postId}`, { method: 'DELETE' });
      if (disposed) return;
      showMessage(actionMessage, 'Post został usunięty.', 'success');
      await loadPage(0);
    } catch (error) {
      if (disposed) return;
      if (error.status === 401) { onUnauthorized?.(); return; }
      showMessage(actionMessage, error.message, 'error');
      if (error.status === 404) await loadPage(0);
    } finally {
      button.textContent = 'Usuń post';
      if (!disposed) setBusy(false);
    }
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const length = [...content.value.trim()].length;
    if (disposed || busy || !currentUser || length === 0 || length > 280) return;
    setBusy(true);
    publishButton.textContent = 'Publikowanie…';
    showMessage(actionMessage, 'Publikowanie posta…');
    try {
      await apiRequest('/posts', { method: 'POST', body: { content: content.value } });
      if (disposed) return;
      content.value = '';
      showMessage(actionMessage, 'Post został opublikowany.', 'success');
      await loadPage(0);
    } catch (error) {
      if (disposed) return;
      if (error.status === 401) { onUnauthorized?.(); return; }
      showMessage(actionMessage, error.message, 'error');
    } finally {
      publishButton.textContent = 'Opublikuj';
      if (!disposed) setBusy(false);
    }
  }, { signal: events.signal });

  content.addEventListener('input', updateControls, { signal: events.signal });
  previousButton.addEventListener('click', () => { if (!busy) loadPage(Math.max(0, offset - PAGE_SIZE)); }, { signal: events.signal });
  nextButton.addEventListener('click', () => { if (!busy) loadPage(offset + PAGE_SIZE); }, { signal: events.signal });
  refreshButton.addEventListener('click', () => { if (!busy) loadPage(0); }, { signal: events.signal });
  composer.hidden = false;
  updateControls();
  loadPage(0);

  return {
    clear() {
      disposed = true;
      events.abort();
      currentUser = null;
      posts = [];
      content.value = '';
      counter.textContent = '0/280';
      list.replaceChildren();
      actionMessage.textContent = '';
      feedMessage.textContent = '';
      composer.hidden = true;
    },
  };
}