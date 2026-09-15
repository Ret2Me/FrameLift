//! Geoscan CC1125 framing and single-image, loss-aware JPEG reassembly.
//! Wire format: gr-satellites geoscan_deframer / pn9_scrambler; image layout:
//! kng/geoscan-tools process_simple.py. No guessed bytes are marked received.
use crate::protocol::ProtocolFrame;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

pub fn crc(data: &[u8]) -> u16 {
    let mut c = 0xffffu16;
    for &b in data {
        c ^= (b as u16) << 8;
        for _ in 0..8 {
            c = if c & 0x8000 != 0 {
                (c << 1) ^ 0x8005
            } else {
                c << 1
            };
        }
    }
    c
}
pub fn valid_frame(frame: &[u8]) -> bool {
    frame.len() == 66 && crc(&frame[..64]) == u16::from_be_bytes([frame[64], frame[65]])
}
pub fn pn9(bytes: &mut [u8]) {
    let mut s = 0x1ffu16;
    for b in bytes {
        for k in 0..8 {
            *b ^= ((s & 1) as u8) << k;
            let feedback = (s ^ (s >> 5)) & 1;
            s = (s >> 1) | (feedback << 8);
        }
    }
}
pub fn decode(
    soft: &[f64],
    threshold: f64,
    max_hamming: usize,
) -> Result<Vec<ProtocolFrame>, String> {
    if max_hamming > 4 || !threshold.is_finite() || soft.iter().any(|x| !x.is_finite()) {
        return Err("invalid Geoscan sync threshold or nonfinite symbols".into());
    }
    let mut found = BTreeSet::new();
    for inv in [false, true] {
        let mut sync = 0u32;
        for i in 0..soft.len() {
            sync = (sync << 1) | u32::from((soft[i] >= threshold) ^ inv);
            if i < 31
                || (sync ^ 0x930b51de).count_ones() as usize > max_hamming
                || soft.len() - i - 1 < 528
            {
                continue;
            }
            let mut bytes = vec![0u8; 66];
            for (j, b) in bytes.iter_mut().enumerate() {
                for k in 0..8 {
                    *b = (*b << 1) | u8::from((soft[i + 1 + j * 8 + k] >= threshold) ^ inv);
                }
            }
            pn9(&mut bytes);
            if valid_frame(&bytes) {
                found.insert(bytes);
            }
        }
    }
    Ok(found
        .into_iter()
        .map(|frame| ProtocolFrame {
            frame,
            validation_layers: vec!["geoscan_sync_pn9".into(), "crc16_cc11xx".into()],
        })
        .collect())
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Image {
    pub bytes: Vec<u8>,
    pub received: Vec<bool>,
    pub missing_ranges: Vec<(usize, usize)>,
    pub used_packets: usize,
    pub rejected_packets: usize,
    pub base_address: usize,
    pub has_received_eoi: bool,
    pub all_bytes_received_through_eoi: bool,
}
/// Input is explicitly CRC-stripped PDU bytes; transport alone proves no CRC.
/// Caller must distinguish externally validated KISS from native received CRC.
pub fn assemble(pdus: &[Vec<u8>]) -> Result<Image, String> {
    let mut packets = BTreeSet::new();
    let mut rejected = 0;
    for p in pdus {
        if p.len() != 64 || p[..2] != [1, 0] || !(7..=62).contains(&p[2]) || ![1, 5].contains(&p[3])
        {
            rejected += 1;
            continue;
        }
        packets.insert(p.clone());
    }
    let bases: BTreeSet<usize> = packets
        .iter()
        .filter(|p| p[3] == 1 && p[2] >= 8 && p[8..10] == [0xff, 0xd8])
        .map(|p| u16::from_le_bytes([p[5], p[6]]) as usize)
        .collect();
    if bases.len() != 1 {
        return Err("single unambiguous received JPEG start required".into());
    }
    let base = *bases.first().unwrap();
    let mut bytes = vec![0; 65536];
    let mut received = vec![false; 65536];
    let mut end = 0;
    let mut used = 0;
    for p in packets {
        let addr = u16::from_le_bytes([p[5], p[6]]) as usize;
        if addr < base {
            rejected += 1;
            continue;
        }
        let start = addr - base;
        let payload = &p[8..p[2] as usize + 2];
        if start + payload.len() > 65536 {
            return Err("image address exceeds bound".into());
        }
        for (j, &b) in payload.iter().enumerate() {
            let i = start + j;
            if received[i] && bytes[i] != b {
                return Err(format!(
                    "conflicting image byte at {i}; mixed images or corruption"
                ));
            }
            bytes[i] = b;
            received[i] = true;
        }
        end = end.max(start + payload.len());
        used += 1;
    }
    let eoi = (1..end)
        .find(|&i| received[i - 1] && received[i] && bytes[i - 1..=i] == [0xff, 0xd9])
        .map(|i| i + 1);
    end = eoi.unwrap_or(end);
    bytes.truncate(end);
    received.truncate(end);
    let mut gaps = vec![];
    let mut i = 0;
    while i < end {
        if received[i] {
            i += 1;
            continue;
        }
        let start = i;
        while i < end && !received[i] {
            i += 1;
        }
        gaps.push((start, i));
    }
    Ok(Image {
        bytes,
        received,
        all_bytes_received_through_eoi: eoi.is_some() && gaps.is_empty(),
        missing_ranges: gaps,
        used_packets: used,
        rejected_packets: rejected,
        base_address: base,
        has_received_eoi: eoi.is_some(),
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    fn p(addr: u16, kind: u8, data: &[u8]) -> Vec<u8> {
        let mut p = vec![0; 64];
        p[..4].copy_from_slice(&[1, 0, (data.len() + 6) as u8, kind]);
        p[5..7].copy_from_slice(&addr.to_le_bytes());
        p[8..8 + data.len()].copy_from_slice(data);
        p
    }
    #[test]
    fn pn9_known_prefix() {
        let mut b = [0; 8];
        pn9(&mut b);
        assert_eq!(b, [0xff, 0xe1, 0x1d, 0x9a, 0xed, 0x85, 0x33, 0x24]);
        pn9(&mut b);
        assert_eq!(b, [0; 8]);
    }
    #[test]
    fn crc_known_check() {
        assert_eq!(crc(b"123456789"), 0xaee7);
    }
    #[test]
    fn waveform_polarity_corruption_and_end() {
        let mut f = vec![0; 64];
        f.extend(crc(&f).to_be_bytes());
        pn9(&mut f);
        let mut b = vec![0x93, 0x0b, 0x51, 0xde];
        b.extend(f);
        let mut s: Vec<_> = b
            .iter()
            .flat_map(|b| {
                (0..8)
                    .rev()
                    .map(move |k| if b & (1 << k) != 0 { 1.0 } else { -1.0 })
            })
            .collect();
        assert_eq!(decode(&s, 0.0, 0).unwrap().len(), 1);
        for x in &mut s {
            *x = -*x;
        }
        assert_eq!(decode(&s, 0.0, 0).unwrap().len(), 1);
        s[100] = -s[100];
        assert!(decode(&s, 0.0, 0).unwrap().is_empty());
        assert!(decode(&s[..40], 0.0, 0).unwrap().is_empty());
    }
    #[test]
    fn order_duplicates() {
        let a = p(100, 1, &[255, 216, 1, 2]);
        let b = p(104, 5, &[3, 255, 217]);
        let i = assemble(&[b, a.clone(), a]).unwrap();
        assert_eq!(i.bytes, vec![255, 216, 1, 2, 3, 255, 217]);
        assert!(i.all_bytes_received_through_eoi);
        assert_eq!(i.used_packets, 2);
    }
    #[test]
    fn missing_bytes_explicit() {
        let i = assemble(&[p(100, 1, &[255, 216]), p(104, 5, &[255, 217])]).unwrap();
        assert_eq!(i.missing_ranges, vec![(2, 4)]);
        assert!(!i.all_bytes_received_through_eoi);
    }
    #[test]
    fn conflict_rejected() {
        assert!(assemble(&[p(100, 1, &[255, 216, 1]), p(102, 5, &[2])]).is_err());
    }
    #[test]
    fn missing_start_rejected() {
        assert!(assemble(&[p(104, 5, &[255, 217])]).is_err());
    }
}
