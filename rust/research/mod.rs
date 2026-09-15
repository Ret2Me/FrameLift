//! Offline research utilities. No network access or Python subprocesses.
//!
//! A reported checksum flag is input evidence, not an independent integrity
//! check. The packet auditor performs its own IPv4 and ICMP checksum checks.

pub mod config;
pub mod events;
pub mod input;
pub mod metrics;
pub mod pcap;
pub mod selection;
pub mod store;

use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

fn nonempty(name: &str, value: &str) -> Result<(), String> {
    if value.trim().is_empty() {
        Err(format!("{name} must be a non-empty string"))
    } else {
        Ok(())
    }
}

/// One event may appear repeatedly, but never in different dataset splits.
pub fn assert_no_event_leakage(rows: &[(String, String)]) -> Result<(), String> {
    let mut by_event: BTreeMap<&str, BTreeSet<&str>> = BTreeMap::new();
    for (event, split) in rows {
        nonempty("event", event)?;
        nonempty("split", split)?;
        by_event.entry(event).or_default().insert(split);
    }
    let leaked: Vec<_> = by_event
        .into_iter()
        .filter(|(_, splits)| splits.len() > 1)
        .map(|(event, splits)| format!("{event}: {splits:?}"))
        .collect();
    if leaked.is_empty() {
        Ok(())
    } else {
        Err(format!(
            "transmission_event_id leakage: {}",
            leaked.join(", ")
        ))
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct LicenseMetadata {
    pub source_id: String,
    pub license_spdx: String,
    pub license_verified: bool,
    #[serde(default)]
    pub publication_reviewed: bool,
}

impl LicenseMetadata {
    pub fn validate(&self) -> Result<(), String> {
        nonempty("source_id", &self.source_id)?;
        nonempty("license_spdx", &self.license_spdx)
    }
}

pub fn unverified_source_ids(licenses: &[LicenseMetadata]) -> Result<BTreeSet<String>, String> {
    for item in licenses {
        item.validate()?;
    }
    Ok(licenses
        .iter()
        .filter(|item| !item.license_verified)
        .map(|item| item.source_id.clone())
        .collect())
}

pub fn unpublishable_source_ids(licenses: &[LicenseMetadata]) -> Result<BTreeSet<String>, String> {
    for item in licenses {
        item.validate()?;
    }
    Ok(licenses
        .iter()
        .filter(|item| !item.license_verified || !item.publication_reviewed)
        .map(|item| item.source_id.clone())
        .collect())
}

pub fn assert_export_allowed(licenses: &[LicenseMetadata]) -> Result<(), String> {
    if licenses.is_empty() {
        return Err("export blocked: provenance set is empty".into());
    }
    let blocked = unpublishable_source_ids(licenses)?;
    if blocked.is_empty() {
        Ok(())
    } else {
        Err(format!(
            "export blocked by unverified or unreviewed licences: {}",
            blocked.into_iter().collect::<Vec<_>>().join(", ")
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repeated_event_is_allowed_but_cross_split_leakage_is_not() {
        assert!(assert_no_event_leakage(&[]).is_ok());
        assert!(
            assert_no_event_leakage(&[("a".into(), "train".into()), ("a".into(), "train".into())])
                .is_ok()
        );
        let error =
            assert_no_event_leakage(&[("a".into(), "train".into()), ("a".into(), "test".into())])
                .unwrap_err();
        assert!(error.contains("a") && error.contains("test") && error.contains("train"));
        assert!(assert_no_event_leakage(&[(" ".into(), "train".into())]).is_err());
    }

    #[test]
    fn export_requires_nonempty_verified_reviewed_provenance() {
        let good = LicenseMetadata {
            source_id: "one".into(),
            license_spdx: "Unlicense".into(),
            license_verified: true,
            publication_reviewed: true,
        };
        assert!(assert_export_allowed(std::slice::from_ref(&good)).is_ok());
        assert!(assert_export_allowed(&[]).is_err());
        let mut bad = good.clone();
        bad.publication_reviewed = false;
        assert!(assert_export_allowed(&[good, bad.clone()]).is_err());
        assert!(
            unverified_source_ids(std::slice::from_ref(&bad))
                .unwrap()
                .is_empty()
        );
        bad.license_verified = false;
        assert_eq!(
            unverified_source_ids(&[bad.clone(), bad]).unwrap(),
            BTreeSet::from(["one".into()])
        );
    }

    #[test]
    fn license_metadata_rejects_invalid_types_and_identity() {
        assert!(
            serde_json::from_value::<LicenseMetadata>(
                serde_json::json!({"source_id":"a","license_spdx":"MIT","license_verified":1})
            )
            .is_err()
        );
        let item: LicenseMetadata = serde_json::from_value(
            serde_json::json!({"source_id":"","license_spdx":"MIT","license_verified":true}),
        )
        .unwrap();
        assert!(item.validate().is_err());
        assert!(!item.publication_reviewed);
    }
}
