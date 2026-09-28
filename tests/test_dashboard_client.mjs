import {test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';

test('account selection is committed to the server before retail reconnect', async () => {
  const path = new URL('../server/web/dashboard-api.mjs', import.meta.url);
  assert.ok(existsSync(path), 'The dashboard needs a shared account-selection API client');
  const {selectAccount} = await import(path.href);
  const oldFetch = globalThis.fetch;
  let selected = 'Alpha';
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/select_account');
    assert.equal(options.method, 'POST');
    assert.equal(options.headers['Content-Type'], 'application/json');
    selected = JSON.parse(options.body).name;
    return {ok: true, json: async () => ({status: 'ok'})};
  };
  try {
    await selectAccount('Beta');
    assert.equal(selected, 'Beta');
  } finally { globalThis.fetch = oldFetch; }
});

test('a refused account switch remains an error for the UI', async () => {
  const path = new URL('../server/web/dashboard-api.mjs', import.meta.url);
  assert.ok(existsSync(path));
  const {selectAccount} = await import(path.href);
  const oldFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ok: false, status: 404, json: async () => ({error: 'Profile not found'})});
  try { await assert.rejects(selectAccount('Missing'), /Profile not found/); }
  finally { globalThis.fetch = oldFetch; }
});
