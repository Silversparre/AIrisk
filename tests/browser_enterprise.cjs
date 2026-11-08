// Real browser, disposable database and the provider API contracts simulated by halo_browser.py.
const {chromium} = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const base = process.env.AIRISK_TEST_URL;
const errors = [];
(async () => {
  const browser = await chromium.launch({headless:true,
    ...(process.env.AIRISK_BROWSER ? {executablePath:process.env.AIRISK_BROWSER} : {})});
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {if(response.status() >= 500) errors.push(`${response.status()} ${response.url()}`);});
  async function login(who) {
    await page.goto(base + '/login');
    await page.getByLabel('Email',{exact:true}).fill(`${who}@halo-qa.test`);
    await page.getByLabel('Password',{exact:true}).fill(process.env.AIRISK_HALO_PASSWORD);
    await page.getByRole('button',{name:'Sign in',exact:true}).click();
    await page.waitForURL(base + '/');
  }
  try {
    await login('owner');
    await page.getByRole('link',{name:'Integrations',exact:true}).click();
    for(const name of ['HaloITSM','OneTrust','Coupa']) assert(await page.getByRole('heading',{name,exact:true}).isVisible());
    await page.getByLabel('Requisition ID',{exact:true}).fill('100');
    await page.getByLabel('Requisition line ID',{exact:true}).fill('501');
    await page.getByRole('button',{name:'Import Coupa line',exact:true}).click();
    await page.waitForURL('**/integrations/records/*');
    const coupaURL = page.url();
    await page.getByRole('link',{name:'Complete AI context',exact:true}).click();
    assert.equal(await page.getByLabel('Use case name',{exact:true}).inputValue(),'Assistant subscription');
    await page.getByLabel('Purpose and affected people',{exact:true}).fill('Assist employees with internal policies. Human review is required.');
    await page.getByLabel('Model or product version',{exact:true}).fill('Version 2026-11');
    for(const [name,value] of Object.entries({ai_type:'generative',lifecycle:'pilot',data_sensitivity:'confidential',
      audience:'internal',autonomy:'advisory',eu_scope:'yes',operator_role:'deployer',use_domain:'general',prohibited:'none'})) {
      await page.locator(`select[name="${name}"]`).selectOption(value);
    }
    await page.getByRole('button',{name:'Save use case',exact:true}).click();
    await page.getByRole('button',{name:'Start new assessment',exact:true}).click();
    await page.waitForURL('**/assessments/*');
    const assessmentURL = page.url();
    await page.goto(coupaURL);
    await page.getByRole('button',{name:'Publish review summary',exact:true}).click();
    assert(await page.getByText('Summary publication confirmed.',{exact:true}).isVisible());
    assert(await page.locator('.integration-preview').getByText(/Current review status: review_required/).isVisible());
    await page.goto(assessmentURL);
    await page.locator('summary').filter({hasText:'Connected assessments & procurement'}).click();
    await page.getByRole('button',{name:'Launch OneTrust assessment',exact:true}).click();
    await page.waitForURL('**/integrations/records/*');
    const oneTrustURL = page.url();
    await page.getByRole('button',{name:'Sync progress',exact:true}).click();
    assert(await page.getByText('NOT_STARTED',{exact:true}).isVisible());
    await page.getByRole('button',{name:'Publish review summary',exact:true}).click();
    assert(await page.getByText('Summary publication confirmed.',{exact:true}).isVisible());
    await page.getByRole('button',{name:'Publish review summary',exact:true}).click();
    fs.writeFileSync(process.env.AIRISK_HALO_CONTROL,JSON.stringify({onetrust_status:'COMPLETED',coupa_supplier:21}));
    await page.getByRole('button',{name:'Sync progress',exact:true}).click();
    assert(await page.getByText('COMPLETED',{exact:true}).isVisible());
    assert(await page.locator('.integration-preview').getByText(/Recorded decision: draft/).isVisible());
    await page.goto(coupaURL);
    await page.getByRole('button',{name:'Sync progress',exact:true}).click();
    assert(await page.getByText(/supplier or product has changed since intake/).isVisible());
    const exported = await (await page.request.get(assessmentURL + '/export.json')).json();
    assert.equal(exported.state,'draft');
    assert.equal(exported.integrations.onetrust.progress.status,'COMPLETED');
    assert.equal(exported.integrations.coupa.source_changed,true);
    fs.mkdirSync('test-results',{recursive:true});
    await page.screenshot({path:'test-results/coupa-intake-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.goto(oneTrustURL);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1),'Mobile page must not overflow');
    await page.screenshot({path:'test-results/onetrust-progress-mobile.png',fullPage:true});
    await page.getByRole('button',{name:'Sign out',exact:true}).click();
    await login('reviewer');
    await page.goto(oneTrustURL);
    assert.equal(await page.getByRole('button',{name:'Publish review summary',exact:true}).count(),0);
    assert(await page.getByRole('button',{name:'Sync progress',exact:true}).isVisible());
    assert.deepEqual(errors,[]);
    console.log('OneTrust and Coupa browser acceptance passed: intake, handoff, publication, sync, provenance and mobile layout.');
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode=1;});
