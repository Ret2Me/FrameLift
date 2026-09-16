//! Fixed-seed engineering benchmark; exact transmitter bytes are the oracle.
//! The supplied true channel is deliberately not an estimated-channel result.
use clap::Parser;
use serde_json::{Value, json};
use std::{path::PathBuf, time::Instant};
use telemetry_yield_rs::{coded, fec::LdpcConfig, input, protocol, soft_sequence, turbo};

#[derive(Parser)]
struct Args {
    #[arg(long)]
    output: PathBuf,
}

struct Noise(u64);
impl Noise {
    fn word(&mut self) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }
    fn uniform(&mut self) -> f64 {
        ((self.word() >> 11) as f64 + 0.5) / (1_u64 << 53) as f64
    }
    fn gaussian(&mut self) -> f64 {
        (-2.0 * self.uniform().ln()).sqrt() * (std::f64::consts::TAU * self.uniform()).cos()
    }
}

// Published CCSDS231 table4-2 generator. Does not encode from decoder H.
fn encode(data: &[u8; 32]) -> Vec<u8> {
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
        if data[bit / 8] & (1 << (7 - bit % 8)) != 0 {
            for (block, output) in parity.iter_mut().enumerate() {
                *output ^= ROWS[bit / 64][block].rotate_right((bit % 64) as u32);
            }
        }
    }
    let bytes: Vec<u8> = data
        .iter()
        .copied()
        .chain(parity.iter().flat_map(|p| p.to_be_bytes()))
        .collect();
    bytes
        .iter()
        .flat_map(|b| (0..8).rev().map(move |i| (b >> i) & 1))
        .collect()
}

fn request(frame: &[u8; 32], variance: f64, noise: &mut Noise) -> Result<turbo::Request, String> {
    let bits = encode(frame);
    let mut code = LdpcConfig::ccsds_tc512();
    code.max_iterations = 12;
    if !code.syndrome_is_zero(&bits)? {
        return Err("independent generator/H mismatch".into());
    }
    let permutation: Vec<_> = (0..512).map(|i| (i * 73 + 19) % 512).collect();
    let levels: Vec<_> = permutation
        .iter()
        .map(|&i| 2.0 * f64::from(bits[i]) - 1.0)
        .collect();
    let channel = soft_sequence::Channel {
        taps: [0.6, 1.0, 0.5],
        bias: 0.0,
        noise_variance: variance,
    };
    let samples = (0..512)
        .map(|i| {
            let mut mean = levels[i];
            if i > 0 {
                mean += 0.6 * levels[i - 1];
            }
            if i + 1 < 512 {
                mean += 0.5 * levels[i + 1];
            }
            mean + variance.sqrt() * noise.gaussian()
        })
        .collect();
    Ok(turbo::Request {
        samples,
        channel,
        code,
        wire_to_code: permutation,
        randomizer: coded::Randomizer::None,
        validator: coded::FrameValidator::CcsdsTm {
            config: protocol::TmTransferFrameConfig {
                frame_length_bytes: 32,
                fecf_present: true,
            },
        },
        iterations: 4,
        damping: 0.7,
    })
}

fn run(args: &Args) -> Result<Value, String> {
    let out = input::existing_new_dir(&args.output)?;
    let seed = 0x20260916bc001_u64;
    let variances = [0.16, 0.36, 0.64, 1.0];
    let arms = [
        ("single-12", 1, 12),
        ("single-48", 1, 48),
        ("turbo-4x12", 4, 12),
    ];
    let plan = json!({"schema":"soft-iterative-synthetic-plan-v1","seed":seed,"cases_per_variance":64,
        "noise_variances":variances,"channel_taps":[0.6,1.0,0.5],"arms":arms,"damping":0.7,
        "negative_cases_each":32,"channel_parameters":"known oracle, not estimated",
        "executable":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,
        "field_evidence":false,"publication_ready":false});
    input::write_json_new(&out.join("plan.json"), &plan)?;
    let mut rows = Vec::new();
    let mut noise = Noise(seed);
    for (group, variance) in variances.into_iter().enumerate() {
        for case in 0..64 {
            let mut frame = [0u8; 32];
            for byte in &mut frame {
                *byte = noise.word() as u8;
            }
            frame[..6].copy_from_slice(&[0, 16, case, case, 0x18, 0]);
            let crc = protocol::compute_tm_fecf(&frame[..30]);
            frame[30..].copy_from_slice(&crc.to_be_bytes());
            let expected = hex::encode(frame);
            let mut r = request(&frame, variance, &mut noise)?;
            let mut results = serde_json::Map::new();
            // Rotate execution order; shared-host times are still diagnostic.
            for offset in 0..3 {
                let (name, rounds, inner) = arms[(case as usize + offset) % 3];
                r.iterations = rounds;
                r.code.max_iterations = inner;
                let start = Instant::now();
                let report = turbo::decode(&r)?;
                let exact = report.frame_hex.as_ref() == Some(&expected);
                results.insert(name.into(), json!({"exact":exact,"accepted":report.accepted,
                    "false_accept":report.accepted && !exact,"wall_seconds":start.elapsed().as_secs_f64(),
                    "report":report}));
            }
            rows.push(json!({"group":group,"case":case,"noise_variance":variance,"expected_hex":expected,"arms":results}));
        }
    }
    let mut controls = Vec::new();
    for case in 0..32 {
        let mut frame = [0u8; 32];
        frame[..6].copy_from_slice(&[0, 16, case, case, 0x18, 0]);
        for b in &mut frame[6..30] {
            *b = noise.word() as u8;
        }
        let wrong_crc = protocol::compute_tm_fecf(&frame[..30]) ^ 1;
        frame[30..].copy_from_slice(&wrong_crc.to_be_bytes());
        let mut r = request(&frame, 0.16, &mut noise)?;
        let invalid_crc = turbo::decode(&r)?;
        for sample in &mut r.samples {
            *sample = noise.gaussian();
        }
        r.channel.noise_variance = 1.0;
        let noise_only = turbo::decode(&r)?;
        controls.push(json!({"case":case,"wrong_crc":invalid_crc,"noise_only":noise_only}));
    }
    let mut summary = Vec::new();
    for variance in variances {
        let mut counts = serde_json::Map::new();
        for (name, _, _) in arms {
            let selected: Vec<_> = rows
                .iter()
                .filter(|r| r["noise_variance"] == variance)
                .collect();
            counts.insert(name.into(), json!({"exact":selected.iter().filter(|r|r["arms"][name]["exact"]==true).count(),
                "false_accept":selected.iter().filter(|r|r["arms"][name]["false_accept"]==true).count(),"total":selected.len()}));
        }
        let selected: Vec<_> = rows
            .iter()
            .filter(|r| r["noise_variance"] == variance)
            .collect();
        summary.push(json!({"noise_variance":variance,"counts":counts,
            "turbo_added_vs_single48":selected.iter().filter(|r| r["arms"]["turbo-4x12"]["exact"]==true && r["arms"]["single-48"]["exact"]==false).count(),
            "turbo_lost_vs_single48":selected.iter().filter(|r| r["arms"]["turbo-4x12"]["exact"]==false && r["arms"]["single-48"]["exact"]==true).count()}));
    }
    let negative_accepted = controls
        .iter()
        .map(|r| {
            usize::from(r["wrong_crc"]["accepted"] == true)
                + usize::from(r["noise_only"]["accepted"] == true)
        })
        .sum::<usize>();
    let report = json!({"schema":"soft-iterative-synthetic-result-v1","plan":plan,"summary":summary,
        "negative_accepted":negative_accepted,"negative_cases":64,"rows":rows,"controls":controls});
    input::write_json_new(&out.join("report.json"), &report)?;
    Ok(
        json!({"summary":summary,"negative_accepted":negative_accepted,"negative_cases":64,"positive_cases":256}),
    )
}

fn main() {
    match run(&Args::parse()) {
        Ok(value) => println!("{value}"),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}
