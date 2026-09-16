//! Evidence-preserving Chase combining. Only disjoint received copies with an
//! independently checked repeat key may enter; timing variants are NOT copies.
use serde::{Deserialize, Serialize};

/// A mission's existing independently protected header, not a new on-air header.
/// A profile must document that its selected fields name one immutable codeword
/// throughout the configured repeat window. Packet type alone is insufficient.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HeaderLayout {
    pub bytes: usize,
    pub protected_start: usize,
    pub protected_end: usize,
    pub checksum_offset: usize,
    pub checksum: HeaderChecksum,
    pub identity_fields: Vec<ByteRange>,
    pub immutable_codeword_contract: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ByteRange {
    pub start: usize,
    pub end: usize,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HeaderChecksum {
    Crc32cBe,
    Crc16CcittFalseBe,
}

impl HeaderLayout {
    pub fn validate(&self) -> Result<(), String> {
        let checksum_bytes = match self.checksum {
            HeaderChecksum::Crc32cBe => 4,
            HeaderChecksum::Crc16CcittFalseBe => 2,
        };
        if !(4..=256).contains(&self.bytes)
            || self.protected_start >= self.protected_end
            || self.protected_end > self.bytes
            || self
                .checksum_offset
                .checked_add(checksum_bytes)
                .is_none_or(|n| n > self.bytes)
            || (self.checksum_offset < self.protected_end
                && self.checksum_offset + checksum_bytes > self.protected_start)
            || self.identity_fields.is_empty()
            || self.identity_fields.len() > 8
            || self.immutable_codeword_contract.trim().len() < 12
            || self.immutable_codeword_contract.len() > 1024
        {
            return Err(
                "invalid protected mission-header layout or missing immutable-codeword contract"
                    .into(),
            );
        }
        let mut seen = vec![false; self.bytes];
        for field in &self.identity_fields {
            if field.start < self.protected_start
                || field.start >= field.end
                || field.end > self.protected_end
            {
                return Err("repeat identity must be entirely checksum protected".into());
            }
            for present in &mut seen[field.start..field.end] {
                if *present {
                    return Err("overlapping repeat identity fields".into());
                }
                *present = true;
            }
        }
        Ok(())
    }
    pub fn key(&self, header: &[u8]) -> Result<Vec<u8>, String> {
        self.validate()?;
        if header.len() != self.bytes {
            return Err("mission repeat header length mismatch".into());
        }
        let protected = &header[self.protected_start..self.protected_end];
        let at = self.checksum_offset;
        let valid = match self.checksum {
            HeaderChecksum::Crc32cBe => {
                crate::space_link::csp_crc32c(protected)
                    == u32::from_be_bytes(header[at..at + 4].try_into().unwrap())
            }
            HeaderChecksum::Crc16CcittFalseBe => {
                let mut crc = 0xffffu16;
                for byte in protected {
                    crc ^= u16::from(*byte) << 8;
                    for _ in 0..8 {
                        crc = if crc & 0x8000 != 0 {
                            (crc << 1) ^ 0x1021
                        } else {
                            crc << 1
                        };
                    }
                }
                crc == u16::from_be_bytes(header[at..at + 2].try_into().unwrap())
            }
        };
        if !valid {
            return Err("mission repeat-header checksum failed".into());
        }
        Ok(self
            .identity_fields
            .iter()
            .flat_map(|r| header[r.start..r.end].iter().copied())
            .collect())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Copy {
    pub source_sha256: String,
    pub start_sample: usize,
    pub end_sample: usize,
    /// CRC32C-protected on-air header: repeat key followed by big-endian CRC.
    pub header: Vec<u8>,
    pub llr: Vec<f64>,
}

pub fn key(header: &[u8]) -> Result<&[u8], String> {
    if !(8..=36).contains(&header.len()) {
        return Err("repeat header requires 4..32 key bytes and CRC32C".into());
    }
    let n = header.len() - 4;
    let expected = u32::from_be_bytes(header[n..].try_into().unwrap());
    if crate::space_link::csp_crc32c(&header[..n]) != expected {
        return Err("repeat key CRC32C failed".into());
    }
    Ok(&header[..n])
}

pub fn combine(copies: &[Copy], minimum_correlation: f64) -> Result<Vec<f64>, String> {
    combine_with_layout(copies, minimum_correlation, None)
}

pub fn combine_with_layout(
    copies: &[Copy],
    minimum_correlation: f64,
    layout: Option<&HeaderLayout>,
) -> Result<Vec<f64>, String> {
    if !(2..=16).contains(&copies.len())
        || !minimum_correlation.is_finite()
        || !(0.1..=0.99).contains(&minimum_correlation)
    {
        return Err("combining requires 2..16 copies and correlation in [0.1,0.99]".into());
    }
    let checked_key = |header: &[u8]| -> Result<Vec<u8>, String> {
        match layout {
            Some(layout) => layout.key(header),
            None => key(header).map(<[u8]>::to_vec),
        }
    };
    let expected = checked_key(&copies[0].header)?;
    let n = copies[0].llr.len();
    if !(8..=4096).contains(&n) {
        return Err("combined block needs 8..4096 bits".into());
    }
    for (i, a) in copies.iter().enumerate() {
        if a.start_sample >= a.end_sample
            || a.source_sha256.len() != 64
            || !a
                .source_sha256
                .bytes()
                .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
            || a.llr.len() != n
            || a.llr.iter().any(|v| !v.is_finite() || v.abs() > 100.0)
            || checked_key(&a.header)? != expected
        {
            return Err("invalid copy identity, likelihoods or differing repeat keys".into());
        }
        for b in &copies[..i] {
            if a.source_sha256 == b.source_sha256
                && a.start_sample < b.end_sample
                && b.start_sample < a.end_sample
            {
                return Err("overlapping samples cannot provide independent copy evidence".into());
            }
            let dot = a.llr.iter().zip(&b.llr).map(|(x, y)| x * y).sum::<f64>();
            let norm = (a.llr.iter().map(|v| v * v).sum::<f64>()
                * b.llr.iter().map(|v| v * v).sum::<f64>())
            .sqrt();
            if norm < 1e-12 || dot / norm < minimum_correlation {
                return Err("copy likelihoods disagree despite equal repeat keys".into());
            }
        }
    }
    Ok((0..n)
        .map(|i| {
            copies
                .iter()
                .map(|c| c.llr[i])
                .sum::<f64>()
                .clamp(-100.0, 100.0)
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn copy(start: usize) -> Copy {
        let mut header = vec![0, 0, 0, 42];
        header.extend(crate::space_link::csp_crc32c(&header).to_be_bytes());
        Copy {
            source_sha256: "ab".repeat(32),
            start_sample: start,
            end_sample: start + 100,
            header,
            llr: vec![2.; 32],
        }
    }
    #[test]
    fn disjoint_copies_add_only_received_evidence() {
        assert_eq!(combine(&[copy(0), copy(100)], 0.2).unwrap(), vec![4.; 32]);
        assert!(combine(&[copy(0), copy(99)], 0.2).is_err());
        let mut b = copy(100);
        b.llr.fill(-2.);
        assert!(combine(&[copy(0), b], 0.2).is_err());
    }
    #[test]
    fn corrupt_or_mismatched_identity_and_nonfinite_are_rejected() {
        let mut b = copy(100);
        b.header[0] ^= 1;
        assert!(combine(&[copy(0), b], 0.2).is_err());
        let mut b = copy(100);
        b.llr[0] = f64::NAN;
        assert!(combine(&[copy(0), b], 0.2).is_err());
        assert!(combine(&[copy(0)], 0.2).is_err());
    }

    #[test]
    fn mission_header_checks_existing_crc_and_all_identity_fields() {
        let layout = HeaderLayout { bytes: 12, protected_start: 0, protected_end: 8,
            checksum_offset: 8, checksum: HeaderChecksum::Crc32cBe,
            identity_fields: vec![ByteRange{start:0,end:4},ByteRange{start:6,end:8}],
            immutable_codeword_contract:"Epoch and packet counter identify one immutable coded payload for this bounded replay window".into() };
        let mut header = vec![0x12, 0x34, 0, 1, 99, 88, 0, 42];
        header.extend(crate::space_link::csp_crc32c(&header).to_be_bytes());
        assert_eq!(layout.key(&header).unwrap(), [0x12, 0x34, 0, 1, 0, 42]);
        let mut a = copy(0);
        a.header = header.clone();
        let mut b = copy(100);
        b.header = header.clone();
        assert_eq!(
            combine_with_layout(&[a.clone(), b.clone()], 0.2, Some(&layout)).unwrap(),
            vec![4.; 32]
        );
        b.header[7] ^= 1;
        assert!(combine_with_layout(&[a.clone(), b.clone()], 0.2, Some(&layout)).is_err());
        let crc = crate::space_link::csp_crc32c(&b.header[..8]);
        b.header[8..].copy_from_slice(&crc.to_be_bytes());
        assert!(combine_with_layout(&[a, b], 0.2, Some(&layout)).is_err());
        let mut bad = layout;
        bad.identity_fields[0].start = usize::MAX;
        assert!(bad.validate().is_err());
    }

    #[test]
    fn header_crc16_known_check_and_unprotected_identity_are_rejected() {
        let mut layout = HeaderLayout {
            bytes: 11,
            protected_start: 0,
            protected_end: 9,
            checksum_offset: 9,
            checksum: HeaderChecksum::Crc16CcittFalseBe,
            identity_fields: vec![ByteRange { start: 0, end: 9 }],
            immutable_codeword_contract: "Verified mission managed-parameter example only".into(),
        };
        let mut bytes = b"123456789".to_vec();
        bytes.extend([0x29, 0xb1]);
        assert_eq!(layout.key(&bytes).unwrap(), b"123456789");
        bytes[10] ^= 1;
        assert!(layout.key(&bytes).is_err());
        layout.identity_fields[0].end = 11;
        assert!(layout.validate().is_err());
        layout.bytes = usize::MAX;
        assert!(layout.validate().is_err());
    }
}
