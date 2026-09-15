//! Bounded, offline protocol-evidence audit; this does not demodulate or qualify telemetry.
//! The LilacSat-1 main output is an 81-byte KISS-stream chunk, not an integrity-checked PDU.
use serde::Serialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, fs, io::Write, path::Path};
use telemetry_yield_rs::formats;

const MAX_PACKET: usize = 4096; // Audit resource bound, NOT a protocol maximum.
const LAYOUTS: &[(u16, &str, usize)] = &[
    (0x1ca1, "Hk_STM32", 76),
    (0x1ca2, "Cfg", 84),
    (0x1ca3, "Hk_AVR", 24),
    (0xaaa1, "LilacSat1Tlm", 66),
    (0x1aa1, "Hk_OBC", 70),
    (0x1aa2, "ADCS_Par", 96),
    (0x1aa8, "Orbit_Par", 104),
    (0x1aa9, "OBC_Threshold", 26),
    (0x1aaa, "Orbital_Par", 85),
    (0x1ea1, "Hk_EPS", 82),
    (0x1ea2, "EPS_Onboard", 27),
    (0x1ea3, "EPS_Reboot", 38),
    (0x1ea4, "EPS_Command", 42),
    (0x1ea5, "EPS_Error", 42),
];

#[derive(Clone, Copy, Serialize)]
struct Position {
    outer_record_index: usize,
    chunk_byte: usize,
}

#[derive(Serialize)]
struct Packet {
    hex: String,
    bytes: usize,
    first: Position,
    last: Position,
    layout: Value,
}

#[derive(Default, Serialize)]
struct StrictStream {
    packets: Vec<Packet>,
    leading_unframed_bytes: usize,
    empty_delimiters: usize,
    invalid_escape_segments: usize,
    oversized_segments: usize,
    trailing_incomplete_segments: usize,
    #[serde(skip)]
    buffer: Vec<u8>,
    #[serde(skip)]
    opened: bool,
    #[serde(skip)]
    escaped: bool,
    #[serde(skip)]
    rejected: bool,
    #[serde(skip)]
    first: Option<Position>,
    #[serde(skip)]
    last: Option<Position>,
}

impl StrictStream {
    fn feed(&mut self, byte: u8, position: Position) {
        if byte == 0xc0 {
            if self.opened {
                if self.escaped && !self.rejected {
                    self.invalid_escape_segments += 1;
                    self.rejected = true;
                }
                if !self.rejected && !self.buffer.is_empty() {
                    self.packets.push(Packet {
                        hex: hex::encode(&self.buffer),
                        bytes: self.buffer.len(),
                        first: self.first.unwrap(),
                        last: self.last.unwrap(),
                        layout: classify(&self.buffer),
                    });
                } else if !self.rejected {
                    self.empty_delimiters += 1;
                }
            }
            self.buffer.clear();
            self.opened = true;
            self.escaped = false;
            self.rejected = false;
            self.first = None;
            self.last = None;
            return;
        }
        if !self.opened {
            self.leading_unframed_bytes += 1;
            return;
        }
        if self.rejected {
            return;
        }
        if self.first.is_none() {
            self.first = Some(position);
        }
        self.last = Some(position);
        if self.escaped {
            self.escaped = false;
            match byte {
                0xdc => self.buffer.push(0xc0),
                0xdd => self.buffer.push(0xdb),
                _ => {
                    self.invalid_escape_segments += 1;
                    self.rejected = true;
                }
            }
        } else if byte == 0xdb {
            self.escaped = true;
        } else {
            self.buffer.push(byte);
        }
        if self.buffer.len() > MAX_PACKET {
            self.oversized_segments += 1;
            self.buffer.clear();
            self.rejected = true;
        }
    }
    fn finish(&mut self) {
        if self.opened && (self.first.is_some() || self.rejected || self.escaped) {
            self.trailing_incomplete_segments += 1;
        }
    }
}

// Minimum readable sizes only, from pinned gr-satellites v5.9.0 by70_1.py.
// No generic CCSDS header/sequence semantics or checksum algorithm is invented here.
fn classify(bytes: &[u8]) -> Value {
    if bytes.len() < 14 {
        return json!({"status":"shorter_than_opaque_header_and_magic", "integrity":"unresolved"});
    }
    let magic = u16::from_be_bytes([bytes[12], bytes[13]]);
    if let Some((_, name, body_bytes)) = LAYOUTS.iter().find(|x| x.0 == magic) {
        let required = 12 + body_bytes;
        json!({"status":if bytes.len() < required {"known_magic_truncated_layout"}
                else if bytes.len() == required {"known_magic_exact_layout_size"}
                else {"known_magic_minimum_layout_size_with_uninterpreted_trailing_bytes"},
            "name":name,"magic":format!("{magic:04x}"),"minimum_total_bytes":required,
            "opaque_header_hex":hex::encode(&bytes[..12]),
            "trailing_bytes":bytes.len().saturating_sub(required),
            "independent_crc_checked":false,"integrity":"unresolved",
            "physical_continuity_verified":false,"sequence_semantics":"unknown"})
    } else {
        json!({"status":"unrecognized_magic", "magic":format!("{magic:04x}"),
            "opaque_header_hex":hex::encode(&bytes[..12]), "integrity":"unresolved"})
    }
}

// Reproduce the pinned GR KISS-no-control-byte parser for *comparison only*.
// It accepts a leading unterminated fragment and drops invalid escape codes.
fn gr_compatible(bytes: &[u8]) -> Vec<Vec<u8>> {
    let mut packets = vec![];
    let mut packet = vec![];
    let mut escaped = false;
    for &byte in bytes {
        if byte == 0xc0 {
            if !packet.is_empty() {
                packets.push(std::mem::take(&mut packet));
            }
        } else if escaped {
            if byte == 0xdc {
                packet.push(0xc0);
            } else if byte == 0xdd {
                packet.push(0xdb);
            }
            escaped = false;
        } else if byte == 0xdb {
            escaped = true;
        } else {
            packet.push(byte);
        }
    }
    packets
}

fn identity(path: &Path) -> Result<Value, String> {
    let bytes = fs::read(path).map_err(|e| format!("{}: {e}", path.display()))?;
    Ok(
        json!({"path":fs::canonicalize(path).map_err(|e|e.to_string())?,
        "bytes":bytes.len(),"sha256":hex::encode(Sha256::digest(&bytes))}),
    )
}

fn verify_identity(value: &Value) -> Result<(), String> {
    let path = value["path"].as_str().ok_or("missing identity path")?;
    let actual = identity(Path::new(path))?;
    if actual["bytes"] != value["bytes"] || actual["sha256"] != value["sha256"] {
        return Err(format!("identity mismatch: {path}"));
    }
    Ok(())
}

fn analyze(root: &Path) -> Result<Value, String> {
    let result_path = root.join("result.json");
    let prior: Value = serde_json::from_slice(&fs::read(&result_path).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    let arm = &prior["arms"]["gr_satellites"];
    if prior["status"] != "complete"
        || arm["status"] != "complete"
        || arm["process"]["success"] != true
        || arm["process"]["timed_out"] != false
    {
        return Err("GR evidence is not a complete successful run".into());
    }
    for v in [
        &prior["source"],
        &prior["shared_input"],
        &arm["executable"],
        &arm["profile"]["candidate"],
    ] {
        verify_identity(v)?;
    }
    let profile: Value = serde_json::from_slice(
        &fs::read(
            arm["profile"]["candidate"]["path"]
                .as_str()
                .ok_or("missing profile path")?,
        )
        .map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    if profile["norad"] != 44530
        || profile["transmitters"]["selected"]["framing"] != "LilacSat-1"
        || profile["transmitters"]["selected"]["modulation"] != "BPSK"
        || profile["transmitters"]["selected"]["baudrate"] != 9600
    {
        return Err("not the pinned Taurus main-output BPSK9600 profile".into());
    }
    let path = root.join("gr_satellites/frames.kiss");
    if fs::metadata(&path).map_err(|e| e.to_string())?.len() > 64 * 1024 * 1024 {
        return Err("outer capture exceeds64MiB audit bound".into());
    }
    let bytes = fs::read(&path).map_err(|e| e.to_string())?;
    if !bytes.is_empty() && (bytes.first() != Some(&0xc0) || bytes.last() != Some(&0xc0)) {
        return Err("outer capture is not delimiter bounded".into());
    }
    let outer = formats::parse_kiss(&bytes)?;
    if outer.malformed_records != 0
        || !outer.other_commands.is_empty()
        || outer
            .data_frames
            .iter()
            .any(|r| r.port != 0 || r.payload.len() != 81)
    {
        return Err("malformed/unsupported outer capture or non81byte mainchunk".into());
    }
    let unique: BTreeSet<_> = outer
        .data_frames
        .iter()
        .map(|x| hex::encode(&x.payload))
        .collect();
    let declared: BTreeSet<String> =
        serde_json::from_value(arm["candidate_pdus"].clone()).map_err(|e| e.to_string())?;
    if unique != declared || arm["unique_count"].as_u64() != Some(unique.len() as u64) {
        return Err("raw capture does not reproduce recorded candidate set".into());
    }
    let mut stream = StrictStream::default();
    let mut concatenated = vec![];
    let mut chunks = vec![];
    for record in &outer.data_frames {
        for (chunk_byte, &byte) in record.payload.iter().enumerate() {
            stream.feed(
                byte,
                Position {
                    outer_record_index: record.record_index,
                    chunk_byte,
                },
            );
        }
        concatenated.extend_from_slice(&record.payload);
        chunks.push(json!({"outer_record_index":record.record_index,
            "capture_timestamp_ms_not_rf_time":record.timestamp_ms,
            "bytes":record.payload.len(),"sha256":hex::encode(Sha256::digest(&record.payload))}));
    }
    stream.finish();
    let compatible = gr_compatible(&concatenated);
    let compatible_rows: Vec<_> = compatible
        .iter()
        .map(|p| {
            json!({"hex":hex::encode(p),
        "bytes":p.len(),"layout":classify(p)})
        })
        .collect();
    let known = |p: &Value| {
        p["status"]
            .as_str()
            .is_some_and(|s| s.starts_with("known_magic_"))
    };
    Ok(
        json!({"observation_id":prior["observation_id"],"result":identity(&result_path)?,
        "outer_capture":identity(&path)?,"source":prior["source"],"shared_input":prior["shared_input"],
        "input_identities_reverified":true,"candidate_set_reproduced":true,
        "raw_main_chunk_count":chunks.len(),"unique_raw_main_chunk_count":unique.len(),
        "total_main_stream_bytes":concatenated.len(),"chunks_in_capture_order":chunks,
        "outer_timestamp_record_count":outer.timestamp_commands.len(),
        "strict_nested_packet_count":stream.packets.len(),
        "strict_known_magic_count":stream.packets.iter().filter(|p|known(&p.layout)).count(),
        "strict_stream":stream,"gr_compatible_nested_packet_count":compatible.len(),
        "gr_compatible_known_magic_count":compatible_rows.iter().filter(|p|known(&p["layout"])).count(),
        "gr_compatible_packets":compatible_rows,
        "independently_integrity_validated_telemetry_packets":0,
        "validated_new_rust_frames":0,"validation_status":"unresolved_no_supported_integrity_check",
        "continuity_limit":"capture order retained, but merged Viterbi branch IDs and missing-chunk RF positions absent; no physical continuity or sequence claim",
        "codec2_voice":"separate demux output omitted, no recovered voice claim"}),
    )
}

fn run() -> Result<(), String> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 4 {
        return Err("usage: taurus_validation_probe OUT.json PINNED_SOURCE_DIR OBS_ROOT...".into());
    }
    if Path::new(&args[1]).exists() {
        return Err("refusing to overwrite prior evidence".into());
    }
    let source = Path::new(&args[2]);
    let mut sources = vec![];
    for (name, pinned) in [
        (
            "lilacsat_1_deframer.py",
            "0f65f716d58ac7ec5b5fb0e77bb39158a3a9fe637865d3591d4e25cccfdf7409",
        ),
        (
            "by70_1.py",
            "a815f20346e316b02f21c9cab24ec08c4fa25a7fed7823065adb726e89d9e140",
        ),
        (
            "kiss_to_pdu.py",
            "0508a04b714ccd4f165c006ec48f4198db5a90722787442a779487e0910e1909",
        ),
        (
            "Taurus-1.yml",
            "fcbd0a5c3a5f049be04a12c035246188544c1ec9f6fb40816d1e09411b02b551",
        ),
    ] {
        let item = identity(&source.join(name))?;
        if item["sha256"] != pinned {
            return Err(format!("pinned source changed:{name}"));
        }
        sources.push(item);
    }
    for (name, pinned) in [
        (
            "lilacsat1_demux_impl.cc",
            "82e457d13c7149f1085c6335f92772a968527ffb4109e652d9d868e4750a743c",
        ),
        (
            "lilacsat1_demux_impl.h",
            "437a8b440ebf83ecb861ec7b04ca53b57f19813c3dc9fe1cc5283eeb72f273c1",
        ),
    ] {
        let item = identity(&source.join(name))?;
        if item["sha256"] != pinned {
            return Err(format!("pinned source changed:{name}"));
        }
        sources.push(item);
    }
    let rows = args[3..]
        .iter()
        .map(|p| analyze(Path::new(p)))
        .collect::<Result<Vec<_>, _>>()?;
    let result = json!({"schema":"taurus-stream-evidence-v1","status":"complete",
        "executable":identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,
        "reference":"gr-satellites v5.9.0","sources":sources,"rows":rows,
        "strict_packet_resource_bound_bytes":MAX_PACKET,
        "protocol_evidence":{"fec":"rate1/2 K7 continuous Viterbi, two alignment branches",
            "viterbi_is_integrity_check":false,"asm":"32bit CCSDS ASM, up to4 mismatches",
            "main_chunk_bytes":81,"reed_solomon_in_this_deframer":false,
            "independent_crc_check_in_this_deframer":false,
            "required_downstream_transport":"KISS no control byte",
            "layout_match_is_integrity":false,"header_and_sequence_semantics":"unresolved"},
        "publication_ready":false,"new_rust_yield_claim":false});
    let mut output = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&args[1])
        .map_err(|e| e.to_string())?;
    output
        .write_all(&serde_json::to_vec_pretty(&result).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    println!(
        "{}",
        json!({"output":args[1],"status":"complete","observations":result["rows"].as_array().unwrap().len()})
    );
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("{e}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn parse(bytes: &[u8]) -> StrictStream {
        let mut s = StrictStream::default();
        for (i, &b) in bytes.iter().enumerate() {
            s.feed(
                b,
                Position {
                    outer_record_index: i / 81,
                    chunk_byte: i % 81,
                },
            );
        }
        s.finish();
        s
    }
    fn encode(p: &[u8]) -> Vec<u8> {
        let mut v = vec![0xc0];
        for &b in p {
            match b {
                0xc0 => v.extend([0xdb, 0xdc]),
                0xdb => v.extend([0xdb, 0xdd]),
                _ => v.push(b),
            }
        }
        v.push(0xc0);
        v
    }
    #[test]
    fn roundtrip_all_octets_and_chunk_boundaries() {
        let p: Vec<_> = (0..=255).collect();
        let s = parse(&encode(&p));
        assert_eq!(s.packets.len(), 1);
        assert_eq!(s.packets[0].hex, hex::encode(&p));
        assert!(s.packets[0].last.outer_record_index > 0);
    }
    #[test]
    fn duplicates_preserved() {
        let p = [encode(&[1, 2]), encode(&[1, 2])].concat();
        let s = parse(&p);
        assert_eq!(s.packets.len(), 2);
    }
    #[test]
    fn leading_and_trailing_not_promoted() {
        let s = parse(&[1, 2, 0xc0, 3, 0xc0, 4]);
        assert_eq!(s.leading_unframed_bytes, 2);
        assert_eq!(s.packets[0].hex, "03");
        assert_eq!(s.trailing_incomplete_segments, 1);
        assert_eq!(gr_compatible(&[1, 2, 0xc0, 3, 0xc0, 4]).len(), 2);
    }
    #[test]
    fn invalid_escapes_reject_segment_and_recover() {
        let s = parse(&[0xc0, 1, 0xdb, 0, 2, 0xc0, 3, 0xc0]);
        assert_eq!(s.invalid_escape_segments, 1);
        assert_eq!(s.packets.len(), 1);
        assert_eq!(s.packets[0].hex, "03");
    }
    #[test]
    fn dangling_escape_never_completes() {
        let s = parse(&[0xc0, 1, 0xdb, 0xc0]);
        assert_eq!(s.invalid_escape_segments, 1);
        assert!(s.packets.is_empty());
    }
    #[test]
    fn oversize_rejected_not_truncated() {
        let mut v = vec![0xc0];
        v.extend(vec![1; MAX_PACKET + 1]);
        v.extend([0xc0, 2, 0xc0]);
        let s = parse(&v);
        assert_eq!(s.oversized_segments, 1);
        assert_eq!(s.packets.len(), 1);
        assert_eq!(s.packets[0].hex, "02");
    }
    #[test]
    fn no_control_byte_removed() {
        let s = parse(&[0xc0, 0x1e, 0xa5, 0xc0]);
        assert_eq!(s.packets[0].hex, "1ea5");
    }
    #[test]
    fn every_layout_is_structure_only() {
        for &(magic, _, size) in LAYOUTS {
            let mut p = vec![0; 12 + size];
            p[12..14].copy_from_slice(&magic.to_be_bytes());
            assert_eq!(classify(&p)["status"], "known_magic_exact_layout_size");
            assert_eq!(classify(&p)["integrity"], "unresolved");
            assert_eq!(classify(&p)["independent_crc_checked"], false);
            p.pop();
            assert_eq!(classify(&p)["status"], "known_magic_truncated_layout");
            p.extend([0, 0]);
            assert_eq!(
                classify(&p)["status"],
                "known_magic_minimum_layout_size_with_uninterpreted_trailing_bytes"
            );
        }
    }
    #[test]
    fn shifted_magic_and_short_packets_are_not_layouts() {
        assert_eq!(
            classify(&[0; 13])["status"],
            "shorter_than_opaque_header_and_magic"
        );
        let mut p = vec![0; 100];
        p[13..15].copy_from_slice(&[0x1e, 0xa5]);
        assert_eq!(classify(&p)["status"], "unrecognized_magic");
    }
    #[test]
    fn only_idle_fends_is_not_telemetry() {
        let s = parse(&[0xc0; 100]);
        assert!(s.packets.is_empty());
        assert_eq!(s.empty_delimiters, 99);
    }

    #[test]
    #[ignore = "Requires pinned external GR Python installation and immutable real/control artifacts"]
    fn actual_gr_parser_matches_real_and_noise_and_layout_sizes() {
        // Python is only the external reference implementation under test; the
        // candidate decoder and this evidence analyzer are Rust.
        let script = r#"
import json,importlib,types,numpy as np,pmt,hashlib
from pathlib import Path
m=importlib.import_module('satellites.kiss_to_pdu')
t=importlib.import_module('satellites.telemetry.by70_1')
assert hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()=='0508a04b714ccd4f165c006ec48f4198db5a90722787442a779487e0910e1909'
assert hashlib.sha256(Path(t.__file__).read_bytes()).hexdigest()=='a815f20346e316b02f21c9cab24ec08c4fa25a7fed7823065adb726e89d9e140'
names=['Hk_STM32','Cfg','Hk_AVR','LilacSat1Tlm','Hk_OBC','ADCS_Par','Orbit_Par','OBC_Threshold','Orbital_Par','Hk_EPS','EPS_Onboard','EPS_Reboot','EPS_Command','EPS_Error']
summary=[]
for filename in ['real-observations-v1.json','white-noise-validation-v1.json']:
 doc=json.loads(Path('work/taurus-validation-20260911-v1',filename).read_text())
 for row in doc['rows']:
  raw=Path(row['outer_capture']['path']).read_bytes()
  assert hashlib.sha256(raw).hexdigest()==row['outer_capture']['sha256']
  chunks=[]
  for rec in raw.split(bytes([192])):
   if not rec: continue
   decoded=rec.replace(bytes([219,220]),bytes([192])).replace(bytes([219,221]),bytes([219]))
   if decoded[0]==0: chunks.append(decoded[1:])
  assert all(len(c)==81 for c in chunks)
  assert [hashlib.sha256(c).hexdigest() for c in chunks]==[r['sha256'] for r in row['chunks_in_capture_order']]
  out=[]
  state=types.SimpleNamespace(pdu=[],transpose=False,control_byte=False,message_port_pub=lambda port,msg:out.append(bytes(pmt.u8vector_elements(pmt.cdr(msg)))))
  m.kiss_to_pdu.work(state,[np.frombuffer(b''.join(chunks),dtype=np.uint8)],[])
  assert [p.hex() for p in out]==[p['hex'] for p in row['gr_compatible_packets']]
  valid=[]
  for p in out:
   try: t.taurus1.parse(p); valid.append(p.hex())
   except Exception: pass
  assert not valid
  summary.append({'observation_id':row['observation_id'],'reference_packet_count':len(out),'telemetry_parse_count':len(valid),'exact_compatibility':True})
print(json.dumps({'layouts':{n:getattr(t,n).sizeof() for n in names},'rows':summary}))
"#;
        let output = std::process::Command::new("work/golden/env/bin/python")
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .args(["-c", script])
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let receipt: Value = serde_json::from_slice(&output.stdout).unwrap();
        for &(_, name, size) in LAYOUTS {
            assert_eq!(receipt["layouts"][name], size);
        }
        assert_eq!(receipt["rows"].as_array().unwrap().len(), 3);
        println!("{receipt}");
    }
}
