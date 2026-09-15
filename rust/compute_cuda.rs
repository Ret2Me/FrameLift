//! Runtime-loaded CUDA 12 driver + NVRTC. No CUDA context is initialized in
//! the progressive supervisor before fork/exec. Buffers and streams are reused
//! under bounded lane ownership; callers cannot alias a mutable device buffer.
use super::{Layout, Options};
use cudarc::driver::{
    CudaContext, CudaFunction, CudaSlice, CudaStream, LaunchConfig, PushKernelArg,
};
use cudarc::nvrtc::{CompileOptions, compile_ptx_with_opts};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

const SOURCE: &str = include_str!("kernels/fir_f64.cu");
const MAX_TAPS: usize = 65_536;
fn err(e: impl std::fmt::Debug) -> String {
    format!("CUDA: {e:?}; CPU fallback is disabled")
}

struct Buffers {
    input: CudaSlice<f64>,
    taps: CudaSlice<f64>,
    output: CudaSlice<f64>,
}
struct Lane {
    stream: Arc<CudaStream>,
    buffers: Option<Buffers>,
}
pub(super) struct Gpu {
    lanes: Vec<Mutex<Lane>>,
    next: AtomicUsize,
    kernel: CudaFunction,
    input_capacity: usize,
    output_capacity: usize,
    pub identity: Value,
}

impl Gpu {
    pub fn new(options: &Options) -> Result<Self, String> {
        // Dynamic bindings panic if used before a missing library is detected.
        // Presence probes load libraries but create no context or allocation.
        if !unsafe { cudarc::driver::sys::is_culib_present() } {
            return Err("CUDA driver library unavailable; attach an NVIDIA GPU/driver or select --compute cpu".into());
        }
        if !unsafe { cudarc::nvrtc::sys::is_culib_present() } {
            return Err("CUDA NVRTC library unavailable; install matching CUDA 12 NVRTC runtime or select --compute cpu".into());
        }
        let context = CudaContext::new(options.cuda_device).map_err(err)?;
        let (major, minor) = context.compute_capability().map_err(err)?;
        if major < 5 {
            return Err("CUDA backend requires compute capability >= 5.0".into());
        }
        let mut driver_version = 0;
        let mut nvrtc_major = 0;
        let mut nvrtc_minor = 0;
        unsafe {
            cudarc::driver::sys::cuDriverGetVersion(&mut driver_version)
                .result()
                .map_err(err)?;
            cudarc::nvrtc::sys::nvrtcVersion(&mut nvrtc_major, &mut nvrtc_minor)
                .result()
                .map_err(err)?;
        }
        let compile = CompileOptions {
            fmad: Some(false),
            ftz: Some(false),
            prec_div: Some(true),
            prec_sqrt: Some(true),
            use_fast_math: Some(false),
            options: vec![
                format!("--gpu-architecture=compute_{major}{minor}"),
                "--std=c++11".into(),
            ],
            ..Default::default()
        };
        let ptx = compile_ptx_with_opts(SOURCE, compile).map_err(err)?;
        let ptx_sha256 = hex::encode(Sha256::digest(ptx.to_src().as_bytes()));
        let module = context.load_module(ptx).map_err(err)?;
        let kernel = module.load_function("fir_f64").map_err(err)?;
        let per_lane = options.cuda_buffer_mib * 1024 * 1024 / options.cuda_streams / 8;
        let input_capacity = (per_lane - MAX_TAPS) / 2;
        let output_capacity = per_lane - MAX_TAPS - input_capacity;
        let mut lanes = Vec::new();
        for _ in 0..options.cuda_streams {
            lanes.push(Mutex::new(Lane {
                stream: context.new_stream().map_err(err)?,
                buffers: None,
            }));
        }
        let identity = json!({"name":context.name().map_err(err)?,"ordinal":options.cuda_device,
            "uuid":hex::encode(context.uuid().map_err(err)?.bytes.map(|b|b as u8)),
            "compute_capability":[major,minor],"driver_version":driver_version,
            "nvrtc_version":[nvrtc_major,nvrtc_minor],"bindings":"cudarc-0.19.9-cuda12000",
            "kernel_source_sha256":hex::encode(Sha256::digest(SOURCE.as_bytes())),
            "ptx_sha256":ptx_sha256,
            "compiler_policy":"fmad=false,ftz=false,prec-div=true,prec-sqrt=true,no-fast-math; dadd_rn/dmul_rn",
            "startup_bit_parity_test":true});
        let gpu = Self {
            lanes,
            next: AtomicUsize::new(0),
            kernel,
            input_capacity,
            output_capacity,
            identity,
        };
        // Hard admission, not a hardware qualification claim: a basic arithmetic
        // or ABI mismatch cannot enter a real decoding session.
        for channels in [1, 2] {
            for centered in [false, true] {
                let samples: Vec<f64> = (0..47 * channels)
                    .map(|i| ((i * 31 % 47) as f64 - 23.0) / 37.0)
                    .collect();
                let taps = [-0.0, 0.5, -0.125, 1.0 / 7.0, -0.2];
                let layout = Layout::new(47, taps.len(), if centered { 1 } else { 3 }, centered)?;
                let actual = gpu.filter(&samples, &taps, &layout, channels)?;
                for (s, &value) in actual.iter().enumerate() {
                    let end = layout.first + (s / channels) * layout.decimation;
                    let mut expected = 0.0;
                    for (j, tap) in taps.iter().enumerate() {
                        if let Some(k) = end.checked_sub(j).filter(|&k| k < 47) {
                            expected += tap * samples[k * channels + s % channels];
                        }
                    }
                    if value.to_bits() != expected.to_bits() {
                        return Err("CUDA startup f64 parity test failed".into());
                    }
                }
            }
        }
        Ok(gpu)
    }

    pub fn filter(
        &self,
        input: &[f64],
        taps: &[f64],
        layout: &Layout,
        channels: usize,
    ) -> Result<Vec<f64>, String> {
        if layout.outputs == 0 {
            return Ok(vec![]);
        }
        let index = self.next.fetch_add(1, Ordering::Relaxed) % self.lanes.len();
        let mut lane = self.lanes[index]
            .lock()
            .map_err(|_| "CUDA lane poisoned; restart the process")?;
        let stream = lane.stream.clone();
        if lane.buffers.is_none() {
            // Fixed capacities per lane enforce the aggregate buffer budget.
            lane.buffers = Some(Buffers {
                input: stream
                    .alloc_zeros::<f64>(self.input_capacity)
                    .map_err(err)?,
                taps: stream.alloc_zeros::<f64>(MAX_TAPS).map_err(err)?,
                output: stream
                    .alloc_zeros::<f64>(self.output_capacity)
                    .map_err(err)?,
            });
        }
        let buffers = lane.buffers.as_mut().unwrap();
        let signal_samples = input.len() / channels;
        let input_samples_capacity = self.input_capacity / channels;
        if taps.len() > input_samples_capacity {
            return Err("CUDA buffer budget is too small for FIR overlap".into());
        }
        let chunk_outputs = ((input_samples_capacity - taps.len()) / layout.decimation + 1)
            .min(self.output_capacity / channels)
            .min(1_048_576);
        let mut output = vec![0.0; layout.outputs * channels];
        stream
            .memcpy_htod(taps, &mut buffers.taps.slice_mut(..taps.len()))
            .map_err(err)?;
        for start in (0..layout.outputs).step_by(chunk_outputs) {
            let count = chunk_outputs.min(layout.outputs - start);
            let first = layout.first + start * layout.decimation;
            let last = first + (count - 1) * layout.decimation;
            let input_start = first.saturating_sub(taps.len() - 1).min(signal_samples);
            let input_end = (last + 1).min(signal_samples);
            let input_count = input_end - input_start;
            stream
                .memcpy_htod(
                    &input[input_start * channels..input_end * channels],
                    &mut buffers.input.slice_mut(..input_count * channels),
                )
                .map_err(err)?;
            let mut out = buffers.output.slice_mut(..count * channels);
            let a = input_start as u64;
            let b = input_count as u64;
            let c = signal_samples as u64;
            let d = first as u64;
            let e = layout.decimation as u64;
            let f = taps.len() as u64;
            let g = count as u64;
            let h = channels as u32;
            // SAFETY: scalar launch bound is count*channels; host-validated
            // geometry bounds each kernel access to the retained buffers. The
            // mutex owns them until synchronized readback completes.
            unsafe {
                stream
                    .launch_builder(&self.kernel)
                    .arg(&buffers.input)
                    .arg(&buffers.taps)
                    .arg(&mut out)
                    .arg(&a)
                    .arg(&b)
                    .arg(&c)
                    .arg(&d)
                    .arg(&e)
                    .arg(&f)
                    .arg(&g)
                    .arg(&h)
                    .launch(LaunchConfig::for_num_elems((count * channels) as u32))
                    .map_err(err)?;
            }
            stream
                .memcpy_dtoh(
                    &out,
                    &mut output[start * channels..(start + count) * channels],
                )
                .map_err(err)?;
            stream.synchronize().map_err(err)?;
        }
        Ok(output)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    #[ignore = "requires CUDA 12 NVRTC runtime, but no GPU or driver"]
    fn nvrtc_compiles_strict_fir_without_device() {
        assert!(
            unsafe { cudarc::nvrtc::sys::is_culib_present() },
            "NVRTC runtime required"
        );
        let ptx = compile_ptx_with_opts(
            SOURCE,
            CompileOptions {
                fmad: Some(false),
                ftz: Some(false),
                prec_div: Some(true),
                prec_sqrt: Some(true),
                arch: Some("compute_75"),
                ..Default::default()
            },
        )
        .unwrap()
        .to_src();
        assert!(ptx.contains("fir_f64"));
        assert!(ptx.contains("mul.rn.f64"));
        assert!(ptx.contains("add.rn.f64"));
        assert!(!ptx.contains("fma.rn.f64"));
    }
    #[test]
    #[ignore = "requires NVIDIA GPU and CUDA NVRTC; absence must fail, not skip"]
    fn gpu_hardware_full_parity_and_concurrent_streams() {
        let options = Options {
            backend: super::super::Backend::Cuda,
            cuda_buffer_mib: 16,
            cuda_streams: 2,
            ..Default::default()
        };
        let gpu = super::super::Engine::new(options).unwrap();
        let cpu = super::super::Engine::new(Options::default()).unwrap();
        std::thread::scope(|scope| {
            for thread in 0..4 {
                let gpu = &gpu;
                let cpu = &cpu;
                scope.spawn(move || {
                    for n in [0, 1, 2, 17, 257, 8193, 250_003] {
                        let x: Vec<_> = (0..n)
                            .map(|i| ((i * 127 + thread * 19) % 1031) as f64 / 31.0 - 16.0)
                            .collect();
                        for taps in [vec![1.0], vec![-0.0, 0.1, -0.2], vec![1.0 / 115.0; 115]] {
                            for d in [1, 2, 7] {
                                let a = cpu.fir_real(&x, &taps, d).unwrap();
                                let b = gpu.fir_real(&x, &taps, d).unwrap();
                                assert!(a.iter().zip(&b).all(|(x, y)| x.to_bits() == y.to_bits()));
                                assert_eq!(a.len(), b.len());
                                let z: Vec<_> = x
                                    .iter()
                                    .map(|&v| num_complex::Complex64::new(v, -v))
                                    .collect();
                                for centered in [false, true] {
                                    let d = if centered { 1 } else { d };
                                    let a = cpu.fir_complex(&z, &taps, d, centered).unwrap();
                                    let b = gpu.fir_complex(&z, &taps, d, centered).unwrap();
                                    assert_eq!(a.len(), b.len());
                                    assert!(
                                        a.iter()
                                            .zip(&b)
                                            .all(|(x, y)| x.re.to_bits() == y.re.to_bits()
                                                && x.im.to_bits() == y.im.to_bits())
                                    );
                                }
                            }
                        }
                    }
                });
            }
        });
    }
}
