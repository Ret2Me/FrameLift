//! Explicit diagnostic commands, separate from normal receiver dispatch.

use serde_json::{Value, json};
use sha2::Digest;
use std::path::Path;
use std::time::Instant;
use telemetry_yield_rs::{input, physical, receiver};

pub(super) fn bounded_score_work(
    candidate_lengths: &[usize],
    truth: &[u8],
    policy: &physical::AlignmentPolicy,
    qpsk: bool,
) -> Result<(), String> {
    if truth.is_empty()
        || truth.len() > 4_194_304
        || truth.iter().any(|b| *b > 1)
        || candidate_lengths.is_empty()
        || candidate_lengths.len() > 64
        || candidate_lengths.iter().any(|n| *n > 4_194_304)
    {
        return Err(
            "scorer requires binary truth and bounded1..64 variants of at most4194304 bits".into(),
        );
    }
    let mut work = 0u128;
    for &n in candidate_lengths {
        let longest = n.max(truth.len()) as u128;
        let shift = (policy.max_shift_bits as u128).min(longest + 2);
        let transformations = if qpsk {
            if policy.allow_iq_reflection { 8 } else { 4 }
        } else if policy.allow_global_polarity_inversion {
            2
        } else {
            1
        };
        work += (2 * shift + 1) * longest * transformations;
    }
    if work > 1_000_000_000 {
        return Err("scoring work exceeds1e9 bit-comparison upper estimate; use explicit smaller segments/policy".into());
    }
    Ok(())
}
pub(super) fn null_smoke(
    output: &Path,
    count: usize,
    config: &receiver::DecodeConfig,
) -> Result<Value, String> {
    if !(1..=1000).contains(&count) {
        return Err("per-kind count must be in 1..=1000".into());
    }
    let out = input::existing_new_dir(output)?;
    let samples = (48000.0 * config.window_seconds) as usize;
    if !(8192..=48000 * 30).contains(&samples) {
        return Err("null windows must be between 8192 samples and 30 seconds".into());
    }
    let mut state = 20260908u64;
    let mut uniform = || {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        ((state >> 11) as f64 + 0.5) / 9007199254740992.0
    };
    let mut rows = Vec::new();
    let started = Instant::now();
    for kind in ["silence", "gaussian_box_muller_xorshift64", "bursty_tones"] {
        for index in 0..count {
            let mut pcm = Vec::with_capacity(samples);
            for n in 0..samples {
                let value = match kind {
                    "silence" => 0.0,
                    "gaussian_box_muller_xorshift64" => {
                        (-2.0 * uniform().ln()).sqrt() * (std::f64::consts::TAU * uniform()).cos()
                    }
                    _ => {
                        let t = n as f64 / 48000.0;
                        let gate = if (n / 4000 + index) % 3 == 0 {
                            1.0
                        } else {
                            0.05
                        };
                        gate * ((std::f64::consts::TAU * (731.0 + index as f64 * 71.0) * t).sin()
                            + 0.7 * (std::f64::consts::TAU * 2381.0 * t).cos())
                    }
                };
                pcm.push(value);
            }
            let pcm_sha = hex::encode(sha2::Sha256::digest(
                pcm.iter().flat_map(|x| x.to_le_bytes()).collect::<Vec<_>>(),
            ));
            let (frames, windows, _) = receiver::decode_samples(&pcm, 48000, config)?;
            rows.push(json!({"kind":kind,"index":index,"samples":samples,"pcm_f64le_sha256":pcm_sha,"frames":frames.len(),
                "failed_windows":windows.iter().filter(|w|!w.failures.is_empty()).count()}));
        }
    }
    let result = json!({"schema":"rust-null-smoke-v1","seed":20260908,"config":config,"cases":rows,
        "pass":rows.iter().all(|r|r["frames"]==0&&r["failed_windows"]==0),
        "elapsed_seconds":started.elapsed().as_secs_f64(),"far_qualified":false,
        "note":"Synthetic smoke only; repeated silence is not independent noise exposure"});
    input::write_json_new(&out.join("result.json"), &result)?;
    Ok(result)
}

pub(super) fn capabilities() -> Value {
    json!({"runtime":"Rust","python_runtime_required":false,
            "audio_inputs":["mono WAV PCM integer / float32","mono OGG via external FFmpeg"],
            "iq_inputs":["ci16_le","cf32_le","cf64_le","validated raw-IQ manifests and SigMF scalar/byte-order layouts"],
            "audio_modes":["FSK/GFSK/GMSK demodulated PCM","Bell202 AFSK demodulated PCM"],
            "iq_frontends":["phase-first FSK","channel-conditioned FSK","Bell202 AFSK","explicit legacy packaged AFSK bank","coherent BPSK/QPSK/OQPSK soft bits to validated frames (explicit generic plans)"],
            "physical_unaligned_bits":["BPSK","FSK/GMSK","QPSK","OQPSK legacy and v2"],
            "protocol_library":["AX.25 plain and G3RUH with original CRC","CCSDS Space Packet with explicit integrity","CCSDS TM with explicit FECF/config","fixed sync with explicit validator","CSP v1/v2 explicit CRC32C scope; no extension/security processing","CCSDS AOS Issue-5 configured layout and FECF; no FHEC/SDLS","CCSDS USLP non-truncated frame/TFDF and FECF; no SDLS or cross-frame reassembly"],
            "native_fec":["GF256 Reed-Solomon, shortening/interleaving/CCSDS dual basis; RS255/223 preset","soft normalized-min-sum LDPC with explicit sparse H/output map; CCSDS TC128 and TC512 presets"],
            "coded_sync":"explicit sync -> none/TM255/TM131071/TC255 derandomizer -> optional RS/LDPC -> mandatory CRC/FECF; no automatic mission profile inference",
            "psk_qualification":"synthetic IQ and coding vectors; not yet a real-satellite PSK yield or baseline-parity claim",
            "candidate_only_lanes":["list/syndrome soft repair","GNU-compatible HDLC PDUs","external baseline KISS"],
            "other_interfaces":["clipping-robust CI16","bandlimited reconstruction","signal/waveform/burst diagnostics","CW/Morse untrusted candidates","event-local candidate ledger","SatYAML registry"],
            "external_dependencies":{"ogg_codec":["ffmpeg","ffprobe"],"optional_comparison":"separately installed gr-satellites"},
            "parallelism":"deterministic existing windows, bounded Rayon pool","all_legacy_capabilities_migrated":false,
            "scope":"native receiver library/CLI; archived research scripts and separate planning project are retained, not rewritten",
            "unsupported_claims":["all modulations/protocols","all CCSDS FEC profiles, puncturing or convolutional concatenation","complete AOS/USLP mission services or security","universal zero accuracy loss","CRC repair as independent validation"],
            "publication_ready":false,"deployment_ready":false})
}
