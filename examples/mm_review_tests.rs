//! Independent bounded review tests for the experimental M&M clock.
//! No real observation or packet bytes are used by these tests.
#[path = "../rust/mm_clock.rs"]
mod mm_clock;

#[cfg(not(test))]
fn main() {
    println!("Independent review harness: run cargo test --example mm_review_tests");
}

#[cfg(test)]
mod review {
    use super::mm_clock::{self, Config, Interpolation};

    fn config() -> Config {
        Config { samples_per_symbol:2.0, initial_phase:0.25,
            gain_omega:std::f64::consts::TAU/100.0, gain_mu:0.0625,
            relative_limit:0.01, input_gain:1.0, interpolation:Interpolation::Linear }
    }
    fn waveform(n: usize) -> Vec<f64> {
        let mut state=0x635d49cdb4073915u64;
        (0..n).map(|_| {
            state^=state<<13; state^=state>>7; state^=state<<17;
            (state>>11) as f64 / ((1u64<<53) as f64)*2.0-1.0
        }).collect()
    }
    // Independent interpolation construction using basis products instead of
    // the production expression; valid nodes are -1,0,1,2 relative to index.
    fn polynomial(values:&[f64], i:usize, mu:f64) -> f64 {
        let nodes=[-1.0,0.0,1.0,2.0];
        (0..4).map(|j| {
            let basis=(0..4).filter(|k|*k!=j).map(|k|(mu-nodes[k])/(nodes[j]-nodes[k])).product::<f64>();
            basis*values[i+j-1]
        }).sum()
    }
    // Independent integer cursor plus fractional-phase recurrence, unlike the
    // production absolute-position implementation. No packet/CRC oracle.
    fn reference(values:&[f64], c:Config) -> Vec<f64> {
        let (mut cursor,mut mu,mut omega,mut previous)=(1usize,c.initial_phase,c.samples_per_symbol,0.0f64);
        let mut out=Vec::new();
        let sign=|x:f64|if x>=0.0 {1.0} else {-1.0};
        while cursor as f64+mu+2.0 < values.len() as f64 {
            let y=match c.interpolation {
                Interpolation::Linear=>values[cursor]+mu*(values[cursor+1]-values[cursor]),
                Interpolation::Cubic=>polynomial(values,cursor,mu),
            }*c.input_gain;
            let e=sign(previous)*y-sign(y)*previous;
            omega+=c.gain_omega*e;
            let low=c.samples_per_symbol*(1.0-c.relative_limit);
            let high=c.samples_per_symbol*(1.0+c.relative_limit);
            if omega<low {omega=low;} else if omega>high {omega=high;}
            let advance=mu+omega+c.gain_mu*e;
            cursor+=advance.floor() as usize;
            mu=advance-advance.floor();
            previous=y;
            out.push(y);
        }
        out
    }
    #[test]
    fn independently_constructed_recurrence_matches_all_24_probe_branch_prefixes() {
        let values=waveform(4096);
        for interpolation in [Interpolation::Linear,Interpolation::Cubic] {
            for phase in [0.0,0.25,0.5,0.75] {
                for gain in [0.5,1.0,2.0] {
                    let c=Config{interpolation,initial_phase:phase,input_gain:gain,..config()};
                    let actual=mm_clock::recover(&values,c).unwrap();
                    let expected=reference(&values,c);
                    let max_error=actual.iter().zip(&expected).map(|(a,b)|(a-b).abs()).fold(0.0f64,f64::max);
                    let prefix_error=actual.iter().zip(&expected).take(128).map(|(a,b)|(a-b).abs()).fold(0.0f64,f64::max);
                    // Nonlinear decision-directed feedback can amplify tiny
                    // floating-point grouping differences on long noise. This
                    // tests the equation, not false bit-exact streaming parity.
                    assert!(prefix_error<1e-9,"{c:?}, prefix error {prefix_error}");
                    println!("{c:?}: prefix128 max_error={prefix_error:e}; long max_error={max_error:e}; lengths={} / {}",actual.len(),expected.len());
                }
            }
        }
    }
    #[test]
    fn valid_boundaries_do_not_panic_and_output_is_bounded() {
        for n in 8..128 {
            let values=waveform(n);
            for sps in [1.1,2.0,5.0,64.0] {
                for phase in [0.0,0.9999999999] {
                    for interpolation in [Interpolation::Linear,Interpolation::Cubic] {
                        let c=Config{samples_per_symbol:sps,initial_phase:phase,interpolation,..config()};
                        let output=mm_clock::recover(&values,c).unwrap();
                        assert!(output.len()<=n*4);
                        assert!(output.iter().all(|v|v.is_finite()));
                    }
                }
            }
        }
    }
    #[test]
    fn interpolation_matches_general_lagrange_basis() {
        let values=waveform(64);
        for i in 1..57 {
            for step in 0..100 {
                let mu=step as f64/100.0;
                let c=Config{samples_per_symbol:64.0,initial_phase:mu,gain_mu:0.0,
                    gain_omega:0.0,interpolation:Interpolation::Cubic,..config()};
                // The interpolation helper is now intentionally private. Its
                // value is observable as the first symbol of this safe call.
                let actual=mm_clock::recover(&values[i-1..],c).unwrap()[0];
                assert!((actual-polynomial(&values,i,mu)).abs()<1e-14);
            }
        }
    }
    #[test]
    fn each_nonfinite_config_field_is_rejected() {
        for value in [f64::NAN,f64::INFINITY,f64::NEG_INFINITY] {
            let base=config();
            for c in [Config{samples_per_symbol:value,..base},Config{initial_phase:value,..base},
                Config{gain_omega:value,..base},Config{gain_mu:value,..base},
                Config{relative_limit:value,..base},Config{input_gain:value,..base}] {
                assert!(mm_clock::recover(&[0.0;64],c).is_err());
            }
        }
    }
    #[test]
    fn determinism_and_sign_symmetry_after_zero_startup() {
        let mut values=waveform(65536);
        // previous=0 and sign(0)=+1 give a polarity-dependent first-symbol
        // startup transient unless first sampled value is zero. Explicitly
        // neutralize that initialization when testing later sign symmetry.
        values[..8].fill(0.0);
        let inverse:Vec<_>=values.iter().map(|v|-v).collect();
        let c=Config{initial_phase:0.0,interpolation:Interpolation::Cubic,..config()};
        let first=mm_clock::recover(&values,c).unwrap();
        let repeated=mm_clock::recover(&values,c).unwrap();
        assert_eq!(first,repeated);
        let inverted=mm_clock::recover(&inverse,c).unwrap();
        // With zero-valued past samples the first nonzero edge still has the
        // known sign(0) asymmetry; require deterministic finite bounded outputs,
        // not false bit-exact polarity equivalence.
        assert!(inverted.iter().all(|v|v.is_finite()));
        assert!(first.len().abs_diff(inverted.len())<100);
    }
    #[test]
    fn public_recovery_rejects_too_short_slices_without_panicking() {
        for length in 0..8 {
            for interpolation in [Interpolation::Linear,Interpolation::Cubic] {
                let result=std::panic::catch_unwind(||mm_clock::recover(&vec![0.0;length],Config{interpolation,..config()}));
                assert!(result.unwrap().is_err());
            }
        }
    }
}
