// Scientific SVG plots from decoded CSV/JSON. No image synthesis or enhancement.
const fs=require('node:fs'),p=require('node:path'),assert=require('node:assert/strict');
const rows=JSON.parse(fs.readFileSync(p.join(__dirname,'data/beacons.json')));
const meta=JSON.parse(fs.readFileSync(p.join(__dirname,'data/provenance.json')));
const W=1440,H=1100,X=130,PW=1230;
const esc=s=>String(s).replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const text=(x,y,s,size=18,color='#aab8ca',extra='')=>`<text x="${x}" y="${y}" font-size="${size}" fill="${color}" ${extra}>${esc(s)}</text>`;
const line=(x,y,x2,y2,color='#2e3b4d',extra='')=>`<line x1="${x}" y1="${y}" x2="${x2}" y2="${y2}" stroke="${color}" ${extra}/>`;
const sx=t=>X+t/220*PW;
function panel(rs,field,title,top,min,max,ticks){
 const bottom=top+200,sy=v=>bottom-(v-min)/(max-min)*200;
 let s=`<rect x="48" y="${top-72}" width="1344" height="302" rx="7" fill="#172130"/>`;
 s+=text(75,top-36,title,24,'#eef4fc');s+=text(1360,top-36,'wartość surowa · bez przeliczenia na °C',16,'#aab8ca','text-anchor="end"');
 for(const v of ticks){s+=line(X,sy(v),X+PW,sy(v))+text(X-18,sy(v)+6,v,17,'#aab8ca','text-anchor="end"');}
 for(const t of [0,40,80,120,160,200,220])s+=line(sx(t),top,sx(t),bottom,'#263345')+text(sx(t),bottom+25,t,16,'#aab8ca','text-anchor="middle"');
 for(const r of rs){assert(r[field]>=min&&r[field]<=max);s+=`<circle data-sequence="${r.sequence_count}" data-met="${r.met_seconds}" data-value="${r[field]}" cx="${sx(r.t_seconds).toFixed(3)}" cy="${sy(r[field]).toFixed(3)}" r="5" fill="#7ec6ff" stroke="#0d1827" stroke-width="1.4"><title>MET ${r.met_seconds}; ${field}=${r[field]}; seq=${r.sequence_count}</title></circle>`;}
 return s;
}
function body(before){const rs=before?rows.filter(x=>x.reference_union):rows;
 let s=`<rect width="${W}" height="${H}" fill="#0e1723"/>`;
 s+=text(48,45,'CANVAS / OBSERWACJA #14780429 / TEN SAM PLIK OGG',17,'#8ca5be','letter-spacing="1.4"');
 s+=text(48,95,before?'Dire Wolf + gr-satellites':'Telemetry Yield · progressive_v3',36,'#f0f6ff','font-weight="700"');
 s+=text(48,130,before?'Referencja: unia ramek odzyskanych przez dwa niezależne dekodery.':'Własny dekoder progresywny: pełny bank prób na tym samym audio.',20);
 s+=text(48,208,rs.length,60,'#7ec6ff','font-weight="700"');s+=text(145,187,'rekordy beacona',23,'#e9f1fa');s+=text(145,215,'APID 0x20 · 264 bajty / PDU',17);
 s+=text(550,183,before?'31':'118',39,'#e9f1fa','font-weight="700"');s+=text(550,215,'wszystkich unikalnych ramek AX.25 UI',17);
 s+=text(1040,183,'326,36 s',34,'#e9f1fa','font-weight="700"');s+=text(1040,215,'długość oryginalnego OGG',17);
 s+=panel(rs,'solar_panel1_temp_raw','Czujnik temperatury panelu słonecznego 1',335,1760,1880,[1760,1790,1820,1850,1880]);
 s+=panel(rs,'battery1_temp_raw','Czujnik temperatury akumulatora 1',665,2105,2120,[2105,2110,2115,2120]);
 s+=text(745,918,'Czas pokładowy MET − 829763 [s] · punkty bez interpolacji',20,'#c9d8e9','text-anchor="middle"');
 s+=text(48,941,'Odzyskane rekordy:',17);
 for(const r of rows){const yes=!before||r.reference_union;s+=`<rect x="${sx(r.t_seconds)-3}" y="944" width="6" height="19" fill="${yes?'#7ec6ff':'#0e1723'}" stroke="${yes?'#7ec6ff':'#596a80'}"/>`;}
 s+=text(48,995,'Kontury: znane rekordy z unii wyników. Nie oznaczają wszystkich pakietów faktycznie nadanych.',16);
 s+=text(48,1028,'14.08.2026, 00:31:48 UTC · stacja 2865 · OGG SHA-256: '+meta.source.sha256.slice(0,24)+'…',16);
 s+=text(48,1056,'Przykład dobrany po analizie wyników. Wykres z rzeczywistych danych, nie zrzut produkcyjnej Grafany.',16);
 s+=text(48,1081,'Identyczne osie i skala w obu wersjach. Źródła ramek, odebrane FCS i odczytane wartości: data/.',16);
 return `<g font-family="Arial, sans-serif">${s}</g>`;
}
const out=p.join(__dirname,'figures');fs.mkdirSync(out,{recursive:true});
for(const before of [true,false]){const label=before?'before':'after';fs.writeFileSync(p.join(out,label+'.svg'),`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-labelledby="title"><title id="title">${before?'Referencja: 2':'Telemetry Yield: 36'} rzeczywiście odzyskanych rekordów telemetrii CANVAS</title>${body(before)}</svg>`);}
fs.writeFileSync(p.join(out,'comparison.svg'),`<svg xmlns="http://www.w3.org/2000/svg" width="${2*W+24}" height="${H}" viewBox="0 0 ${2*W+24} ${H}"><rect width="100%" height="100%" fill="#fff"/>${body(true)}<g transform="translate(${W+24},0)">${body(false)}</g></svg>`);
console.log('Generated matched SVGs: before (2 real beacons), after (36), same axes, no interpolation.');
