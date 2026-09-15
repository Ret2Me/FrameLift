// Static-site QA only. Uses an isolated official Playwright package in /tmp.
const {chromium}=require('/tmp/telemetry-paper-browser/package');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {pathToFileURL,fileURLToPath}=require('node:url');
let browser;
(async()=>{
  const output=path.join(__dirname,'qa');fs.mkdirSync(output,{recursive:true});
  const b=await chromium.launch({executablePath:'/tmp/telemetry-paper-browser/chrome-linux/chrome',headless:true,args:['--no-sandbox']});browser=b;
  const errors=[],results=[];
  for(const width of [1440,768,390,320]){
    const context=await b.newContext({viewport:{width,height:1000},reducedMotion:'reduce'});
    const page=await context.newPage();page.on('pageerror',e=>errors.push(String(e)));
    await page.goto(pathToFileURL(path.join(__dirname,'index.html')).href);
    await page.screenshot({path:path.join(output,`desktop-${width}.png`),fullPage:true});
    assert.equal(await page.locator('html').getAttribute('lang'),'pl');
    assert(await page.locator('h1').innerText());
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`page overflow at ${width}`);
    await page.locator('input[value=deep]').check();assert.match(await page.locator('#command').innerText(),/--mode deep.*--resume/);
    await page.locator('input[value=full]').check();assert.match(await page.locator('#command').innerText(),/--mode full.*--resume/);
    await page.locator('input[value=quick]').check();assert(!/--resume/.test(await page.locator('#command').innerText()));
    await page.locator('input[value=quick]').focus();await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('input[value=deep]').isChecked(),true);
    await page.evaluate(()=>{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('denied')}}})});
    await page.locator('#copy-command').click();assert.match(await page.locator('#copy-status').innerText(),/Zaznaczono/);
    await page.evaluate(()=>{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.__copied=text}}})});
    await page.locator('#copy-command').click();assert.match(await page.locator('#copy-status').innerText(),/skopiowane/);
    assert.match(await page.evaluate(()=>window.__copied),/--mode deep/);
    const links=await page.locator('a[href]').evaluateAll(as=>as.map(a=>a.getAttribute('href')));
    for(const link of links){if(link.startsWith('#'))assert.equal(await page.locator(link).count(),1);else if(!/^https?:/.test(link))assert(fs.existsSync(path.resolve(__dirname,link)),`missing ${link}`)}
    await page.goto(pathToFileURL(path.join(__dirname,'index.html')).href);await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(()=>document.activeElement.className),'skip');
    results.push({width,overflow:false,mode_click_and_keyboard:'pass',clipboard_success_and_denial:'pass',skip_link:'pass',local_links:'pass'});
    await context.close();
  }
  const context=await b.newContext({javaScriptEnabled:false,viewport:{width:390,height:844}});const page=await context.newPage();
  await page.goto(pathToFileURL(path.join(__dirname,'index.html')).href);
  assert(await page.locator('noscript').isVisible());assert.equal(await page.locator('#copy-command').isVisible(),false);
  await context.close();await b.close();assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({checked_utc:new Date().toISOString(),engine:'Chromium 117 / Playwright 1.38.1',results,no_javascript:'pass',page_errors:errors,limits:'Not a complete WCAG audit; one browser engine; external reference availability not certified.'},null,2)+'\n');
  console.log('PASS: 4 widths, keyboard radios and skip link, clipboard success/denial, local assets/anchors, no-JS fallback, no page errors.');
})().catch(async e=>{console.error(e);if(browser)await browser.close();process.exitCode=1});
