// Invoked by halo_browser.py with disposable credentials and a simulated Halo API.
const {chromium} = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const base = process.env.AIRISK_TEST_URL;
const assessment = process.env.AIRISK_HALO_ASSESSMENT;
const risk = process.env.AIRISK_HALO_RISK;
const errors = [];

(async () => {
  const browser = await chromium.launch({headless:true,
    ...(process.env.AIRISK_BROWSER ? {executablePath:process.env.AIRISK_BROWSER} : {})});
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (response.status() >= 500) errors.push(`${response.status()} ${response.url()}`);
  });
  async function login(who) {
    await page.goto(base + '/login');
    await page.getByLabel('Email', {exact:true}).fill(`${who}@halo-qa.test`);
    await page.getByLabel('Password', {exact:true}).fill(process.env.AIRISK_HALO_PASSWORD);
    await page.getByRole('button', {name:'Sign in',exact:true}).click();
    await page.waitForURL(base + '/');
  }
  const card = page.locator(`#risk-${risk}`);
  const halo = card.locator('.halo-action');
  async function changeStatus(status_id, status_name) {
    fs.writeFileSync(process.env.AIRISK_HALO_CONTROL, JSON.stringify({status_id,status_name}));
    await halo.getByRole('button',{name:'Sync Halo progress',exact:true}).click();
    await page.waitForURL(`**/assessments/${assessment}#risk-${risk}`);
    await halo.getByText(status_name,{exact:true}).waitFor();
  }
  try {
    await login('owner');
    await page.getByRole('link',{name:'Integrations',exact:true}).click();
    assert(await page.getByRole('heading',{name:'HaloITSM',exact:true}).isVisible());
    await page.getByRole('link',{name:'Connection details',exact:false}).click();
    assert(await page.getByText(/Configuration is present/).isVisible());
    await page.goto(`${base}/assessments/${assessment}`);
    await halo.getByRole('button',{name:'Create Halo ticket',exact:true}).click();
    await page.waitForURL(`**/assessments/${assessment}#risk-${risk}`);
    assert(await halo.getByRole('link',{name:/Halo ticket #700/}).isVisible());
    assert(await halo.getByText('Implementation is in progress.',{exact:true}).isVisible());
    await changeStatus(9, 'Resolved');
    assert(await halo.getByText(/Halo reports completion/).isVisible());
    assert(await card.getByText(/Awaiting verified completion/).isVisible());
    assert.equal(await card.getByLabel('Completion evidence link (HTTPS, optional)').inputValue(),
      'https://halo.example.test/ticket?id=700');
    await card.getByLabel('Completion evidence and results').fill('Synthetic evidence: evaluated retrieval permissions and reviewed results in Halo.');
    await card.getByRole('button',{name:'Submit action completion',exact:true}).click();
    await page.getByRole('button',{name:'Sign out',exact:true}).click();
    await login('reviewer');
    await page.goto(`${base}/assessments/${assessment}`);
    await card.getByRole('button',{name:'Verify completion',exact:true}).click();
    assert(await card.getByText(/Completion verified/).isVisible());
    await changeStatus(2, 'In progress');
    assert(await halo.getByText(/returned to an incomplete status/).isVisible());
    const report = await (await page.request.get(`${base}/assessments/${assessment}/export.json`)).json();
    assert.equal(report.state, 'conditional');
    assert.equal(report.risks[0].action_resolved_now, true);
    assert.equal(report.risks[0].halo_ticket.reopened, true);
    fs.mkdirSync('test-results',{recursive:true});
    await halo.screenshot({path:'test-results/halo-progress-desktop.png'});
    await page.setViewportSize({width:390,height:844});
    await halo.scrollIntoViewIfNeeded();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Mobile page must not overflow');
    await halo.screenshot({path:'test-results/halo-progress-mobile.png'});
    assert.deepEqual(errors, []);
    console.log('Halo browser journey passed: creation, public progress, independent completion, reopening, exports and mobile layout.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
