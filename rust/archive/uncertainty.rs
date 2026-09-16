//! Descriptive paired bootstrap. Shared stations/pass groups are kept together.
//! These intervals do not establish that the resulting components are independent.
use serde_json::{Value, json};
use std::collections::BTreeMap;

pub(crate) struct Pair {
    pub station: u64,
    pub group: String,
    pub reference: usize,
    pub candidate: usize,
}

fn root(parents: &mut [usize], mut i: usize) -> usize {
    while parents[i] != i {
        parents[i] = parents[parents[i]];
        i = parents[i];
    }
    i
}

pub(crate) fn bootstrap(pairs: &[Pair]) -> Value {
    let mut parents: Vec<_> = (0..pairs.len()).collect();
    let mut stations = BTreeMap::new();
    let mut groups = BTreeMap::new();
    for (i, pair) in pairs.iter().enumerate() {
        for prior in [
            stations.insert(pair.station, i),
            groups.insert(&pair.group, i),
        ]
        .into_iter()
        .flatten()
        {
            let a = root(&mut parents, prior);
            let b = root(&mut parents, i);
            parents[b] = a;
        }
    }
    let mut totals = BTreeMap::<usize, (usize, usize)>::new();
    for (i, pair) in pairs.iter().enumerate() {
        let entry = totals.entry(root(&mut parents, i)).or_default();
        entry.0 += pair.reference;
        entry.1 += pair.candidate;
    }
    let components: Vec<_> = totals.into_values().collect();
    if components.len() < 2 {
        return json!({"component_count":components.len(),"interval":null,"reason":"fewer than two station/pass components"});
    }
    let mut random = 0x2b09_2026_0916_a11du64;
    let mut values = Vec::with_capacity(10_000);
    for _ in 0..10_000 {
        let (mut reference, mut candidate) = (0usize, 0usize);
        for _ in 0..components.len() {
            random ^= random << 13;
            random ^= random >> 7;
            random ^= random << 17;
            let (a, b) = components[(random % components.len() as u64) as usize];
            reference += a;
            candidate += b;
        }
        if reference != 0 {
            values.push(100.0 * (candidate as f64 - reference as f64) / reference as f64);
        }
    }
    values.sort_by(f64::total_cmp);
    let interval = if values.is_empty() {
        Value::Null
    } else {
        json!([
            values[(values.len() - 1) * 25 / 1000],
            values[(values.len() - 1) * 975 / 1000]
        ])
    };
    json!({"method":"paired connected station/pass component percentile bootstrap","replicates":10000,
        "component_count":components.len(),"nonzero_reference_replicates":values.len(),"seed":"2b0920260916a11d",
        "net_gain_percent_interval_95":interval,"scope":"descriptive, component independence unverified"})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn shared_stations_and_passes_form_transitive_components() {
        let pairs = [
            Pair {
                station: 1,
                group: "a".into(),
                reference: 10,
                candidate: 20,
            },
            Pair {
                station: 2,
                group: "a".into(),
                reference: 5,
                candidate: 10,
            },
            Pair {
                station: 2,
                group: "b".into(),
                reference: 5,
                candidate: 10,
            },
            Pair {
                station: 3,
                group: "c".into(),
                reference: 2,
                candidate: 4,
            },
        ];
        let result = bootstrap(&pairs);
        assert_eq!(result["component_count"], 2);
        assert_eq!(
            result["net_gain_percent_interval_95"],
            json!([100.0, 100.0])
        );
        assert_eq!(bootstrap(&pairs), result);
        assert!(bootstrap(&pairs[..3])["interval"].is_null());
    }
}
