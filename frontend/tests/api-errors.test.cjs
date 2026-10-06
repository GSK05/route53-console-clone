const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../components/types.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;

function client(status, payload) {
  const exports = {};
  vm.runInNewContext(compiled, {
    exports, FormData,
    fetch: async () => ({ ok: status < 400, status, json: async () => payload }),
  });
  return exports.api;
}

test('FastAPI field validation reports the domain and its reason', async () => {
  const api = client(422, { detail: [{ loc: ['body', 'name'], msg: 'Value error, Enter a domain such as example.com' }] });
  await assert.rejects(api('/zones'), { message: 'Name: Enter a domain such as example.com' });
});

test('multiple validation fields are included in the error', async () => {
  const api = client(422, { detail: [{ loc: ['body', 'comment'], msg: 'Too long' }, { loc: ['body', 'tags', 0, 'key'], msg: 'Key required' }] });
  await assert.rejects(api('/zones'), { message: 'Description: Too long; Tags › 0 › key: Key required' });
});

test('private-zone and BIND errors retain their messages', async () => {
  await assert.rejects(client(422, { detail: 'A VPC ID is required' })('/zones'), { message: 'A VPC ID is required' });
  await assert.rejects(client(422, { detail: { errors: ['Line 2: invalid TTL'] } })('/zones/Z/import'), { message: 'Line 2: invalid TTL' });
});

test('unknown error shapes still report the HTTP status', async () => {
  await assert.rejects(client(500, {})('/zones'), { message: 'Request failed (500)' });
});

test('authentication failures retain status for clearing the signed-in workspace', async () => {
  await assert.rejects(client(401, { detail: 'Session expired' })('/auth/me'), { message: 'Session expired', status: 401 });
});
