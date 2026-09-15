//! Postprocess a preserved report-size failure; never execute a decoder.
#[allow(dead_code)]
#[path = "innovation_benchmark_run.rs"]
mod benchmark;

use clap::Parser;
use std::path::PathBuf;
use telemetry_yield_rs::input;

#[derive(Parser)]
struct Args {
    #[arg(long)]
    observation_result: PathBuf,
    #[arg(long)]
    output: PathBuf,
}

fn main() {
    let args = Args::parse();
    let result =
        benchmark::recover_large_native_report(&args.observation_result).and_then(|result| {
            input::write_json_new(&args.output, &result)?;
            Ok(result)
        });
    match result {
        Ok(result) => println!(
            "{}",
            serde_json::json!({
                "status":"complete", "observation_id":result["observation_id"],
                "recovered_report_frames":result["scored"]["strict_ui_unique_count"],
                "decoder_rerun":false, "output":args.output
            })
        ),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}
