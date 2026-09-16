//! Explicit, bounded frame FEC. Positive soft values mean bit ONE; bytes are
//! MSB-first. A valid code syndrome is not an independent payload CRC or proof
//! that decoding recovered the transmitted message. See docs/fec-support.md.

use serde::{Deserialize, Serialize};

#[path = "fec_ldpc.rs"]
mod ldpc;
#[path = "fec_rs.rs"]
mod rs;
pub use ldpc::{LdpcConfig, LdpcSoftOutput};
pub use rs::{ReedSolomonConfig, SymbolBasis};

pub const MAX_FRAME_CODE_BITS: usize = 65_536;
pub const MAX_UNCODED_FRAME_BYTES: usize = 65_536;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum FrameCode {
    #[default]
    None,
    ReedSolomon {
        config: ReedSolomonConfig,
    },
    Ldpc {
        config: LdpcConfig,
    },
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DecodedCodeword {
    pub bytes: Vec<u8>,
    pub corrected_symbols: Option<usize>,
    pub iterations: Option<usize>,
    /// All configured code checks passed; not equivalent to independent CRC.
    pub parity_verified: bool,
}

impl FrameCode {
    pub fn validate(&self) -> Result<(), String> {
        match self {
            Self::None => Ok(()),
            Self::ReedSolomon { config } => config.validate(),
            Self::Ldpc { config } => config.validate(),
        }
    }

    pub fn encoded_bits(&self, decoded_bytes: usize) -> Result<usize, String> {
        let decoded_bits = decoded_bytes
            .checked_mul(8)
            .ok_or("decoded bit count overflow")?;
        if decoded_bytes == 0 || decoded_bytes > MAX_UNCODED_FRAME_BYTES {
            return Err("decoded frame must contain 1..=65536 bytes; coded variants impose narrower explicit dimensions".into());
        }
        self.validate()?;
        match self {
            Self::None => Ok(decoded_bits),
            Self::ReedSolomon { config } => {
                if decoded_bytes != config.data_symbols() * config.interleaving {
                    return Err(
                        "decoded frame length disagrees with explicit RS shortening/interleaving"
                            .into(),
                    );
                }
                Ok(config.codeword_symbols() * config.interleaving * 8)
            }
            Self::Ldpc { config } => {
                if decoded_bits != config.output_bits.len() {
                    return Err(
                        "decoded frame length disagrees with LDPC output coordinates".into(),
                    );
                }
                Ok(config.codeword_bits)
            }
        }
    }

    pub fn decode(&self, soft: &[f64], decoded_bytes: usize) -> Result<DecodedCodeword, String> {
        let expected = self.encoded_bits(decoded_bytes)?;
        if soft.len() != expected {
            return Err("FEC input length differs from configured codeword length".into());
        }
        if soft.iter().any(|s| !s.is_finite()) {
            return Err("FEC soft input must be finite".into());
        }
        match self {
            Self::None => Ok(DecodedCodeword {
                bytes: hard_bytes(soft),
                corrected_symbols: None,
                iterations: None,
                parity_verified: false,
            }),
            Self::ReedSolomon { config } => {
                let (bytes, corrected) = config.decode(&hard_bytes(soft))?;
                Ok(DecodedCodeword {
                    bytes,
                    corrected_symbols: Some(corrected),
                    iterations: None,
                    parity_verified: true,
                })
            }
            Self::Ldpc { config } => {
                let (bits, iterations) = config.decode(soft)?;
                let mut bytes = vec![0; decoded_bytes];
                for (i, bit) in bits.into_iter().enumerate() {
                    bytes[i / 8] |= bit << (7 - i % 8);
                }
                Ok(DecodedCodeword {
                    bytes,
                    corrected_symbols: None,
                    iterations: Some(iterations),
                    parity_verified: true,
                })
            }
        }
    }
}

fn hard_bytes(soft: &[f64]) -> Vec<u8> {
    soft.as_chunks::<8>()
        .0
        .iter()
        .map(|chunk| chunk.iter().fold(0, |b, x| (b << 1) | u8::from(*x >= 0.0)))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    pub(super) fn soft(bytes: &[u8]) -> Vec<f64> {
        bytes
            .iter()
            .flat_map(|b| {
                (0..8)
                    .rev()
                    .map(move |i| if b & (1 << i) != 0 { 5.0 } else { -5.0 })
            })
            .collect()
    }
    #[test]
    fn frame_contract_rejects_wrong_lengths_and_nonfinite_values() {
        assert_eq!(
            FrameCode::None
                .decode(&soft(&[0x81, 0x42]), 2)
                .unwrap()
                .bytes,
            [0x81, 0x42]
        );
        assert!(
            !FrameCode::None
                .decode(&soft(&[0x81]), 1)
                .unwrap()
                .parity_verified
        );
        assert!(FrameCode::None.decode(&[0.0; 7], 1).is_err());
        assert!(FrameCode::None.encoded_bits(0).is_err());
        assert!(FrameCode::None.encoded_bits(usize::MAX).is_err());
        assert_eq!(FrameCode::None.encoded_bits(65536).unwrap(), 524288);
        assert!(FrameCode::None.encoded_bits(65537).is_err());
        for v in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            assert!(FrameCode::None.decode(&[v; 8], 1).is_err());
        }
    }
}
