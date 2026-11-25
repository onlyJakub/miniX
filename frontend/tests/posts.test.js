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
  set innerHTML(value) { this.children = [new Element('svg'), new Element('span')]; }
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
}

const post = (id, content = `Post ${id}`) => ({
  id, content, created_at: '2026-11-13T08:00:00Z', author: { id: 1, username: 'tester' },
  is_anonymous: false, is_owner: true,
  likes_count: 0, liked_by_me: false,
});
const settle = () => new Promise((resolve) => setImmediate(resolve));

function mount(t) {
  const originalDocument = globalThis.document;
  const originalFetch = globalThis.fetch;
  const elements = new Map();
  const requests = [];
  let unauthorized = 0;
  globalThis.document = {
    querySelector(selector) {
      if (!elements.has(selector)) elements.set(selector, new Element());
      return elements.get(selector);
    },
    createElement: (tagName) => new Element(tagName),
    createDocumentFragment: () => new Element('fragment'),
  };
  globalThis.fetch = (url, options) => new Promise((resolve, reject) => {
    requests.push({ url, options, resolve: (data, status = 200) => resolve(Response.json(data, { status })), reject });
  });
  const ui = setupPosts({ user: { id: 1 }, onUnauthorized: () => { unauthorized += 1; } });
  t.after(() => {
    ui.clear();
    globalThis.document = originalDocument;
    globalThis.fetch = originalFetch;
  });
  return { elements, requests, ui, unauthorized: () => unauthorized };
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
  assert.equal(cards[0].children[0].children[0].textContent, '🔥 1. @tester');
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
  assert.equal(first.children[0].children[0].textContent, '🔥 1. @tester');
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
  assert.equal(ranked.children[0].children[0].textContent, '🔥 1. Gall Anonim');
  assert.equal(ranked.querySelectorAll('button').length, 1);
});

test('normal cards also use server ownership instead of comparing author ids', async (t) => {
  const { elements, requests } = mount(t);
  requests[0].resolve([{ ...post(1), is_owner: false }]);
  await settle();
  assert.equal(elements.get('#post-list').children[0].querySelectorAll('button').length, 1);
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
  }

  ui.clear();
  assert.equal(content.value, '');
  assert.equal(anonymous.checked, false);
  assert.equal(message.textContent, '');
});
