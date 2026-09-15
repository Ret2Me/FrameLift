//! Focused DSP/receiver tests against current source and existing protocol oracle.
pub use telemetry_yield_rs::{compute, input, protocol};
#[path = "../rust/dsp.rs"]
mod dsp;
#[path = "../rust/receiver.rs"]
mod receiver;
#[cfg(not(test))]
fn main() {
    println!("Run cargo test --example mm_runtime_repair_tests");
}
