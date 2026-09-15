//! Strict classic-PCAP and length-prefixed IPv4/ICMP audit.
//!
//! Checksums detect errors, not source authenticity. Unsupported packets remain
//! counted as rejections; malformed file framing fails the whole audit.

use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::net::Ipv4Addr;
use std::path::Path;

const MAX_INPUT_BYTES: u64 = 256 * 1024 * 1024;
const MAX_RECORDS: usize = 1_000_000;

pub fn internet_checksum(payload: &[u8]) -> u16 {
    // Fold incrementally so arbitrarily long callers cannot overflow the sum.
    let mut sum = 0_u32;
    for pair in payload.chunks(2) {
        sum += u16::from_be_bytes([pair[0], *pair.get(1).unwrap_or(&0)]) as u32;
        sum = (sum & 0xffff) + (sum >> 16);
    }
    !((sum & 0xffff) + (sum >> 16)) as u16
}

fn word(bytes: &[u8], offset: usize) -> u16 {
    u16::from_be_bytes([bytes[offset], bytes[offset + 1]])
}

fn audit_echo_request(packet: &[u8]) -> Result<Value, String> {
    if packet.len() < 20 {
        return Err("truncated IPv4 header".into());
    }
    let ihl = (packet[0] & 15) as usize * 4;
    if packet[0] >> 4 != 4 || ihl < 20 || ihl > packet.len() {
        return Err("invalid IPv4 version or header length".into());
    }
    let total_length = word(packet, 2) as usize;
    if total_length < ihl + 8 || total_length != packet.len() {
        return Err("IPv4 total length does not equal captured packet length".into());
    }
    if internet_checksum(&packet[..ihl]) != 0 {
        return Err("invalid IPv4 header checksum".into());
    }
    if word(packet, 6) & 0x3fff != 0 {
        return Err("fragmented IPv4 packet".into());
    }
    if packet[9] != 1 {
        return Err("non-ICMP IPv4 packet".into());
    }
    let icmp = &packet[ihl..];
    if icmp[0] != 8 || icmp[1] != 0 {
        return Err("non-echo-request ICMP packet".into());
    }
    if internet_checksum(icmp) != 0 {
        return Err("invalid ICMP checksum".into());
    }
    Ok(json!({
        "sha256": hex::encode(Sha256::digest(packet)), "length_bytes": total_length,
        "source": Ipv4Addr::new(packet[12],packet[13],packet[14],packet[15]).to_string(),
        "destination": Ipv4Addr::new(packet[16],packet[17],packet[18],packet[19]).to_string(),
        "icmp_identifier": word(icmp,4), "icmp_sequence": word(icmp,6)
    }))
}

fn ip_from_link(packet: &[u8], link_type: u32) -> Result<&[u8], String> {
    match link_type {
        101 => Ok(packet),
        276 => {
            if packet.len() < 20 {
                return Err("truncated Linux cooked v2 header".into());
            }
            if word(packet, 0) != 0x0800 {
                return Err("non-IPv4 Linux cooked packet".into());
            }
            Ok(&packet[20..])
        }
        _ => Err(format!("unsupported PCAP link type {link_type}")),
    }
}

#[derive(Default)]
struct Inventory {
    records: usize,
    accepted: usize,
    packets: BTreeMap<String, Value>,
    rejected: BTreeMap<String, usize>,
}

impl Inventory {
    fn add(&mut self, packet: Result<&[u8], String>) -> Result<(), String> {
        if self.records == MAX_RECORDS {
            return Err("packet audit record limit exceeded".into());
        }
        self.records += 1;
        match packet.and_then(audit_echo_request) {
            Ok(row) => {
                self.accepted += 1;
                self.packets
                    .insert(row["sha256"].as_str().unwrap().into(), row);
            }
            Err(reason) => *self.rejected.entry(reason).or_default() += 1,
        }
        Ok(())
    }

    fn report(self) -> Value {
        json!({"records":self.records, "accepted_echo_requests":self.accepted,
            "unique_echo_requests":self.packets.len(),
            "duplicate_echo_requests":self.accepted-self.packets.len(),
            "rejected_records":self.records-self.accepted,
            "rejection_reasons":self.rejected,
            "packets":self.packets.into_values().collect::<Vec<_>>()})
    }
}

fn take<'a>(bytes: &mut &'a [u8], size: usize, error: &str) -> Result<&'a [u8], String> {
    if size > bytes.len() {
        return Err(error.into());
    }
    let (head, tail) = bytes.split_at(size);
    *bytes = tail;
    Ok(head)
}

fn read_input(path: &Path) -> Result<Vec<u8>, String> {
    super::input::read_regular_bounded(path, MAX_INPUT_BYTES)
}

pub fn audit_pcap_echo_requests(path: &Path) -> Result<Value, String> {
    let bytes = read_input(path)?;
    audit_pcap_bytes(&bytes, &path.to_string_lossy())
}

pub fn audit_length_prefixed_echo_requests(path: &Path) -> Result<Value, String> {
    let bytes = read_input(path)?;
    audit_length_prefixed_bytes(&bytes, &path.to_string_lossy())
}

pub fn audit_pcap_bytes(bytes: &[u8], name: &str) -> Result<Value, String> {
    let mut input = bytes;
    let global = take(&mut input, 24, "truncated PCAP global header")?;
    let (little, divisor) = match &global[..4] {
        [0xd4, 0xc3, 0xb2, 0xa1] => (true, 1_000_000),
        [0xa1, 0xb2, 0xc3, 0xd4] => (false, 1_000_000),
        [0x4d, 0x3c, 0xb2, 0xa1] => (true, 1_000_000_000),
        [0xa1, 0xb2, 0x3c, 0x4d] => (false, 1_000_000_000),
        _ => return Err("unsupported PCAP magic".into()),
    };
    let u16_at = |b: &[u8], i| {
        let a = [b[i], b[i + 1]];
        if little {
            u16::from_le_bytes(a)
        } else {
            u16::from_be_bytes(a)
        }
    };
    let u32_at = |b: &[u8], i| {
        let a = [b[i], b[i + 1], b[i + 2], b[i + 3]];
        if little {
            u32::from_le_bytes(a)
        } else {
            u32::from_be_bytes(a)
        }
    };
    let (major, minor) = (u16_at(global, 4), u16_at(global, 6));
    if (major, minor) != (2, 4) {
        return Err(format!("unsupported PCAP version {major}.{minor}"));
    }
    let (snaplen, link_type) = (u32_at(global, 16), u32_at(global, 20));
    let (mut first, mut last) = (None, None);
    let mut inventory = Inventory::default();
    while !input.is_empty() {
        let header = take(&mut input, 16, "truncated PCAP record header")?;
        let (seconds, fraction, included, original) = (
            u32_at(header, 0),
            u32_at(header, 4),
            u32_at(header, 8),
            u32_at(header, 12),
        );
        if included > snaplen || included > original {
            return Err("invalid PCAP record lengths".into());
        }
        if fraction >= divisor {
            return Err("invalid PCAP timestamp fraction".into());
        }
        let packet = take(&mut input, included as usize, "truncated PCAP packet data")?;
        let timestamp = seconds as f64 + fraction as f64 / divisor as f64;
        first.get_or_insert(timestamp);
        last = Some(timestamp);
        inventory.add(ip_from_link(packet, link_type))?;
    }
    let mut report = inventory.report();
    report["schema_version"] = json!("telemetry-yield-pcap-ipv4-audit-v1");
    report["path"] = json!(name);
    report["pcap_sha256"] = json!(hex::encode(Sha256::digest(bytes)));
    report["pcap_size_bytes"] = json!(bytes.len());
    report["link_type"] = json!(link_type);
    report["first_timestamp"] = json!(first);
    report["last_timestamp"] = json!(last);
    Ok(report)
}

pub fn audit_length_prefixed_bytes(bytes: &[u8], name: &str) -> Result<Value, String> {
    let mut input = bytes;
    let mut inventory = Inventory::default();
    while !input.is_empty() {
        let header = take(&mut input, 4, "truncated PDU record header")?;
        let size = u32::from_be_bytes(header.try_into().unwrap()) as usize;
        if size == 0 || size > 65_535 {
            return Err("invalid PDU record length".into());
        }
        inventory.add(Ok(take(&mut input, size, "truncated PDU record")?))?;
    }
    let mut report = inventory.report();
    report["schema_version"] = json!("telemetry-yield-length-prefixed-ipv4-audit-v1");
    report["path"] = json!(name);
    report["file_sha256"] = json!(hex::encode(Sha256::digest(bytes)));
    report["file_size_bytes"] = json!(bytes.len());
    Ok(report)
}

#[cfg(test)]
#[path = "pcap_tests.rs"]
mod tests;
