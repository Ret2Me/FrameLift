// Publication-only snapshot generator; never edits or starts a receiver campaign.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = '/home/ubuntu/telemetry-yield';
const campaign = path.join(root, 'work/publication-speed-restart-20260912-v1');
const out = path.join(__dirname, 'evidence');
if (fs.existsSync(out)) throw Error('Evidence already frozen; choose a new version, do not overwrite.');
fs.mkdirSync(out);
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
const arms = ['progressive_v3', 'innovation_v2', 'innovation_v1_no_codec', 'direwolf', 'gr_satellites'];
const cutoff = new Date().toISOString();
const cohortBytes = fs.readFileSync(path.join(root, 'work/publication-execution-20260912-v1/historical-acquisition-v1/cohort.json'));
const cohort = JSON.parse(cohortBytes);
const files = fs.readdirSync(path.join(campaign, 'comparison')).filter(n => /^obs-\d+$/.test(n)).sort();
const rows = [], pending = [];
for (const dir of files) {
  const filename = path.join(campaign, 'comparison', dir, 'result.json');
  if (!fs.existsSync(filename)) continue;
  const bytes = fs.readFileSync(filename), r = JSON.parse(bytes);
  if (r.status !== 'complete' || r.finished_utc > cutoff || !arms.every(a => r.decoders[a]?.status === 'complete')) {
    pending.push({id:r.observation_id,status:r.status}); continue;
  }
  const sets = Object.fromEntries(arms.map(a => [a,[...new Set(r.decoders[a].strict_ui_payloads)].sort()]));
  const baseline = [...new Set([...sets.direwolf, ...sets.gr_satellites])];
  const ours = sets.progressive_v3;
  rows.push({id:r.observation_id, result_sha256:sha(bytes), source:r.source, input:r.input,
    start:r.row.start, station:r.row.ground_station, waterfall_status:r.row.waterfall_status,
    archive_status:r.row.status, archive_demoddata:r.row.demoddata, audio_seconds:r.audio_seconds,
    finished_utc:r.finished_utc, payloads:sets,
    counts:Object.fromEntries(arms.map(a=>[a,sets[a].length])),
    reference_union:baseline.length, added:ours.filter(p=>!baseline.includes(p)).length,
    lost:baseline.filter(p=>!ours.includes(p)).length,
    processes:Object.fromEntries(arms.map(a=>[a,r.decoders[a].process])),
    crc_evidence:Object.fromEntries(arms.map(a=>[a,r.decoders[a].crc_evidence]))});
}
const sum = (rs,f) => rs.reduce((n,r)=>n+f(r),0);
const summarize = rs => {const p=sum(rs,r=>r.counts.progressive_v3), b=sum(rs,r=>r.reference_union); return {
  observations:rs.length, ours:p, reference_union:b, added:sum(rs,r=>r.added),lost:sum(rs,r=>r.lost),
  net:p-b, net_percent:b ? 100*(p-b)/b : null, observations_with_gain:rs.filter(r=>r.added>0).length,
  arm_frames:Object.fromEntries(arms.map(a=>[a,sum(rs,r=>r.counts[a])])),
  arm_wall_seconds:Object.fromEntries(arms.map(a=>[a,sum(rs,r=>r.processes[a].wall_seconds)])),
  audio_seconds:sum(rs,r=>r.audio_seconds)}; };
const summary={schema:'telemetry-yield-paper-snapshot-v1',cutoff_utc:cutoff, status:'preliminary_complete_case_snapshot',
  planned_observations:266, remaining_observations:266-rows.length, cohort_sha256:sha(cohortBytes),
  all:summarize(rows),confirmed_waterfall_signal:summarize(rows.filter(r=>r.waterfall_status==='with-signal')),
  confirmed_waterfall_planned:24, pending_seen:pending,
  caveats:['Repeated historical evaluation after performance-only restart; not an unseen holdout.',
    'Unique AX.25 UI PDUs within observation, not globally unique spacecraft telemetry.',
    'Reference union = controlled replays of Dire Wolf and gr-satellites, not the native SatNOGS decoder.',
    'Independent received-FCS recheck for native arms; external arms strip FCS and rely on their internal checks.',
    'Concurrent shared-host wall times; common PCM conversion excluded from individual arm timing.',
    'Snapshot derived from committed result summaries; final artifact-level campaign analysis pending.']};
fs.writeFileSync(path.join(out,'observations.json'),JSON.stringify(rows,null,2)+'\n');
fs.writeFileSync(path.join(out,'summary.json'),JSON.stringify(summary,null,2)+'\n');
for (const name of ['receiver-freeze.json','protocol.md','prior-evaluation-exposure.json'])
  fs.copyFileSync(path.join(campaign,name),path.join(out,name));
fs.copyFileSync(path.join(campaign,'comparison/grsat-profile.yml'),path.join(out,'grsat-profile.yml'));
fs.copyFileSync(path.join(campaign,'comparison/manifest.json'),path.join(out,'campaign-manifest.json'));
fs.writeFileSync(path.join(out,'checksums.sha256'),fs.readdirSync(out).sort().map(n=>`${sha(fs.readFileSync(path.join(out,n)))}  ${n}`).join('\n')+'\n');
console.log(JSON.stringify(summary,null,2));
