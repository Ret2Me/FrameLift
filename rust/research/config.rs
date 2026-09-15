//! Legacy configuration identity, reusing the native canonical-JSON encoder.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EffectiveConfig {
    pub protocol_id: String,
    pub decoder: String,
    pub decoder_version: String,
    pub seed: i64,
    pub config: Map<String, Value>,
}

impl EffectiveConfig {
    pub fn fingerprint(&self) -> Result<String, String> {
        for (name, value) in [
            ("protocol_id", &self.protocol_id),
            ("decoder", &self.decoder),
            ("decoder_version", &self.decoder_version),
        ] {
            super::nonempty(name, value)?;
        }
        let value = serde_json::to_value(self).map_err(|e| e.to_string())?;
        Ok(hex::encode(Sha256::digest(
            crate::ledger::canonical_json(&value)?.as_bytes(),
        )))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn every_effective_field_is_bound_and_map_order_is_irrelevant() {
        let base = json!({"protocol_id":"afsk1200_ax25","decoder":"direwolf","decoder_version":"1.7","seed":7,"config":{"baud":1200,"agc":"slow"}});
        let fingerprint = serde_json::from_value::<EffectiveConfig>(base.clone())
            .unwrap()
            .fingerprint()
            .unwrap();
        // Frozen output of canonical.config_hash from the Python reference.
        assert_eq!(
            fingerprint,
            "6f1d3809fed4498f55c4cfc070fb47b657e784abd39eecf4a88ab5c74d62905a"
        );
        for (key, value) in [
            ("protocol_id", json!("fsk9600_g3ruh")),
            ("decoder", json!("gr_satellites")),
            ("decoder_version", json!("1.8")),
            ("seed", json!(8)),
            ("config", json!({"baud":1201,"agc":"slow"})),
        ] {
            let mut changed = base.clone();
            changed[key] = value;
            assert_ne!(
                fingerprint,
                serde_json::from_value::<EffectiveConfig>(changed)
                    .unwrap()
                    .fingerprint()
                    .unwrap()
            );
        }
        let reordered: EffectiveConfig=serde_json::from_str(r#"{"config":{"agc":"slow","baud":1200},"seed":7,"decoder_version":"1.7","decoder":"direwolf","protocol_id":"afsk1200_ax25"}"#).unwrap();
        assert_eq!(fingerprint, reordered.fingerprint().unwrap());
    }

    #[test]
    fn invalid_effective_config_is_not_fingerprinted() {
        let unicode: EffectiveConfig = serde_json::from_value(json!({
            "protocol_id":"ax25","decoder":"Łódź","decoder_version":"1","seed":-1,
            "config":{"small":1e-7,"large":1e20,"negative_zero":-0.0,"utf8":"żółć 🚀"}
        }))
        .unwrap();
        assert_eq!(
            unicode.fingerprint().unwrap(),
            "3e3ca3d601b5d98f905c5d4d1901e9cfee85566c5a46f6cc058489ee2551869a"
        );
        let value =
            json!({"protocol_id":"a","decoder":"b","decoder_version":"c","seed":true,"config":{}});
        assert!(serde_json::from_value::<EffectiveConfig>(value).is_err());
        let mut value = EffectiveConfig {
            protocol_id: "a".into(),
            decoder: "b".into(),
            decoder_version: "c".into(),
            seed: -1,
            config: Map::new(),
        };
        assert!(value.fingerprint().is_ok());
        value.decoder.clear();
        assert!(value.fingerprint().is_err());
    }
}
