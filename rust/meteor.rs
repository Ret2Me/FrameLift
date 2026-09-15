//! Experimental Meteor LRPT channel receiver (not the MSU-MR image codec).
//! Encoded-ASM acquisition -> soft Viterbi -> optional NRZ-M -> PN255 -> RS(255,223)x4.
//! RS verification is NOT independent CRC authentication. No reference frames
//! or images participate in acquisition or acceptance.
use crate::{
    convolutional::viterbi_k7,
    fec::{ReedSolomonConfig, SymbolBasis},
};
use serde::Serialize;

pub const ASM: [u8; 4] = [0x1a, 0xcf, 0xfc, 0x1d];
pub const CADU_BYTES: usize = 1024;
const CODED_BITS: usize = CADU_BYTES * 16;
const GUARD: usize = 128;

/// Mission choices are explicit: the original M2 uses QPSK without NRZ-M,
/// whereas the M2-x 72k profile uses OQPSK and post-Viterbi NRZ-M.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum Profile {
    MeteorM2,
    MeteorM2x,
}
impl Profile {
    pub fn modulation(self) -> &'static str {
        match self {
            Self::MeteorM2 => "qpsk",
            Self::MeteorM2x => "oqpsk",
        }
    }
    fn nrzm(self) -> bool {
        self == Self::MeteorM2x
    }
}

fn rs() -> ReedSolomonConfig {
    let mut config = ReedSolomonConfig::ccsds_rs255_223(4, 0);
    config.basis = SymbolBasis::Conventional;
    config
}

/// CCSDS TM 255-bit sequence, restarted after the ASM, before RS decoding.
pub fn randomize_bytes(bytes: &mut [u8]) {
    let mut state = 255_u8;
    for byte in bytes {
        let mut mask = 0;
        for _ in 0..8 {
            mask = (mask << 1) | (state & 1);
            let feedback = (state ^ (state >> 3) ^ (state >> 5) ^ (state >> 7)) & 1;
            state = (state >> 1) | (feedback << 7);
        }
        *byte ^= mask;
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct Frame {
    pub coded_bit_offset: usize,
    pub corrected_symbols: usize,
    pub transfer_frame_version: u8,
    pub spacecraft_id: u8,
    pub virtual_channel_id: u8,
    pub frame_counter: u32,
    #[serde(skip)]
    pub cadu: Vec<u8>,
}

/// Verify a derandomized CADU and regenerate its corrected RS parity. This
/// canonicalizes both benchmark arms; payload hashes exclude ASM and parity.
pub fn verify_cadu(cadu: &[u8]) -> Result<Frame, String> {
    if cadu.len() != CADU_BYTES || cadu[..4] != ASM {
        return Err("invalid CADU extent/ASM".into());
    }
    let (payload, corrected_symbols) = rs().decode(&cadu[4..])?;
    // Zero corrected symbols means every received RS syndrome was already
    // zero. Its systematic codeword is unique: preserve it without reencoding.
    let corrected = if corrected_symbols == 0 {
        cadu.to_vec()
    } else {
        let mut bytes = ASM.to_vec();
        bytes.extend(rs().encode(&payload)?);
        bytes
    };
    Ok(Frame {
        coded_bit_offset: 0,
        corrected_symbols,
        transfer_frame_version: payload[0] >> 6,
        spacecraft_id: (payload[0] << 2) | (payload[1] >> 6),
        virtual_channel_id: payload[1] & 63,
        frame_counter: u32::from_be_bytes([0, payload[2], payload[3], payload[4]]),
        cadu: corrected,
    })
}

#[derive(Default, Debug, Serialize)]
pub struct Scan {
    pub sync_candidates: usize,
    pub viterbi_attempts: usize,
    pub asm_rejections: usize,
    pub rs_rejections: usize,
    pub boundary_candidates: usize,
    pub frames: Vec<Frame>,
}

fn encoded_asm_tail(profile: Profile) -> u64 {
    let mut nrzm = 0_u8;
    let mut state = 0_u8;
    let mut encoded = 0_u64;
    for byte in ASM {
        for bit in (0..8).rev() {
            let value = (byte >> bit) & 1;
            nrzm = if profile.nrzm() { nrzm ^ value } else { value };
            state = (state << 1) | nrzm;
            for p in [79_u8, 109] {
                encoded = (encoded << 1) | ((state & p).count_ones() as u64 & 1);
            }
        }
    }
    encoded & ((1_u64 << 52) - 1)
}

/// Bounded heuristic acquisition. The first six input bits of encoded ASM are
/// omitted to remove unknown convolutional state; both NRZ-M states are tried.
/// This is not an exhaustive ML receiver, and no zero-loss claim is made.
pub fn scan_soft(soft: &[f64], max_sync_errors: u32) -> Result<Scan, String> {
    scan_soft_profile(soft, max_sync_errors, Profile::MeteorM2x)
}

pub fn scan_soft_profile(
    soft: &[f64],
    max_sync_errors: u32,
    profile: Profile,
) -> Result<Scan, String> {
    if max_sync_errors > 10 || soft.iter().any(|x| !x.is_finite()) {
        return Err("invalid sync threshold or nonfinite Meteor symbols".into());
    }
    let pattern = encoded_asm_tail(profile);
    let mask = (1_u64 << 52) - 1;
    let mut word = 0_u64;
    let mut out = Scan::default();
    for (i, &value) in soft.iter().enumerate() {
        word = ((word << 1) | u64::from(value > 0.0)) & mask;
        if i < 63 {
            continue;
        }
        let distance = (word ^ pattern).count_ones();
        if distance.min(52 - distance) > max_sync_errors {
            continue;
        }
        let start = i + 1 - 64;
        out.sync_candidates += 1;
        if start < GUARD || start + CODED_BITS + GUARD > soft.len() {
            out.boundary_candidates += 1;
            continue;
        }
        if out.viterbi_attempts >= 4096 {
            return Err("Meteor candidate limit exceeded; split input or reduce threshold".into());
        }
        out.viterbi_attempts += 1;
        let bits = viterbi_k7(&soft[start - GUARD..start + CODED_BITS + GUARD], [79, 109])?;
        let offset = GUARD / 2;
        let mut bytes = vec![0_u8; CADU_BYTES];
        for bit in 0..CADU_BYTES * 8 {
            let value = if profile.nrzm() {
                bits[offset + bit] ^ bits[offset + bit - 1]
            } else {
                bits[offset + bit]
            };
            bytes[bit / 8] = (bytes[bit / 8] << 1) | value;
        }
        // Non-differential QPSK retains the 180-degree polarity ambiguity.
        // Both K=7 generators have odd weight, so a global inversion of the
        // coded stream complements the recovered bits after the guard.
        if !profile.nrzm() && bytes[..4].iter().zip(ASM).all(|(a, b)| *a == !b) {
            for byte in &mut bytes {
                *byte = !*byte;
            }
        }
        if bytes[..4] != ASM {
            out.asm_rejections += 1;
            continue;
        }
        randomize_bytes(&mut bytes[4..]);
        match verify_cadu(&bytes) {
            Ok(mut frame) => {
                frame.coded_bit_offset = start;
                out.frames.push(frame);
            }
            Err(_) => out.rs_rejections += 1,
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (Vec<u8>, Vec<f64>) {
        fixture_profile(Profile::MeteorM2x)
    }
    fn fixture_profile(profile: Profile) -> (Vec<u8>, Vec<f64>) {
        let payload: Vec<u8> = (0..892).map(|i| (i * 17 + i / 8) as u8).collect();
        let mut cadu = ASM.to_vec();
        cadu.extend(rs().encode(&payload).unwrap());
        let mut transmitted = cadu.clone();
        randomize_bytes(&mut transmitted[4..]);
        let mut nrzm = 1_u8;
        let mut state = 27_u8;
        let mut soft = Vec::new();
        // Boundary guards are actual continuous encoded data, not soft padding.
        for byte in std::iter::repeat_n(0x93, 32)
            .chain(transmitted)
            .chain(std::iter::repeat_n(0x63, 32))
        {
            for bit in (0..8).rev() {
                let value = (byte >> bit) & 1;
                nrzm = if profile.nrzm() { nrzm ^ value } else { value };
                state = (state << 1) | nrzm;
                for p in [79_u8, 109] {
                    soft.push(if (state & p).count_ones() % 2 == 1 {
                        1.0
                    } else {
                        -1.0
                    });
                }
            }
        }
        (cadu, soft)
    }
    #[test]
    fn complete_chain_and_inverted_soft() {
        let (expected, mut soft) = fixture();
        for i in (811..soft.len() - 300).step_by(193) {
            soft[i] *= -0.2;
        }
        for polarity in [1.0, -1.0] {
            let samples: Vec<_> = soft.iter().map(|x| x * polarity).collect();
            let scan = scan_soft(&samples, 8).unwrap();
            assert_eq!(scan.frames.len(), 1);
            assert_eq!(scan.frames[0].cadu, expected);
        }
    }
    #[test]
    fn original_m2_no_nrzm_and_wrong_profile_rejection() {
        // Published SatDump v1.2.2 original-M2 correlator word uses the
        // opposite soft-bit polarity to our positive=ONE convention. The
        // first 12 encoded bits depend on the preceding convolutional state.
        assert_eq!(
            encoded_asm_tail(Profile::MeteorM2),
            !0xfca2b63db00d9794_u64 & ((1_u64 << 52) - 1)
        );
        let (expected, mut soft) = fixture_profile(Profile::MeteorM2);
        for i in (811..soft.len() - 300).step_by(193) {
            soft[i] *= -0.2;
        }
        for polarity in [1.0, -1.0] {
            let samples: Vec<_> = soft.iter().map(|x| x * polarity).collect();
            let scan = scan_soft_profile(&samples, 8, Profile::MeteorM2).unwrap();
            assert_eq!(scan.frames.len(), 1);
            assert_eq!(scan.frames[0].cadu, expected);
            assert!(scan_soft(&samples, 8).unwrap().frames.is_empty());
        }
    }
    #[test]
    fn pn_vector_and_rs_rejection() {
        let mut bytes = [0; 8];
        randomize_bytes(&mut bytes);
        assert_eq!(bytes, [0xff, 0x48, 0x0e, 0xc0, 0x9a, 0x0d, 0x70, 0xbc]);
        let (mut cadu, soft) = fixture();
        for i in 0..20 {
            cadu[4 + i * 4] ^= 0x69;
        }
        assert!(verify_cadu(&cadu).is_err());
        assert!(scan_soft(&soft[..1000], 8).unwrap().frames.is_empty());
        assert!(scan_soft(&vec![0.0; 20000], 8).unwrap().frames.is_empty());
        let mut seed = 42_u32;
        let random: Vec<_> = (0..100000)
            .map(|_| {
                seed ^= seed << 13;
                seed ^= seed >> 17;
                seed ^= seed << 5;
                if seed & 1 == 1 { 1.0 } else { -1.0 }
            })
            .collect();
        assert!(scan_soft(&random, 10).unwrap().frames.is_empty());
    }
    #[test]
    fn canonical_cadu_preserves_clean_and_corrects_damaged_words() {
        let (expected, _) = fixture();
        let clean = verify_cadu(&expected).unwrap();
        assert_eq!(clean.corrected_symbols, 0);
        assert_eq!(clean.cadu, expected);
        let mut damaged = expected.clone();
        for i in 0..16 {
            damaged[4 + i * 4] ^= 0x41;
        }
        let corrected = verify_cadu(&damaged).unwrap();
        assert_eq!(corrected.corrected_symbols, 16);
        assert_eq!(corrected.cadu, expected);
    }
}
