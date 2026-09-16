//! Independent transmitter for development tests, never receiver input truth.
use num_complex::Complex64;
use telemetry_yield_rs::{advanced_iq, coded, fec::LdpcConfig, space_link, turbo};

pub fn frame(sequence: u8) -> Vec<u8> {
    let mut bytes = vec![0, sequence, sequence.wrapping_mul(7), 1];
    bytes.extend(space_link::csp_crc32c(&bytes).to_be_bytes());
    bytes
}
pub fn encode(bytes: &[u8]) -> Vec<u8> {
    let rows = [
        0x0e69166bef4c0bc2u64,
        0x7766137ebb248418,
        0xc480feb9cd53a713,
        0x4eaa22fa465eea11,
    ];
    let data = u64::from_be_bytes(bytes.try_into().unwrap());
    let mut parity = 0u64;
    for bit in 0..64 {
        if data & (1 << (63 - bit)) != 0 {
            for block in 0..4 {
                let chunk = (rows[bit / 16] >> (48 - block * 16)) as u16;
                parity ^= u64::from(chunk.rotate_right((bit % 16) as u32)) << (48 - block * 16);
            }
        }
    }
    bytes
        .iter()
        .copied()
        .chain(parity.to_be_bytes())
        .flat_map(|b| (0..8).rev().map(move |i| (b >> i) & 1))
        .collect()
}
pub fn config() -> advanced_iq::Config {
    let mut code = LdpcConfig::ccsds_tc128();
    code.max_iterations = 12;
    advanced_iq::Config {
        modulation: "bpsk_rectangular".into(),
        waveform: None,
        symbol_rate: 1000.,
        carrier_centers_hz: vec![0.],
        residual_carrier_bound_hz: 100.,
        clock_errors_ppm: vec![0.],
        phase_bins: 4,
        syncword: [0x1au8, 0xcf, 0xfc, 0x1d, 0xd3, 0x91, 0xc5, 0xa7]
            .iter()
            .flat_map(|b| (0..8).rev().map(move |i| (b >> i) & 1))
            .collect(),
        maximum_sync_hamming: 2,
        code: code.into(),
        wire_to_code: Vec::new(),
        randomizer: coded::Randomizer::None,
        validator: coded::FrameValidator::SpaceLink {
            config: space_link::SpaceLinkConfig::CspV1 {
                crc32: space_link::CspCrc32Mode::RequiredHeaderAndPayload,
            },
        },
        turbo_iterations: 4,
        damping: 0.7,
        joint: Some(turbo::JointConfig {
            segment_symbols: 32,
            channel_damping: 0.3,
        }),
        repetition: None,
        cancellation: None,
        maximum_candidates: 32,
        maximum_work: 500_000_000,
        recovery: None,
    }
}
pub fn symbols(c: &advanced_iq::Config, bytes: &[u8], key: Option<u32>) -> Vec<f64> {
    let mut bits = c.syncword.clone();
    if let Some(key) = key {
        let mut header = key.to_be_bytes().to_vec();
        header.extend(space_link::csp_crc32c(&header).to_be_bytes());
        bits.extend(
            header
                .iter()
                .flat_map(|b| (0..8).rev().map(move |i| (b >> i) & 1)),
        );
    }
    let word = encode(bytes);
    let mut polarity = vec![1.; word.len()];
    coded::derandomize(&mut polarity, &c.randomizer);
    bits.extend((0..word.len()).map(|wire| {
        let index = c.wire_to_code.get(wire).copied().unwrap_or(wire);
        word[index] ^ u8::from(polarity[index] < 0.)
    }));
    bits.iter().map(|b| 2. * f64::from(*b) - 1.).collect()
}
pub fn add(iq: &mut [Complex64], symbols: &[f64], start: usize, amplitude: f64, carrier: f64) {
    for (k, symbol) in symbols.iter().enumerate() {
        for j in 0..4 {
            let index = start + k * 4 + j;
            iq[index] += Complex64::from_polar(
                amplitude * symbol,
                std::f64::consts::TAU * carrier * index as f64 / 4000. + 0.37,
            );
        }
    }
}
pub struct Noise(pub u64);
impl Noise {
    pub fn uniform(&mut self) -> f64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        ((self.0 >> 11) as f64 + 0.5) / (1u64 << 53) as f64
    }
    pub fn normal(&mut self) -> f64 {
        (-2. * self.uniform().ln()).sqrt() * (std::f64::consts::TAU * self.uniform()).cos()
    }
    pub fn fill(&mut self, length: usize, scale: f64) -> Vec<Complex64> {
        (0..length)
            .map(|_| Complex64::new(self.normal() * scale, self.normal() * scale))
            .collect()
    }
}
