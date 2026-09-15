// Loopback-only static preview. No uploads, decoding, analytics or public bind.
const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const root=__dirname;
const allowed=new Set(['index.html','styles.css','app.js','assets/paper.pdf','assets/paper-source.zip','assets/summary.json']);
const mime={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'application/javascript; charset=utf-8','.pdf':'application/pdf','.zip':'application/zip','.json':'application/json; charset=utf-8'};
const server=http.createServer((req,res)=>{
  if(!['GET','HEAD'].includes(req.method)){res.writeHead(405,{'Allow':'GET, HEAD'});res.end();return;}
  let name;try{name=decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\//,'')||'index.html';}catch{res.writeHead(400);res.end();return;}
  if(!allowed.has(name)){res.writeHead(404);res.end('Not found');return;}
  const file=path.join(root,name);fs.stat(file,(err,stat)=>{
    if(err||!stat.isFile()){res.writeHead(404);res.end('Not found');return;}
    res.writeHead(200,{'Content-Type':mime[path.extname(file)],'Content-Length':stat.size,'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'});
    if(req.method==='HEAD'){res.end();return;}const stream=fs.createReadStream(file);stream.on('error',()=>res.destroy());stream.pipe(res);
  });
});
server.listen(0,'127.0.0.1',()=>console.log(`Telemetry Yield local preview: http://127.0.0.1:${server.address().port}`));
