//! PD120/PD180 analog SSTV from mono audio.
//! Timing source: G4IJE/K0HEO PD specification and N7CXI Dayton 2000 paper.
//! Global sync fitting is established synchronization, not a claimed new invention.
use rustfft::{FftPlanner, num_complex::Complex32};
use serde::Serialize;

#[derive(Clone, Copy)]
pub enum Mode {
    Pd120,
    Pd180,
}
impl Mode {
    pub fn parse(s: &str) -> Result<Self, String> {
        match s {
            "pd120" => Ok(Self::Pd120),
            "pd180" => Ok(Self::Pd180),
            _ => Err("mode must be pd120 or pd180".into()),
        }
    }
    fn component(self) -> f64 {
        match self {
            Self::Pd120 => 0.1216,
            Self::Pd180 => 0.18304,
        }
    }
    fn period(self) -> f64 {
        0.02208 + 4. * self.component()
    }
}
#[derive(Serialize)]
pub struct Pair {
    pub start_seconds: f64,
    pub period_seconds: f64,
    pub observed_sync: bool,
    pub frequency_offset_hz: f64,
    /// Finite demodulator samples available in this pair; NOT pixel correctness.
    pub finite_audio_fraction: f64,
}
pub struct Image {
    pub rgb: Vec<u8>,
    pub rows: usize,
    pub candidates: usize,
    pub observed: usize,
    pub predicted: usize,
    pub period: f64,
    pub first: f64,
    pub pairs: Vec<Pair>,
    pub vis_origin: Option<f64>,
    pub sync_times: Vec<f64>,
    pub timing_segments: usize,
}

// A valid VIS identifies the image origin, not the integrity of analog pixels.
fn vis_origin(f: &[f32], fs: f64, mode: Mode, near: f64) -> Option<f64> {
    let expected: u8 = match mode {
        Mode::Pd120 => 95,
        Mode::Pd180 => 96,
    };
    let value = |t: f64, duration: f64| mean(f, (t * fs) as usize, ((t + duration) * fs) as usize);
    let near_tone = |v: f64, target: f64| v.is_finite() && (v - target).abs() < 35.;
    let lo = ((near - 10. * mode.period() - 0.30).max(0.30) * 1000.) as usize;
    let hi = (near * 1000.) as usize;
    let mut matches = Vec::new();
    for ms in lo..hi {
        let t = ms as f64 / 1000.;
        if !near_tone(value(t - 0.27, 0.24), 1900.) || !near_tone(value(t + 0.008, 0.014), 1200.) {
            continue;
        }
        let mut bits = 0u8;
        let mut ok = true;
        for b in 0..8 {
            let hz = value(t + 0.038 + b as f64 * 0.030, 0.014);
            if near_tone(hz, 1100.) {
                bits |= 1 << b;
            } else if !near_tone(hz, 1300.) {
                ok = false;
                break;
            }
        }
        if ok
            && bits & 127 == expected
            && bits.count_ones().is_multiple_of(2)
            && near_tone(value(t + 0.278, 0.014), 1200.)
        {
            matches.push(t + 0.300);
        }
    }
    // Midpoint of the admissible interval avoids choosing a threshold's leading edge.
    if matches.is_empty() {
        None
    } else {
        Some(matches[matches.len() / 2])
    }
}

// Overlap-save Hilbert analytic signal using FFT blocks. Guard samples are discarded.
fn frequencies(x: &[f32], fs: f64) -> Vec<f32> {
    const N: usize = 16384;
    const GUARD: usize = 1024;
    const STEP: usize = N - 2 * GUARD;
    let mut planner = FftPlanner::<f32>::new();
    let fft = planner.plan_fft_forward(N);
    let ifft = planner.plan_fft_inverse(N);
    let mut out = vec![f32::NAN; x.len()];
    let mut buf = vec![Complex32::new(0., 0.); N];
    for origin in (0..x.len()).step_by(STEP) {
        for (j, b) in buf.iter_mut().enumerate() {
            let i = origin as isize + j as isize - GUARD as isize;
            *b = Complex32::new(
                if i >= 0 {
                    x.get(i as usize).copied().unwrap_or(0.)
                } else {
                    0.
                },
                0.,
            );
        }
        fft.process(&mut buf);
        // Analytic audio; reject DC and frequencies outside the SSTV tone band.
        for (j, b) in buf.iter_mut().enumerate() {
            let hz = j as f64 * fs / N as f64;
            if !(700. ..=2800.).contains(&hz) {
                *b = Complex32::new(0., 0.);
            } else {
                *b *= 2. / N as f32;
            }
        }
        ifft.process(&mut buf);
        for j in GUARD..N - GUARD {
            let i = origin + j - GUARD;
            if i >= out.len() {
                break;
            }
            let z = buf[j] * buf[j - 1].conj();
            if z.norm_sqr() > 1e-16 {
                out[i] = z.arg() * fs as f32 / (2. * std::f32::consts::PI);
            }
        }
    }
    out
}
fn mean(x: &[f32], a: usize, b: usize) -> f64 {
    let slice = &x[a.min(x.len())..b.min(x.len()).max(a.min(x.len()))];
    let (s, n) = slice
        .iter()
        .filter(|v| v.is_finite())
        .fold((0., 0), |(s, n), v| (s + *v as f64, n + 1));
    if n == 0 { f64::NAN } else { s / n as f64 }
}
fn syncs(f: &[f32], fs: f64) -> Vec<f64> {
    // 1 ms means tolerate isolated phase spikes; require a sustained 1200 Hz tone.
    let hop = (fs * 0.001).round() as usize;
    let bins: Vec<_> = (0..f.len())
        .step_by(hop)
        .map(|i| mean(f, i, i + hop))
        .collect();
    let mut starts = Vec::new();
    let mut begin = None;
    for i in 0..=bins.len() {
        let good = i < bins.len() && (1080. ..=1320.).contains(&bins[i]);
        match (begin, good) {
            (None, true) => begin = Some(i),
            (Some(a), false) => {
                if (14..=27).contains(&(i - a)) {
                    starts.push(a as f64 * hop as f64 / fs);
                }
                begin = None;
            }
            _ => {}
        }
    }
    starts
}
struct LineClock {
    first: f64,
    period: f64,
    matched: Vec<(usize, f64)>,
}

fn fit(starts: &[f64], nominal: f64) -> Result<LineClock, String> {
    let mut best = Vec::new();
    for &seed in starts {
        let mut matches: Vec<_> = starts
            .iter()
            .filter_map(|&t| {
                let k = ((t - seed) / nominal).round();
                if (0. ..248.).contains(&k) && (t - seed - k * nominal).abs() < 0.025 {
                    Some((k as usize, t))
                } else {
                    None
                }
            })
            .collect();
        matches.dedup_by_key(|p| p.0);
        if matches.len() > best.len() {
            best = matches;
        }
    }
    if best.len() < 8 {
        return Err("no supported PD sync train (minimum 8 line-pair syncs)".into());
    }
    let n = best.len() as f64;
    let sx: f64 = best.iter().map(|p| p.0 as f64).sum();
    let sy: f64 = best.iter().map(|p| p.1).sum();
    let sxx: f64 = best.iter().map(|p| (p.0 * p.0) as f64).sum();
    let sxy: f64 = best.iter().map(|p| p.0 as f64 * p.1).sum();
    let period = (n * sxy - sx * sy) / (n * sxx - sx * sx);
    if !period.is_finite() || (period / nominal - 1.).abs() > 0.003 {
        return Err("invalid fitted line clock".into());
    }
    Ok(LineClock {
        first: (sy - period * sx) / n,
        period,
        matched: best,
    })
}
fn rgb(y: f64, cr: f64, cb: f64) -> [u8; 3] {
    let cr = cr - 128.;
    let cb = cb - 128.;
    [
        y + 1.402 * cr,
        y - 0.344136 * cb - 0.714136 * cr,
        y + 1.772 * cb,
    ]
    .map(|v| v.round().clamp(0., 255.) as u8)
}
pub fn decode(x: &[f32], fs: f64, mode: Mode, global: bool) -> Result<Image, String> {
    if !fs.is_finite()
        || !(12000. ..=48000.).contains(&fs)
        || x.is_empty()
        || x.iter().any(|v| !v.is_finite())
    {
        return Err("invalid audio".into());
    }
    let f = frequencies(x, fs);
    let starts = syncs(&f, fs);
    let LineClock {
        mut first,
        period,
        matched,
    } = fit(&starts, mode.period())?;
    let vis = vis_origin(&f, fs, mode, first);
    let shift = vis
        .map(|v| ((first - v) / period).round().max(0.) as usize)
        .unwrap_or(0);
    if vis.is_some() {
        first -= shift as f64 * period;
    }
    // Fit separate clock segments across abrupt timing jumps in the recording.
    // This changes sampling times only; never synthesizes image pixels.
    let mut segments = Vec::new();
    let mut begin = 0;
    for end in 1..=matched.len() {
        let split = end == matched.len() || {
            let (k0, t0) = matched[end - 1];
            let (k1, t1) = matched[end];
            ((t1 - t0) - (k1 - k0) as f64 * period).abs() > 0.003
        };
        if split {
            let points = &matched[begin..end];
            let n = points.len() as f64;
            let mx = points.iter().map(|p| (p.0 + shift) as f64).sum::<f64>() / n;
            let my = points.iter().map(|p| p.1).sum::<f64>() / n;
            let xx = points
                .iter()
                .map(|p| ((p.0 + shift) as f64 - mx).powi(2))
                .sum::<f64>();
            let xy = points
                .iter()
                .map(|p| ((p.0 + shift) as f64 - mx) * (p.1 - my))
                .sum::<f64>();
            let slope = if points.len() >= 8 && xx > 0. {
                xy / xx
            } else {
                period
            };
            let slope = if (slope / mode.period() - 1.).abs() < 0.003 {
                slope
            } else {
                period
            };
            segments.push((
                if begin == 0 { 0 } else { points[0].0 + shift },
                my - slope * mx,
                slope,
            ));
            begin = end;
        }
    }
    let last = (matched.iter().map(|p| p.0).max().unwrap() + shift).min(247);
    let rows = 2 * (last + 1);
    let mut image = Image {
        rgb: vec![0; rows * 640 * 3],
        rows,
        candidates: starts.len(),
        observed: 0,
        predicted: 0,
        period,
        first,
        pairs: Vec::new(),
        vis_origin: vis,
        sync_times: starts.clone(),
        timing_segments: segments.len(),
    };
    for k in 0..=last {
        let &(_, origin, pair_period) = segments.iter().rev().find(|s| s.0 <= k).unwrap();
        let pred = origin + k as f64 * pair_period;
        let measured = starts
            .iter()
            .copied()
            .filter(|t| (*t - pred).abs() < 0.007)
            .min_by(|a, b| (a - pred).abs().total_cmp(&(b - pred).abs()));
        // Never extrapolate outside the span supported by observed syncs.
        let t = if global {
            pred
        } else {
            measured.unwrap_or(pred)
        };
        let observed = measured.is_some();
        if observed {
            image.observed += 1;
        } else {
            image.predicted += 1;
        }
        let offset = if observed {
            mean(
                &f,
                ((measured.unwrap() + 0.004) * fs) as usize,
                ((measured.unwrap() + 0.016) * fs) as usize,
            ) - 1200.
        } else {
            0.
        };
        let offset = if offset.is_finite() {
            offset.clamp(-120., 120.)
        } else {
            0.
        };
        image.pairs.push(Pair {
            start_seconds: t,
            period_seconds: pair_period,
            observed_sync: observed,
            frequency_offset_hz: offset,
            finite_audio_fraction: {
                let a = (t * fs).round() as usize;
                let b = ((t + period) * fs).round() as usize;
                f[a.min(f.len())..b.min(f.len())]
                    .iter()
                    .filter(|v| v.is_finite())
                    .count() as f64
                    / (b - a).max(1) as f64
            },
        });
        if !observed && !global {
            continue;
        }
        let scale = if global {
            pair_period / mode.period()
        } else {
            1.
        };
        for col in 0..640 {
            let mut c = [0.; 4];
            for (channel, v) in c.iter_mut().enumerate() {
                let a =
                    t + scale * (0.02208 + mode.component() * (channel as f64 + col as f64 / 640.));
                let b = a + scale * mode.component() / 640.;
                *v = ((mean(&f, (a * fs).round() as usize, (b * fs).round() as usize)
                    - offset
                    - 1500.)
                    * 255.
                    / 800.)
                    .clamp(0., 255.);
            }
            for row in 0..2 {
                let p = ((2 * k + row) * 640 + col) * 3;
                image.rgb[p..p + 3].copy_from_slice(&rgb(
                    c[if row == 0 { 0 } else { 3 }],
                    c[1],
                    c[2],
                ));
            }
        }
    }
    Ok(image)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn timing_spec() {
        assert!((Mode::Pd120.period() - 0.50848).abs() < 1e-9);
        assert!((Mode::Pd180.period() - 0.75424).abs() < 1e-9);
    }
    #[test]
    fn silence_rejected() {
        assert!(decode(&vec![0.; 120000], 12000., Mode::Pd120, true).is_err());
    }
    #[test]
    fn invalid_rejected() {
        assert!(decode(&[f32::NAN], 12000., Mode::Pd120, true).is_err());
        assert!(Mode::parse("iq").is_err());
    }
    #[test]
    fn clock_with_missing_sync() {
        let p = Mode::Pd120.period() * 1.00002;
        let s: Vec<_> = (0..100)
            .filter(|k| *k != 30)
            .map(|k| 2. + k as f64 * p)
            .collect();
        let LineClock {
            first: a,
            period: b,
            matched: m,
        } = fit(&s, Mode::Pd120.period()).unwrap();
        assert!((a - 2.).abs() < 1e-9);
        assert!((b - p).abs() < 1e-9);
        assert_eq!(m.len(), 99);
    }
    #[test]
    fn tone_frequency() {
        let fs = 12000.;
        let x: Vec<_> = (0..24000)
            .map(|i| (i as f64 * 2. * std::f64::consts::PI * 1900. / fs).sin() as f32)
            .collect();
        let f = frequencies(&x, fs);
        assert!((mean(&f, 2000, 20000) - 1900.).abs() < 0.1);
    }
    #[test]
    fn neutral_colors() {
        assert_eq!(rgb(128., 128., 128.), [128, 128, 128]);
        assert_eq!(rgb(0., 128., 128.), [0, 0, 0]);
    }
    #[test]
    fn audio_to_gray_image_both_modes() {
        for mode in [Mode::Pd120, Mode::Pd180] {
            let fs = 12000.;
            let duration = 0.1 + 12. * mode.period();
            let mut phase = 0.;
            let mut audio: Vec<f32> = (0..(duration * fs) as usize)
                .map(|i| {
                    let t = i as f64 / fs - 0.1;
                    let within = t.rem_euclid(mode.period());
                    // Remove one sync only; keep the actual image-bearing tones.
                    let erased = (t / mode.period()).floor() == 6.;
                    let hz = if t >= 0. && within < 0.020 && !erased {
                        1200.
                    } else if t >= 0. && within < 0.02208 && !erased {
                        1500.
                    } else {
                        1900.
                    };
                    phase += 2. * std::f64::consts::PI * hz / fs;
                    phase.sin() as f32 * 0.5
                })
                .collect();
            let jump = ((0.1 + 8. * mode.period()) * fs).round() as usize;
            audio.splice(jump..jump, std::iter::repeat_n(0., 72));
            let image = decode(&audio, fs, mode, true).unwrap();
            assert!(image.timing_segments >= 2);
            assert!(image.observed >= 10);
            assert!(image.predicted >= 1);
            for row in 2..image.rows - 2 {
                let p = (row * 640 + 320) * 3;
                assert!(
                    image.rgb[p..p + 3]
                        .iter()
                        .all(|v| (*v as i32 - 128).abs() < 8)
                );
            }
        }
    }
    #[test]
    fn duplicate_syncs_cannot_fake_train() {
        assert!(fit(&[1.; 20], Mode::Pd120.period()).is_err());
    }
    #[test]
    fn vis_parity_and_mode() {
        let fs = 12000.;
        let mut f = vec![1900.; 36000];
        let t = 1.;
        let mut tone = |a: f64, b: f64, hz: f32| {
            for v in &mut f[(a * fs) as usize..(b * fs) as usize] {
                *v = hz;
            }
        };
        tone(t, t + 0.03, 1200.);
        // PD120=95 has even parity, parity bit zero.
        for b in 0..8 {
            tone(
                t + 0.03 + b as f64 * 0.03,
                t + 0.06 + b as f64 * 0.03,
                if 95u8 & (1 << b) != 0 { 1100. } else { 1300. },
            );
        }
        tone(t + 0.27, t + 0.32, 1200.);
        let origin = vis_origin(&f, fs, Mode::Pd120, 1.3 + Mode::Pd120.period()).unwrap();
        assert!((origin - 1.3).abs() < 0.004);
        assert!(vis_origin(&f, fs, Mode::Pd180, 2.).is_none());
        for v in &mut f[(1.24 * fs) as usize..(1.27 * fs) as usize] {
            *v = 1100.;
        }
        assert!(vis_origin(&f, fs, Mode::Pd120, 1.3 + Mode::Pd120.period()).is_none());
    }
}
