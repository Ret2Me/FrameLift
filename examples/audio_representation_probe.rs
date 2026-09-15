//! Audit PCM16 preparation against a decoded float32 source. No decoding or
//! reference packet bytes are used. Overshoot is measured, not blamed for loss.
use clap::Parser;
use serde_json::{Value, json};
use std::path::{Path, PathBuf};
use telemetry_yield_rs::input;

#[derive(Parser)]
struct Args {
    #[arg(long)]
    float_wav: PathBuf,
    #[arg(long)]
    pcm16_wav: Option<PathBuf>,
    /// Explicit multiplier used to prepare the PCM16 waveform.
    #[arg(long, default_value_t = 1.0)]
    gain: f64,
    #[arg(long)]
    output: PathBuf,
}

fn measure(values: &[f32], pcm: Option<&[i16]>, gain: f64) -> Result<Value, String> {
    if values.is_empty() || !gain.is_finite() || gain <= 0.0 {
        return Err("nonempty finite waveform and positive finite gain required".into());
    }
    if pcm.is_some_and(|v| v.len() != values.len()) {
        return Err("sample counts differ; comparison must not truncate".into());
    }
    let mut minimum = f64::INFINITY;
    let mut maximum = f64::NEG_INFINITY;
    let mut power = 0.0;
    let mut overshoot = 0usize;
    let mut transformed_overshoot = 0usize;
    let mut error_power = 0.0;
    let mut max_error = 0.0f64;
    let mut original_max_error = 0.0f64;
    let mut large_error = 0usize;
    for (i, &sample) in values.iter().enumerate() {
        let x = f64::from(sample);
        let scaled = x * gain;
        if !x.is_finite() || !scaled.is_finite() {
            return Err("nonfinite source or scaled sample".into());
        }
        minimum = minimum.min(x);
        maximum = maximum.max(x);
        power += x * x;
        overshoot += usize::from(!(-1.0..=32767.0 / 32768.0).contains(&x));
        transformed_overshoot += usize::from(!(-1.0..=32767.0 / 32768.0).contains(&scaled));
        if let Some(pcm) = pcm {
            let error = f64::from(pcm[i]) / 32768.0 - scaled;
            error_power += error * error;
            max_error = max_error.max(error.abs());
            original_max_error = original_max_error.max((f64::from(pcm[i]) / 32768.0 - x).abs());
            // One LSB is a conservative bound, independent of rounding ties.
            large_error += usize::from(error.abs() > 1.0 / 32768.0 + 1e-12);
        }
    }
    if !power.is_finite() || !error_power.is_finite() {
        return Err("accumulator overflow".into());
    }
    Ok(json!({
        "samples":values.len(),"minimum":minimum,"maximum":maximum,
        "rms":(power / values.len() as f64).sqrt(),
        "source_outside_pcm16_range":overshoot,
        "source_outside_pcm16_fraction":overshoot as f64 / values.len() as f64,
        "declared_gain":gain,"scaled_outside_pcm16_range":transformed_overshoot,
        "pcm16_comparison":pcm.map(|_|json!({
            "max_absolute_error":max_error,
            "rms_error":(error_power / values.len() as f64).sqrt(),
            "samples_error_above_one_lsb":large_error,
            "within_one_lsb_of_declared_transform":large_error==0,
            "nonclipping_quantized_transform":transformed_overshoot==0 && large_error==0,
            "bit_exact_declared_transform_preservation":max_error==0.0,
            "bit_exact_original_float_sample_preservation":original_max_error==0.0
        })),
        "causes_frame_loss":null,
        "interpretation":"Input representation audit only; quantization is not lossless and overshoot alone does not establish a decoding failure cause."
    }))
}

fn read_float(path: &Path) -> Result<(u32, Vec<f32>), String> {
    let mut reader = hound::WavReader::open(path).map_err(|e| e.to_string())?;
    let spec = reader.spec();
    if spec.channels != 1
        || spec.sample_rate == 0
        || spec.bits_per_sample != 32
        || spec.sample_format != hound::SampleFormat::Float
    {
        return Err("reference must be mono float32 WAV".into());
    }
    if reader.len() as u64 > input::MAX_LOADED_AUDIO_SAMPLES {
        return Err("reference sample bound exceeded".into());
    }
    let samples = reader
        .samples::<f32>()
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    Ok((spec.sample_rate, samples))
}

fn run(args: Args) -> Result<Value, String> {
    let source = input::identity(&args.float_wav)?;
    let (rate, samples) = read_float(&args.float_wav)?;
    let (pcm_identity, pcm) = if let Some(path) = &args.pcm16_wav {
        let identity = input::identity(path)?;
        let mut reader = hound::WavReader::open(path).map_err(|e| e.to_string())?;
        let spec = reader.spec();
        if spec.channels != 1
            || spec.sample_rate != rate
            || spec.bits_per_sample != 16
            || spec.sample_format != hound::SampleFormat::Int
            || reader.len() as usize != samples.len()
        {
            return Err("PCM16 layout/rate/length differs from reference".into());
        }
        let data = reader
            .samples::<i16>()
            .collect::<Result<Vec<_>, _>>()
            .map_err(|e| e.to_string())?;
        if input::identity(path)?.sha256 != identity.sha256 {
            return Err("PCM changed during reading".into());
        }
        (Some(identity), Some(data))
    } else {
        (None, None)
    };
    let stats = measure(&samples, pcm.as_deref(), args.gain)?;
    if input::identity(&args.float_wav)?.sha256 != source.sha256 {
        return Err("reference changed during reading".into());
    }
    let result = json!({"schema":"audio-representation-audit-v1","source":source,"pcm16":pcm_identity,"sample_rate_hz":rate,"statistics":stats});
    input::write_json_new(&args.output, &result)?;
    Ok(result)
}
fn main() {
    match run(Args::parse()) {
        Ok(result) => println!("{}", result),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn measures_clipping_not_just_container_equality() {
        let r = measure(&[-1.5, 0.0, 1.5], Some(&[-32768, 0, 32767]), 1.0).unwrap();
        assert_eq!(r["source_outside_pcm16_range"], 2);
        assert_eq!(r["pcm16_comparison"]["samples_error_above_one_lsb"], 2);
        assert_eq!(
            r["pcm16_comparison"]["nonclipping_quantized_transform"],
            false
        );
    }
    #[test]
    fn explicit_gain_can_avoid_saturation() {
        let r = measure(&[-1.5, 0.0, 1.5], Some(&[-24576, 0, 24576]), 0.5).unwrap();
        assert_eq!(r["scaled_outside_pcm16_range"], 0);
        assert_eq!(
            r["pcm16_comparison"]["nonclipping_quantized_transform"],
            true
        );
        assert_eq!(
            r["pcm16_comparison"]["bit_exact_declared_transform_preservation"],
            true
        );
        assert_eq!(
            r["pcm16_comparison"]["bit_exact_original_float_sample_preservation"],
            false
        );
    }
    #[test]
    fn never_truncates_misaligned_inputs() {
        assert!(measure(&[0.0, 1.0], Some(&[0]), 1.0).is_err());
    }
    #[test]
    fn rejects_nonfinite_and_empty_inputs() {
        for values in [vec![], vec![f32::NAN], vec![f32::INFINITY]] {
            assert!(measure(&values, None, 1.0).is_err());
        }
        for gain in [0.0, -1.0, f64::INFINITY, f64::NAN] {
            assert!(measure(&[0.0], None, gain).is_err());
        }
    }
    #[test]
    fn quantization_is_not_bit_exact() {
        let r = measure(&[0.1], Some(&[3277]), 1.0).unwrap();
        assert_eq!(
            r["pcm16_comparison"]["nonclipping_quantized_transform"],
            true
        );
        assert_eq!(
            r["pcm16_comparison"]["bit_exact_original_float_sample_preservation"],
            false
        );
    }
}
