const fs=require('node:fs'),assert=require('node:assert/strict');
const log=fs.readFileSync('main.log','utf8');
assert(!/undefined references|Citation .* undefined|^! /m.test(log),'TeX error or unresolved citation');
const boxes=log.split('\n').filter(l=>l.includes('Overfull'));
// The untouched 2026-05-13 IEEE Access title constructor emits two empty
// 9.2679pt boxes at \maketitle. Page 1 was visually checked; no text clips.
// Do not suppress other layout errors or silently claim a warning-free build.
const titleLine=fs.readFileSync('main.tex','utf8').split('\n').findIndex(l=>l==='\\maketitle')+1;
assert(boxes.length===0 || (boxes.length===2 && boxes.every(l=>l===`Overfull \\hbox (9.2679pt too wide) in paragraph at lines ${titleLine}--${titleLine}`)),JSON.stringify(boxes));
console.log(`PASS: no TeX errors/unresolved references/body overflows; ${boxes.length} known, visually checked template title-box warnings retained.`);
