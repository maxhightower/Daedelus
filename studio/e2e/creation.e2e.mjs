// Run against a local backend serving the production studio build.
// STUDIO_URL=http://127.0.0.1:8765 node e2e/creation.e2e.mjs
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const base = process.env.STUDIO_URL || 'http://127.0.0.1:8765';
const api = async (path, body) => {
  const r = await fetch(base + '/api' + path, body === undefined ? {} : { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  assert.ok(r.ok, `${path}: ${r.status} ${r.ok ? '' : await r.text()}`); return r.json();
};
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
try {
  const project = await api('/projects', { name: 'Creation flow test' });
  const p = `/projects/${project.id}`;
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = []; page.on('pageerror', (e) => errors.push(String(e)));
  await page.addInitScript((id) => localStorage.setItem('daedelus.project', id), project.id);
  await page.goto(base); await page.getByTestId('explorer').waitFor();
  const health = await api('/health');
  for (const adapter of ['document', 'spreadsheet', 'presentation', 'layered2d', 'code', 'blender']) {
    await page.getByRole('button', { name: '+ artifact', exact: true }).click();
    const form = page.getByRole('form', { name: 'New artifact' });
    await form.getByLabel('artifact name').fill(`New ${adapter}`);
    await form.getByLabel('Artifact type').selectOption(adapter);
    if (!health.adapters.find((a) => a.name === adapter)?.available) {
      assert.ok(await form.getByRole('button', { name: 'Create', exact: true }).isDisabled());
      await form.getByRole('button', { name: 'Cancel' }).click();
      console.log(`PASS ${adapter}: unavailable tool explained and blocked`); continue;
    }
    await form.getByRole('button', { name: 'Create', exact: true }).click();
    await form.waitFor({ state: 'detached', timeout: 120000 });
    const artifacts = await api(p + '/artifacts');
    const artifact = artifacts.find((a) => a.name === `New ${adapter}`);
    assert.ok(artifact?.head_revision_id, `${adapter}: initial revision`);
    const boards = await api(p + '/boards'); const board = await api(p + `/boards/${boards[0].id}`);
    assert.ok(board.items.some((i) => i.resource_ref.id === artifact.id), `${adapter}: placed on board`);
    console.log(`PASS ${adapter}: created, revision saved, board placement persisted`);
  }
  for (const name of ['First workflow', 'Second workflow']) {
    await page.getByRole('button', { name: '+ workflow', exact: true }).click();
    const form = page.getByRole('form', { name: 'New workflow' });
    await form.getByLabel('workflow name').fill(name);
    await form.getByRole('button', { name: 'Create', exact: true }).click();
    await form.waitFor({ state: 'detached' });
  }
  const workflows = await api(p + '/workflows'); assert.equal(workflows.length, 2);
  const boards = await api(p + '/boards'); const board = await api(p + `/boards/${boards[0].id}`);
  for (const w of workflows) assert.ok(board.items.some((i) => i.resource_ref.id === w.id));
  await page.reload(); await page.getByTestId('explorer').waitFor();
  await page.getByTestId('ex-workflows').getByText('Second workflow', { exact: true }).waitFor();
  await page.getByRole('button', { name: '+ artifact', exact: true }).click();
  if (process.env.SCREENSHOT) await page.screenshot({ path: process.env.SCREENSHOT });
  assert.deepEqual(errors, []);
  console.log('PASS two independent workflows, saved layout after reload, no browser errors');
} finally { await browser.close(); }
