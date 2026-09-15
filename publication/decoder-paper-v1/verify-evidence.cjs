const fs=require('node:fs'), path=require('node:path'), assert=require('node:assert/strict'), crypto=require('node:crypto');
const dir=path.join(__dirname,'evidence');
const read=n=>JSON.parse(fs.readFileSync(path.join(dir,n),'utf8'));
const rows=read('observations.json'), summary=read('summary.json');
const arms=['progressive_v3','innovation_v2','innovation_v1_no_codec','direwolf','gr_satellites'];
const sum=(rs,f)=>rs.reduce((a,r)=>a+f(r),0);
assert.equal(new Set(rows.map(r=>r.id)).size,rows.length);
for(const r of rows){
  for(const a of arms){assert.equal(r.counts[a],r.payloads[a].length);assert.equal(new Set(r.payloads[a]).size,r.payloads[a].length);assert.equal(r.processes[a].success,true);assert.equal(r.processes[a].timed_out,false);}
  const b=new Set([...r.payloads.direwolf,...r.payloads.gr_satellites]), p=new Set(r.payloads.progressive_v3);
  assert.equal(r.reference_union,b.size);
  assert.equal(r.added,[...p].filter(x=>!b.has(x)).length);
  assert.equal(r.lost,[...b].filter(x=>!p.has(x)).length);
  assert(new Date(r.finished_utc)<=new Date(summary.cutoff_utc));
}
for(const [s,rs] of [[summary.all,rows],[summary.confirmed_waterfall_signal,rows.filter(r=>r.waterfall_status==='with-signal')]]){
  assert.equal(s.observations,rs.length);assert.equal(s.ours,sum(rs,r=>r.counts.progressive_v3));
  assert.equal(s.reference_union,sum(rs,r=>r.reference_union));assert.equal(s.added,sum(rs,r=>r.added));assert.equal(s.lost,sum(rs,r=>r.lost));
  assert.equal(s.net,s.added-s.lost);assert.equal(s.net,s.ours-s.reference_union);
  for(const a of arms){assert.equal(s.arm_frames[a],sum(rs,r=>r.counts[a]));assert.equal(s.arm_wall_seconds[a],sum(rs,r=>r.processes[a].wall_seconds));}
}
assert.equal(summary.planned_observations,rows.length+summary.remaining_observations);
if(process.argv.includes('--local')){
  const root='/home/ubuntu/telemetry-yield';
  const cohortBytes=fs.readFileSync(path.join(root,'work/publication-execution-20260912-v1/historical-acquisition-v1/cohort.json'));
  const digest=b=>crypto.createHash('sha256').update(b).digest('hex');
  assert.equal(digest(cohortBytes),summary.cohort_sha256);
  const ids=new Set(JSON.parse(cohortBytes).observations.map(r=>r.id));
  assert.equal(ids.size,summary.planned_observations);
  for(const r of rows){
    assert(ids.has(r.id));
    const bytes=fs.readFileSync(path.join(root,`work/publication-speed-restart-20260912-v1/comparison/obs-${r.id}/result.json`));
    assert.equal(digest(bytes),r.result_sha256);
    const original=JSON.parse(bytes);
    for(const a of arms){
      assert.equal(original.decoders[a].input.sha256,r.input.sha256);
      assert.equal(original.decoders[a].source.sha256,r.source.sha256);
      assert.deepEqual([...new Set(original.decoders[a].strict_ui_payloads)].sort(),r.payloads[a]);
    }
  }
}
console.log(`PASS: ${rows.length} frozen complete cases, five-arm sets/counts/timings, cutoff, gains/losses, subgroup${process.argv.includes('--local')?', local cohort membership and SHA/common-input bindings':''}.`);
