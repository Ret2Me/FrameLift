//! Explicit Amdahl-style acceleration model. It converts measured CPU stage
//! times plus assumed per-stage acceleration into a scenario, never a hardware
//! benchmark or deployment claim.

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Stage {
    pub name: String,
    pub cpu_seconds: f64,
    /// 1.0 means this stage stays on CPU.
    pub assumed_speedup: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GpuScenario {
    pub label: String,
    pub fixed_overhead_seconds: f64,
    pub stages: Vec<Stage>,
}

#[derive(Clone, Debug, Serialize)]
pub struct StageEstimate {
    pub name: String,
    pub cpu_seconds: f64,
    pub assumed_speedup: f64,
    pub estimated_seconds: f64,
}

#[derive(Clone, Debug, Serialize)]
pub struct Estimate {
    pub schema: &'static str,
    pub label: String,
    pub measured_cpu_seconds: f64,
    pub estimated_gpu_seconds: f64,
    pub estimated_speedup: f64,
    pub fixed_overhead_seconds: f64,
    pub accelerated_cpu_fraction: f64,
    pub stages: Vec<StageEstimate>,
    pub hardware_benchmark: bool,
}

pub fn estimate(scenario: &GpuScenario) -> Result<Estimate, String> {
    if scenario.label.trim().is_empty()
        || scenario.label.len() > 256
        || !scenario.fixed_overhead_seconds.is_finite()
        || scenario.fixed_overhead_seconds < 0.0
        || scenario.stages.is_empty()
        || scenario.stages.len() > 128
    {
        return Err("invalid GPU scenario identity, overhead or stage count".into());
    }
    let mut cpu = 0.0;
    let mut predicted = scenario.fixed_overhead_seconds;
    let mut accelerated = 0.0;
    let mut stages = Vec::with_capacity(scenario.stages.len());
    for stage in &scenario.stages {
        if stage.name.trim().is_empty()
            || stage.name.len() > 128
            || !stage.cpu_seconds.is_finite()
            || stage.cpu_seconds < 0.0
            || !stage.assumed_speedup.is_finite()
            || !(1.0..=1_000_000.0).contains(&stage.assumed_speedup)
        {
            return Err("invalid GPU scenario stage".into());
        }
        let seconds = stage.cpu_seconds / stage.assumed_speedup;
        cpu += stage.cpu_seconds;
        predicted += seconds;
        if stage.assumed_speedup > 1.0 {
            accelerated += stage.cpu_seconds;
        }
        stages.push(StageEstimate {
            name: stage.name.clone(),
            cpu_seconds: stage.cpu_seconds,
            assumed_speedup: stage.assumed_speedup,
            estimated_seconds: seconds,
        });
    }
    if cpu <= 0.0 || !predicted.is_finite() || predicted <= 0.0 {
        return Err("GPU scenario requires positive finite measured CPU time".into());
    }
    Ok(Estimate {
        schema: "framelift-gpu-scenario-v1",
        label: scenario.label.clone(),
        measured_cpu_seconds: cpu,
        estimated_gpu_seconds: predicted,
        estimated_speedup: cpu / predicted,
        fixed_overhead_seconds: scenario.fixed_overhead_seconds,
        accelerated_cpu_fraction: accelerated / cpu,
        stages,
        hardware_benchmark: false,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn amdahl_model_charges_unaccelerated_work_and_overhead() {
        let estimate = estimate(&GpuScenario {
            label: "example".into(),
            fixed_overhead_seconds: 3.0,
            stages: vec![
                Stage {
                    name: "host".into(),
                    cpu_seconds: 60.0,
                    assumed_speedup: 1.0,
                },
                Stage {
                    name: "device".into(),
                    cpu_seconds: 40.0,
                    assumed_speedup: 10.0,
                },
            ],
        })
        .unwrap();
        assert_eq!(estimate.measured_cpu_seconds, 100.0);
        assert_eq!(estimate.estimated_gpu_seconds, 67.0);
        assert!((estimate.estimated_speedup - 100.0 / 67.0).abs() < 1e-12);
        assert_eq!(estimate.accelerated_cpu_fraction, 0.4);
        assert!(!estimate.hardware_benchmark);
    }

    #[test]
    fn invalid_or_claim_like_inputs_fail_closed() {
        for value in [f64::NAN, f64::INFINITY, -1.0] {
            assert!(
                estimate(&GpuScenario {
                    label: "bad".into(),
                    fixed_overhead_seconds: value,
                    stages: vec![Stage {
                        name: "x".into(),
                        cpu_seconds: 1.0,
                        assumed_speedup: 2.0,
                    }],
                })
                .is_err()
            );
        }
    }
}
