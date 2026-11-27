import { apiRequest } from './api.js';
import { canPublish, dateFormat, deleteIcon, remainingCharacters, showMessage, updateCharacterCounter } from './post-content.js';
import { createReplyThread } from './replies.js';

const PAGE_SIZE = 20;
const SUCCESS_MESSAGE_DURATION = 3000;
const previewSegments = new Intl.Segmenter('pl', { granularity: 'grapheme' });
const heartIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s-9-5.5-9-12a5 5 0 0 1 9-3 5 5 0 0 1 9 3c0 6.5-9 12-9 12Z"/></svg>';

function postAuthorName(post) {
  return post.is_anonymous ? 'Gall Anonim' : `@${post.author.username}`;
}

function trendingExcerpt(content) {
  const characters = Array.from(previewSegments.segment(content), ({ segment }) => segment);
  return characters.length > 40 ? `${characters.slice(0, 40).join('')}…` : content;
}

export function setupPosts({ user, onUnauthorized } = {}) {
  const feed = document.querySelector('#feed');
  if (!feed || !user) return null;

  const list = document.querySelector('#post-list');
  const feedMessage = document.querySelector('#feed-message');
  const actionMessage = document.querySelector('#post-action-message');
  const composer = document.querySelector('#post-composer');
  const form = document.querySelector('#post-form');
  const formMessage = document.querySelector('#post-form-message');
  const content = document.querySelector('#post-content');
  const anonymous = document.querySelector('#post-anonymous');
  const counter = document.querySelector('#post-counter');
  const publishButton = document.querySelector('#publish-post');
  const previousButton = document.querySelector('#previous-page');
  const nextButton = document.querySelector('#next-page');
  const refreshButton = document.querySelector('#refresh-posts');
  const pageLabel = document.querySelector('#page-label');
  const pagination = document.querySelector('#feed-pagination');
  const feedTitle = document.querySelector('#feed-title');
  const feedHeading = document.querySelector('#feed-heading');
  const forYouTab = document.querySelector('#for-you-tab');
  const trendingTab = document.querySelector('#trending-tab');
  const tabs = [forYouTab, trendingTab];
  const homeNav = document.querySelector('#nav-home');
  const newPost = document.querySelector('#new-post');
  const accountAvatar = document.querySelector('#account-avatar');
  const composerAvatar = document.querySelector('#composer-avatar');
  const previewList = document.querySelector('#trending-preview-list');
  const previewMessage = document.querySelector('#trending-preview-message');
  const previewMedia = globalThis.matchMedia?.('(min-width: 75rem)');
  const userInitial = user.username.charAt(0).toUpperCase();
  accountAvatar.textContent = userInitial;

  let currentUser = user;
  let activeTab = 'feed';
  let posts = [];
  let postActionButtons = [];
  let offset = 0;
  let hasNext = false;
  let busy = false;
  let mutationPending = false;
  let feedRequest = 0;
  let previewRequest = 0;
  let disposed = false;
  let successMessageTimer;
  const events = new AbortController();

  function showFormMessage(text, kind = '') {
    clearTimeout(successMessageTimer);
    successMessageTimer = undefined;
    showMessage(formMessage, text, kind);
    if (kind === 'success') {
      successMessageTimer = setTimeout(() => showFormMessage(''), SUCCESS_MESSAGE_DURATION);
    }
  }

  function updateControls() {
    const remaining = updateCharacterCounter(content, counter);
    publishButton.disabled = busy || !currentUser || !canPublish(remaining);
    content.disabled = busy;
    anonymous.disabled = busy;
    composerAvatar.textContent = anonymous.checked ? '?' : userInitial;
    composerAvatar.className = anonymous.checked ? 'avatar avatar-anonymous' : 'avatar';
    previousButton.disabled = busy || offset === 0;
    nextButton.disabled = busy || !hasNext;
    refreshButton.disabled = busy;
    pageLabel.textContent = `Strona ${Math.floor(offset / PAGE_SIZE) + 1}`;
    const trending = activeTab === 'trending';
    composer.hidden = trending;
    pagination.hidden = trending;
    feedTitle.textContent = trending ? 'Popularne' : 'Strona główna';
    feedHeading.hidden = !trending;
    const refreshLabel = trending ? 'Odśwież ranking' : 'Odśwież feed';
    refreshButton.setAttribute('aria-label', refreshLabel);
    refreshButton.title = refreshLabel;
    forYouTab.setAttribute('aria-selected', String(!trending));
    trendingTab.setAttribute('aria-selected', String(trending));
    forYouTab.tabIndex = trending ? -1 : 0;
    trendingTab.tabIndex = trending ? 0 : -1;
    feed.setAttribute('aria-labelledby', trending ? 'trending-tab' : 'for-you-tab');
    homeNav.setAttribute('aria-current', String(!trending));
    for (const button of [...tabs, homeNav]) button.disabled = mutationPending;
    newPost.disabled = mutationPending || (busy && !trending);
    list.setAttribute('aria-busy', String(busy));
    for (const button of postActionButtons) button.disabled = busy;
  }

  function setBusy(value) {
    busy = value;
    updateControls();
  }

  function updateLikeButton(button, post) {
    button.setAttribute('aria-pressed', String(post.liked_by_me));
    button.setAttribute('aria-label', `${post.liked_by_me ? 'Cofnij polubienie' : 'Polub post'}, liczba polubień ${post.likes_count}`);
    button.querySelector('span').textContent = String(post.likes_count);
  }

  function renderPosts() {
    const fragment = document.createDocumentFragment();
    const trending = activeTab === 'trending';
    postActionButtons = [];
    for (const [index, post] of posts.entries()) {
      const article = document.createElement('article');
      article.className = 'post-card';
      article.dataset.postId = String(post.id);

      const header = document.createElement('header');
      header.className = 'post-meta';
      const author = document.createElement('h3');
      author.className = 'post-author';
      const authorName = postAuthorName(post);
      author.textContent = trending ? `${index + 1}. ${authorName}` : authorName;
      const date = document.createElement('time');
      date.dateTime = post.created_at;
      date.textContent = dateFormat.format(new Date(post.created_at));
      const avatar = document.createElement('span');
      avatar.className = post.is_anonymous ? 'avatar avatar-anonymous' : 'avatar';
      avatar.setAttribute('aria-hidden', 'true');
      avatar.textContent = post.is_anonymous ? '?' : post.author.username.charAt(0).toUpperCase();
      header.append(author, date, avatar);

      const text = document.createElement('p');
      text.className = 'post-text';
      text.textContent = trending ? trendingExcerpt(post.content) : post.content;
      article.append(header, text);

      const actions = document.createElement('div');
      actions.className = 'post-actions';
      const reactions = document.createElement('div');
      reactions.className = 'post-reactions';
      const likeButton = document.createElement('button');
      likeButton.type = 'button';
      likeButton.className = 'like-button';
      likeButton.disabled = busy;
      likeButton.innerHTML = `${heartIcon}<span aria-hidden="true"></span>`;
      updateLikeButton(likeButton, post);
      likeButton.addEventListener('click', () => toggleLike(post, likeButton));
      const thread = createReplyThread(post, () => !disposed && article.isConnected, onUnauthorized);
      reactions.append(likeButton, thread.button);
      actions.append(reactions);
      postActionButtons.push(likeButton, thread.button);

      if (post.is_owner) {
        const deleteButton = document.createElement('button');
        deleteButton.type = 'button';
        deleteButton.className = 'feed-icon-button delete-button';
        deleteButton.setAttribute('aria-label', 'Usuń post');
        deleteButton.title = 'Usuń post';
        deleteButton.innerHTML = deleteIcon;
        deleteButton.disabled = busy;
        deleteButton.addEventListener('click', () => removePost(post.id, deleteButton));
        actions.append(deleteButton);
        postActionButtons.push(deleteButton);
      }
      article.append(actions, thread.panel);
      fragment.append(article);
    }
    list.replaceChildren(fragment);
  }

  function renderTrendingPreview(rankedPosts) {
    const fragment = document.createDocumentFragment();
    for (const post of rankedPosts.slice(0, 3)) {
      const item = document.createElement('li');
      const author = document.createElement('p');
      author.className = 'preview-author';
      author.textContent = postAuthorName(post);
      const text = document.createElement('p');
      text.className = 'preview-text';
      text.textContent = trendingExcerpt(post.content);
      const likes = document.createElement('p');
      likes.className = 'preview-likes';
      likes.innerHTML = heartIcon;
      const label = document.createElement('span');
      label.className = 'sr-only';
      label.textContent = 'Liczba polubień: ';
      const count = document.createElement('span');
      count.textContent = String(post.likes_count);
      likes.append(label, count);
      item.append(author, text, likes);
      fragment.append(item);
    }
    previewList.replaceChildren(fragment);
    showMessage(previewMessage, rankedPosts.length ? '' : 'W ostatnich 24 godzinach nie pojawił się żaden wpis.');
  }

  async function loadTrendingPreview() {
    if (disposed || !previewMedia?.matches) return;
    const request = ++previewRequest;
    if (!previewList.children.length) showMessage(previewMessage, 'Ładowanie rankingu…');
    try {
      const result = await apiRequest('/posts/trending');
      if (!disposed && request === previewRequest) renderTrendingPreview(result);
    } catch {
      if (disposed || request !== previewRequest) return;
      previewList.replaceChildren();
      showMessage(previewMessage, 'Ranking jest chwilowo niedostępny.', 'error');
    }
  }

  async function loadPage(newOffset = offset) {
    if (disposed) return;
    const request = ++feedRequest;
    const trending = activeTab === 'trending';
    if (trending) previewRequest += 1;
    setBusy(true);
    posts = [];
    postActionButtons = [];
    list.replaceChildren();
    showMessage(feedMessage, trending ? 'Ładowanie rankingu…' : 'Ładowanie postów…');
    try {
      const path = trending ? '/posts/trending' : `/posts?limit=${PAGE_SIZE + 1}&offset=${newOffset}`;
      const loading = apiRequest(path);
      if (!trending && newOffset === 0) loadTrendingPreview();
      const result = await loading;
      if (disposed || request !== feedRequest) return;
      posts = trending ? result : result.slice(0, PAGE_SIZE);
      hasNext = !trending && result.length > PAGE_SIZE;
      offset = newOffset;
      renderPosts();
      if (trending && previewMedia?.matches) renderTrendingPreview(result);
      let emptyMessage = 'Nie ma jeszcze postów. Opublikuj pierwszy wpis!';
      if (trending) emptyMessage = 'Nie ma postów z ostatnich 24 godzin.';
      else if (offset > 0) emptyMessage = 'Nie ma kolejnych postów. Wróć na poprzednią stronę.';
      showMessage(feedMessage, posts.length ? '' : emptyMessage);
    } catch (error) {
      if (disposed || request !== feedRequest) return;
      if (error.status === 401) { onUnauthorized?.(); return; }
      showMessage(feedMessage, error.message, 'error');
      if (trending && previewMedia?.matches) {
        previewList.replaceChildren();
        showMessage(previewMessage, 'Ranking jest chwilowo niedostępny.', 'error');
      }
    } finally {
      if (!disposed && request === feedRequest) setBusy(false);
    }
  }

  async function switchTab(tab) {
    if (disposed || mutationPending || activeTab === tab) return;
    activeTab = tab;
    offset = 0;
    hasNext = false;
    showMessage(actionMessage, '');
    await loadPage(0);
  }

  async function toggleLike(post, button) {
    if (disposed || busy || !currentUser) return;
    mutationPending = true;
    setBusy(true);
    showMessage(actionMessage, 'Zapisywanie polubienia…');
    try {
      const result = await apiRequest(`/posts/${post.id}/like`, { method: post.liked_by_me ? 'DELETE' : 'POST' });
      if (disposed) return;
      post.likes_count = result.likes_count;
      post.liked_by_me = result.liked_by_me;
      updateLikeButton(button, post);
      showMessage(actionMessage, post.liked_by_me ? 'Polubiono post.' : 'Cofnięto polubienie.', 'success');
      if (activeTab === 'trending') await loadPage(0);
      else loadTrendingPreview();
    } catch (error) {
      if (disposed) return;
      if (error.status === 401) { onUnauthorized?.(); return; }
      showMessage(actionMessage, error.message, 'error');
      if (error.status === 404) await loadPage(0);
    } finally {
      mutationPending = false;
      if (!disposed) setBusy(false);
    }
  }

  async function removePost(postId, button) {
    if (disposed || busy || !currentUser) return;
    mutationPending = true;
    setBusy(true);
    button.setAttribute('aria-label', 'Usuwanie posta…');
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
      mutationPending = false;
      button.setAttribute('aria-label', 'Usuń post');
      if (!disposed) setBusy(false);
    }
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (disposed || busy || !currentUser || activeTab !== 'feed' || !canPublish(remainingCharacters(content.value))) return;
    mutationPending = true;
    setBusy(true);
    showFormMessage('Sprawdzanie treści...', 'pending');
    try {
      await apiRequest('/posts', {
        method: 'POST',
        body: { content: content.value, is_anonymous: anonymous.checked },
        timeout: 30000,
      });
      if (disposed) return;
      content.value = '';
      anonymous.checked = false;
      showFormMessage('Post został opublikowany.', 'success');
      await loadPage(0);
    } catch (error) {
      if (disposed) return;
      if (error.status === 401) { onUnauthorized?.(); return; }
      showFormMessage(error.message, 'error');
    } finally {
      mutationPending = false;
      if (!disposed) setBusy(false);
    }
  }, { signal: events.signal });

  content.addEventListener('input', () => {
    if (formMessage.dataset.kind === 'success' || formMessage.dataset.kind === 'error') showFormMessage('');
    updateControls();
  }, { signal: events.signal });
  anonymous.addEventListener('change', updateControls, { signal: events.signal });
  previousButton.addEventListener('click', () => { if (!busy) loadPage(Math.max(0, offset - PAGE_SIZE)); }, { signal: events.signal });
  nextButton.addEventListener('click', () => { if (!busy) loadPage(offset + PAGE_SIZE); }, { signal: events.signal });
  refreshButton.addEventListener('click', () => { if (!busy) loadPage(0); }, { signal: events.signal });
  forYouTab.addEventListener('click', () => switchTab('feed'), { signal: events.signal });
  trendingTab.addEventListener('click', () => switchTab('trending'), { signal: events.signal });
  homeNav.addEventListener('click', () => {
    globalThis.scrollTo(0, 0);
    switchTab('feed');
  }, { signal: events.signal });
  newPost.addEventListener('click', async () => {
    if (disposed || mutationPending) return;
    if (activeTab !== 'feed') await switchTab('feed');
    if (disposed || activeTab !== 'feed' || content.disabled) return;
    composer.scrollIntoView({ block: 'start' });
    content.focus({ preventScroll: true });
  }, { signal: events.signal });
  previewMedia?.addEventListener('change', () => {
    if (!previewMedia.matches) previewRequest += 1;
    else if (activeTab === 'feed') loadTrendingPreview();
    else if (!busy) renderTrendingPreview(posts);
  }, { signal: events.signal });
  for (const tab of tabs) {
    tab.addEventListener('keydown', (event) => {
      if (mutationPending || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      let target = tabs.find((item) => item !== tab);
      if (event.key === 'Home') target = forYouTab;
      if (event.key === 'End') target = trendingTab;
      target.focus();
      switchTab(target === forYouTab ? 'feed' : 'trending');
    }, { signal: events.signal });
  }
  updateControls();
  loadPage(0);

  return {
    clear() {
      disposed = true;
      events.abort();
      currentUser = null;
      posts = [];
      postActionButtons = [];
      content.value = '';
      anonymous.checked = false;
      accountAvatar.textContent = '';
      composerAvatar.textContent = '';
      updateCharacterCounter(content, counter);
      list.replaceChildren();
      previewList.replaceChildren();
      previewMessage.textContent = '';
      actionMessage.textContent = '';
      showFormMessage('');
      feedMessage.textContent = '';
      composer.hidden = true;
    },
  };
}