// Read-only campaign import + deterministic figure evidence extraction.
const fs=require('node:fs'),p=require('node:path'),crypto=require('node:crypto'),cp=require('node:child_process'),assert=require('node:assert/strict');
const yaml=require('js-yaml');
const root='/home/ubuntu/telemetry-yield',id=14780429;
const input=p.join(root,`work/publication-speed-restart-20260912-v1/comparison/obs-${id}/result.json`);
const out=p.join(__dirname,'data');if(fs.existsSync(out)&&fs.readdirSync(out).length)throw Error('Evidence exists; do not overwrite.');fs.mkdirSync(out,{recursive:true});
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
const bytes=fs.readFileSync(input),r=JSON.parse(bytes);assert.equal(r.status,'complete');
const selected=['progressive_v3','direwolf','gr_satellites'];
const nativeIdentity=r.decoders.progressive_v3.artifacts[0];const nativeBytes=fs.readFileSync(nativeIdentity.path);assert.equal(sha(nativeBytes),nativeIdentity.sha256);
const native=JSON.parse(nativeBytes);assert.equal(native.complete,true);assert.equal(native.bit_repair_enabled,false);
assert.equal(sha(fs.readFileSync(r.source.path)),r.source.sha256);
const same=(a,b)=>assert.deepEqual([...new Set(a)].sort(),[...new Set(b)].sort());
for(const a of selected){assert.equal(r.decoders[a].status,'complete');assert.equal(r.decoders[a].input.sha256,r.input.sha256);assert.equal(r.decoders[a].source.sha256,r.source.sha256);}
same(native.frame_with_fcs_hex.map(h=>h.slice(0,-4)),r.decoders.progressive_v3.strict_ui_payloads);

const dw=r.decoders.direwolf.artifacts.find(x=>x.path.endsWith('run.stdout.log'));const dwBytes=fs.readFileSync(dw.path);assert.equal(sha(dwBytes),dw.sha256);
const dwFrames=[];let frame=[];
for(const l of dwBytes.toString('utf8').replace(/\x1b\[[0-?]*[ -/]*[@-~]/g,'').split('\n')){
 const m=l.match(/^\s+([0-9a-f]{3,}):  (.*)$/i);if(!m)continue;const offset=parseInt(m[1],16);
 if(offset===0&&frame.length){dwFrames.push(Buffer.from(frame).toString('hex'));frame=[];}
 assert.equal(offset,frame.length);const hex=m[2].slice(0,47).trim();assert(/^(?:[0-9a-f]{2})(?: [0-9a-f]{2})*$/i.test(hex));frame.push(...hex.split(' ').map(x=>parseInt(x,16)));
}if(frame.length)dwFrames.push(Buffer.from(frame).toString('hex'));same(dwFrames,r.decoders.direwolf.strict_ui_payloads);
const gr=r.decoders.gr_satellites.artifacts.find(x=>x.path.endsWith('frames.kiss'));const grBytes=fs.readFileSync(gr.path);assert.equal(sha(grBytes),gr.sha256);
const grFrames=[];let packet=[],escape=false;
for(const b of grBytes){if(b===0xc0){if(packet.length){if((packet[0]&15)===0){grFrames.push(Buffer.from(packet.slice(1)).toString('hex'));}else{assert.equal(packet[0],9);assert.equal(packet.length,9);/* gr-satellites KISS timestamp metadata, not a PDU */}}packet=[];escape=false;continue;}if(escape){assert(b===0xdc||b===0xdd);packet.push(b===0xdc?0xc0:0xdb);escape=false;}else if(b===0xdb)escape=true;else packet.push(b);}
assert.equal(packet.length,0);same(grFrames,r.decoders.gr_satellites.strict_ui_payloads);

// Independently calculate field offsets from the pinned schema, including bits.
const schemaBytes=fs.readFileSync(p.join(__dirname,'sources/canvas.ksy')),schema=yaml.load(schemaBytes);assert.equal(schema.meta.endian,'be');
const positions={};let bit=28*8;
for(const f of schema.types.beacon_t.seq){const t=f.type;positions[f.id]=bit/8;if(/^b\d+$/.test(t))bit+=Number(t.slice(1));else{assert.equal(bit%8,0);assert(/^[usf][1248]$/.test(t));bit+=Number(t.slice(1))*8;}}
assert.equal(bit/8,264);
for(const [name,offset] of Object.entries({bcn_des_met_time_sec:44,bcn_eps_batt1_temp:98,bcn_solar_panel1_temp:104,bcn_eps_battery_soc:146}))assert.equal(positions[name],offset);
const hex=native.frame_with_fcs_hex.join('\n')+'\n';
const run=cp.spawnSync(p.join(__dirname,'extract-beacons'),[],{input:hex,encoding:'utf8'});assert.equal(run.status,0,run.stderr);
const lines=run.stdout.trim().split('\n'),header=lines.shift().split(',');const raw=lines.map(l=>Object.fromEntries(l.split(',').map((v,i)=>[header[i],i===0?v:Number(v)])));
const baseline=new Set([...dwFrames,...grFrames]);const origin=Math.min(...raw.map(x=>x.met_seconds));
const rows=raw.map(x=>({...x,t_seconds:x.met_seconds-origin,direwolf:dwFrames.includes(x.pdu_hex),gr_satellites:grFrames.includes(x.pdu_hex),reference_union:baseline.has(x.pdu_hex)})).sort((a,b)=>a.t_seconds-b.t_seconds);
assert.equal(rows.length,36);assert.equal(new Set(rows.map(x=>x.met_seconds)).size,36);assert(rows.every(x=>x.stored_flag===0));
assert.equal(rows.filter(x=>x.reference_union).length,2);
for(const x of rows){const b=Buffer.from(x.pdu_hex,'hex');assert.equal(x.battery1_temp_raw,b.readUInt16BE(positions.bcn_eps_batt1_temp));assert.equal(x.solar_panel1_temp_raw,b.readUInt16BE(positions.bcn_solar_panel1_temp));assert.equal(x.met_seconds,b.readUInt32BE(positions.bcn_des_met_time_sec));}
const paperRows=JSON.parse(fs.readFileSync(p.join(root,'publication/decoder-paper-v1/evidence/observations.json')));
const meta={schema:'telemetry-yield-real-payload-figure-v1',prepared_utc:new Date().toISOString(),observation_id:id,source_observation_url:`https://network.satnogs.org/observations/${id}/`,observation_start_utc:r.row.start,observation_end_utc:r.row.end,station_id:r.row.ground_station,audio_seconds:r.audio_seconds,source:r.source,input:r.input,result_sha256:sha(bytes),native_result_sha256:sha(nativeBytes),direwolf_stdout_sha256:sha(dwBytes),gr_kiss_sha256:sha(grBytes),schema_url:'https://gitlab.com/librespacefoundation/satnogs/satnogs-decoders/-/blob/dcf8af2c41fa9ac9ac755173719400ccd1a6c33f/ksy/canvas.ksy',schema_sha256:sha(schemaBytes),met_origin_seconds:origin,met_span_seconds:Math.max(...rows.map(x=>x.t_seconds)),all_pdu_counts:{progressive:native.frame_with_fcs_hex.length,direwolf:new Set(dwFrames).size,gr_satellites:new Set(grFrames).size,reference_union:baseline.size},beacon_counts:{progressive:rows.length,reference_union:rows.filter(x=>x.reference_union).length,added:rows.filter(x=>!x.reference_union).length,lost:0},selection:'Post-hoc illustrative positive observation; not a representative effect-size estimate.',in_interim_paper_189_snapshot:paperRows.some(x=>x.id===id),parsing:'264-byte CANVAS APID0x20, CCSDS version0 TLM/secondary header, exact lengths; raw u16BE readings without physical-unit conversion.',verification:run.stderr.trim(),application_checksum_verified:false,external_fcs_note:'External outputs strip received FCS; their internal FCS checks are relied upon. Native received FCS independently rechecked for every packet.'};
fs.writeFileSync(p.join(out,'beacons.json'),JSON.stringify(rows,null,2)+'\n');fs.writeFileSync(p.join(out,'provenance.json'),JSON.stringify(meta,null,2)+'\n');
fs.writeFileSync(p.join(out,'native-frames-with-received-fcs.hex'),hex);fs.writeFileSync(p.join(out,'native-result.json'),nativeBytes);
fs.writeFileSync(p.join(out,'direwolf.stdout.log'),dwBytes);fs.writeFileSync(p.join(out,'gr-satellites.kiss'),grBytes);
fs.writeFileSync(p.join(out,'beacons.csv'),Object.keys(rows[0]).join(',')+'\n'+rows.map(r=>Object.values(r).join(',')).join('\n')+'\n');
fs.writeFileSync(p.join(out,'checksums.sha256'),fs.readdirSync(out).sort().map(n=>sha(fs.readFileSync(p.join(out,n)))+'  '+n).join('\n')+'\n');
console.log(JSON.stringify({beacons:meta.beacon_counts,packets:meta.all_pdu_counts,met_origin:origin,met_span:meta.met_span_seconds,reference_rows:rows.filter(x=>x.reference_union).map(({pdu_hex,...x})=>x),verification:meta.verification},null,2));
