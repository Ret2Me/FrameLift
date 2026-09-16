//! Causal, source-bound channel reuse for variable-length HDLC IQ.
//!
//! Only baseline-validated windows populate memory. Supplemental frames never
//! become training truth. Blind fits are diagnostic hypotheses, not anchors.
use super::*;
use crate::progressive_channel;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub maximum_age_samples: u64,
    pub maximum_models: usize,
    pub blind_bootstrap: bool,
    /// Separate deterministic work allowance, including the second frontend.
    pub work_budget: u64,
}

impl Config {
    fn validate(&self) -> Result<(), String> {
        if self.maximum_age_samples == 0
            || !(1..=32).contains(&self.maximum_models)
            || !(1..=1_000_000_000_000).contains(&self.work_budget)
        {
            return Err("invalid HDLC memory age, capacity or work budget".into());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Receipt {
    pub target_stream: usize,
    pub kind: String,
    pub source_window: Option<(u64, u64)>,
    pub source_stream: Option<usize>,
    pub channels: Vec<soft_sequence::Channel>,
    pub excluded_symbol_spans: Vec<(usize, usize)>,
    pub blind_fits: Vec<progressive_channel::BlindFit>,
    pub accepted_frames: Vec<String>,
    pub reason: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct WindowReport {
    pub schema: String,
    pub start_sample: u64,
    pub end_sample: u64,
    /// Original receiver report, with coordinates relative to this window.
    pub local: super::Report,
    pub frames: Vec<Frame>,
    pub memory_added_frames: Vec<String>,
    pub receipts: Vec<Receipt>,
    pub expired_models: usize,
    pub evicted_models: usize,
    pub retained_models: usize,
    pub memory_consumed_work: u64,
}

#[derive(Clone)]
struct Model {
    window: (u64, u64),
    stream: usize,
    lane: String,
    channels: Vec<soft_sequence::Channel>,
}

/// Not deserializable: callers cannot inject arbitrary models as validated truth.
pub struct Session {
    source: String,
    rate: u32,
    receiver: super::Config,
    options: Config,
    models: Vec<Model>,
    previous_start: Option<u64>,
}

fn charge(work: &mut u64, amount: u64, limit: u64) -> Result<(), String> {
    *work = work
        .checked_add(amount)
        .ok_or("HDLC memory work overflow")?;
    if *work > limit {
        return Err("HDLC memory work budget exceeded; window not committed".into());
    }
    Ok(())
}

fn detect(stream: &Stream, channels: &[soft_sequence::Channel]) -> Result<Vec<f64>, String> {
    let arms = channels.len();
    let mut decoded = vec![0.; stream.soft.len()];
    for (arm, channel) in channels.iter().enumerate() {
        let samples: Vec<_> = stream
            .soft
            .iter()
            .skip(arm)
            .step_by(arms)
            .copied()
            .collect();
        for (i, llr) in soft_sequence::detect_complete(&samples, channel, &[])?
            .posterior_llr
            .into_iter()
            .enumerate()
        {
            decoded[i * arms + arm] = llr;
        }
    }
    Ok(decoded)
}

type BlindModels = (
    Vec<soft_sequence::Channel>,
    Vec<progressive_channel::BlindFit>,
    Vec<(usize, usize)>,
);

fn blind(stream: &Stream, arms: usize) -> Result<BlindModels, String> {
    let mut channels = vec![];
    let mut fits = vec![];
    let mut excluded = vec![];
    for arm in 0..arms {
        let samples: Vec<_> = stream
            .soft
            .iter()
            .skip(arm)
            .step_by(arms)
            .copied()
            .collect();
        let fit = progressive_channel::fit_blind_diagnostic(&samples)?;
        let block = &samples[fit.validation_start_symbol..fit.validation_end_symbol];
        let mean = block.iter().sum::<f64>() / block.len() as f64;
        let variance = block.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / block.len() as f64;
        let power = fit.model.taps.iter().map(|x| x * x).sum::<f64>();
        let channel = soft_sequence::Channel {
            taps: fit.model.taps,
            bias: fit.model.bias,
            noise_variance: (variance * fit.validation_waveform_nmse)
                .max(power * 1e-3)
                .clamp(1e-12, 1e12),
        };
        channel.validate()?;
        // Both inferred training AND model-selection validation are excluded.
        excluded.push((
            fit.training_start_symbol * arms,
            fit.validation_end_symbol * arms,
        ));
        channels.push(channel);
        fits.push(fit);
    }
    Ok((channels, fits, excluded))
}

fn accept(
    stream: &Stream,
    receipt: &mut Receipt,
    receiver: &super::Config,
    start: u64,
    end: u64,
    all: &mut BTreeMap<String, Frame>,
) -> Result<(), String> {
    let decoded = detect(stream, &receipt.channels)?;
    let guard = receipt.channels.len() * sequence::TRAINING_GUARD_SYMBOLS + 25;
    for span in spans(&decoded, 0., receiver) {
        let left = span.start.saturating_sub(guard);
        let right = span.end.saturating_add(guard);
        if receipt
            .excluded_symbol_spans
            .iter()
            .any(|(a, b)| left < *b && right > *a)
        {
            continue;
        }
        let hex = hex::encode(span.frame);
        receipt.accepted_frames.push(hex.clone());
        all.entry(hex.clone())
            .or_insert_with(|| Frame {
                hex,
                validation_layers: vec![
                    "crc16_x25_received_fcs".into(),
                    "independent_received_residue_f0b8".into(),
                    "ax25_ui".into(),
                ],
                provenance: vec![],
            })
            .provenance
            .push(Provenance {
                stream: receipt.target_stream,
                lane: format!("{}:{}", receipt.kind, stream.lane),
                start_symbol: span.start,
                end_symbol: span.end,
                start_sample: start,
                end_sample: end,
            });
    }
    receipt.accepted_frames.sort();
    receipt.accepted_frames.dedup();
    Ok(())
}

impl Session {
    pub fn new(
        source_sha256: &str,
        rate: u32,
        receiver: super::Config,
        options: Config,
    ) -> Result<Self, String> {
        options.validate()?;
        if rate == 0
            || source_sha256.len() != 64
            || !source_sha256.bytes().all(|x| x.is_ascii_hexdigit())
        {
            return Err("HDLC memory requires sample rate and SHA-256 source identity".into());
        }
        if receiver.sequence.is_none() {
            return Err("HDLC memory requires the baseline-trained sequence lane".into());
        }
        Ok(Self {
            source: source_sha256.to_ascii_lowercase(),
            rate,
            receiver,
            options,
            models: vec![],
            previous_start: None,
        })
    }

    /// A failed window does not advance or modify memory. Profile/rate are fixed
    /// for the lifetime of the session. Overlapping windows cannot train targets.
    pub fn decode_window(
        &mut self,
        source_sha256: &str,
        start: u64,
        iq: &[Complex64],
    ) -> Result<WindowReport, String> {
        if !source_sha256.eq_ignore_ascii_case(&self.source) {
            return Err("HDLC memory source identity mismatch".into());
        }
        if self.previous_start.is_some_and(|last| start <= last) {
            return Err("HDLC memory windows must have strictly increasing starts".into());
        }
        let end = start
            .checked_add(iq.len() as u64)
            .ok_or("HDLC memory interval overflow")?;
        let local = super::decode(iq, self.rate, &self.receiver, &self.source)?;
        // Deliberately separate additive pass. This duplicates frontend work;
        // its full conservative admission cost is charged, not hidden.
        let mut frontend_config = self.receiver.clone();
        frontend_config.work_budget = frontend_config.work_budget.min(self.options.work_budget);
        let (streams, frontend_work) = collect_streams(iq, self.rate, &frontend_config)?;
        let mut work = 0;
        charge(&mut work, frontend_work, self.options.work_budget)?;
        let mut models = self.models.clone();
        models.retain(|m| start.saturating_sub(m.window.1) <= self.options.maximum_age_samples);
        let expired = self.models.len() - models.len();
        let mut all: BTreeMap<_, _> = local
            .frames
            .iter()
            .cloned()
            .map(|mut frame| {
                for p in &mut frame.provenance {
                    p.start_sample += start;
                    p.end_sample += start;
                }
                (frame.hex.clone(), frame)
            })
            .collect();
        let mut receipts = vec![];
        let arms = if quadrature(&self.receiver) { 2 } else { 1 };
        for (index, stream) in streams.iter().enumerate() {
            // Timing-bank indices are not stable across windows: use every
            // compatible retained model, bounded by capacity/work, not CRC rank.
            for model in models.iter().filter(|m| {
                m.window.1 <= start && m.lane == stream.lane && m.channels.len() == arms
            }) {
                charge(
                    &mut work,
                    stream.soft.len() as u64 * 512,
                    self.options.work_budget,
                )?;
                let mut receipt = Receipt {
                    target_stream: index,
                    kind: "cross_window_bcjr".into(),
                    source_window: Some(model.window),
                    source_stream: Some(model.stream),
                    channels: model.channels.clone(),
                    excluded_symbol_spans: vec![],
                    blind_fits: vec![],
                    accepted_frames: vec![],
                    reason: "causal_disjoint_baseline_anchor".into(),
                };
                accept(stream, &mut receipt, &self.receiver, start, end, &mut all)?;
                receipts.push(receipt);
            }
            if self.options.blind_bootstrap {
                charge(
                    &mut work,
                    stream.soft.len() as u64 * 8192,
                    self.options.work_budget,
                )?;
                let mut receipt = Receipt {
                    target_stream: index,
                    kind: "blind_bcjr".into(),
                    source_window: None,
                    source_stream: None,
                    channels: vec![],
                    excluded_symbol_spans: vec![],
                    blind_fits: vec![],
                    accepted_frames: vec![],
                    reason: String::new(),
                };
                match blind(stream, arms) {
                    Ok((channels, fits, excluded)) => {
                        receipt.channels = channels;
                        receipt.blind_fits = fits;
                        receipt.excluded_symbol_spans = excluded;
                        receipt.reason = "inferred_symbols_disjoint_fit_and_validation".into();
                        accept(stream, &mut receipt, &self.receiver, start, end, &mut all)?;
                    }
                    Err(error) => receipt.reason = error,
                }
                receipts.push(receipt);
            }
        }
        // Only untouched baseline anchor fits are eligible for future windows.
        for fit in &local.sequence {
            if fit.reason == "complete_bcjr_disjoint_target_acceptance"
                && fit.channels.len() == arms
                && !fit.training_spans.is_empty()
            {
                models.push(Model {
                    window: (start, end),
                    stream: fit.stream,
                    lane: streams[fit.stream].lane.clone(),
                    channels: fit.channels.clone(),
                });
            }
        }
        let evicted = models.len().saturating_sub(self.options.maximum_models);
        if evicted > 0 {
            models.drain(..evicted);
        }
        let original: BTreeSet<_> = local.frames.iter().map(|f| &f.hex).collect();
        let added = all
            .keys()
            .filter(|h| !original.contains(h))
            .cloned()
            .collect();
        self.models = models;
        self.previous_start = Some(start);
        Ok(WindowReport {
            schema: "framelift-hdlc-memory-window-v1".into(),
            start_sample: start,
            end_sample: end,
            local,
            frames: all.into_values().collect(),
            memory_added_frames: added,
            receipts,
            expired_models: expired,
            evicted_models: evicted,
            retained_models: self.models.len(),
            memory_consumed_work: work,
        })
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FilePlan {
    pub format: generic::InputFormat,
    pub sample_rate_hz: u32,
    pub receiver: super::Config,
    pub memory: Config,
    pub windows: Vec<crate::recovery_session::Window>,
}

/// Streaming file runner. A new output directory is required; reports from a
/// failed run are diagnostic only. Resume/import of channel state is not offered.
pub fn decode_file(
    path: &Path,
    plan: &FilePlan,
    output: &Path,
) -> Result<serde_json::Value, String> {
    if plan.windows.is_empty()
        || plan.windows.len() > 4096
        || plan
            .windows
            .windows(2)
            .any(|w| w[1].start_sample <= w[0].start_sample)
    {
        return Err("HDLC memory needs 1..4096 ordered windows".into());
    }
    if !matches!(
        plan.format,
        generic::InputFormat::Ci16Le | generic::InputFormat::Cf32Le | generic::InputFormat::Cf64Le
    ) {
        return Err("HDLC memory requires original coherent IQ".into());
    }
    plan.memory.validate()?;
    for w in &plan.windows {
        plan.receiver
            .validate(plan.sample_rate_hz, w.sample_count)?;
    }
    let out = input::existing_new_dir(output)?;
    let result = (|| {
        let guard = crate::recovery_guard::Guard::open(path)?;
        let runtime = crate::recovery_guard::Guard::open(
            &std::env::current_exe().map_err(|e| e.to_string())?,
        )?;
        let source = guard.identity().sha256.clone();
        let mut session = Session::new(
            &source,
            plan.sample_rate_hz,
            plan.receiver.clone(),
            plan.memory.clone(),
        )?;
        input::write_json_new(
            &out.join("plan.json"),
            &serde_json::json!({"schema":"framelift-hdlc-memory-plan-v1","plan":plan,"source":guard.identity(),"runtime":runtime.identity(),"compute":crate::compute::current().identity(),"publication_ready":false}),
        )?;
        let mut local = BTreeSet::new();
        let mut union = BTreeSet::new();
        let mut work = 0u64;
        for (i, window) in plan.windows.iter().enumerate() {
            let iq =
                guard.read_iq_window(&plan.format, window.start_sample, window.sample_count)?;
            let report = session.decode_window(&source, window.start_sample as u64, &iq)?;
            guard.verify()?;
            runtime.verify()?;
            local.extend(report.local.frames.iter().map(|f| f.hex.clone()));
            union.extend(report.frames.iter().map(|f| f.hex.clone()));
            work = work
                .checked_add(report.local.consumed_work)
                .and_then(|x| x.checked_add(report.memory_consumed_work))
                .ok_or("pass work overflow")?;
            input::write_json_new(&out.join(format!("window-{i:04}.json")), &report)?;
        }
        guard.verify()?;
        runtime.verify()?;
        let summary = serde_json::json!({"status":"complete","experimental":true,"publication_ready":false,"windows":plan.windows.len(),"local_frames":local,"frames":union,"memory_added_frames":union.difference(&local).collect::<Vec<_>>(),"consumed_work":work});
        input::write_json_new(&out.join("summary.json"), &summary)?;
        Ok(summary)
    })();
    if let Err(error) = &result {
        input::write_json_new(
            &out.join("failure.json"),
            &serde_json::json!({"status":"failed","error":error}),
        )?;
    }
    result
}

#[cfg(test)]
#[path = "tests/recovery_hdlc_memory_tests.rs"]
mod tests;
