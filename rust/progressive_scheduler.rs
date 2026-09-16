//! Causal, resumable ordering of an unchanged progressive task bank.
//!
//! Four-task barriers make feedback independent of worker completion order.
//! Costs are measured task wall time, not claimed CPU cost or calibrated
//! probabilities. Every fourth batch uses the original order for exploration.
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet, VecDeque};

const BATCH_SIZE: usize = 4;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize, clap::ValueEnum)]
#[serde(rename_all = "kebab-case")]
pub enum Policy {
    #[default]
    Fixed,
    MarginalYield,
}

impl Policy {
    pub fn identity(self) -> Value {
        match self {
            Self::Fixed => json!({"name":"fixed","version":1}),
            Self::MarginalYield => json!({
                "name":"marginal-yield","version":1,"batch_size":BATCH_SIZE,
                "quick_window_order":"breadth-first interval midpoints",
                "exploration_every_batches":4,"prior_payload_bytes":256,
                "prior_milliseconds":{"quick":100,"baseline-remainder":2000,
                    "early-nearest":200,"nearest":200,"blind":2000,
                    "early-multi":400,"multi-anchor":400},
                "window_weight":"1 + min(quick unique frames, 3)",
                "reward":"new received-FCS validated payload bytes; canonical batch order",
                "cost":"ceil(task elapsed milliseconds), minimum 1",
                "full_bank_pruning":false
            }),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Key {
    pub stage: String,
    pub window: usize,
}

#[derive(Clone, Debug)]
pub(super) struct Evidence {
    pub frames: BTreeSet<String>,
    pub elapsed_seconds: f64,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Decision {
    pub schema: String,
    pub session_sha256: String,
    pub serial: usize,
    pub phase: usize,
    pub tasks: Vec<Key>,
}

#[derive(Default)]
struct Learner {
    seen: BTreeSet<String>,
    // (new payload bytes, measured task wall milliseconds).
    totals: BTreeMap<String, (u128, u128)>,
    quick_counts: BTreeMap<usize, usize>,
}

impl Learner {
    fn observe(&mut self, key: &Key, evidence: &Evidence) -> Result<(), String> {
        if !evidence.elapsed_seconds.is_finite() || evidence.elapsed_seconds < 0.0 {
            return Err("invalid scheduler cost in task checkpoint".into());
        }
        if key.stage == "quick" {
            self.quick_counts.insert(key.window, evidence.frames.len());
        }
        let mut reward = 0;
        for frame in &evidence.frames {
            if self.seen.insert(frame.clone()) {
                // Caller validates hex, received FCS and AX.25 structure first.
                reward += (frame.len() / 2).saturating_sub(2) as u128;
            }
        }
        let cost = (evidence.elapsed_seconds * 1000.0).ceil();
        if cost > u64::MAX as f64 {
            return Err("scheduler cost overflow".into());
        }
        let total = self.totals.entry(key.stage.clone()).or_default();
        total.0 += reward;
        total.1 += (cost as u64).max(1) as u128;
        Ok(())
    }

    fn score(&self, key: &Key) -> (u128, u128) {
        let (reward, cost) = self.totals.get(&key.stage).copied().unwrap_or_default();
        let prior_cost = match key.stage.as_str() {
            "quick" => 100,
            "baseline-remainder" | "blind" => 2000,
            "early-multi" | "multi-anchor" => 400,
            _ => 200,
        };
        let weight = 1 + self
            .quick_counts
            .get(&key.window)
            .copied()
            .unwrap_or(0)
            .min(3);
        ((reward + 256) * weight as u128, cost + prior_cost)
    }
}

pub(super) struct Engine {
    session: String,
    phases: Vec<Vec<Key>>,
    pub phase: usize,
    serial: usize,
    learner: Learner,
}

fn coverage_order(windows: usize) -> Vec<usize> {
    let mut queue = VecDeque::from([(0, windows)]);
    let mut order = Vec::with_capacity(windows);
    while let Some((start, end)) = queue.pop_front() {
        if start == end {
            continue;
        }
        let middle = start + (end - start) / 2;
        order.push(middle);
        queue.push_back((start, middle));
        queue.push_back((middle + 1, end));
    }
    order
}

impl Engine {
    pub fn new(session: &str, phases: &[Vec<&str>], windows: usize) -> Self {
        Self {
            session: session.into(),
            phases: phases
                .iter()
                .enumerate()
                .map(|(phase, stages)| {
                    let order = if phase == 0 {
                        coverage_order(windows)
                    } else {
                        (0..windows).collect()
                    };
                    order
                        .into_iter()
                        .flat_map(|window| {
                            stages.iter().map(move |stage| Key {
                                stage: (*stage).into(),
                                window,
                            })
                        })
                        .collect()
                })
                .collect(),
            phase: 0,
            serial: 0,
            learner: Learner::default(),
        }
    }

    pub fn next(&self) -> Option<Decision> {
        let remaining = self.phases.get(self.phase)?;
        if remaining.is_empty() {
            return None;
        }
        let mut ranked: Vec<_> = remaining.iter().enumerate().collect();
        // The initial sweep covers the recording at multiple scales. Later
        // reserved exploration batches retain the established source order.
        if self.phase != 0 && !self.serial.is_multiple_of(4) {
            ranked.sort_by(|(a_index, a), (b_index, b)| {
                let (an, ad) = self.learner.score(a);
                let (bn, bd) = self.learner.score(b);
                // Floating division avoids integer cross-product overflow for
                // arbitrary retained task durations. Stable index breaks ties.
                (bn as f64 / bd as f64)
                    .total_cmp(&(an as f64 / ad as f64))
                    .then(a_index.cmp(b_index))
            });
        }
        Some(Decision {
            schema: "progressive-scheduler-decision-v1".into(),
            session_sha256: self.session.clone(),
            serial: self.serial,
            phase: self.phase,
            tasks: ranked
                .into_iter()
                .take(BATCH_SIZE)
                .map(|(_, k)| k.clone())
                .collect(),
        })
    }

    pub fn commit(
        &mut self,
        decision: &Decision,
        evidence: &BTreeMap<Key, Evidence>,
    ) -> Result<(), String> {
        if self.next().as_ref() != Some(decision) {
            return Err("scheduler journal is not the causal policy replay".into());
        }
        for key in &decision.tasks {
            self.learner
                .observe(key, evidence.get(key).ok_or("incomplete scheduler batch")?)?;
        }
        self.phases[self.phase].retain(|key| !decision.tasks.contains(key));
        if self.phases[self.phase].is_empty() {
            self.phase += 1;
        }
        self.serial += 1;
        Ok(())
    }
}

/// All committed tasks must belong to the causal journal, and only its last
/// batch may be incomplete. Unknown records never silently train the policy.
pub(super) fn audit(
    session: &str,
    phases: &[Vec<&str>],
    windows: usize,
    decisions: &[Decision],
    evidence: &BTreeMap<Key, Evidence>,
) -> Result<(), String> {
    let mut engine = Engine::new(session, phases, windows);
    let mut accounted = BTreeSet::new();
    for (index, decision) in decisions.iter().enumerate() {
        if engine.next().as_ref() != Some(decision) {
            return Err("scheduler journal changed or has a noncausal decision".into());
        }
        let complete = decision.tasks.iter().all(|key| evidence.contains_key(key));
        accounted.extend(
            decision
                .tasks
                .iter()
                .filter(|key| evidence.contains_key(*key))
                .cloned(),
        );
        if complete {
            engine.commit(decision, evidence)?;
        } else if index + 1 != decisions.len() {
            return Err("scheduler decision follows an incomplete batch".into());
        }
    }
    if accounted != evidence.keys().cloned().collect() {
        return Err("committed task is missing its scheduler decision".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(byte: u8) -> Evidence {
        Evidence {
            frames: BTreeSet::from([hex::encode([byte; 20])]),
            elapsed_seconds: 0.1,
        }
    }

    #[test]
    fn quick_sweep_spans_the_file_without_dropping_or_repeating_windows() {
        assert_eq!(&coverage_order(16)[..4], &[8, 4, 12, 2]);
        for n in 0..129 {
            let mut order = coverage_order(n);
            order.sort_unstable();
            assert_eq!(order, (0..n).collect::<Vec<_>>());
        }
    }

    #[test]
    fn duplicate_payloads_cannot_inflate_reward() {
        let mut learner = Learner::default();
        let key = Key {
            stage: "quick".into(),
            window: 0,
        };
        learner.observe(&key, &sample(1)).unwrap();
        learner
            .observe(&Key { window: 1, ..key }, &sample(1))
            .unwrap();
        assert_eq!(learner.totals["quick"], (18, 200));
    }

    #[test]
    fn causal_replay_covers_each_task_once_and_never_crosses_dependencies() {
        let phases = vec![
            vec!["quick"],
            vec!["blind", "early-nearest", "baseline-remainder"],
            vec!["nearest"],
        ];
        let mut engine = Engine::new("session", &phases, 7);
        let mut evidence = BTreeMap::new();
        let mut decisions = vec![];
        while let Some(decision) = engine.next() {
            for key in &decision.tasks {
                assert!(
                    evidence
                        .insert(key.clone(), sample(key.window as u8))
                        .is_none()
                );
            }
            engine.commit(&decision, &evidence).unwrap();
            decisions.push(decision);
            audit("session", &phases, 7, &decisions, &evidence).unwrap();
        }
        assert_eq!(evidence.len(), 35);
        let last = decisions.last().unwrap().tasks.last().unwrap().clone();
        evidence.remove(&last);
        audit("session", &phases, 7, &decisions, &evidence).unwrap();
        decisions[0].tasks.swap(0, 1);
        assert!(audit("session", &phases, 7, &decisions, &evidence).is_err());
    }

    #[test]
    fn observed_yield_changes_rank_but_exploration_keeps_unpromising_tasks() {
        let mut engine = Engine::new("x", &[vec!["quick"], vec!["blind", "nearest"]], 4);
        let first = engine.next().unwrap();
        let evidence = first
            .tasks
            .iter()
            .map(|k| (k.clone(), sample(k.window as u8)))
            .collect();
        engine.commit(&first, &evidence).unwrap();
        assert!(
            engine
                .next()
                .unwrap()
                .tasks
                .iter()
                .all(|k| k.stage == "nearest")
        );
        engine.learner.totals.insert("blind".into(), (100_000, 1));
        assert!(
            engine
                .next()
                .unwrap()
                .tasks
                .iter()
                .all(|k| k.stage == "blind")
        );
        engine.serial = 4;
        assert_eq!(engine.next().unwrap().tasks[0].stage, "blind");
        assert_eq!(engine.next().unwrap().tasks[1].stage, "nearest");
    }

    #[test]
    fn orphan_nonfinite_and_future_evidence_are_rejected() {
        let phases = vec![vec!["quick"], vec!["blind"]];
        let mut engine = Engine::new("x", &phases, 1);
        let first = engine.next().unwrap();
        let key = first.tasks[0].clone();
        let mut evidence = BTreeMap::from([(key.clone(), sample(1))]);
        assert!(audit("x", &phases, 1, &[], &evidence).is_err());
        evidence.get_mut(&key).unwrap().elapsed_seconds = f64::NAN;
        assert!(engine.commit(&first, &evidence).is_err());
        evidence.clear();
        assert!(audit("x", &phases, 1, &[first.clone(), first], &evidence).is_err());
    }
}
