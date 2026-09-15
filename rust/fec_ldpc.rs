//! Sparse, bounded normalized-min-sum LDPC decoder. Every successful result
//! satisfies all configured parity checks; this is not an independent CRC.
use serde::{Deserialize, Serialize};

const MAX_EDGES: usize = 1_048_576;
const MAX_EDGE_ITERATIONS: usize = 100_000_000;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LdpcConfig {
    pub codeword_bits: usize,
    /// Each row lists distinct zero-based variable positions whose XOR is zero.
    pub checks: Vec<Vec<usize>>,
    /// Exact information/output coordinates in desired MSB-first byte order.
    /// They are not inferred from H; caller supplies the encoder's mapping.
    pub output_bits: Vec<usize>,
    pub max_iterations: usize,
    pub normalization: f64,
    pub llr_clip: f64,
}

impl LdpcConfig {
    /// Real CCSDS231.0-B-4 §4.2.2 (128,64) telecommand LDPC code.
    /// No CLTU synchronization, randomization or tail-sequence processing here.
    pub fn ccsds_tc128() -> Self {
        let shifts: [[&[usize]; 8]; 4] = [
            [&[0, 7], &[2], &[14], &[6], &[], &[0], &[13], &[0]],
            [&[6], &[0, 15], &[0], &[1], &[0], &[], &[0], &[7]],
            [&[4], &[1], &[0, 15], &[14], &[11], &[0], &[], &[3]],
            [&[0], &[1], &[9], &[0, 13], &[14], &[1], &[0], &[]],
        ];
        Self::from_circulants(16, shifts)
    }

    /// Real CCSDS231.0-B-4 §4.2.2 (512,256) telecommand LDPC code.
    /// Thirty-two information bytes; this does not perform TC randomization
    /// or CLTU framing. TC randomization is different from TM randomization.
    pub fn ccsds_tc512() -> Self {
        let shifts: [[&[usize]; 8]; 4] = [
            [&[0, 63], &[30], &[50], &[25], &[], &[43], &[62], &[0]],
            [&[56], &[0, 61], &[50], &[23], &[0], &[], &[37], &[26]],
            [&[16], &[0], &[0, 55], &[27], &[56], &[0], &[], &[43]],
            [&[35], &[56], &[62], &[0, 11], &[58], &[3], &[0], &[]],
        ];
        Self::from_circulants(64, shifts)
    }

    fn from_circulants(size: usize, shifts: [[&[usize]; 8]; 4]) -> Self {
        let mut checks = Vec::with_capacity(4 * size);
        for block_row in shifts {
            for row in 0..size {
                let mut positions = Vec::new();
                for (block_column, shifts) in block_row.iter().enumerate() {
                    for &shift in *shifts {
                        positions.push(block_column * size + (row + shift) % size);
                    }
                }
                positions.sort_unstable();
                checks.push(positions);
            }
        }
        Self {
            codeword_bits: size * 8,
            checks,
            output_bits: (0..size * 4).collect(),
            max_iterations: 50,
            normalization: 0.8,
            llr_clip: 50.0,
        }
    }

    pub fn validate(&self) -> Result<(), String> {
        if !(8..=super::MAX_FRAME_CODE_BITS).contains(&self.codeword_bits)
            || self.checks.is_empty()
            || self.checks.len() > super::MAX_FRAME_CODE_BITS
            || self.output_bits.is_empty()
            || self.output_bits.len() >= self.codeword_bits
            || !self.output_bits.len().is_multiple_of(8)
            || !(1..=200).contains(&self.max_iterations)
            || !self.normalization.is_finite()
            || self.normalization <= 0.0
            || self.normalization > 1.0
            || !self.llr_clip.is_finite()
            || self.llr_clip <= 0.0
            || self.llr_clip > 100.0
        {
            return Err("invalid LDPC dimensions, byte output mapping, iteration count, normalization or LLR clip".into());
        }
        let mut degree = vec![0usize; self.codeword_bits];
        let mut seen = vec![usize::MAX; self.codeword_bits];
        let mut edges = 0usize;
        for (row, variables) in self.checks.iter().enumerate() {
            if variables.len() < 2 || variables.len() > self.codeword_bits {
                return Err("LDPC checks need at least two distinct in-range variables".into());
            }
            edges = edges
                .checked_add(variables.len())
                .ok_or("LDPC edge count overflow")?;
            if edges > MAX_EDGES {
                return Err("LDPC exceeds edge bound".into());
            }
            for &variable in variables {
                if variable >= self.codeword_bits || seen[variable] == row {
                    return Err("LDPC check has duplicate or out-of-range coordinate".into());
                }
                seen[variable] = row;
                degree[variable] += 1;
            }
        }
        if degree.contains(&0) {
            return Err("LDPC H leaves an unconstrained variable".into());
        }
        if edges
            .checked_mul(self.max_iterations)
            .is_none_or(|n| n > MAX_EDGE_ITERATIONS)
        {
            return Err("LDPC edge-iteration work bound exceeded".into());
        }
        let mut output_seen = vec![false; self.codeword_bits];
        for &position in &self.output_bits {
            if position >= self.codeword_bits || output_seen[position] {
                return Err("LDPC output map has duplicate or out-of-range coordinate".into());
            }
            output_seen[position] = true;
        }
        Ok(())
    }

    pub fn syndrome_is_zero(&self, bits: &[u8]) -> Result<bool, String> {
        self.validate()?;
        if bits.len() != self.codeword_bits || bits.iter().any(|&b| b > 1) {
            return Err("LDPC syndrome input must be an exact binary codeword".into());
        }
        Ok(self.syndrome_validated(bits))
    }

    fn syndrome_validated(&self, bits: &[u8]) -> bool {
        self.checks
            .iter()
            .all(|row| row.iter().fold(0u8, |parity, &i| parity ^ bits[i]) == 0)
    }

    /// Input is signed evidence for ONE (not the usual zero-positive LLR).
    /// Normalize a calibrated LLR upstream if available; internal message
    /// saturation is explicit in config and part of this approximate decoder.
    pub fn decode(&self, soft: &[f64]) -> Result<(Vec<u8>, usize), String> {
        self.validate()?;
        if soft.len() != self.codeword_bits || soft.iter().any(|x| !x.is_finite()) {
            return Err("LDPC needs one finite soft value per configured bit".into());
        }
        if soft.iter().all(|&x| x == 0.0) {
            return Err("LDPC input has no nonzero channel evidence".into());
        }
        let channel: Vec<f64> = soft
            .iter()
            .map(|&x| -x.clamp(-self.llr_clip, self.llr_clip))
            .collect();
        let mut hard: Vec<u8> = channel.iter().map(|&llr| u8::from(llr <= 0.0)).collect();
        if self.syndrome_validated(&hard) {
            return Ok((self.output_bits.iter().map(|&i| hard[i]).collect(), 0));
        }
        let offsets: Vec<usize> = self
            .checks
            .iter()
            .scan(0, |offset, row| {
                let start = *offset;
                *offset += row.len();
                Some(start)
            })
            .collect();
        let edges: usize = self.checks.iter().map(Vec::len).sum();
        let mut check_to_variable = vec![0.0f64; edges];
        let mut variable_to_check = vec![0.0f64; edges];
        let mut posterior = channel.clone();
        for iteration in 1..=self.max_iterations {
            for (row, &offset) in self.checks.iter().zip(&offsets) {
                for (local, &variable) in row.iter().enumerate() {
                    let edge = offset + local;
                    variable_to_check[edge] = (posterior[variable] - check_to_variable[edge])
                        .clamp(-self.llr_clip, self.llr_clip);
                }
            }
            posterior.copy_from_slice(&channel);
            for (row, &offset) in self.checks.iter().zip(&offsets) {
                let mut negative = false;
                let (mut smallest, mut second) = (f64::INFINITY, f64::INFINITY);
                let mut smallest_index = 0;
                for local in 0..row.len() {
                    let value = variable_to_check[offset + local];
                    negative ^= value < 0.0;
                    let magnitude = value.abs();
                    if magnitude < smallest {
                        second = smallest;
                        smallest = magnitude;
                        smallest_index = local;
                    } else if magnitude < second {
                        second = magnitude;
                    }
                }
                for (local, &variable) in row.iter().enumerate() {
                    let magnitude = if local == smallest_index {
                        second
                    } else {
                        smallest
                    } * self.normalization;
                    let sign = negative ^ (variable_to_check[offset + local] < 0.0);
                    let message = if sign { -magnitude } else { magnitude };
                    check_to_variable[offset + local] = message;
                    posterior[variable] += message;
                }
            }
            for (bit, &llr) in hard.iter_mut().zip(&posterior) {
                *bit = u8::from(llr <= 0.0);
            }
            if self.syndrome_validated(&hard) {
                return Ok((
                    self.output_bits.iter().map(|&i| hard[i]).collect(),
                    iteration,
                ));
            }
        }
        Err("LDPC did not converge to a zero-syndrome codeword within iteration budget".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    // Independent generator rows from CCSDS231.0-B-4 table4-1; H is not
    // used to produce these words. Right rotate separately inside each 16-bit
    // circulant, not the whole 64-bit parity row.
    fn published_word(information: u64) -> Vec<u8> {
        let rows = [
            0x0e69166bef4c0bc2u64,
            0x7766137ebb248418,
            0xc480feb9cd53a713,
            0x4eaa22fa465eea11,
        ];
        let mut parity = 0u64;
        for bit in 0..64 {
            if information & (1 << (63 - bit)) == 0 {
                continue;
            }
            for block in 0..4 {
                let chunk = (rows[bit / 16] >> (48 - block * 16)) as u16;
                parity ^= u64::from(chunk.rotate_right((bit % 16) as u32)) << (48 - block * 16);
            }
        }
        [information.to_be_bytes(), parity.to_be_bytes()]
            .concat()
            .iter()
            .flat_map(|b| (0..8).rev().map(move |i| (b >> i) & 1))
            .collect()
    }

    // Independent table4-2 generator rows from CCSDS231.0-B-4, page4-3.
    // Each 64-bit word is a separately rotated 64x64 circulant. This encoder
    // does not use the parity-check matrix or the production decoder.
    fn published_tc512_word(information: &[u8; 32]) -> Vec<u8> {
        const ROWS: [[u64; 4]; 4] = [
            [
                0x1D21794A22761FAE,
                0x59945014257E130D,
                0x74D6054003794014,
                0x2DADEB9CA25EF12E,
            ],
            [
                0x60E0B6623C5CE512,
                0x4D2C81ECC7F469AB,
                0x20678DBFB7523ECE,
                0x2B54B906A9DBE98C,
            ],
            [
                0xF6739BCF54273E77,
                0x167BDA120C6C4774,
                0x4C071EFF5E32A759,
                0x3138670C095C39B5,
            ],
            [
                0x28706BD045300258,
                0x2DAB85F05B9201D0,
                0x8DFDEE2D9D84CA88,
                0xB371FAE63A4EB07E,
            ],
        ];
        let mut parity = [0u64; 4];
        for bit in 0..256 {
            if information[bit / 8] & (1 << (7 - bit % 8)) == 0 {
                continue;
            }
            for block in 0..4 {
                parity[block] ^= ROWS[bit / 64][block].rotate_right((bit % 64) as u32);
            }
        }
        information
            .iter()
            .copied()
            .chain(parity.into_iter().flat_map(u64::to_be_bytes))
            .flat_map(|b| (0..8).rev().map(move |i| (b >> i) & 1))
            .collect()
    }

    #[test]
    fn standard_tc512_matches_every_published_generator_row() {
        let config = LdpcConfig::ccsds_tc512();
        assert_eq!(config.checks.len(), 256);
        assert!(config.checks.iter().all(|r| r.len() == 8));
        for bit in 0..256 {
            let mut information = [0u8; 32];
            information[bit / 8] = 1 << (7 - bit % 8);
            let word = published_tc512_word(&information);
            assert!(
                config.syndrome_is_zero(&word).unwrap(),
                "generator row {bit}"
            );
            let soft: Vec<f64> = word
                .iter()
                .map(|&b| if b == 1 { 5.0 } else { -5.0 })
                .collect();
            assert_eq!(config.decode(&soft).unwrap(), (word[..256].to_vec(), 0));
        }
    }

    #[test]
    fn standard_tc512_corrects_every_one_bit_location_and_returns_32_bytes() {
        let config = LdpcConfig::ccsds_tc512();
        let information = std::array::from_fn(|i| (i * 19 + 37) as u8);
        let word = published_tc512_word(&information);
        let clean: Vec<f64> = word
            .iter()
            .map(|&b| if b == 1 { 5.0 } else { -5.0 })
            .collect();
        for error in 0..512 {
            let mut soft = clean.clone();
            soft[error] *= -0.1;
            let (actual, iterations) = config.decode(&soft).unwrap();
            assert_eq!(actual, word[..256], "error location {error}");
            assert!(iterations > 0);
        }
        let code = super::super::FrameCode::Ldpc { config };
        assert_eq!(code.encoded_bits(32).unwrap(), 512);
        assert!(code.encoded_bits(31).is_err());
        assert_eq!(code.decode(&clean, 32).unwrap().bytes, information);
    }
    #[test]
    fn standard_tc128_matches_all_published_generator_rows() {
        let config = LdpcConfig::ccsds_tc128();
        assert_eq!(config.checks.len(), 64);
        assert!(config.checks.iter().all(|r| r.len() == 8));
        for bit in 0..64 {
            let word = published_word(1 << bit);
            assert!(
                config.syndrome_is_zero(&word).unwrap(),
                "published generator bit {bit}"
            );
            let soft: Vec<f64> = word
                .iter()
                .map(|&b| if b == 1 { 5.0 } else { -5.0 })
                .collect();
            assert_eq!(config.decode(&soft).unwrap(), (word[..64].to_vec(), 0));
        }
    }
    #[test]
    fn standard_tc128_soft_decoder_corrects_each_single_symbol() {
        let config = LdpcConfig::ccsds_tc128();
        let word = published_word(0x123456789abcdef0);
        for error in 0..128 {
            let mut soft: Vec<f64> = word
                .iter()
                .map(|&b| if b == 1 { 5.0 } else { -5.0 })
                .collect();
            soft[error] *= -0.1;
            let (actual, iterations) = config.decode(&soft).unwrap();
            assert_eq!(actual, word[..64]);
            assert!(iterations > 0);
        }
        let code = super::super::FrameCode::Ldpc { config };
        let soft: Vec<f64> = word
            .iter()
            .map(|&b| if b == 1 { 5.0 } else { -5.0 })
            .collect();
        assert_eq!(
            code.decode(&soft, 8).unwrap().bytes,
            0x123456789abcdef0u64.to_be_bytes()
        );
        assert!(code.encoded_bits(7).is_err());
    }
    #[test]
    fn malformed_sparse_matrices_and_soft_inputs_fail_closed() {
        let standard = LdpcConfig::ccsds_tc128();
        for variation in 0..10 {
            let mut bad = standard.clone();
            match variation {
                0 => {
                    let duplicate = bad.checks[0][0];
                    bad.checks[0].push(duplicate);
                }
                1 => bad.checks[0][0] = 128,
                2 => bad.output_bits[1] = bad.output_bits[0],
                3 => bad.output_bits[1] = 128,
                4 => bad.max_iterations = 201,
                5 => bad.normalization = f64::NAN,
                6 => bad.llr_clip = f64::INFINITY,
                7 => bad.codeword_bits = usize::MAX,
                8 => bad.checks.iter_mut().for_each(|r| r.retain(|&v| v != 0)),
                _ => bad.checks[0].clear(),
            }
            assert!(bad.validate().is_err(), "variation {variation}");
        }
        assert!(standard.decode(&[0.0; 128]).is_err());
        assert!(standard.decode(&[f64::NAN; 128]).is_err());
        assert!(standard.decode(&[1.0; 127]).is_err());
        assert!(standard.syndrome_is_zero(&[2; 128]).is_err());
        let mut no_convergence = standard.clone();
        no_convergence.max_iterations = 1;
        let soft: Vec<f64> = (0..128)
            .map(|i| if (i * 17 + i / 3) % 11 < 5 { 5.0 } else { -5.0 })
            .collect();
        assert!(no_convergence.decode(&soft).is_err());
        let mut overwork = standard.clone();
        overwork.checks = vec![(0..128).collect(); 5000];
        overwork.max_iterations = 200;
        assert!(overwork.validate().unwrap_err().contains("work bound"));
    }
    #[test]
    fn generic_h_uses_exact_explicit_output_order() {
        let mut config = LdpcConfig::ccsds_tc128();
        config.output_bits.reverse();
        let word = published_word(0x123456789abcdef0);
        let soft: Vec<f64> = word
            .iter()
            .map(|&b| if b == 1 { 5.0 } else { -5.0 })
            .collect();
        let (bits, _) = config.decode(&soft).unwrap();
        assert_eq!(bits, word[..64].iter().rev().copied().collect::<Vec<_>>());
    }
}
