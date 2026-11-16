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