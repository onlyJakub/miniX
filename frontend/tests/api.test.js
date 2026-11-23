import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { apiRequest } from '../src/api.js';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

test('DELETE handles an empty HTTP 204 without parsing JSON', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/posts/12');
    assert.equal(options.method, 'DELETE');
    assert.equal(options.credentials, 'same-origin');
    return new Response(null, { status: 204 });
  };
  assert.equal(await apiRequest('/posts/12', { method: 'DELETE' }), null);
});

test('POST sends only content as JSON and returns the saved post', async () => {
  const post = { id: 1, content: 'Cześć', author: { id: 2, username: 'testuser' } };
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/posts');
    assert.equal(options.headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(options.body), { content: post.content });
    return Response.json(post, { status: 201 });
  };
  assert.deepEqual(await apiRequest('/posts', { method: 'POST', body: { content: post.content } }), post);
});

test('validation errors preserve the HTTP status and readable message', async () => {
  globalThis.fetch = async () => Response.json({ message: 'Sprawdź dane.', errors: [{ field: 'content', message: 'Maksymalnie 280 znaków.' }] }, { status: 422 });
  await assert.rejects(apiRequest('/posts'), (error) => error.status === 422 && error.message.includes('280'));
});

test('non-JSON proxy failure gives a readable error', async () => {
  globalThis.fetch = async () => new Response('Proxy error', { status: 500 });
  await assert.rejects(apiRequest('/posts'), /JSON/);
});

test('network failure gives a readable error', async () => {
  globalThis.fetch = async () => { throw new TypeError('Failed to fetch'); };
  await assert.rejects(apiRequest('/posts'), /Nie można połączyć/);
});