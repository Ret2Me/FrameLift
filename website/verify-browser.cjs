const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const { pathToFileURL } = require('node:url');

// A separately installed Playwright module and browser are test dependencies only.
const { chromium } = require(process.argv[2] || 'playwright');
const executablePath = process.argv[3];
const root = __dirname;
const out = path.join(root, 'verification');
fs.mkdirSync(out, { recursive: true });
const mime = { '.html':'text/html; charset=utf-8', '.css':'text/css', '.js':'application/javascript', '.pdf':'application/pdf', '.json':'application/json', '.zip':'application/zip' };
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const file = path.resolve(root, '.' + decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname));
  if (!file.startsWith(root + path.sep)) { res.writeHead(403).end(); return; }
  try {
    const data = fs.readFileSync(file);
    res.writeHead(200, { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream' }).end(data);
  } catch { res.writeHead(404).end(); }
});
const report = { checked_utc:new Date().toISOString(), tests:[], screenshots:[], console_errors:[], page_errors:[] };
const pass = (test, detail) => { report.tests.push({ test, status:'pass', detail }); };
let browser;

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ executablePath, headless:true, args:['--no-sandbox', '--disable-dev-shm-usage'] });
  report.browser = browser.version();
  const context = await browser.newContext({ viewport:{ width:1440, height:1000 }, reducedMotion:'reduce', permissions:['clipboard-read','clipboard-write'] });
  const page = await context.newPage();
  const requests = [];
  page.on('request', request => requests.push(request.url()));
  page.on('console', message => { if (message.type() === 'error') report.console_errors.push(message.text()); });
  page.on('pageerror', error => report.page_errors.push(String(error)));
  await page.goto(origin, { waitUntil:'networkidle' });
  assert.equal(await page.locator('html').getAttribute('lang'), 'pl');
  assert.match(await page.title(), /Telemetry Yield/);
  assert.equal(await page.locator('h1').count(), 1);
  pass('Document semantics', 'Polish language, descriptive title, one h1, native form controls.');
  const links = await page.locator('a[href]').evaluateAll(nodes => nodes.map(node => node.getAttribute('href')));
  for (const href of [...new Set(links)]) {
    if (href.startsWith('#')) assert.equal(await page.locator(href).count(), 1, href);
    else if (!/^https?:/.test(href)) {
      assert.ok(fs.statSync(path.join(root, href)).size > 0, href);
      const response = await page.request.get(origin + '/' + href);
      assert.equal(response.status(), 200, href);
    }
  }
  pass('All local links', 'Anchors resolve; PDF, source ZIP and JSON assets return HTTP 200. External research links are not loaded automatically.');
  const summary = JSON.parse(fs.readFileSync(path.join(root, 'assets/summary.json')));
  assert.equal(summary.all.ours, 4541);
  assert.equal(summary.all.reference_union, 2998);
  assert.equal(summary.all.added, 1547);
  assert.equal(summary.all.lost, 4);
  const publication = path.join(root, '../publication/decoder-paper-v1');
  const hash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  assert.equal(hash(path.join(root, 'assets/paper.pdf')), hash(path.join(publication, 'telemetry-yield-preliminary-paper.pdf')));
  assert.equal(hash(path.join(root, 'assets/summary.json')), hash(path.join(publication, 'evidence/summary.json')));
  pass('Frozen assets', 'Exact PDF and JSON match the paper snapshot; 189/266, 4541 vs 2998, +1547/-4.');
  for (const width of [1440, 1024, 768, 375, 320]) {
    await page.setViewportSize({ width, height:1000 });
    const dimensions = await page.evaluate(() => ({ client:document.documentElement.clientWidth, scroll:document.documentElement.scrollWidth }));
    assert.ok(dimensions.scroll <= dimensions.client + 1, `${width}px: ${JSON.stringify(dimensions)}`);
    pass(`Reflow ${width}px`, dimensions);
  }
  await page.setViewportSize({ width:1440, height:1000 });
  await page.goto(origin, { waitUntil:'networkidle' });
  await page.keyboard.press('Tab');
  assert.equal(await page.locator(':focus').textContent(), 'Przejdź do treści');
  const outline = await page.locator(':focus').evaluate(node => getComputedStyle(node).outlineWidth);
  assert.equal(outline, '3px');
  await page.keyboard.press('Enter');
  pass('Keyboard entry', 'Skip link is first tab stop, visible 3px focus, targets the main content.');
  const quick = page.locator('input[value="quick"]');
  await quick.focus();
  await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('input[value="deep"]').isChecked(), true);
  assert.match(await page.locator('#command').textContent(), /--mode deep --threads 4 --resume/);
  await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('input[value="full"]').isChecked(), true);
  assert.match(await page.locator('#command').textContent(), /--mode full --threads 4 --resume/);
  await page.keyboard.press('ArrowRight');
  assert.equal(await quick.isChecked(), true);
  assert.doesNotMatch(await page.locator('#command').textContent(), /--resume/);
  pass('Native radio keyboard', 'Arrow keys select deep/full/quick, update CLI and explanatory text, preserve correct resume flags.');
  await page.locator('#copy-command').click();
  await page.waitForFunction(() => document.querySelector('#copy-status').textContent === 'Polecenie skopiowane.');
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), await page.locator('#command').textContent());
  pass('Clipboard success', 'Actual Chromium clipboard write/read on localhost; status announces success.');
  await page.evaluate(() => { Object.defineProperty(navigator, 'clipboard', { configurable:true, value:{ writeText:async () => { throw new DOMException('Test permission denial', 'NotAllowedError'); } } }); });
  await page.locator('#copy-command').click();
  await page.waitForFunction(() => document.querySelector('#copy-status').textContent.includes('Ctrl+C'));
  assert.equal(await page.evaluate(() => getSelection().toString()), await page.locator('#command').textContent());
  pass('Clipboard denial', 'Injected permission denial selects the command and gives Ctrl+C/⌘C recovery; no native alert.');
  const disclosure = page.locator('.guide summary');
  await disclosure.focus();
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('.guide').getAttribute('open'), '');
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('.guide').getAttribute('open'), null);
  pass('Disclosure keyboard', 'Enter opens and closes setup guide without hiding the trigger.');
  assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).scrollBehavior), 'auto');
  assert.equal(await page.locator('.button').first().evaluate(node => getComputedStyle(node).transitionDuration), '0s');
  pass('Reduced motion', 'Smooth scrolling and component transitions disabled.');
  await page.goto(origin, { waitUntil:'networkidle' });
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({ path:path.join(out, 'desktop-1440.png'), fullPage:true });
  await page.screenshot({ path:path.join(out, 'desktop-hero-1440.png') });
  await page.locator('#wyniki').scrollIntoViewIfNeeded();
  await page.screenshot({ path:path.join(out, 'desktop-results-1440.png') });
  await page.setViewportSize({ width:375, height:900 });
  await page.goto(origin, { waitUntil:'networkidle' });
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({ path:path.join(out, 'mobile-375.png'), fullPage:true });
  await page.screenshot({ path:path.join(out, 'mobile-hero-375.png') });
  report.screenshots = ['desktop-1440.png','desktop-hero-1440.png','desktop-results-1440.png','mobile-375.png','mobile-hero-375.png'];
  await context.setOffline(true);
  await page.locator('input[value="deep"]').check();
  assert.match(await page.locator('#command').textContent(), /--mode deep/);
  pass('Offline after load', 'Radio controls and CLI remain usable without a network.');
  await page.evaluate(() => { Object.defineProperty(navigator, 'clipboard', { configurable:true, value:{ writeText:async () => { throw new DOMException('Test permission denial', 'NotAllowedError'); } } }); });
  const idleHeight = (await page.locator('.console').boundingBox()).height;
  await page.locator('#copy-command').click();
  await page.waitForFunction(() => document.querySelector('#copy-status').textContent.includes('Ctrl+C'));
  assert.equal((await page.locator('.console').boundingBox()).height, idleHeight);
  pass('Mobile feedback stability', 'Clipboard failure text uses reserved space without changing console height at 375px.');
  const fileContext = await browser.newContext({ javaScriptEnabled:false, viewport:{ width:375, height:900 } });
  const filePage = await fileContext.newPage();
  await filePage.goto(pathToFileURL(path.join(root, 'index.html')).href);
  assert.equal(await filePage.locator('h1').isVisible(), true);
  assert.equal(await filePage.locator('#copy-command').isVisible(), false);
  assert.equal(await filePage.locator('input[value="deep"]').isDisabled(), true);
  assert.match(await filePage.locator('noscript').textContent(), /--resume/);
  const fileWidth = await filePage.evaluate(() => ({ client:document.documentElement.clientWidth, scroll:document.documentElement.scrollWidth }));
  assert.ok(fileWidth.scroll <= fileWidth.client + 1);
  pass('Offline file and no JavaScript', 'Direct file URL renders the site, hides copy enhancement, retains quick example and explicit resume instructions.');
  assert.ok(requests.every(url => url.startsWith(origin + '/')), JSON.stringify(requests));
  assert.deepEqual(report.console_errors, []);
  assert.deepEqual(report.page_errors, []);
  pass('Network and console', 'No external runtime requests, console errors or uncaught page errors.');
  const pairs = [['#172b3a','#ffffff'],['#506474','#edf3f7'],['#164bcb','#ffffff'],['#ffffff','#506474']];
  const luminance = hex => {
    const values = hex.slice(1).match(/../g).map(v => parseInt(v,16)/255).map(v => v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4);
    return values[0]*.2126 + values[1]*.7152 + values[2]*.0722;
  };
  const ratios = pairs.map(([fg,bg]) => { const a=luminance(fg), b=luminance(bg); return { fg,bg,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05) }; });
  assert.ok(ratios.every(pair => pair.ratio >= 4.5));
  pass('Text color token contrast', ratios);
  report.status = 'pass';
})().catch(error => { report.status='fail'; report.error=String(error.stack || error); process.exitCode=1; }).finally(async () => {
  fs.writeFileSync(path.join(out, 'browser-report.json'), JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({ status:report.status, tests:report.tests.length, error:report.error, browser:report.browser }));
  if (browser) await browser.close();
  await new Promise(resolve => server.close(resolve));
});
