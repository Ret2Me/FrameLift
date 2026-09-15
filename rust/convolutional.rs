//! Native soft-decision K=7, rate-1/2 convolutional decoder.
//! Positive inputs mean one. Generator order is explicit (not inverted).

/// Unknown initial state and unconstrained terminal state. Callers should keep
/// a prefix/suffix and discard their boundary decisions for continuous streams.
pub fn viterbi_k7(soft: &[f64], generators: [u8; 2]) -> Result<Vec<u8>, String> {
    if !soft.len().is_multiple_of(2) || soft.len() > 4_000_000 {
        return Err("Viterbi requires an even, bounded soft-bit block".into());
    }
    if generators.iter().any(|p| *p == 0 || *p > 127) || soft.iter().any(|x| !x.is_finite()) {
        return Err("invalid convolutional generators or nonfinite soft bits".into());
    }
    let signs: [[f64; 2]; 128] = std::array::from_fn(|register| {
        generators.map(|p| {
            if (register as u8 & p).count_ones() % 2 == 1 {
                1.0
            } else {
                -1.0
            }
        })
    });
    let scale = soft.iter().fold(0.0_f64, |a, b| a.max(b.abs())).max(1.0);
    let mut metric = [0.0; 64];
    let mut decisions = Vec::with_capacity(soft.len() / 2);
    for pair in soft.as_chunks::<2>().0 {
        let x = pair[0] / scale;
        let y = pair[1] / scale;
        let mut next = [0.0_f64; 64];
        let mut history = 0_u64;
        for state in 0..64 {
            let low = state >> 1;
            let a = metric[low] + x * signs[state][0] + y * signs[state][1];
            let b = metric[low | 32] + x * signs[state | 64][0] + y * signs[state | 64][1];
            if b > a {
                next[state] = b;
                history |= 1 << state;
            } else {
                next[state] = a;
            }
        }
        let best = next.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        for v in &mut next {
            *v -= best;
        }
        metric = next;
        decisions.push(history);
    }
    let mut state = 0;
    for candidate in 1..64 {
        if metric[candidate] > metric[state] {
            state = candidate;
        }
    }
    let mut bits = vec![0; decisions.len()];
    for i in (0..decisions.len()).rev() {
        bits[i] = (state & 1) as u8;
        state = (state >> 1) | (((decisions[i] >> state) & 1) as usize * 32);
    }
    Ok(bits)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn known_impulse_and_noisy_continuous_state() {
        // Independent hand-calculated impulse response of 0x4f/0x6d.
        let bits = [1, 0, 0, 0, 0, 0, 0];
        let encoded = [1, 1, 1, 0, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1];
        let soft: Vec<_> = encoded
            .iter()
            .map(|b| if *b == 1 { 1.0 } else { -1.0 })
            .collect();
        assert_eq!(viterbi_k7(&soft, [79, 109]).unwrap(), bits);
        let mut seed = 123456_u32;
        let input: Vec<u8> = (0..2048)
            .map(|_| {
                seed ^= seed << 13;
                seed ^= seed >> 17;
                seed ^= seed << 5;
                (seed & 1) as u8
            })
            .collect();
        let mut state = 37_u8;
        let mut noisy = Vec::new();
        for &bit in &input {
            state = (state << 1) | bit;
            for p in [79_u8, 109] {
                noisy.push(if (state & p).count_ones() % 2 == 1 {
                    1.0
                } else {
                    -1.0
                });
            }
        }
        for i in (77..noisy.len() - 100).step_by(97) {
            noisy[i] *= -0.15;
        }
        let output = viterbi_k7(&noisy, [79, 109]).unwrap();
        assert_eq!(&output[32..2000], &input[32..2000]);
    }
    #[test]
    fn rejects_invalid_input() {
        assert!(viterbi_k7(&[1.0], [79, 109]).is_err());
        assert!(viterbi_k7(&[f64::NAN, 0.0], [79, 109]).is_err());
    }
}
