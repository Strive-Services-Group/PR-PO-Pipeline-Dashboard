'use strict';

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const XLSX = require('C:/Users/w.amjad/Documents/GitHub/pr-po-proxy/node_modules/xlsx');
const email = require('C:/Users/w.amjad/Documents/GitHub/pr-po-proxy/src/functions/prpoEmail.js');

const root = path.resolve(__dirname, '..');
const exportDir = process.argv[2];
if (!exportDir) throw new Error('usage: node tests/verify_correction02.js <Email-Drops directory>');

function rows(file) {
  const workbook = XLSX.readFile(file, { cellDates: true });
  return XLSX.utils.sheet_to_json(workbook.Sheets[workbook.SheetNames[0]], { defval: null, raw: true });
}
function live(row) { return ['draft', 'in review', 'approved'].includes(String(row.Status || '').trim().toLowerCase()); }
function owner(value) {
  const raw = String(value == null ? '' : value).trim();
  return raw.toLowerCase().startsWith('no named owner') ? '' : raw;
}
function counts(input, field) {
  const out = {};
  for (const row of input) {
    const name = owner(row[field]);
    if (name) out[name] = (out[name] || 0) + 1;
  }
  return out;
}
function find(input, number) { return input.find(row => String(row['Purchase requisition'] || '').toUpperCase() === number); }

const exportPr = rows(path.join(exportDir, 'Purchase Reques.xlsx')).filter(live);
const exportPo = rows(path.join(exportDir, 'Purchase order.xlsx'));
const publishedPr = rows(path.join(root, 'pr.xlsx'));
const publishedPo = rows(path.join(root, 'po.xlsx'));
const emailItems = email.applyDeliveryPolicy(email.buildItems(publishedPr, publishedPo));
const emailPr = emailItems.filter(item => item.doc === 'PR').map(item => ({ owner: item.owner }));

const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const start = html.indexOf('const PR_DATA = []');
const end = html.indexOf('let _t=null;');
if (start < 0 || end <= start) throw new Error('dashboard core markers not found');
const context = vm.createContext({ console, window: {}, setTimeout, clearTimeout, addEventListener() {} });
vm.runInContext(html.slice(start, end), context, { filename: 'index-dashboard-core.js' });
context.__rows = publishedPr;
const dashboardPr = vm.runInContext('buildPRRecords(__rows)', context).map(row => ({ owner: row.pendingUser }));

const exportCounts = counts(exportPr, 'Pending Approver/User');
const publishedCounts = counts(publishedPr, 'Pending Approver/User');
const emailCounts = counts(emailPr, 'owner');
const dashboardCounts = counts(dashboardPr, 'owner');
const people = Array.from(new Set([...Object.keys(exportCounts), ...Object.keys(publishedCounts), ...Object.keys(emailCounts), ...Object.keys(dashboardCounts)])).sort();
const comparison = people.map(person => ({
  person,
  export: exportCounts[person] || 0,
  publishedPrXlsx: publishedCounts[person] || 0,
  email: emailCounts[person] || 0,
  dashboard: dashboardCounts[person] || 0,
  difference: Math.max(
    Math.abs((publishedCounts[person] || 0) - (exportCounts[person] || 0)),
    Math.abs((emailCounts[person] || 0) - (exportCounts[person] || 0)),
    Math.abs((dashboardCounts[person] || 0) - (exportCounts[person] || 0))
  )
}));

const exportDate = new Date(Math.min(
  fs.statSync(path.join(exportDir, 'Purchase Reques.xlsx')).mtimeMs,
  fs.statSync(path.join(exportDir, 'Purchase order.xlsx')).mtimeMs
));
const label = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Dubai', day: 'numeric', month: 'long', year: 'numeric' }).format(exportDate);
const provenanceSentence = `These figures come from the Dynamics 365 F&O export supplied by IT, dated ${label}.`;
const staleWarning = `Warning: the latest Dynamics 365 F&O export supplied by IT is dated ${label}, so these figures are older than this morning's send.`;
emailItems.provenanceSentence = provenanceSentence;
emailItems.freshnessWarning = staleWarning;
for (const item of emailItems) { item.provenanceSentence = provenanceSentence; item.freshnessWarning = staleWarning; }
const adnan = email.groupByOwner(email.personalPool(emailItems)).find(person => person.user === 'Adnan.Ullah');
const procurement = email.buildDivision(email.DIVS.find(item => item.key === 'procurement'), emailItems, {});
const evidenceDir = path.join(root, 'evidence');
fs.mkdirSync(evidenceDir, { recursive: true });
fs.writeFileSync(path.join(evidenceDir, 'correction02-email-preview-adnan.html'), email.buildPersonal(adnan, {}).html);
fs.writeFileSync(path.join(evidenceDir, 'correction02-email-preview-procurement.html'), procurement.html);

const namedPoExport = exportPo.filter(row => owner(row['Pending Approver/User'])).length;
const namedPoPublished = publishedPo.filter(row => owner(row['Pending Approver/User'])).length;
const specificNumbers = ['CPR-018190', 'CPR-022436', 'CPR-024581', 'CPR-022938', 'CPR-026145'];
const stepNumbers = ['PR-001144', 'CPR-023916', 'CPR-029482', 'CPR-030599'];
const report = {
  export: { prFile: 'Purchase Reques.xlsx', poFile: 'Purchase order.xlsx', date: label, livePr: exportPr.length, po: exportPo.length },
  classification: {
    named: exportPr.filter(row => owner(row['Pending Approver/User'])).length,
    noNamedOwner: exportPr.filter(row => !owner(row['Pending Approver/User'])).length,
    unclassified: exportPr.length - publishedPr.length
  },
  multiOwner: {
    exportPr: exportPr.filter(row => String(row['Pending Approver/User'] || '').includes(',')).length,
    publishedPr: publishedPr.filter(row => String(row['Pending Approver/User'] || '').includes(',')).length,
    exportPo: exportPo.filter(row => String(row['Pending Approver/User'] || '').includes(',')).length,
    publishedPo: publishedPo.filter(row => String(row['Pending Approver/User'] || '').includes(',')).length
  },
  comparison,
  fiveOwners: specificNumbers.map(number => ({ number, owner: find(publishedPr, number)['Pending Approver/User'], step: find(publishedPr, number)['Step name'] })),
  threeAmounts: specificNumbers.slice(0, 3).map(number => ({ number, amount: find(publishedPr, number)['Total amount'] })),
  operationSteps: stepNumbers.map(number => ({ number, owner: find(publishedPr, number)['Pending Approver/User'], step: find(publishedPr, number)['Step name'] })),
  poNamedHolders: { export: namedPoExport, published: namedPoPublished, difference: namedPoPublished - namedPoExport },
  provenanceSentence,
  staleWarning,
  previews: ['evidence/correction02-email-preview-adnan.html', 'evidence/correction02-email-preview-procurement.html']
};
if (comparison.some(row => row.difference !== 0)) throw new Error('owner count comparison failed');
if (Object.values(report.multiOwner).some(Boolean)) throw new Error('multi-owner record found');
if (report.classification.named + report.classification.noNamedOwner !== report.export.livePr || report.classification.unclassified !== 0) throw new Error('live PR classification failed');
if (namedPoExport !== namedPoPublished) throw new Error('PO holder preservation failed');
fs.writeFileSync(path.join(evidenceDir, 'correction02-verification.json'), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
