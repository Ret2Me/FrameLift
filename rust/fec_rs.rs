//! GF(256) Reed–Solomon: Berlekamp–Massey, Chien search and a bounded GF
//! linear solve for error magnitudes. No erasures or soft-list search.
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SymbolBasis {
    Conventional,
    CcsdsDual,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReedSolomonConfig {
    /// Degree-eight primitive polynomial including the x^8 coefficient.
    pub field_polynomial: u16,
    /// Generator roots are alpha^((first_root + j) * primitive_step).
    pub first_root: usize,
    pub primitive_step: usize,
    pub parity_symbols: usize,
    /// Number of omitted leading ZERO symbols per full-length 255-symbol lane.
    pub shortening: usize,
    /// Byte interleave: lane `l` receives `input[l]`, `input[l+I]`, ... .
    pub interleaving: usize,
    pub basis: SymbolBasis,
}

impl Default for ReedSolomonConfig {
    fn default() -> Self {
        Self::ccsds_rs255_223(1, 0)
    }
}

impl ReedSolomonConfig {
    pub fn ccsds_rs255_223(interleaving: usize, shortening: usize) -> Self {
        Self {
            field_polynomial: 0x187,
            first_root: 112,
            primitive_step: 11,
            parity_symbols: 32,
            shortening,
            interleaving,
            basis: SymbolBasis::CcsdsDual,
        }
    }
    pub fn validate(&self) -> Result<(), String> {
        if !(2..=128).contains(&self.parity_symbols)
            || !self.parity_symbols.is_multiple_of(2)
            || self.shortening >= 255 - self.parity_symbols
            || !(1..=8).contains(&self.interleaving)
            || self.first_root >= 255
            || !(1..255).contains(&self.primitive_step)
            || gcd(self.primitive_step, 255) != 1
        {
            return Err("RS requires even parity 2..128, nonempty shortened payload, interleave 1..8, root 0..254 and coprime primitive step 1..254".into());
        }
        if self.basis == SymbolBasis::CcsdsDual && self.field_polynomial != 0x187 {
            return Err("CCSDS dual basis requires field polynomial 0x187".into());
        }
        Gf::new(self.field_polynomial)?;
        Ok(())
    }
    pub fn codeword_symbols(&self) -> usize {
        255usize.saturating_sub(self.shortening)
    }
    pub fn data_symbols(&self) -> usize {
        self.codeword_symbols().saturating_sub(self.parity_symbols)
    }

    /// Encode payload bytes for configuration/fixture interoperability tests.
    /// Input data remain systematic; this is also useful for local link tests.
    pub fn encode(&self, bytes: &[u8]) -> Result<Vec<u8>, String> {
        self.validate()?;
        let k = self.data_symbols();
        let n = self.codeword_symbols();
        if bytes.len() != k * self.interleaving {
            return Err("RS payload length mismatch".into());
        }
        let gf = Gf::new(self.field_polynomial)?;
        let (forward, inverse) = basis_tables();
        let mut generator = vec![1u8]; // Descending polynomial coefficients.
        for root in 0..self.parity_symbols {
            let value = gf.alpha((self.first_root + root) * self.primitive_step);
            let mut next = vec![0; generator.len() + 1];
            for (i, &c) in generator.iter().enumerate() {
                next[i] ^= c;
                next[i + 1] ^= gf.mul(c, value);
            }
            generator = next;
        }
        let mut result = vec![0; n * self.interleaving];
        for lane in 0..self.interleaving {
            let mut polynomial = vec![0; n];
            for i in 0..k {
                let byte = bytes[i * self.interleaving + lane];
                polynomial[i] = if self.basis == SymbolBasis::CcsdsDual {
                    inverse[byte as usize]
                } else {
                    byte
                };
                result[i * self.interleaving + lane] = byte;
            }
            for i in 0..k {
                let lead = polynomial[i];
                for j in 1..generator.len() {
                    polynomial[i + j] ^= gf.mul(lead, generator[j]);
                }
            }
            for i in k..n {
                result[i * self.interleaving + lane] = if self.basis == SymbolBasis::CcsdsDual {
                    forward[polynomial[i] as usize]
                } else {
                    polynomial[i]
                };
            }
        }
        Ok(result)
    }

    pub fn decode(&self, bytes: &[u8]) -> Result<(Vec<u8>, usize), String> {
        self.validate()?;
        let n = self.codeword_symbols();
        let k = self.data_symbols();
        if bytes.len() != n * self.interleaving {
            return Err("RS codeword length mismatch".into());
        }
        let gf = Gf::new(self.field_polynomial)?;
        let (forward, inverse) = basis_tables();
        let mut decoded = vec![0; k * self.interleaving];
        let mut corrected = 0;
        for lane in 0..self.interleaving {
            let mut word: Vec<u8> = bytes
                .iter()
                .skip(lane)
                .step_by(self.interleaving)
                .map(|&b| {
                    if self.basis == SymbolBasis::CcsdsDual {
                        inverse[b as usize]
                    } else {
                        b
                    }
                })
                .collect();
            corrected += correct(&mut word, self, &gf)?;
            for i in 0..k {
                decoded[i * self.interleaving + lane] = if self.basis == SymbolBasis::CcsdsDual {
                    forward[word[i] as usize]
                } else {
                    word[i]
                };
            }
        }
        Ok((decoded, corrected))
    }
}

fn gcd(mut a: usize, mut b: usize) -> usize {
    while b != 0 {
        (a, b) = (b, a % b);
    }
    a
}

struct Gf {
    exp: [u8; 255],
    log: [usize; 256],
}
impl Gf {
    fn new(polynomial: u16) -> Result<Self, String> {
        if !(0x101..=0x1ff).contains(&polynomial) || polynomial & 1 == 0 {
            return Err("RS field polynomial must be primitive of degree eight".into());
        }
        let mut result = Self {
            exp: [0; 255],
            log: [usize::MAX; 256],
        };
        let mut x = 1u16;
        for i in 0..255 {
            if x == 0 || x > 255 || result.log[x as usize] != usize::MAX {
                return Err("RS field polynomial is not primitive for alpha=x".into());
            }
            result.exp[i] = x as u8;
            result.log[x as usize] = i;
            x <<= 1;
            if x & 0x100 != 0 {
                x ^= polynomial;
            }
        }
        if x != 1 {
            return Err("RS field generator did not close at order 255".into());
        }
        Ok(result)
    }
    fn alpha(&self, power: usize) -> u8 {
        self.exp[power % 255]
    }
    fn mul(&self, a: u8, b: u8) -> u8 {
        if a == 0 || b == 0 {
            0
        } else {
            self.alpha(self.log[a as usize] + self.log[b as usize])
        }
    }
    fn inv(&self, a: u8) -> u8 {
        debug_assert_ne!(a, 0);
        self.alpha(255 - self.log[a as usize])
    }
}

fn syndromes(word: &[u8], config: &ReedSolomonConfig, gf: &Gf) -> Vec<u8> {
    (0..config.parity_symbols)
        .map(|j| {
            let root = gf.alpha((config.first_root + j) * config.primitive_step);
            word.iter()
                .fold(0u8, |acc, &symbol| gf.mul(acc, root) ^ symbol)
        })
        .collect()
}

fn correct(word: &mut [u8], config: &ReedSolomonConfig, gf: &Gf) -> Result<usize, String> {
    let syndrome = syndromes(word, config, gf);
    if syndrome.iter().all(|&s| s == 0) {
        return Ok(0);
    }
    let roots = config.parity_symbols;
    let mut locator = vec![0; roots + 1];
    locator[0] = 1;
    let mut previous = locator.clone();
    let (mut degree, mut distance, mut last_discrepancy) = (0usize, 1usize, 1u8);
    for step in 0..roots {
        let mut discrepancy = syndrome[step];
        for i in 1..=degree {
            discrepancy ^= gf.mul(locator[i], syndrome[step - i]);
        }
        if discrepancy == 0 {
            distance += 1;
            continue;
        }
        let old_locator = locator.clone();
        let factor = gf.mul(discrepancy, gf.inv(last_discrepancy));
        for i in 0..=roots - distance {
            locator[i + distance] ^= gf.mul(factor, previous[i]);
        }
        if 2 * degree <= step {
            degree = step + 1 - degree;
            previous = old_locator;
            last_discrepancy = discrepancy;
            distance = 1;
        } else {
            distance += 1;
        }
    }
    if degree == 0 || degree * 2 > roots {
        return Err("RS uncorrectable: locator exceeds correction radius".into());
    }
    let positions: Vec<usize> = (0..word.len())
        .filter(|&position| {
            let power = (word.len() - 1 - position) * config.primitive_step % 255;
            let inverse = gf.alpha(255 - power);
            locator[..=degree]
                .iter()
                .rev()
                .fold(0, |acc, &c| gf.mul(acc, inverse) ^ c)
                == 0
        })
        .collect();
    if positions.len() != degree {
        return Err("RS uncorrectable: locator roots do not match transmitted positions".into());
    }
    // S_j = sum(error_l * alpha^((fcr+j)*prim*(n-1-position_l))).
    // A tiny bounded Vandermonde solve avoids convention-sensitive Forney
    // formulas, especially for nonunit primitive steps and nonzero first roots.
    let mut matrix = vec![vec![0; degree + 1]; degree];
    for row in 0..degree {
        for (column, &position) in positions.iter().enumerate() {
            matrix[row][column] = gf.alpha(
                (config.first_root + row) * config.primitive_step * (word.len() - 1 - position),
            );
        }
        matrix[row][degree] = syndrome[row];
    }
    for column in 0..degree {
        let pivot = (column..degree)
            .find(|&row| matrix[row][column] != 0)
            .ok_or("RS uncorrectable: singular error system")?;
        matrix.swap(column, pivot);
        let (before, rest) = matrix.split_at_mut(column);
        let (pivot_row, after) = rest.split_first_mut().expect("column is within matrix");
        let scale = gf.inv(pivot_row[column]);
        for value in &mut pivot_row[column..=degree] {
            *value = gf.mul(*value, scale);
        }
        for row in before.iter_mut().chain(after) {
            let factor = row[column];
            for (value, pivot) in row[column..=degree]
                .iter_mut()
                .zip(&pivot_row[column..=degree])
            {
                *value ^= gf.mul(factor, *pivot);
            }
        }
    }
    for (i, &position) in positions.iter().enumerate() {
        if matrix[i][degree] == 0 {
            return Err("RS uncorrectable: zero error magnitude".into());
        }
        word[position] ^= matrix[i][degree];
    }
    if syndromes(word, config, gf).iter().any(|&s| s != 0) {
        return Err("RS uncorrectable: corrected word fails full syndrome verification".into());
    }
    Ok(degree)
}

fn basis_tables() -> &'static ([u8; 256], [u8; 256]) {
    // These conversion maps never depend on a codeword or RS configuration.
    // Build them at compile time and borrow them instead of rebuilding/copying
    // 512 bytes for every encode/decode. All conversion arithmetic is unchanged.
    static TABLES: ([u8; 256], [u8; 256]) = make_basis_tables();
    &TABLES
}

const fn make_basis_tables() -> ([u8; 256], [u8; 256]) {
    // CCSDS131.0-B-5 §4.3.9.3 matrix rows, MSB first. Mathematical
    // conversion constants also agree with libfec's published gen_ccsds_tal.c.
    const ROWS: [u8; 8] = [0x8d, 0xef, 0xec, 0x86, 0xfa, 0x99, 0xaf, 0x7b];
    let mut forward = [0u8; 256];
    let mut inverse = [0u8; 256];
    let mut symbol = 0;
    while symbol < 256 {
        let mut bit = 0;
        while bit < 8 {
            if symbol & (1 << (7 - bit)) != 0 {
                forward[symbol] ^= ROWS[bit];
            }
            bit += 1;
        }
        inverse[forward[symbol] as usize] = symbol as u8;
        symbol += 1;
    }
    (forward, inverse)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn basis_tables_are_shared_immutable_and_match_every_legacy_symbol() {
        let cached = basis_tables();
        assert!(std::ptr::eq(cached, basis_tables()));
        // Original runtime loop, retained only as a byte-exact regression oracle.
        let rows = [0x8d, 0xef, 0xec, 0x86, 0xfa, 0x99, 0xaf, 0x7b];
        let mut forward = [0u8; 256];
        let mut inverse = [0u8; 256];
        for symbol in 0..256 {
            for (bit, &row) in rows.iter().enumerate() {
                if symbol & (1 << (7 - bit)) != 0 {
                    forward[symbol] ^= row;
                }
            }
            inverse[forward[symbol] as usize] = symbol as u8;
        }
        assert_eq!(cached, &(forward, inverse));
    }
    // Directly transcribed mathematical coefficients from CCSDS131.0-B-5,
    // Annex G page G-1, not produced by this encoder. A unit message in the
    // last data position yields g(x) with the leading coefficient systematic.
    const ANNEX_G_PARITY: [u8; 32] = [
        0x5b, 0x7f, 0x56, 0x10, 0x1e, 0x0d, 0xeb, 0x61, 0xa5, 0x08, 0x2a, 0x36, 0x56, 0xab, 0x20,
        0x71, 0x20, 0xab, 0x56, 0x36, 0x2a, 0x08, 0xa5, 0x61, 0xeb, 0x0d, 0x1e, 0x10, 0x56, 0x7f,
        0x5b, 0x01,
    ];
    fn annex_word() -> Vec<u8> {
        let mut word = vec![0; 222];
        word.push(1);
        word.extend(ANNEX_G_PARITY);
        word
    }
    fn conventional() -> ReedSolomonConfig {
        ReedSolomonConfig {
            basis: SymbolBasis::Conventional,
            ..Default::default()
        }
    }
    #[test]
    fn published_ccsds_polynomial_is_an_independent_codeword_oracle() {
        let config = conventional();
        let word = annex_word();
        assert_eq!(config.encode(&word[..223]).unwrap(), word);
        assert_eq!(config.decode(&word).unwrap(), (word[..223].to_vec(), 0));
        // Every possible single-symbol position, both systematic and parity.
        for position in 0..255 {
            let mut damaged = word.clone();
            damaged[position] ^= (position as u8).wrapping_add(1).max(1);
            assert_eq!(
                config.decode(&damaged).unwrap(),
                (word[..223].to_vec(), 1),
                "position {position}"
            );
        }
        for errors in 1..=16 {
            for seed in 0..8 {
                let mut damaged = word.clone();
                for i in 0..errors {
                    damaged[(i * 19 + seed * 3) % 255] ^= (31 + i * 7) as u8;
                }
                assert_eq!(
                    config.decode(&damaged).unwrap(),
                    (word[..223].to_vec(), errors)
                );
            }
        }
        let mut damaged = word;
        for (i, byte) in damaged[..17].iter_mut().enumerate() {
            *byte ^= (i + 1) as u8;
        }
        assert!(config.decode(&damaged).is_err());
    }
    #[test]
    fn shortened_interleaving_and_generic_field_are_real_codecs() {
        for field in [0x187, 0x11d] {
            for first_root in [0, 1, 112] {
                for step in [1, 11] {
                    for parity in [2, 16, 32, 64, 128] {
                        let config = ReedSolomonConfig {
                            field_polynomial: field,
                            first_root,
                            primitive_step: step,
                            parity_symbols: parity,
                            shortening: 19,
                            interleaving: 3,
                            basis: SymbolBasis::Conventional,
                        };
                        let data: Vec<u8> = (0..config.data_symbols() * config.interleaving)
                            .map(|i| (i * 13 + 37) as u8)
                            .collect();
                        let word = config.encode(&data).unwrap();
                        assert_eq!(config.decode(&word).unwrap(), (data.clone(), 0));
                        let mut damaged = word;
                        for lane in 0..3 {
                            for i in 0..parity / 2 {
                                damaged[(i * 3 + lane) * 3 + lane] ^= (i + 1) as u8;
                            }
                        }
                        assert_eq!(config.decode(&damaged).unwrap(), (data, parity / 2 * 3));
                    }
                }
            }
        }
    }
    #[test]
    fn ccsds_dual_basis_and_shortening_do_not_change_payload_bytes() {
        let (forward, inverse) = basis_tables();
        assert_eq!(
            [forward[1], forward[2], forward[4], forward[128]],
            [0x7b, 0xaf, 0x99, 0x8d]
        );
        for byte in 0..=255 {
            assert_eq!(inverse[forward[byte] as usize] as usize, byte);
        }
        // Annex-G polynomial mapped independently by the explicit normative
        // §4.3.9.3 matrix. These literal wire bytes do not call this encoder.
        let mut dual_word = vec![0; 222];
        dual_word.push(0x7b);
        dual_word.extend([
            0x47, 0x32, 0x5f, 0x86, 0x4a, 0x18, 0xa0, 0x78, 0x83, 0xfa, 0xb9, 0x5c, 0x5f, 0x4f,
            0xec, 0xfe, 0xec, 0x4f, 0x5f, 0x5c, 0xb9, 0xfa, 0x83, 0x78, 0xa0, 0x18, 0x4a, 0x86,
            0x5f, 0x32, 0x47, 0x7b,
        ]);
        let config = ReedSolomonConfig::default();
        assert_eq!(config.encode(&dual_word[..223]).unwrap(), dual_word);
        assert_eq!(
            config.decode(&dual_word).unwrap(),
            (dual_word[..223].to_vec(), 0)
        );
        let shortened = ReedSolomonConfig::ccsds_rs255_223(1, 200);
        assert_eq!(
            shortened.decode(&dual_word[200..]).unwrap(),
            (dual_word[200..223].to_vec(), 0)
        );
        for interleaving in [1, 2, 3, 4, 5, 8] {
            for shortening in [0, 17, 222] {
                let config = ReedSolomonConfig::ccsds_rs255_223(interleaving, shortening);
                let bytes: Vec<u8> = (0..config.data_symbols() * interleaving)
                    .map(|i| (i * 19 + 71) as u8)
                    .collect();
                let mut word = config.encode(&bytes).unwrap();
                for lane in 0..interleaving {
                    word[lane] ^= 0x81;
                }
                assert_eq!(config.decode(&word).unwrap(), (bytes.clone(), interleaving));
                let code = super::super::FrameCode::ReedSolomon { config };
                let soft: Vec<f64> = word
                    .iter()
                    .flat_map(|b| {
                        (0..8)
                            .rev()
                            .map(move |i| if b & (1 << i) != 0 { 5.0 } else { -5.0 })
                    })
                    .collect();
                assert_eq!(code.decode(&soft, bytes.len()).unwrap().bytes, bytes);
                assert!(code.encoded_bits(bytes.len() + 1).is_err());
            }
        }
    }
    #[test]
    fn rs_configuration_and_boundaries_reject_without_panics() {
        for variation in 0..12 {
            let mut bad = conventional();
            match variation {
                0 => bad.parity_symbols = 0,
                1 => bad.parity_symbols = 3,
                2 => bad.parity_symbols = usize::MAX,
                3 => bad.shortening = 223,
                4 => bad.shortening = usize::MAX,
                5 => bad.primitive_step = 0,
                6 => bad.primitive_step = 3,
                7 => bad.first_root = usize::MAX,
                8 => bad.interleaving = usize::MAX,
                9 => bad.field_polynomial = 0x11b, // irreducible but alpha=x is not primitive
                10 => bad.field_polynomial = 0x7ff,
                _ => {
                    bad.field_polynomial = 0x11d;
                    bad.basis = SymbolBasis::CcsdsDual;
                }
            }
            assert!(bad.validate().is_err(), "variation {variation}");
            assert!(bad.decode(&[]).is_err());
        }
        let config = conventional();
        assert!(config.decode(&[0; 254]).is_err());
        assert!(config.decode(&[0; 256]).is_err());
        assert!(config.encode(&[0; 224]).is_err());
    }
}
