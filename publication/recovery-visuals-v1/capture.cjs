const {chromium}=require('/tmp/telemetry-paper-browser/package');
const fs=require('node:fs'),p=require('node:path'),assert=require('node:assert/strict'),{pathToFileURL}=require('node:url');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/tmp/telemetry-paper-browser/chrome-linux/chrome',args:['--no-sandbox']});
 try{
  for(const [name,width,points] of [['before',1440,4],['after',1440,72],['comparison',2904,76]]){
   const page=await browser.newPage({viewport:{width,height:1100},deviceScaleFactor:1});
   const svg=fs.readFileSync(p.join(__dirname,'figures',name+'.svg'),'utf8');
   await page.setContent('<!doctype html><meta charset="utf-8"><style>html,body{margin:0;padding:0}svg{display:block}</style>'+svg);
   assert.equal(await page.locator('circle[data-met]').count(),points);
   const boxes=await page.locator('text').evaluateAll(nodes=>nodes.map(n=>{const b=n.getBBox();return{x:b.x,y:b.y,w:b.width,h:b.height,text:n.textContent}}));
   assert(boxes.every(b=>b.x>=0&&b.y>=0&&b.x+b.w<=width&&b.y+b.h<=1100),'clipped plot text');
   await page.screenshot({path:p.join(__dirname,'figures',name+'.png'),clip:{x:0,y:0,width,height:1100},timeout:45000});await page.close();
  }
 }finally{await browser.close();}
 console.log('PASS: before4/after72 actual sensor points; all text within plot bounds; 3 PNG screenshots rendered directly from data SVGs.');
})().catch(e=>{console.error(e);process.exitCode=1});
