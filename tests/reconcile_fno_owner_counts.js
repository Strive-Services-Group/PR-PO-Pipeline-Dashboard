'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const datasetPath = process.argv[2];
const proxyRoot = process.argv[3];
if (!datasetPath || !proxyRoot) {
  throw new Error('usage: node tests/reconcile_fno_owner_counts.js <dataset.json> <proxy-root>');
}

const dataset = JSON.parse(fs.readFileSync(datasetPath, 'utf8'));
assert.equal(dataset.sourceState, 'LIVE');
const email = require(path.join(proxyRoot, 'src', 'functions', 'prpoEmail.js'));

function key(value) {
  return String(value || '').trim().toLowerCase().replace(/\s+/g, ' ');
}

function sourceOwners(value) {
  const output = [], seen = new Set();
  for (const part of String(value || '').split(',')) {
    const owner = part.trim(), normalized = key(owner);
    if (!owner || /^\d+$/.test(owner) || seen.has(normalized)) continue;
    seen.add(normalized);
    output.push(owner);
  }
  return output;
}

function increment(map, owner) {
  const normalized = key(owner);
  if (!normalized || normalized === 'not recorded' || normalized.startsWith('no named owner')) return;
  const current = map.get(normalized) || { person: owner, count: 0 };
  current.count++;
  map.set(normalized, current);
}

const actionable = dataset.pr.rows.filter(row =>
  ['draft', 'in review', 'approved'].includes(key(row.Status))
);
const fno = new Map();
for (const row of actionable) {
  for (const owner of sourceOwners(row['Pending Approver/User'])) increment(fno, owner);
}

const proxy = new Map();
for (const item of email.buildItems(dataset.pr.rows, dataset.po.rows)) {
  if (item.doc === 'PR' && !item.noNamedOwner) increment(proxy, item.owner);
}

const html = fs.readFileSync(path.join(process.cwd(), 'index.html'), 'utf8');
const start = html.indexOf('const PR_DATA = []');
const end = html.indexOf('let _t=null;');
assert.ok(start >= 0 && end > start, 'dashboard core markers not found');
const context = vm.createContext({ console, window: {}, setTimeout, clearTimeout, addEventListener() {} });
vm.runInContext(html.slice(start, end), context, { filename: 'index-dashboard-core.js' });
context.__rows = dataset.pr.rows;
const dashboardRows = vm.runInContext("buildPRRecords(__rows).filter(row => row._isOpenPipeline)", context);
const dashboard = new Map();
for (const row of dashboardRows) {
  for (const owner of row.holders) increment(dashboard, owner);
}

const keys = [...new Set([...fno.keys(), ...proxy.keys(), ...dashboard.keys()])];
const table = keys.map(normalized => {
  const source = fno.get(normalized), fromProxy = proxy.get(normalized), fromDashboard = dashboard.get(normalized);
  return {
    person: (source || fromProxy || fromDashboard).person,
    fnoCount: source ? source.count : 0,
    ourCount: fromProxy ? fromProxy.count : 0,
    dashboardCount: fromDashboard ? fromDashboard.count : 0,
    difference: (fromProxy ? fromProxy.count : 0) - (source ? source.count : 0)
  };
}).sort((a, b) => b.fnoCount - a.fnoCount || a.person.localeCompare(b.person));

assert.ok(table.length, 'no named F&O owners found');
assert.ok(table.every(row => row.difference === 0), 'Function sender differs from F&O');
assert.ok(table.every(row => row.dashboardCount === row.fnoCount), 'dashboard differs from F&O');
assert.equal(table.some(row => row.fnoCount === 0 && (row.ourCount || row.dashboardCount)), false, 'invented holder found');

console.log(JSON.stringify({
  revision: dataset.revision,
  generatedAt: dataset.generatedAt,
  actionableDocuments: actionable.length,
  noNamedOwnerDocuments: actionable.filter(row => sourceOwners(row['Pending Approver/User']).length === 0).length,
  table
}, null, 2));
