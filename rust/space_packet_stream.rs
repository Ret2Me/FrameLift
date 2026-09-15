//! Strict AOS M_PDU -> complete CCSDS Space Packets.
//! The caller supplies an already verified transfer frame and its managed
//! packet-zone layout. Missing frames never get zero-filled or joined across.
use std::collections::BTreeMap;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Packet {
    pub spacecraft_id: u8,
    pub virtual_channel_id: u8,
    pub apid: u16,
    pub sequence: u16,
    pub bytes: Vec<u8>,
}
#[derive(Default)]
struct Channel {
    last_counter: Option<u32>,
    pending: Vec<u8>,
}
#[derive(Default)]
pub struct AosPacketDemux {
    channels: BTreeMap<(u8, u8), Channel>,
    pub discontinuities: usize,
    pub incomplete_aborts: usize,
    pub invalid_headers: usize,
}
fn packet_extent(bytes: &[u8]) -> Result<Option<usize>, ()> {
    if bytes.len() < 6 {
        return Ok(None);
    }
    if bytes[0] >> 5 != 0 {
        return Err(());
    }
    Ok(Some(7 + u16::from_be_bytes([bytes[4], bytes[5]]) as usize))
}
fn packet(sc: u8, vc: u8, bytes: Vec<u8>) -> Option<Packet> {
    let apid = u16::from_be_bytes([bytes[0] & 7, bytes[1]]);
    if apid == 2047 {
        return None;
    } // Idle Space Packet.
    Some(Packet {
        spacecraft_id: sc,
        virtual_channel_id: vc,
        apid,
        sequence: u16::from_be_bytes([bytes[2] & 63, bytes[3]]),
        bytes,
    })
}
impl AosPacketDemux {
    /// FHP is the 11-bit first-header pointer; 2046=idle, 2047=no header.
    /// Duplicate/out-of-order frame handling is deliberately conservative.
    pub fn push(
        &mut self,
        sc: u8,
        vc: u8,
        counter: u32,
        fhp: u16,
        zone: &[u8],
    ) -> Result<Vec<Packet>, String> {
        if vc > 63 || counter > 0xffffff || zone.is_empty() || zone.len() > 65536 || fhp > 2047 {
            return Err("invalid AOS packet-zone configuration".into());
        }
        let ch = self.channels.entry((sc, vc)).or_default();
        if ch
            .last_counter
            .is_some_and(|last| counter != ((last + 1) & 0xffffff))
        {
            self.discontinuities += 1;
            if !ch.pending.is_empty() {
                self.incomplete_aborts += 1;
                ch.pending.clear();
            }
        }
        ch.last_counter = Some(counter);
        if fhp == 2046 || (fhp < 2046 && fhp as usize >= zone.len()) {
            if !ch.pending.is_empty() {
                self.incomplete_aborts += 1;
                ch.pending.clear();
            }
            if fhp != 2046 {
                return Err("first-header pointer outside AOS packet zone".into());
            }
            return Ok(vec![]);
        }
        let boundary = if fhp == 2047 {
            zone.len()
        } else {
            fhp as usize
        };
        let mut output = Vec::new();
        if !ch.pending.is_empty() {
            // At most one packet started before the first-header boundary.
            // Incremental fill also supports a primary header split over CADUs.
            let mut offset = 0;
            while offset < boundary {
                if ch.pending.len() < 6 {
                    let n = (6 - ch.pending.len()).min(boundary - offset);
                    ch.pending.extend_from_slice(&zone[offset..offset + n]);
                    offset += n;
                }
                match packet_extent(&ch.pending) {
                    Err(()) => {
                        self.invalid_headers += 1;
                        ch.pending.clear();
                        break;
                    }
                    Ok(None) => break,
                    Ok(Some(total)) => {
                        let n = (total - ch.pending.len()).min(boundary - offset);
                        ch.pending.extend_from_slice(&zone[offset..offset + n]);
                        offset += n;
                        if ch.pending.len() == total {
                            if let Some(p) = packet(sc, vc, std::mem::take(&mut ch.pending)) {
                                output.push(p);
                            }
                            break;
                        }
                    }
                }
            }
            if fhp != 2047 && !ch.pending.is_empty() {
                self.incomplete_aborts += 1;
                ch.pending.clear();
            }
        }
        if fhp == 2047 {
            return Ok(output);
        }
        let mut offset = boundary;
        while offset < zone.len() {
            match packet_extent(&zone[offset..]) {
                Err(()) => {
                    self.invalid_headers += 1;
                    break;
                }
                Ok(None) => {
                    ch.pending.extend_from_slice(&zone[offset..]);
                    break;
                }
                Ok(Some(total)) => {
                    if total > zone.len() - offset {
                        ch.pending.extend_from_slice(&zone[offset..]);
                        break;
                    }
                    if let Some(p) = packet(sc, vc, zone[offset..offset + total].to_vec()) {
                        output.push(p);
                    }
                    offset += total;
                }
            }
        }
        Ok(output)
    }
    pub fn pending_packets(&self) -> usize {
        self.channels
            .values()
            .filter(|c| !c.pending.is_empty())
            .count()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn bytes(apid: u8, len: usize) -> Vec<u8> {
        let mut p = vec![8, apid, 0xc0, 3, ((len - 7) >> 8) as u8, (len - 7) as u8];
        p.resize(len, 0x91);
        p
    }
    #[test]
    fn split_header_and_payload_and_rollover() {
        let p = bytes(67, 23);
        let mut d = AosPacketDemux::default();
        assert!(d.push(0, 5, 0xfffffe, 0, &p[..3]).unwrap().is_empty());
        assert!(d.push(0, 5, 0xffffff, 2047, &p[3..11]).unwrap().is_empty());
        let out = d.push(0, 5, 0, 2047, &p[11..]).unwrap();
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].bytes, p);
        assert_eq!(d.discontinuities, 0);
    }
    #[test]
    fn gaps_never_create_a_packet_and_channels_are_independent() {
        let p = bytes(68, 24);
        let mut d = AosPacketDemux::default();
        d.push(0, 5, 10, 0, &p[..10]).unwrap();
        assert_eq!(d.push(0, 6, 20, 0, &p).unwrap().len(), 1);
        assert!(d.push(0, 5, 12, 2047, &p[10..]).unwrap().is_empty());
        assert_eq!(d.discontinuities, 1);
        assert_eq!(d.incomplete_aborts, 1);
        assert_eq!(d.push(0, 5, 13, 0, &p).unwrap().len(), 1);
    }
    #[test]
    fn pointer_completes_previous_then_starts_multiple() {
        let p = bytes(69, 20);
        let mut d = AosPacketDemux::default();
        d.push(0, 5, 2, 0, &p[..12]).unwrap();
        let z = [&p[12..], &p, &p].concat();
        assert_eq!(d.push(0, 5, 3, 8, &z).unwrap().len(), 3);
        assert!(d.push(0, 5, 4, 40, &p).is_err());
        d.push(0, 5, 5, 0, &p[..2]).unwrap();
        assert_eq!(d.pending_packets(), 1);
        assert!(d.push(0, 5, 6, 2046, &p).unwrap().is_empty());
        assert_eq!(d.pending_packets(), 0);
    }
}
