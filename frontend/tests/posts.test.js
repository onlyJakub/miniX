import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setupPosts } from '../src/posts.js';

class Element extends EventTarget {
  constructor(tagName = 'div') {
    super();
    this.tagName = tagName;
    this.children = [];
    this.attributes = new Map();
    this.dataset = {};
    this.value = '';
    this.textContent = '';
    this.disabled = false;
    this.hidden = false;
    this.checked = false;
  }

  setAttribute(name, value) { this.attributes.set(name, value); }
  getAttribute(name) { return this.attributes.get(name); }
  set innerHTML(value) {
    this.children = [new Element('svg')];
    if (value.includes('<span')) this.children.push(new Element('span'));
  }
  append(...children) {
    for (const child of children) {
      if (child.tagName === 'fragment') this.children.push(...child.children);
      else this.children.push(child);
    }
  }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  querySelectorAll(tagName) {
    return this.children.flatMap((child) => [
      ...(child.tagName === tagName ? [child] : []), ...child.querySelectorAll(tagName),
    ]);
  }
  querySelector(tagName) { return this.querySelectorAll(tagName)[0]; }
  click() { if (!this.disabled) this.dispatchEvent(new Event('click')); }
  focus() { this.focused = true; }
  scrollIntoView(options) { this.scrollOptions = options; }
}

const post = (id, content = `Post ${id}`) => ({
  id, content, created_at: '2026-11-13T08:00:00Z', author: { id: 1, username: 'tester' },
  is_anonymous: false, is_owner: true,
  likes_count: 0, liked_by_me: false,
});
const settle = () => new Promise((resolve) => setImmediate(resolve));

function mount(t, { desktop = false } = {}) {
  const originalDocument = globalThis.document;
  const originalFetch = globalThis.fetch;
  const originalMatchMedia = globalThis.matchMedia;
  const originalScrollTo = globalThis.scrollTo;
  const elements = new Map();
  const requests = [];
  let unauthorized = 0;
  const media = new EventTarget();
  media.matches = desktop;
  globalThis.matchMedia = () => media;
  globalThis.scrollTo = () => {};
  globalThis.document = {
    querySelector(selector) {
      if (!elements.has(selector)) elements.set(selector, new Element());
      return elements.get(selector);
    },
    createElement: (tagName) => new Element(tagName),
    createDocumentFragment: () => new Element('fragment'),
  };
  globalThis.fetch = (url, options) => new Promise((resolve, reject) => {
    requests.push({
      url, options, reject,
      resolve: (data, status = 200) => resolve(status === 204 ? new Response(null, { status }) : Response.json(data, { status })),
    });
  });
  const ui = setupPosts({ user: { id: 1, username: 'tester' }, onUnauthorized: () => { unauthorized += 1; } });
  t.after(() => {
    ui.clear();
    globalThis.document = originalDocument;
    globalThis.fetch = originalFetch;
    globalThis.matchMedia = originalMatchMedia;
    globalThis.scrollTo = originalScrollTo;
  });
  return { elements, requests, ui, media, unauthorized: () => unauthorized };
}

test('rapid tab switches ignore delayed data and errors from previous requests', async (t) => {
  const { elements, requests } = mount(t);
  elements.get('#trending-tab').click();
  elements.get('#for-you-tab').click();
  assert.deepEqual(requests.map((request) => request.url), [
    '/api/posts?limit=21&offset=0', '/api/posts/trending', '/api/posts?limit=21&offset=0',
  ]);
  requests[2].resolve([post(3)]);
  await settle();
  requests[1].resolve([post(2)]);
  requests[0].reject(new Error('Stare żądanie'));
  await settle();
  const list = elements.get('#post-list');
  assert.equal(list.children[0].dataset.postId, '3');
  assert.equal(list.getAttribute('aria-busy'), 'false');
  assert.equal(elements.get('#feed-message').textContent, '');
  assert.equal(elements.get('#post-composer').hidden, false);
  assert.equal(elements.get('#feed-pagination').hidden, false);
});

test('a stale response cannot unlock controls while the current tab is loading', async (t) => {
  const { elements, requests } = mount(t);
  elements.get('#trending-tab').click();
  requests[0].resolve([post(1)]);
  await settle();
  assert.equal(elements.get('#post-list').children.length, 0);
  assert.equal(elements.get('#post-list').getAttribute('aria-busy'), 'true');
  assert.equal(elements.get('#refresh-posts').disabled, true);
  requests[1].resolve([]);
  await settle();
  assert.equal(elements.get('#feed-message').textContent, 'Nie ma postów z ostatnich 24 godzin.');
  assert.equal(elements.get('#refresh-posts').disabled, false);
});

test('trending truncates to forty visible characters without splitting emoji and hides composer and pagination', async (t) => {
  const { elements, requests } = mount(t);
  requests[0].resolve([]);
  await settle();
  elements.get('#post-content').value = 'Zachowany szkic';
  elements.get('#trending-tab').click();
  requests[1].resolve([post(1, 'ą'.repeat(39) + '👩‍🔬dalsza treść'), post(2, '<b>Tekst</b>')]);
  await settle();
  const cards = elements.get('#post-list').children;
  assert.equal(cards[0].children[0].children[0].textContent, '1. @tester');
  assert.equal(cards[0].children[1].textContent, 'ą'.repeat(39) + '👩‍🔬…');
  assert.equal(cards[1].children[1].textContent, '<b>Tekst</b>');
  assert.equal(elements.get('#post-composer').hidden, true);
  assert.equal(elements.get('#feed-pagination').hidden, true);
  elements.get('#for-you-tab').click();
  requests[2].resolve([post(1, 'ą'.repeat(39) + '👩‍🔬dalsza treść')]);
  await settle();
  assert.equal(elements.get('#post-list').children[0].children[1].textContent, 'ą'.repeat(39) + '👩‍🔬dalsza treść');
  assert.equal(elements.get('#post-content').value, 'Zachowany szkic');
});

test('liking in trending sends one mutation and reloads the server ranking', async (t) => {
  const { elements, requests } = mount(t);
  requests[0].resolve([]);
  await settle();
  elements.get('#trending-tab').click();
  requests[1].resolve([post(1), post(2)]);
  await settle();
  const button = elements.get('#post-list').children[1].querySelectorAll('button')[0];
  button.click();
  button.click();
  elements.get('#for-you-tab').click();
  assert.equal(requests.length, 3);
  assert.equal(requests[2].url, '/api/posts/2/like');
  assert.equal(requests[2].options.method, 'POST');
  assert.equal(elements.get('#for-you-tab').disabled, true);
  requests[2].resolve({ likes_count: 1, liked_by_me: true });
  await settle();
  assert.equal(requests[3].url, '/api/posts/trending');
  requests[3].resolve([{ ...post(2), likes_count: 1, liked_by_me: true }, post(1)]);
  await settle();
  const first = elements.get('#post-list').children[0];
  assert.equal(first.dataset.postId, '2');
  assert.equal(first.children[0].children[0].textContent, '1. @tester');
  assert.equal(first.querySelectorAll('button')[0].getAttribute('aria-pressed'), 'true');
  assert.equal(elements.get('#for-you-tab').disabled, false);
});

test('current ranking errors stay visible and expired sessions use the existing callback', async (t) => {
  const { elements, requests, unauthorized } = mount(t);
  requests[0].resolve([]);
  await settle();
  elements.get('#trending-tab').click();
  requests[1].resolve({ message: 'Błąd rankingu' }, 503);
  await settle();
  assert.equal(elements.get('#feed-message').textContent, 'Błąd rankingu');
  assert.equal(elements.get('#feed-message').dataset.kind, 'error');
  elements.get('#refresh-posts').click();
  requests[2].resolve({ message: 'Sesja wygasła' }, 401);
  await settle();
  assert.equal(unauthorized(), 1);
});

test('clearing the session prevents a delayed ranking from restoring private posts', async (t) => {
  const { elements, requests, ui } = mount(t);
  elements.get('#trending-tab').click();
  ui.clear();
  requests[1].resolve([post(1)]);
  requests[0].resolve([post(2)]);
  await settle();
  assert.equal(elements.get('#post-list').children.length, 0);
  assert.equal(elements.get('#post-composer').hidden, true);
});

test('anonymous cards hide author and use is_owner for deletion in both tabs', async (t) => {
  const { elements, requests } = mount(t);
  const own = { ...post(1), is_anonymous: true, author: null };
  const other = { ...own, id: 2, is_owner: false };
  requests[0].resolve([own, other]);
  await settle();
  const cards = elements.get('#post-list').children;
  assert.equal(cards[0].children[0].children[0].textContent, 'Gall Anonim');
  assert.equal(cards[1].children[0].children[0].textContent, 'Gall Anonim');
  assert.equal(cards[0].querySelectorAll('button').length, 2);
  assert.equal(cards[1].querySelectorAll('button').length, 1);
  assert.deepEqual(cards[0].dataset, { postId: '1' });
  elements.get('#trending-tab').click();
  requests[1].resolve([other]);
  await settle();
  const ranked = elements.get('#post-list').children[0];
  assert.equal(ranked.children[0].children[0].textContent, '1. Gall Anonim');
  assert.equal(ranked.querySelectorAll('button').length, 1);
});

test('normal cards also use server ownership instead of comparing author ids', async (t) => {
  const { elements, requests } = mount(t);
  requests[0].resolve([{ ...post(1), is_owner: false }]);
  await settle();
  assert.equal(elements.get('#post-list').children[0].querySelectorAll('button').length, 1);
});

test('desktop preview preserves server order and ignores earlier responses after opening the ranking', async (t) => {
  const { elements, requests, ui } = mount(t, { desktop: true });
  const preview = elements.get('#trending-preview-list');
  requests[0].resolve([]);
  assert.equal(requests[1].url, '/api/posts/trending');
  elements.get('#trending-tab').click();
  assert.equal(requests[2].url, '/api/posts/trending');
  const anonymous = { ...post(3, '<b>Anonimowa treść</b>'), author: null, is_anonymous: true };
  requests[2].resolve([anonymous, post(2), post(4), post(6)]);
  await settle();
  requests[1].resolve([post(9)]);
  await settle();
  assert.equal(preview.children.length, 3);
  assert.equal(preview.children[0].children[0].textContent, 'Gall Anonim');
  assert.deepEqual(preview.children.map((item) => item.children[1].textContent), [anonymous.content, 'Post 2', 'Post 4']);
  ui.clear();
  assert.equal(preview.children.length, 0);
});

test('preview loads only at desktop width and ignores a response after it is hidden', async (t) => {
  const { elements, requests, media } = mount(t);
  requests[0].resolve([]);
  await settle();
  assert.equal(requests.length, 1);
  media.matches = true;
  media.dispatchEvent(new Event('change'));
  assert.equal(requests[1].url, '/api/posts/trending');
  media.matches = false;
  media.dispatchEvent(new Event('change'));
  requests[1].resolve([post(1)]);
  await settle();
  assert.equal(elements.get('#trending-preview-list').children.length, 0);
});

test('New post returns from Popularne and focuses the existing draft', async (t) => {
  const { elements, requests } = mount(t);
  requests[0].resolve([]);
  await settle();
  const content = elements.get('#post-content');
  content.value = 'Zachowany szkic';
  elements.get('#trending-tab').click();
  requests[1].resolve([]);
  await settle();
  assert.equal(elements.get('#trending-tab').getAttribute('aria-selected'), 'true');
  elements.get('#new-post').click();
  requests[2].resolve([]);
  await settle();
  assert.equal(elements.get('#post-composer').hidden, false);
  assert.equal(elements.get('#nav-home').getAttribute('aria-current'), 'true');
  assert.deepEqual(elements.get('#post-composer').scrollOptions, { block: 'start' });
  assert.equal(content.focused, true);
  assert.equal(content.value, 'Zachowany szkic');
  assert.equal(requests.length, 3);
  assert.ok(requests.every((request) => request.options.method === 'GET'));
});

test('desktop preview refreshes after a like, deletion and publication', async (t) => {
  const { elements, requests } = mount(t, { desktop: true });
  const preview = elements.get('#trending-preview-list');
  const list = elements.get('#post-list');
  requests[0].resolve([post(1)]);
  requests[1].resolve([post(1)]);
  await settle();

  const [like, remove] = list.children[0].querySelectorAll('button');
  like.click();
  assert.equal(requests[2].url, '/api/posts/1/like');
  requests[2].resolve({ likes_count: 1, liked_by_me: true });
  await settle();
  assert.equal(requests[3].url, '/api/posts/trending');
  requests[3].resolve([{ ...post(1), likes_count: 1 }]);
  await settle();
  assert.equal(preview.children[0].children[2].children.at(-1).textContent, '1');

  remove.click();
  assert.equal(requests[4].url, '/api/posts/1');
  assert.equal(requests[4].options.method, 'DELETE');
  requests[4].resolve(null, 204);
  await settle();
  assert.equal(requests[6].url, '/api/posts/trending');
  requests[5].resolve([]);
  requests[6].resolve([]);
  await settle();
  assert.equal(preview.children.length, 0);

  const newPost = post(2, 'Nowy wpis');
  elements.get('#post-content').value = newPost.content;
  elements.get('#post-form').dispatchEvent(new Event('submit', { cancelable: true }));
  requests[7].resolve(newPost, 201);
  await settle();
  assert.equal(requests[9].url, '/api/posts/trending');
  requests[8].resolve([newPost]);
  requests[9].resolve([newPost]);
  await settle();
  assert.equal(preview.children[0].children[1].textContent, newPost.content);
});

test('counter shows remaining Unicode code points and warns below twenty', async (t) => {
  const { elements, requests } = mount(t);
  requests[0].resolve([]);
  await settle();
  const content = elements.get('#post-content');
  const counter = elements.get('#post-counter');
  const publish = elements.get('#publish-post');
  const form = elements.get('#post-form');
  assert.equal(counter.textContent, '280');

  for (const [value, remaining, disabled] of [
    ['', 280, true],
    [' \t\n\u0085\u2003', 280, true],
    ['a'.repeat(200), 80, false],
    ['ą'.repeat(260), 20, false],
    ['ą'.repeat(260) + '😀', 19, false],
    ['ą'.repeat(278) + '😀', 1, false],
    ['ą'.repeat(277) + '👩‍🔬', 0, false],
    ['ą'.repeat(278) + '👩‍🔬', -1, true],
    ['ą'.repeat(296), -16, true],
    ['\u0085' + 'ą'.repeat(279) + '😀\u2003', 0, false],
  ]) {
    content.value = value;
    content.dispatchEvent(new Event('input'));
    assert.equal(counter.textContent, String(remaining));
    assert.equal(counter.dataset.kind, remaining < 20 ? 'error' : '');
    assert.equal(publish.disabled, disabled);
    assert.equal(content.getAttribute('aria-invalid'), String(remaining < 0));

    if (disabled) {
      form.dispatchEvent(new Event('submit', { cancelable: true }));
      assert.equal(requests.length, 1);
    }
  }
});

test('successful anonymous publication sends the flag and resets the checkbox', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { elements, requests } = mount(t);
  requests[0].resolve([]);
  await settle();
  elements.get('#post-content').value = 'Anonimowa opinia';
  elements.get('#post-anonymous').checked = true;
  elements.get('#post-form').dispatchEvent(new Event('submit', { cancelable: true }));
  assert.deepEqual(JSON.parse(requests[1].options.body), { content: 'Anonimowa opinia', is_anonymous: true });
  assert.equal(elements.get('#post-anonymous').disabled, true);
  assert.equal(elements.get('#publish-post').disabled, true);
  assert.equal(elements.get('#post-form-message').textContent, 'Sprawdzanie treści...');
  elements.get('#post-form').dispatchEvent(new Event('submit', { cancelable: true }));
  assert.equal(requests.length, 2);
  const savedPost = { ...post(1, 'Anonimowa opinia'), is_anonymous: true, author: null };
  requests[1].resolve(savedPost, 201);
  await settle();
  assert.equal(requests[2].url, '/api/posts?limit=21&offset=0');
  requests[2].resolve([savedPost]);
  await settle();
  assert.equal(elements.get('#post-content').value, '');
  assert.equal(elements.get('#post-anonymous').checked, false);
  assert.equal(elements.get('#post-anonymous').disabled, false);
  assert.equal(elements.get('#post-counter').textContent, '280');
  assert.equal(elements.get('#post-form-message').textContent, 'Post został opublikowany.');
  assert.equal(elements.get('#post-list').children[0].dataset.postId, '1');
  t.mock.timers.tick(2999);
  assert.equal(elements.get('#post-form-message').textContent, 'Post został opublikowany.');
  t.mock.timers.tick(1);
  assert.equal(elements.get('#post-form-message').textContent, '');
});

test('unchecked publication explicitly sends false', async (t) => {
  const { elements, requests } = mount(t);
  requests[0].resolve([]);
  await settle();
  elements.get('#post-content').value = 'Zwykły wpis';
  elements.get('#post-form').dispatchEvent(new Event('submit', { cancelable: true }));
  assert.deepEqual(JSON.parse(requests[1].options.body), { content: 'Zwykły wpis', is_anonymous: false });
  requests[1].resolve(post(1), 201);
  await settle();
  requests[2].resolve([]);
  await settle();
});

test('moderation failures keep the draft and allow a corrected submission', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { elements, requests, ui } = mount(t);
  requests[0].resolve([]);
  await settle();
  const content = elements.get('#post-content');
  const anonymous = elements.get('#post-anonymous');
  const form = elements.get('#post-form');
  const message = elements.get('#post-form-message');
  anonymous.checked = true;

  const failures = [
    [422, { message: 'Ten wpis może wymagać zmian. Popraw treść i spróbuj ponownie.', moderation_decision: 'review' }],
    [422, { message: 'Ten wpis może naruszać zasady społeczności i nie został opublikowany.', moderation_decision: 'unsafe' }],
    [503, { message: 'Moderacja jest chwilowo niedostępna. Spróbuj ponownie później.' }],
  ];
  for (const [index, [status, body]] of failures.entries()) {
    const draft = `Szkic po poprawce ${index}`;
    content.value = draft;
    content.dispatchEvent(new Event('input'));
    assert.equal(message.textContent, '');
    form.dispatchEvent(new Event('submit', { cancelable: true }));
    assert.equal(message.textContent, 'Sprawdzanie treści...');
    const request = requests[index + 1];
    assert.equal(request.url, '/api/posts');
    assert.deepEqual(JSON.parse(request.options.body), { content: draft, is_anonymous: true });
    request.resolve(body, status);
    await settle();
    assert.equal(requests.length, index + 2);
    assert.equal(content.value, draft);
    assert.equal(content.disabled, false);
    assert.equal(anonymous.checked, true);
    assert.equal(anonymous.disabled, false);
    assert.equal(elements.get('#publish-post').disabled, false);
    assert.equal(message.textContent, body.message);
    assert.equal(message.dataset.kind, 'error');
    t.mock.timers.tick(3000);
    assert.equal(message.textContent, body.message);
  }

  ui.clear();
  assert.equal(content.value, '');
  assert.equal(anonymous.checked, false);
  assert.equal(message.textContent, '');
});

test('editing dismisses success immediately and its timer cannot clear the next error', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { elements, requests } = mount(t);
  const content = elements.get('#post-content');
  const form = elements.get('#post-form');
  const message = elements.get('#post-form-message');
  requests[0].resolve([]);
  await settle();

  content.value = 'Pierwszy wpis';
  form.dispatchEvent(new Event('submit', { cancelable: true }));
  requests[1].resolve(post(1, content.value), 201);
  await settle();
  requests[2].resolve([]);
  await settle();
  assert.equal(message.dataset.kind, 'success');

  content.value = 'Kolejny szkic';
  content.dispatchEvent(new Event('input'));
  assert.equal(message.textContent, '');
  form.dispatchEvent(new Event('submit', { cancelable: true }));
  requests[3].resolve({ message: 'Moderacja jest chwilowo niedostępna.' }, 503);
  await settle();
  t.mock.timers.tick(3000);
  assert.equal(message.textContent, 'Moderacja jest chwilowo niedostępna.');
  assert.equal(content.value, 'Kolejny szkic');
});
