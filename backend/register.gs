// Hackathon registration -> this Google Sheet.
// Deploy: Extensions > Apps Script, paste this, Deploy > New deployment > Web app,
// Execute as: Me, Who has access: Anyone. Put the /exec URL in site/index.html (ENDPOINT).
// ponytail: Sheet + LockService is fine for hundreds of sign-ups; move to a DB past ~10k.

const HEADERS = ['registered_at', 'name', 'email', 'github', 'country', 'org', 'updates'];
const RULES = {
  name: /^.{1,100}$/,
  email: /^[^\s@]{1,64}@[^\s@]+\.[^\s@]{2,}$/,
  github: /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/,
  country: /^.{1,60}$/,
};

function doPost(e) {
  const p = (e && e.parameter) || {};
  if (p.website) return json({ ok: true }); // honeypot: bots fill it, pretend success
  const v = {};
  for (const k of ['name', 'email', 'github', 'country', 'org']) v[k] = String(p[k] || '').trim();
  v.email = v.email.toLowerCase();
  for (const k in RULES) if (!RULES[k].test(v[k])) return json({ ok: false, error: 'invalid ' + k });
  v.org = v.org.slice(0, 120);

  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
    if (sheet.getLastRow() === 0) sheet.appendRow(HEADERS);
    const emails = sheet.getLastRow() > 1 ? sheet.getRange(2, 3, sheet.getLastRow() - 1, 1).getValues().flat() : [];
    if (emails.includes(v.email)) return json({ ok: true, duplicate: true });
    // Leading ' stops a value like =HYPERLINK(...) running as a formula when the team opens the sheet.
    const safe = (s) => (/^[=+\-@]/.test(s) ? "'" + s : s);
    sheet.appendRow([new Date(), safe(v.name), v.email, v.github, safe(v.country), safe(v.org), p.updates === 'yes' ? 'yes' : 'no']);
    return json({ ok: true });
  } finally {
    lock.releaseLock();
  }
}

function json(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}
