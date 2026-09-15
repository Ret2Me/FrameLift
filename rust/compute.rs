//! Optional, immutable process-local compute policy. GPU errors never fall back
//! to CPU. Only independent FIR outputs are parallelized: each dot product
//! retains the original f64 multiplication/addition order. Acquisition, timing,
//! MLSE, protocol validation and FEC remain unchanged CPU implementations.
use num_complex::Complex64;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::sync::OnceLock;
use std::sync::atomic::{AtomicU64, Ordering};

#[cfg(feature = "cuda")]
#[path = "compute_cuda.rs"]
mod cuda;
#[path = "compute_qualification.rs"]
pub mod qualification;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize, clap::ValueEnum)]
#[serde(rename_all = "snake_case")]
pub enum Backend {
    #[default]
    Cpu,
    Cuda,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Options {
    pub backend: Backend,
    /// Additional sample-parallel pool; 1 preserves existing window parallelism.
    pub cpu_threads: usize,
    pub cuda_device: usize,
    pub cuda_streams: usize,
    /// Aggregate retained device buffers, not the driver/context overhead.
    pub cuda_buffer_mib: usize,
}
impl Default for Options {
    fn default() -> Self {
        Self {
            backend: Backend::Cpu,
            cpu_threads: 1,
            cuda_device: 0,
            cuda_streams: 2,
            cuda_buffer_mib: 256,
        }
    }
}
impl Options {
    pub fn validate(&self) -> Result<(), String> {
        if !(1..=256).contains(&self.cpu_threads) {
            return Err("compute-threads must be in 1..=256".into());
        }
        if !(1..=16).contains(&self.cuda_streams)
            || self.cuda_device > 255
            || !(16..=16_384).contains(&self.cuda_buffer_mib)
        {
            return Err(
                "CUDA requires device 0..255, streams 1..16 and buffer-mib 16..16384".into(),
            );
        }
        if self.backend == Backend::Cuda && !cfg!(feature = "cuda") {
            return Err("CUDA requested but this binary was built without --features cuda; select --compute cpu or rebuild".into());
        }
        Ok(())
    }
}

pub struct Engine {
    options: Options,
    pool: Option<rayon::ThreadPool>,
    identity: Value,
    calls: AtomicU64,
    outputs: AtomicU64,
    #[cfg(feature = "cuda")]
    gpu: Option<cuda::Gpu>,
}

impl Engine {
    pub fn new(options: Options) -> Result<Self, String> {
        options.validate()?;
        let pool = if options.backend == Backend::Cpu && options.cpu_threads > 1 {
            Some(
                rayon::ThreadPoolBuilder::new()
                    .num_threads(options.cpu_threads)
                    .thread_name(|i| format!("ty-fir-{i}"))
                    .build()
                    .map_err(|e| format!("compute pool: {e}"))?,
            )
        } else {
            None
        };
        #[cfg(feature = "cuda")]
        let gpu = if options.backend == Backend::Cuda {
            Some(cuda::Gpu::new(&options)?)
        } else {
            None
        };
        let mut identity = json!({"schema":"telemetry-compute-v1", "backend":options.backend,
            "arithmetic":"f64-ordered-multiply-add-no-contraction-v1",
            "accelerated_operations":["real_causal_fir", "complex_causal_fir", "complex_centered_fir"],
            "remaining_operations":"CPU; no FFT, timing, MLSE, FEC or framing offload",
            "fallback":false});
        // Keep CUDA and CPU paths separate even in a build with both enabled.
        #[cfg(feature = "cuda")]
        if let Some(gpu) = &gpu {
            identity["device"] = gpu.identity.clone();
        }
        identity["host_arch"] = json!(std::env::consts::ARCH);
        Ok(Self {
            options,
            pool,
            identity,
            calls: AtomicU64::new(0),
            outputs: AtomicU64::new(0),
            #[cfg(feature = "cuda")]
            gpu,
        })
    }

    /// Arithmetic/hardware identity for checkpoints; resource budgets are
    /// invocation metadata, not permission to drop work.
    pub fn identity(&self) -> Value {
        self.identity.clone()
    }
    pub fn report(&self) -> Value {
        json!({"identity":self.identity,"options":self.options,
            "fir_calls":self.calls.load(Ordering::Relaxed),
            "fir_output_samples":self.outputs.load(Ordering::Relaxed),
            "cuda_compiled":cfg!(feature="cuda"),
            "qualification":"requires explicit parity and end-to-end receiver benchmarks"})
    }
    fn count(&self, n: usize) {
        self.calls.fetch_add(1, Ordering::Relaxed);
        self.outputs.fetch_add(n as u64, Ordering::Relaxed);
    }
    fn map<T: Send>(&self, n: usize, f: impl Fn(usize) -> T + Sync + Send) -> Vec<T> {
        if let Some(pool) = &self.pool
            && n >= 4096
        {
            return pool.install(|| (0..n).into_par_iter().map(f).collect());
        }
        (0..n).map(f).collect()
    }

    pub fn fir_real(
        &self,
        pcm: &[f64],
        taps: &[f64],
        decimation: usize,
    ) -> Result<Vec<f64>, String> {
        let layout = Layout::new(pcm.len(), taps.len(), decimation, false)?;
        validate_finite(pcm, taps)?;
        #[cfg(feature = "cuda")]
        if let Some(gpu) = &self.gpu {
            let output = gpu.filter(pcm, taps, &layout, 1)?;
            self.count(output.len());
            return Ok(output);
        }
        let output = self.map(layout.outputs, |i| {
            let end = layout.first + i * layout.decimation;
            let mut sum = 0.0;
            for (offset, tap) in taps.iter().enumerate() {
                sum += tap * pcm[end - offset];
            }
            sum
        });
        self.count(output.len());
        Ok(output)
    }

    pub fn fir_complex(
        &self,
        iq: &[Complex64],
        taps: &[f64],
        decimation: usize,
        centered: bool,
    ) -> Result<Vec<Complex64>, String> {
        let layout = Layout::new(iq.len(), taps.len(), decimation, centered)?;
        if iq.iter().any(|x| !x.re.is_finite() || !x.im.is_finite()) {
            return Err("FIR input must be finite".into());
        }
        validate_finite(&[], taps)?;
        #[cfg(feature = "cuda")]
        if let Some(gpu) = &self.gpu {
            // Explicit interleaving avoids unsafe assumptions about Complex ABI.
            let packed: Vec<f64> = iq.iter().flat_map(|x| [x.re, x.im]).collect();
            let values = gpu.filter(&packed, taps, &layout, 2)?;
            let output: Vec<_> = values
                .as_chunks::<2>()
                .0
                .iter()
                .map(|x| Complex64::new(x[0], x[1]))
                .collect();
            self.count(output.len());
            return Ok(output);
        }
        let output = self.map(layout.outputs, |i| {
            let end = layout.first + i * layout.decimation;
            let mut value = Complex64::new(0.0, 0.0);
            for (offset, &tap) in taps.iter().enumerate() {
                if let Some(k) = end.checked_sub(offset).filter(|&k| k < iq.len()) {
                    value.re += iq[k].re * tap;
                    value.im += iq[k].im * tap;
                }
            }
            value
        });
        self.count(output.len());
        Ok(output)
    }
}

fn validate_finite(input: &[f64], taps: &[f64]) -> Result<(), String> {
    if input.iter().chain(taps).any(|x| !x.is_finite()) {
        return Err("FIR input and taps must be finite".into());
    }
    Ok(())
}

/// Shared launch geometry, including exact zero-padding/startup semantics.
pub(crate) struct Layout {
    pub first: usize,
    pub outputs: usize,
    pub decimation: usize,
}
impl Layout {
    fn new(samples: usize, taps: usize, decimation: usize, centered: bool) -> Result<Self, String> {
        if taps == 0 || taps > 65_536 || decimation == 0 || samples > 134_217_728 {
            return Err(
                "FIR requires 1..65536 taps, positive decimation and at most 134217728 samples"
                    .into(),
            );
        }
        if centered && decimation != 1 {
            return Err("centered FIR requires decimation 1".into());
        }
        Ok(Self {
            first: if centered { (taps - 1) / 2 } else { taps - 1 },
            outputs: if centered {
                samples
            } else if samples < taps {
                0
            } else {
                (samples - taps) / decimation + 1
            },
            decimation,
        })
    }
}

static ENGINE: OnceLock<Engine> = OnceLock::new();
pub fn initialize(options: &Options) -> Result<(), String> {
    if let Some(engine) = ENGINE.get() {
        return if &engine.options == options {
            Ok(())
        } else {
            Err("compute backend cannot change within a process".into())
        };
    }
    let engine = Engine::new(options.clone())?;
    ENGINE
        .set(engine)
        .map_err(|_| "compute initialization raced with another caller".to_string())
}
pub fn current() -> &'static Engine {
    // First use also freezes the default policy. Otherwise a library caller
    // could start work on CPU and initialize CUDA halfway through a session.
    ENGINE.get_or_init(|| Engine::new(Options::default()).expect("valid serial CPU configuration"))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn first_use_freezes_policy_and_late_reconfiguration_is_rejected() {
        let before = current().identity();
        assert!(
            initialize(&Options {
                cpu_threads: 2,
                ..Options::default()
            })
            .is_err()
        );
        assert_eq!(current().identity(), before);
        assert!(initialize(&Options::default()).is_ok());
    }
    fn serial(pcm: &[f64], taps: &[f64], decimation: usize) -> Vec<f64> {
        if pcm.len() < taps.len() {
            return vec![];
        }
        (taps.len() - 1..pcm.len())
            .step_by(decimation)
            .map(|i| {
                let mut sum = 0.0;
                for (j, tap) in taps.iter().enumerate() {
                    sum += tap * pcm[i - j];
                }
                sum
            })
            .collect()
    }
    fn bits(values: &[f64]) -> Vec<u64> {
        values.iter().map(|x| x.to_bits()).collect()
    }
    #[test]
    fn parallel_cpu_matches_frozen_serial_bits_at_boundaries() {
        for threads in [1, 2, 4] {
            let e = Engine::new(Options {
                cpu_threads: threads,
                ..Options::default()
            })
            .unwrap();
            for n in [0, 1, 3, 128, 8193, 20_000] {
                let signal: Vec<_> = (0..n)
                    .map(|i| ((i * 177 % 317) as f64 - 158.0) / 73.0)
                    .collect();
                for taps in [vec![1.0], vec![0.25, -0.125, 0.5], vec![1.0 / 115.0; 115]] {
                    for d in [1, 2, 7] {
                        assert_eq!(
                            bits(&e.fir_real(&signal, &taps, d).unwrap()),
                            bits(&serial(&signal, &taps, d))
                        );
                    }
                }
            }
        }
    }
    #[test]
    fn centered_complex_preserves_legacy_edges_and_signed_zero() {
        let e = Engine::new(Options {
            cpu_threads: 4,
            ..Options::default()
        })
        .unwrap();
        let signal: Vec<_> = (0..8193)
            .map(|i| Complex64::new(i as f64 / 317.0, if i % 2 == 0 { -0.0 } else { -1.0 }))
            .collect();
        for taps in [vec![1.0], vec![-0.5, 0.25, 0.5], vec![0.125; 8]] {
            let got = e.fir_complex(&signal, &taps, 1, true).unwrap();
            for (i, v) in got.iter().enumerate() {
                let mut want = Complex64::new(0.0, 0.0);
                for (j, &tap) in taps.iter().enumerate() {
                    if let Some(k) = (i + (taps.len() - 1) / 2)
                        .checked_sub(j)
                        .filter(|&k| k < signal.len())
                    {
                        want += signal[k] * tap;
                    }
                }
                assert_eq!(
                    [v.re.to_bits(), v.im.to_bits()],
                    [want.re.to_bits(), want.im.to_bits()]
                );
            }
        }
    }
    #[test]
    fn invalid_work_is_rejected_before_silence_or_empty_shortcuts() {
        let e = Engine::new(Options::default()).unwrap();
        assert!(e.fir_real(&[], &[], 1).is_err());
        assert!(e.fir_real(&[], &[1.0], 0).is_err());
        assert!(e.fir_real(&[f64::NAN], &[1.0, 1.0], 1).is_err());
        assert!(e.fir_complex(&[], &[f64::INFINITY], 1, true).is_err());
        assert!(
            Options {
                cpu_threads: 0,
                ..Options::default()
            }
            .validate()
            .is_err()
        );
    }
    #[cfg(not(feature = "cuda"))]
    #[test]
    fn explicit_cuda_never_silently_becomes_cpu() {
        assert!(
            Engine::new(Options {
                backend: Backend::Cuda,
                ..Options::default()
            })
            .err()
            .unwrap()
            .contains("without --features cuda")
        );
    }
}
