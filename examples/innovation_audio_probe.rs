//! Explicit experimental entrypoint. No changes to the qualified CLI defaults.
use clap::Parser;
use std::path::PathBuf;
use telemetry_yield_rs::{adaptive::AdaptiveConfig, innovation_audio};
#[derive(Parser)]
struct Args {
    #[arg(long)]
    input: PathBuf,
    #[arg(long)]
    output: PathBuf,
    #[arg(long, default_value_t = 9600.0)]
    baud: f64,
    #[arg(long, default_value_t = 2)]
    threads: usize,
    /// Use original Vorbis packet features; reject other codecs/geometry.
    #[arg(long)]
    codec_sideinfo: bool,
    /// Pool only disjoint CRC-source calibration; preserve every existing lane.
    #[arg(long, requires = "codec_sideinfo")]
    pooled_codec: bool,
    /// Original Vorbis source for an exact canonical PCM16 WAV benchmark input.
    #[arg(long, requires = "codec_sideinfo")]
    codec_source: Option<PathBuf>,
}
fn main() {
    let args = Args::parse();
    match innovation_audio::decode_file_with_options(
        &args.input,
        &args.output,
        &AdaptiveConfig {
            baud: args.baud,
            threads: args.threads,
        },
        args.codec_sideinfo,
        args.pooled_codec,
        args.codec_source.as_deref(),
    ) {
        Ok(result) => println!("{result}"),
        Err(reason) => {
            eprintln!("{reason}");
            std::process::exit(1);
        }
    }
}
