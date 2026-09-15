//! Frozen, bounded development controls. Oracle timing is explicit; only native
//! received-CRC anchors train models. Target truth is used after decisions only.
use clap::Parser;
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    path::{Path, PathBuf},
    process::Command,
    time::Instant,
};
use telemetry_yield_rs::{anchors, innovation, input, protocol, sequence};

const RATE: u32 = 48_000;
const SPS: usize = 5;
const SECONDS: usize = 18;
const MASTER: u64 = 0x2026_0910_c0de_cafe;
const GOLDEN: u64 = 0x9e37_79b9_7f4a_7c15;
const TAPS: [f64; 3] = [0.06, 0.65, 0.06];
const FLAG: [u8; 8] = [0, 1, 1, 1, 1, 1, 1, 0];
const STARTS: [usize; 4] = [12_000, 156_000, 492_000, 684_000];

#[derive(Parser)]
struct Args {
    #[arg(long)]
    output: PathBuf,
}

struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }
    fn uniform(&mut self) -> f64 {
        ((self.next() >> 12) as f64 + 0.5) / 4_503_599_627_370_496.0
    }
    fn gaussian(&mut self) -> f64 {
        (-2.0 * self.uniform().ln()).sqrt() * (std::f64::consts::TAU * self.uniform()).cos()
    }
}

fn residue(bytes: &[u8]) -> u16 {
    let mut crc = 0xffff_u16;
    for byte in bytes {
        for bit in 0..8 {
            let feedback = (crc ^ u16::from(byte >> bit)) & 1;
            crc >>= 1;
            if feedback != 0 {
                crc ^= 0x8408;
            }
        }
    }
    crc
}

fn frame(seed: u64, id: usize) -> Vec<u8> {
    let mut rng = Rng(seed);
    let mut data = hex::decode("94a662b2a0826094a662b29eb2e103f0").unwrap();
    data.extend_from_slice(b"INNOVATION-CONTROLS-V1");
    data.extend_from_slice(&(id as u64).to_le_bytes());
    data.extend((0..256).map(|_| rng.next() as u8));
    let fcs = !residue(&data);
    data.extend_from_slice(&fcs.to_le_bytes());
    assert_eq!(residue(&data), 0xf0b8);
    data
}

fn encode(frame: &[u8]) -> Vec<f64> {
    let mut bits = FLAG.repeat(900);
    let mut ones = 0;
    for byte in frame {
        for bit in 0..8 {
            let value = (byte >> bit) & 1;
            bits.push(value);
            ones = if value == 1 { ones + 1 } else { 0 };
            if ones == 5 {
                bits.push(0);
                ones = 0;
            }
        }
    }
    bits.extend(FLAG.repeat(900));
    let mut scrambled = Vec::with_capacity(bits.len());
    for (i, mut bit) in bits.into_iter().enumerate() {
        if i >= 12 {
            bit ^= scrambled[i - 12];
        }
        if i >= 17 {
            bit ^= scrambled[i - 17];
        }
        scrambled.push(bit);
    }
    let mut level = 0;
    scrambled
        .into_iter()
        .map(|bit| {
            if bit == 0 {
                level ^= 1;
            }
            2.0 * f64::from(level) - 1.0
        })
        .collect()
}

#[derive(Clone, Copy, Debug)]
enum Kind {
    Clean,
    Colored,
    Clipping,
    Impulsive,
}
impl Kind {
    fn name(self) -> &'static str {
        match self {
            Self::Clean => "clean",
            Self::Colored => "colored",
            Self::Clipping => "clipping",
            Self::Impulsive => "impulsive",
        }
    }
}

fn render(
    pcm: &mut [f64],
    start: usize,
    levels: Option<&[f64]>,
    count: usize,
    kind: Kind,
    anchor: bool,
    rng: &mut Rng,
) {
    let sigma = if anchor || matches!(kind, Kind::Clean) {
        0.025
    } else {
        0.24
    };
    let mut correlated = 0.0;
    for i in 0..count {
        let noise = if matches!(kind, Kind::Colored) {
            correlated = 0.90 * correlated + (1.0_f64 - 0.90_f64.powi(2)).sqrt() * rng.gaussian();
            correlated
        } else {
            rng.gaussian()
        };
        let signal = levels.map_or(0.0, |x| {
            TAPS[0] * x[i.saturating_sub(1)]
                + TAPS[1] * x[i]
                + TAPS[2] * x[(i + 1).min(x.len() - 1)]
        });
        let mut value = signal + sigma * noise;
        if matches!(kind, Kind::Impulsive) && !anchor && rng.next() % 512 == 0 {
            value += if rng.next() & 1 == 0 { -0.90 } else { 0.90 };
        }
        if matches!(kind, Kind::Clipping) {
            value = value.clamp(-0.30, 0.30);
        }
        for sample in &mut pcm[start + i * SPS..start + (i + 1) * SPS] {
            *sample += value;
        }
    }
}

fn write_wav(path: &Path, pcm: &mut [f64]) -> Result<(), String> {
    let spec = hound::WavSpec {
        channels: 1,
        sample_rate: RATE,
        bits_per_sample: 32,
        sample_format: hound::SampleFormat::Float,
    };
    let file = std::fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    let mut writer =
        hound::WavWriter::new(std::io::BufWriter::new(file), spec).map_err(|e| e.to_string())?;
    for x in pcm {
        *x = f64::from(*x as f32);
        writer.write_sample(*x as f32).map_err(|e| e.to_string())?;
    }
    writer.finalize().map_err(|e| e.to_string())
}

fn read_wav(path: &Path) -> Result<Vec<f64>, String> {
    let mut reader = hound::WavReader::open(path).map_err(|e| e.to_string())?;
    let spec = reader.spec();
    if spec.channels != 1
        || spec.sample_rate != RATE
        || spec.sample_format != hound::SampleFormat::Float
        || spec.bits_per_sample != 32
    {
        return Err("paired WAV format changed".into());
    }
    reader
        .samples::<f32>()
        .map(|s| s.map(f64::from).map_err(|e| e.to_string()))
        .collect()
}

fn command(args: &[String]) -> Result<(), String> {
    let result = Command::new("/usr/bin/ffmpeg")
        .args(args)
        .output()
        .map_err(|e| e.to_string())?;
    if !result.status.success() {
        return Err(format!(
            "ffmpeg {}: {}",
            result.status,
            String::from_utf8_lossy(&result.stderr)
        ));
    }
    Ok(())
}

fn symbol_samples(pcm: &[f64], start: usize, count: usize) -> Result<Vec<f64>, String> {
    if start + count * SPS > pcm.len() {
        return Err("decoded WAV too short for fixed timing".into());
    }
    Ok((0..count).map(|i| pcm[start + i * SPS + 2]).collect())
}

fn frame_set(samples: &[f64]) -> Result<BTreeSet<String>, String> {
    let frames = protocol::decode_ax25(samples, 0.0, &[false, true])?;
    if frames.iter().any(|f| residue(f) != 0xf0b8) {
        return Err("independent received FCS check failed".into());
    }
    Ok(frames.iter().map(hex::encode).collect())
}

/// These parameters are samples only; no transmitted frame or levels are in scope.
fn learned_models(
    anchor_samples: &[Vec<f64>],
) -> Result<(sequence::ChannelModel, innovation::NoiseModel, Value), String> {
    let mut combined = Vec::new();
    let mut levels = Vec::new();
    let mut coords = Vec::new();
    let mut accepted = Vec::new();
    for soft in anchor_samples {
        let offset = combined.len();
        let spans = anchors::validated_spans(soft, 0.0)?;
        for span in spans {
            if residue(&span.frame) != 0xf0b8 {
                return Err("independent anchor FCS failed".into());
            }
            coords.push((offset + span.start, offset + span.end));
            accepted.push(hex::encode(&span.frame));
        }
        combined.extend_from_slice(soft);
        levels.extend(soft.iter().map(|x| u8::from(*x >= 0.0)));
    }
    if coords.is_empty() {
        return Err("no native received-CRC anchor".into());
    }
    // Signal-channel estimation and noise calibration use disjoint parts of
    // every CRC packet, separated by 64 symbols; target truth remains absent.
    let channel_coords: Vec<_> = coords
        .iter()
        .map(|&(left, right)| (left, left + (right - left) * 2 / 5))
        .collect();
    let residual_coords: Vec<_> = coords
        .iter()
        .map(|&(left, right)| {
            (
                left + (right - left) * 2 / 5 + 64,
                right - sequence::TRAINING_GUARD_SYMBOLS,
            )
        })
        .collect();
    let channel = sequence::fit_channel(&combined, &levels, &channel_coords)?;
    let residuals: Vec<Vec<f64>> = residual_coords
        .iter()
        .map(|&(left, right)| {
            (left..right)
                .map(|i| {
                    let b = |j| 2.0 * f64::from(levels[j]) - 1.0;
                    combined[i]
                        - channel.bias
                        - channel.taps[0] * b(i - 1)
                        - channel.taps[1] * b(i)
                        - channel.taps[2] * b(i + 1)
                })
                .collect()
        })
        .collect();
    let noise = innovation::fit_noise_model(&residuals)?;
    Ok((
        channel,
        noise,
        json!({"accepted_received_frame_with_fcs_hex":accepted,"spans":coords,"channel_fit_spans":channel_coords,"noise_fit_spans":residual_coords,"channel_noise_guard_symbols":64,"residual_span_lengths":residuals.iter().map(Vec::len).collect::<Vec<_>>() }),
    ))
}

fn score_symbols(pcm: &[f64], truth: &[Vec<u8>], levels: &[Vec<f64>]) -> Result<Value, String> {
    let anchors: Vec<_> = (0..2)
        .map(|i| symbol_samples(pcm, STARTS[i], levels[i].len()))
        .collect::<Result<_, _>>()?;
    let target = symbol_samples(pcm, STARTS[2], levels[2].len())?;
    let null = symbol_samples(pcm, STARTS[3], levels[2].len())?;
    let raw_frames = frame_set(&target)?;
    let started = Instant::now();
    let (channel, noise, provenance) = match learned_models(&anchors) {
        Ok(models) => models,
        Err(error) => {
            return Ok(
                json!({"status":"no-usable-model","reason":error,"raw_target_frames":raw_frames,"oracle_timing":true}),
            );
        }
    };
    let white_started = Instant::now();
    let white = sequence::detect_sequence(&target, &channel, 1.0)?;
    let white_null = sequence::detect_sequence(&null, &channel, 1.0)?;
    let white_seconds = white_started.elapsed().as_secs_f64();
    let innovation_started = Instant::now();
    let aware = innovation::innovations_sequence(&target, &channel, &noise, 1.0, None)?;
    let aware_null = innovation::innovations_sequence(&null, &channel, &noise, 1.0, None)?;
    let innovation_seconds = innovation_started.elapsed().as_secs_f64();
    // Truth is consulted only after all fitting and target decisions are complete.
    let expected: BTreeSet<String> = [hex::encode(&truth[2])].into_iter().collect();
    let expected_anchors: BTreeSet<String> = truth[..2].iter().map(hex::encode).collect();
    let unexpected_anchors: Vec<_> = provenance["accepted_received_frame_with_fcs_hex"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(Value::as_str)
        .filter(|frame| !expected_anchors.contains(*frame))
        .collect();
    let white_frames = frame_set(&white)?;
    let aware_frames = frame_set(&aware)?;
    let white_null_frames = frame_set(&white_null)?;
    let aware_null_frames = frame_set(&aware_null)?;
    let errors = |got: &[f64]| {
        got.iter()
            .zip(&levels[2])
            .skip(8)
            .take(levels[2].len() - 16)
            .filter(|(a, b)| (**a >= 0.0) != (**b >= 0.0))
            .count()
    };
    Ok(json!({
        "status":"complete","oracle_timing":true,"truth_used_for_fit":false,
        "channel":channel,"noise":noise,"training_provenance":provenance,
        "raw_target_frames":raw_frames,"raw_unexpected_target_frames":raw_frames.difference(&expected).collect::<Vec<_>>(),"training_anchor_unexpected_frames":unexpected_anchors,"white_target_frames":white_frames,"innovation_target_frames":aware_frames,
        "white_correct_target_frames":white_frames.intersection(&expected).count(),
        "innovation_correct_target_frames":aware_frames.intersection(&expected).count(),
        "white_unexpected_target_frames":white_frames.difference(&expected).collect::<Vec<_>>(),
        "innovation_unexpected_target_frames":aware_frames.difference(&expected).collect::<Vec<_>>(),
        "white_null_frames":white_null_frames,"innovation_null_frames":aware_null_frames,
        "raw_symbol_errors":errors(&target),"white_symbol_errors":errors(&white),"innovation_symbol_errors":errors(&aware),
        "scored_symbols_excluding_8_each_edge":levels[2].len()-16,
        "white_wall_seconds":white_seconds,"innovation_wall_seconds":innovation_seconds,"total_wall_seconds":started.elapsed().as_secs_f64()
    }))
}

fn corr(x: &[f64], y: &[f64]) -> Option<f64> {
    if x.len() != y.len() || x.len() < 3 {
        return None;
    }
    let n = x.len() as f64;
    let mx = x.iter().sum::<f64>() / n;
    let my = y.iter().sum::<f64>() / n;
    let xx = x.iter().map(|x| (x - mx).powi(2)).sum::<f64>();
    let yy = y.iter().map(|y| (y - my).powi(2)).sum::<f64>();
    if xx <= 1e-30 || yy <= 1e-30 {
        return None;
    }
    Some(
        x.iter()
            .zip(y)
            .map(|(x, y)| (x - mx) * (y - my))
            .sum::<f64>()
            / (xx * yy).sqrt(),
    )
}

fn codec_diagnostic(
    ogg: &Path,
    source: &[f64],
    decoded: &[f64],
    symbol_counts: &[usize],
) -> Result<Value, String> {
    let output = Command::new("/usr/bin/ffprobe")
        .args([
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_frames",
            "-show_entries",
            "frame=nb_samples,pkt_size",
            "-of",
            "json",
        ])
        .arg(ogg)
        .output()
        .map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err("ffprobe codec diagnostic failed".into());
    }
    let meta: Value = serde_json::from_slice(&output.stdout).map_err(|e| e.to_string())?;
    let rows = meta["frames"].as_array().ok_or("ffprobe has no frames")?;
    let number = |v: &Value| {
        v.as_u64()
            .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
            .map(|v| v as usize)
            .ok_or_else(|| "invalid codec frame count".to_string())
    };
    let mut offset = 0;
    let mut density = Vec::new();
    let mut power = Vec::new();
    let mut blocks = Vec::new();
    let mut target_density = Vec::new();
    let mut target_power = Vec::new();
    let mut relative_power = Vec::new();
    for row in rows {
        let count = number(&row["nb_samples"])?;
        let bytes = number(&row["pkt_size"])?;
        if count == 0 || offset + count > decoded.len() || offset + count > source.len() {
            return Ok(
                json!({"status":"alignment-unavailable","source_samples":source.len(),"decoded_samples":decoded.len(),"offset":offset,"frame_count":count}),
            );
        }
        let mse = source[offset..offset + count]
            .iter()
            .zip(&decoded[offset..offset + count])
            .map(|(a, b)| (a - b).powi(2))
            .sum::<f64>()
            / count as f64;
        let bits_per_sample = 8.0 * bytes as f64 / count as f64;
        let source_mean = source[offset..offset + count].iter().sum::<f64>() / count as f64;
        let source_variance = source[offset..offset + count]
            .iter()
            .map(|x| (x - source_mean).powi(2))
            .sum::<f64>()
            / count as f64;
        let target_interior =
            offset >= STARTS[2] && offset + count <= STARTS[2] + symbol_counts[2] * SPS;
        blocks.push(json!({"start_sample":offset,"samples":count,"packet_bytes":bytes,"bits_per_sample":bits_per_sample,"paired_compression_residual_mse":mse,"source_variance":source_variance,"entirely_within_target":target_interior}));
        density.push(bits_per_sample.ln());
        power.push((mse + 1e-30).ln());
        relative_power.push((mse / (source_variance + 1e-20) + 1e-30).ln());
        if target_interior {
            target_density.push(bits_per_sample.ln());
            target_power.push((mse + 1e-30).ln());
        }
        offset += count;
    }
    Ok(
        json!({"status":if offset==decoded.len()&&decoded.len()==source.len(){"complete"}else{"length-mismatch"},"source_samples":source.len(),"decoded_samples":decoded.len(),"frame_samples_sum":offset,"log_density_log_residual_power_pearson":corr(&density,&power),"log_density_log_relative_error_power_pearson":corr(&density,&relative_power),"target_only_log_density_log_residual_power_pearson":corr(&target_density,&target_power),"target_blocks":target_density.len(),"blocks":blocks,"truth_free_metadata":true,"precodec_samples_used_only_for_distortion_scoring":true,"density_is_only_proxy":true,"all_block_correlation_is_confounded_by_signal_amplitude":true}),
    )
}

fn run(args: &Args) -> Result<Value, String> {
    let out = input::existing_new_dir(&args.output)?;
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let executable = input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    let protocol_identity = input::identity(&root.join("docs/innovation-controls-protocol.md"))?;
    if protocol_identity.sha256
        != "fe2ed27ffbb88f359b54e9b9cf4e205c691310ad0750ed445ce98cf648052dc3"
    {
        return Err("frozen protocol changed; create a new experiment version".into());
    }
    let ffmpeg_version = Command::new("/usr/bin/ffmpeg")
        .arg("-version")
        .output()
        .map_err(|e| e.to_string())?;
    input::write_json_new(
        &out.join("plan.json"),
        &json!({"schema":"innovation-controls-plan-v1","master_seed_hex":format!("{MASTER:016x}"),"case_count":32,"protocol":protocol_identity,"executable":executable,"source":input::identity(&root.join("examples/innovation_controls.rs"))?,"ffmpeg":input::identity(Path::new("/usr/bin/ffmpeg"))?,"ffmpeg_version":String::from_utf8_lossy(&ffmpeg_version.stdout),"oracle_timing_primary":true,"publication_ready":false,"maximum_codec_threads":1,"hypotheses_frozen_before_scoring":true}),
    )?;
    let mut results = Vec::new();
    let started = Instant::now();
    for (category, kind) in [Kind::Clean, Kind::Colored, Kind::Clipping, Kind::Impulsive]
        .into_iter()
        .enumerate()
    {
        for repetition in 0..8 {
            let index = category * 8 + repetition;
            let seed = MASTER ^ ((index as u64 + 1).wrapping_mul(GOLDEN));
            let name = format!("case-{index:02}-{}-{repetition}", kind.name());
            let case_out = input::existing_new_dir(&out.join(&name))?;
            let truth: Vec<_> = (0..3)
                .map(|i| {
                    frame(
                        seed ^ ((i as u64 + 11).wrapping_mul(0xd1b5_4a32_d192_ed03)),
                        index * 3 + i,
                    )
                })
                .collect();
            let levels: Vec<_> = truth.iter().map(|f| encode(f)).collect();
            let mut rng = Rng(seed ^ 0x4241_434b_4752_4f55);
            let mut pcm: Vec<_> = (0..SECONDS * RATE as usize)
                .map(|_| 0.001 * rng.gaussian())
                .collect();
            for i in 0..3 {
                render(
                    &mut pcm,
                    STARTS[i],
                    Some(&levels[i]),
                    levels[i].len(),
                    kind,
                    i < 2,
                    &mut Rng(seed ^ ((i as u64 + 31).wrapping_mul(GOLDEN))),
                );
            }
            render(
                &mut pcm,
                STARTS[3],
                None,
                levels[2].len(),
                kind,
                false,
                &mut Rng(seed ^ 0x4e55_4c4c_5441_5247),
            );
            let wav = case_out.join("input.wav");
            let ogg = case_out.join("input.ogg");
            let decoded_path = case_out.join("decoded.wav");
            write_wav(&wav, &mut pcm)?;
            let encode_args = vec![
                "-v".into(),
                "error".into(),
                "-nostdin".into(),
                "-n".into(),
                "-i".into(),
                wav.display().to_string(),
                "-map".into(),
                "0:a:0".into(),
                "-c:a".into(),
                "libvorbis".into(),
                "-q:a".into(),
                "3".into(),
                "-threads".into(),
                "1".into(),
                ogg.display().to_string(),
            ];
            command(&encode_args)?;
            let decode_args = vec![
                "-v".into(),
                "error".into(),
                "-nostdin".into(),
                "-n".into(),
                "-threads".into(),
                "1".into(),
                "-i".into(),
                ogg.display().to_string(),
                "-map".into(),
                "0:a:0".into(),
                "-c:a".into(),
                "pcm_f32le".into(),
                "-threads".into(),
                "1".into(),
                decoded_path.display().to_string(),
            ];
            command(&decode_args)?;
            let decoded = read_wav(&decoded_path)?;
            input::write_json_new(
                &case_out.join("input.json"),
                &json!({"case":name,"index":index,"kind":kind.name(),"repetition":repetition,"seed_hex":format!("{seed:016x}"),"wav":input::identity(&wav)?,"ogg":input::identity(&ogg)?,"decoded_wav":input::identity(&decoded_path)?,"encode_command":encode_args,"decode_command":decode_args,"truth_not_supplied_to_receiver":true}),
            )?;
            let source_score = score_symbols(&pcm, &truth, &levels)?;
            let decoded_score = score_symbols(&decoded, &truth, &levels)?;
            let diagnostic = codec_diagnostic(
                &ogg,
                &pcm,
                &decoded,
                &levels.iter().map(Vec::len).collect::<Vec<_>>(),
            )?;
            input::write_json_new(&case_out.join("codec-diagnostic.json"), &diagnostic)?;
            let score = json!({"case":name,"index":index,"kind":kind.name(),"expected_frame_with_fcs_hex":truth.iter().map(hex::encode).collect::<Vec<_>>(),"wav":source_score,"ogg":decoded_score,"codec_diagnostic_status":diagnostic["status"],"codec_density_correlation":diagnostic["log_density_log_residual_power_pearson"],"oracle_timing":true,"publication_ready":false});
            input::write_json_new(&case_out.join("score.json"), &score)?;
            results.push(score);
        }
    }
    let mut summary = serde_json::Map::new();
    for variant in ["wav", "ogg"] {
        for kind in [Kind::Clean, Kind::Colored, Kind::Clipping, Kind::Impulsive] {
            let cases: Vec<_> = results
                .iter()
                .filter(|r| r["kind"] == kind.name())
                .collect();
            let sum = |key: &str| {
                cases
                    .iter()
                    .map(|r| r[variant][key].as_u64().unwrap_or(0))
                    .sum::<u64>()
            };
            summary.insert(format!("{variant}-{}",kind.name()),json!({"cases":cases.len(),"fitted_cases":cases.iter().filter(|r|r[variant]["status"]=="complete").count(),"white_correct_target_frames":sum("white_correct_target_frames"),"innovation_correct_target_frames":sum("innovation_correct_target_frames"),"white_symbol_errors":sum("white_symbol_errors"),"innovation_symbol_errors":sum("innovation_symbol_errors"),"unexpected_target_or_null_frames":cases.iter().map(|r|["raw_unexpected_target_frames","training_anchor_unexpected_frames","white_unexpected_target_frames","innovation_unexpected_target_frames","white_null_frames","innovation_null_frames"].into_iter().map(|k|r[variant][k].as_array().map_or(0,Vec::len)).sum::<usize>()).sum::<usize>()}));
        }
    }
    let result = json!({"schema":"innovation-controls-result-v1","status":"complete","cases":results,"summary":summary,"wall_seconds":started.elapsed().as_secs_f64(),"oracle_timing":true,"synthetic_development_only":true,"publication_ready":false,"supplied_target_truth_to_fit":false});
    input::write_json_new(&out.join("result.json"), &result)?;
    Ok(result)
}

fn main() {
    match run(&Args::parse()) {
        Ok(result) => println!(
            "{}",
            json!({"status":result["status"],"summary":result["summary"],"wall_seconds":result["wall_seconds"],"oracle_timing":true})
        ),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn independent_fcs_known_check_value() {
        assert_eq!(!residue(b"123456789"), 0x906e);
    }
    #[test]
    fn all_frozen_payloads_distinct_and_roundtrip() {
        let mut unique = BTreeSet::new();
        for case in 0..32 {
            let seed = MASTER ^ ((case as u64 + 1).wrapping_mul(GOLDEN));
            for i in 0..3 {
                let f = frame(
                    seed ^ ((i as u64 + 11).wrapping_mul(0xd1b5_4a32_d192_ed03)),
                    case * 3 + i,
                );
                assert!(unique.insert(hex::encode(&f)));
                assert_eq!(
                    frame_set(&encode(&f)).unwrap(),
                    [hex::encode(&f)].into_iter().collect()
                );
            }
        }
        assert_eq!(unique.len(), 96);
    }
    #[test]
    fn symbol_coordinates_and_null_do_not_overlap() {
        let f = frame(MASTER, 0);
        let n = encode(&f).len() * SPS;
        for starts in STARTS.windows(2) {
            assert!(starts[0] + n < starts[1]);
        }
        assert!(STARTS[3] + n < SECONDS * RATE as usize);
    }
    #[test]
    fn known_affine_correlation() {
        assert!((corr(&[1., 2., 3.], &[6., 4., 2.]).unwrap() + 1.).abs() < 1e-12);
        assert!(corr(&[1.; 3], &[1., 2., 3.]).is_none());
    }

    #[test]
    fn model_fit_ignores_unvalidated_prefix_samples() {
        let packet = frame(MASTER, 0);
        let mut rng = Rng(MASTER ^ 1234);
        let mut soft: Vec<_> = encode(&packet)
            .into_iter()
            .map(|x| 0.65 * x + 0.01 * rng.gaussian())
            .collect();
        let (channel, noise, _) = learned_models(&[soft.clone()]).unwrap();
        for value in &mut soft[..100] {
            *value = 3.0 * rng.gaussian();
        }
        let (changed_channel, changed_noise, _) = learned_models(&[soft]).unwrap();
        assert_eq!(
            serde_json::to_value(channel).unwrap(),
            serde_json::to_value(changed_channel).unwrap()
        );
        assert_eq!(
            serde_json::to_value(noise).unwrap(),
            serde_json::to_value(changed_noise).unwrap()
        );
    }

    #[test]
    fn corrupt_anchor_is_not_repaired_or_trained() {
        let packet = frame(MASTER, 0);
        let mut soft = encode(&packet);
        soft[900 * 8 + 200] *= -1.0;
        assert!(anchors::validated_spans(&soft, 0.0).unwrap().is_empty());
        assert_eq!(
            learned_models(&[soft]).unwrap_err(),
            "no native received-CRC anchor"
        );
    }
}
