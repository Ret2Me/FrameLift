//! Outcome-blind archive selection. Decoder outcomes are not ranking inputs.
use super::{Sealed, digest, read, reserve_space, transport};
use crate::input;
use chrono::DateTime;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;

const API: &str = "https://network.satnogs.org/api/observations/";

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Mission {
    pub name: String,
    pub norad: u64,
    pub baud: u32,
    pub deviation_hz: u32,
    pub profile_source: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Protocol {
    pub schema: String,
    pub start: String,
    pub end: String,
    pub rank_salt: String,
    pub missions: Vec<Mission>,
    pub target: usize,
    pub per_mission: usize,
    pub per_station: usize,
    pub minimum_missions: usize,
    pub minimum_stations: usize,
    pub pass_gap_seconds: i64,
    pub budgets_ms: Vec<u64>,
    pub threads: usize,
    pub max_pages_per_mission: usize,
}

impl Protocol {
    pub fn validate(&self) -> Result<(), String> {
        if self.schema != "framelift-archive-protocol-v1"
            || utc(&self.start)? >= utc(&self.end)?
            || self.rank_salt.len() < 16
            || self.target == 0
            || self.target > 10_000
            || self.per_mission == 0
            || self.per_station == 0
            || self.minimum_missions < 2
            || self.minimum_stations < 2
            || self.minimum_missions > self.missions.len()
            || !(600..=86400).contains(&self.pass_gap_seconds)
            || self.budgets_ms.is_empty()
            || self.budgets_ms.len() > 8
            || self
                .budgets_ms
                .iter()
                .any(|b| !(50..=86_400_000).contains(b))
            || self.budgets_ms.windows(2).any(|w| w[0] >= w[1])
            || !(1..=16).contains(&self.threads)
            || !(1..=1000).contains(&self.max_pages_per_mission)
        {
            return Err("invalid archive protocol bounds or schema".into());
        }
        let mut ids = BTreeSet::new();
        for m in &self.missions {
            if m.name.trim().is_empty()
                || m.norad == 0
                || m.baud != 9600
                || m.deviation_hz == 0
                || !m.profile_source.starts_with("https://")
                || !ids.insert(m.norad)
            {
                return Err(
                    "mission requires unique NORAD and documented FSK9600 AX.25 G3RUH profile"
                        .into(),
                );
            }
        }
        Ok(())
    }
}

pub fn utc(text: &str) -> Result<i64, String> {
    DateTime::parse_from_rfc3339(text)
        .map(|t| t.timestamp())
        .map_err(|e| e.to_string())
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Observation {
    pub id: u64,
    pub norad: u64,
    pub station: u64,
    pub start: String,
    pub end: String,
    pub audio_url: String,
    pub transmitter: String,
    pub mode: String,
    pub baud: u32,
    /// Metadata stratum only, never used for eligibility or rank.
    pub reported_signal: Option<bool>,
}

fn project(
    row: &Value,
    mission: &Mission,
    protocol: &Protocol,
) -> Result<Option<Observation>, String> {
    let integer = |name: &str| {
        row[name]
            .as_u64()
            .filter(|i| *i > 0)
            .ok_or_else(|| format!("missing {name}"))
    };
    let text = |name: &str| {
        row[name]
            .as_str()
            .filter(|s| !s.is_empty())
            .ok_or_else(|| format!("missing {name}"))
    };
    let id = integer("id")?;
    let norad = integer("norad_cat_id")?;
    let start = text("start")?;
    let end = text("end")?;
    if norad != mission.norad
        || utc(start)? < utc(&protocol.start)?
        || utc(start)? >= utc(&protocol.end)?
    {
        return Err("API returned observation outside frozen mission/date filters".into());
    }
    let duration = utc(end)? - utc(start)?;
    let mode = row["transmitter_mode"].as_str().unwrap_or("");
    if !(1..=1800).contains(&duration)
        || !matches!(mode, "FSK" | "GFSK" | "GMSK")
        || row["transmitter_baud"].as_f64() != Some(mission.baud as f64)
        || row["ground_station"].as_u64().is_none_or(|s| s == 0)
        || row["payload"].as_str().is_none_or(str::is_empty)
    {
        return Ok(None);
    }
    let audio_url = text("payload")?;
    if !transport::allowed_url(audio_url) {
        return Err("audio URL outside public archive allowlist".into());
    }
    Ok(Some(Observation {
        id,
        norad,
        station: integer("ground_station")?,
        start: start.into(),
        end: end.into(),
        audio_url: audio_url.into(),
        transmitter: text("transmitter")?.into(),
        mode: mode.into(),
        baud: mission.baud,
        reported_signal: match row["waterfall_status"].as_str() {
            Some("with-signal") => Some(true),
            Some("without-signal") => Some(false),
            _ => None,
        },
    }))
}

fn query(mission: &Mission, protocol: &Protocol) -> String {
    format!(
        "{API}?format=json&norad_cat_id={}&start={}&start__lt={}",
        mission.norad, protocol.start, protocol.end
    )
}

fn next_link(
    headers: &str,
    mission: &Mission,
    protocol: &Protocol,
) -> Result<Option<String>, String> {
    let mut next = None;
    for line in headers.lines() {
        if let Some((name, value)) = line.split_once(':') {
            if !name.eq_ignore_ascii_case("link") {
                continue;
            }
            for part in value.split(',') {
                let (url, relation) = part
                    .trim()
                    .strip_prefix('<')
                    .and_then(|s| s.split_once('>'))
                    .ok_or("invalid Link header")?;
                if !relation.split(';').any(|s| s.trim() == "rel=\"next\"") {
                    continue;
                }
                if next.is_some() {
                    return Err("multiple next links".into());
                }
                let query = url
                    .strip_prefix(&format!("{API}?"))
                    .ok_or("pagination left exact HTTPS API")?;
                let mut fields = BTreeMap::new();
                for pair in query.split('&') {
                    let (key, value) = pair.split_once('=').ok_or("invalid pagination query")?;
                    if !["format", "norad_cat_id", "start", "start__lt", "cursor"].contains(&key)
                        || fields.insert(key, value).is_some()
                    {
                        return Err("unexpected pagination filter".into());
                    }
                }
                // DRF quotes time separators but does not otherwise transform these filters.
                let decoded = |key| {
                    fields
                        .get(key)
                        .map(|s| s.replace("%3A", ":").replace("%3a", ":"))
                };
                if decoded("format").as_deref() != Some("json")
                    || decoded("norad_cat_id") != Some(mission.norad.to_string())
                    || decoded("start") != Some(protocol.start.clone())
                    || decoded("start__lt") != Some(protocol.end.clone())
                    || fields.get("cursor").is_none_or(|v| v.is_empty())
                    || url.contains(['#', '\\', '\n', '\r'])
                {
                    return Err("pagination changed frozen filters".into());
                }
                next = Some(url.to_string());
            }
        }
    }
    Ok(next)
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Catalogue {
    pub schema: String,
    pub protocol_sha256: String,
    pub protocol: Protocol,
    pub pagination_complete: bool,
    pub observations: Vec<Observation>,
    pub pages: Vec<input::Identity>,
    pub rejected_metadata_rows: usize,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub amendment: Option<Value>,
}

pub fn acquire_metadata(protocol_path: &Path, out: &Path) -> Result<Value, String> {
    let protocol: Protocol = read(protocol_path)?;
    protocol.validate()?;
    let root = input::existing_new_dir(out)?;
    input::write_json_new(&root.join("protocol.json"), &protocol)?;
    let mut rows = BTreeMap::new();
    let mut pages = Vec::new();
    let mut rejected = 0;
    for mission in &protocol.missions {
        let mut url = Some(query(mission, &protocol));
        let mut visited = BTreeSet::new();
        for index in 0..protocol.max_pages_per_mission {
            let Some(current) = url.take() else {
                break;
            };
            if !visited.insert(current.clone()) {
                return Err("pagination cycle".into());
            }
            reserve_space(&root, 8 * 1024 * 1024)?;
            let path = root.join(format!("{}-{index:04}.json", mission.norad));
            transport::fetch(&current, &path, 4 * 1024 * 1024)?;
            let receipt: Value =
                read(&root.join(format!("{}-{index:04}.json.http.json", mission.norad)))?;
            let last = receipt["hops"]
                .as_array()
                .and_then(|a| a.last())
                .ok_or("missing HTTP receipt")?;
            let header = root.join(format!(
                "{}-{index:04}.json.hop-{}.try-{}.headers",
                mission.norad, last["hop"], last["attempt"]
            ));
            let headers = String::from_utf8(crate::research::input::read_regular_bounded(
                &header,
                256 * 1024,
            )?)
            .map_err(|e| e.to_string())?;
            url = next_link(&headers, mission, &protocol)?;
            let raw: Vec<Value> = read(&path)?;
            for row in raw {
                match project(&row, mission, &protocol)? {
                    Some(row) => {
                        if let Some(old) = rows.insert(row.id, row.clone())
                            && old != row
                        {
                            return Err("inconsistent duplicate API observation".into());
                        }
                    }
                    None => rejected += 1,
                }
            }
            pages.push(input::identity(&path)?);
        }
        if url.is_some() {
            return Err(
                "page bound reached; partial catalogue cannot be frozen as complete".into(),
            );
        }
    }
    let count = rows.len();
    Sealed::write(
        &root.join("catalogue.json"),
        Catalogue {
            schema: "framelift-archive-catalogue-v1".into(),
            protocol_sha256: digest(&protocol)?,
            protocol,
            pagination_complete: true,
            observations: rows.into_values().collect(),
            pages,
            rejected_metadata_rows: rejected,
            amendment: None,
        },
    )?;
    Ok(
        json!({"catalogue":root.join("catalogue.json"),"eligible_rows":count,"waveforms_downloaded":0}),
    )
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Exposure {
    pub observation_ids: BTreeSet<u64>,
    pub stations: BTreeSet<u64>,
    pub excluded_mission_days: BTreeSet<String>,
    pub evidence: Vec<input::Identity>,
    pub inventory_scope: String,
    pub complete_inventory_attested: bool,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Cohort {
    pub schema: String,
    pub protocol: Protocol,
    pub catalogue_sha256: String,
    pub exclusions: Exposure,
    pub observations: Vec<Observation>,
    pub eligible_count: usize,
    pub excluded_count: usize,
    pub group_ids: BTreeMap<u64, String>,
    pub independently_selected_within_inventory_scope: bool,
}

impl Cohort {
    pub fn validate(&self) -> Result<(), String> {
        self.protocol.validate()?;
        if self.schema != "framelift-archive-cohort-v1"
            || self.observations.len() != self.protocol.target
            || self.catalogue_sha256.len() != 64
            || self.group_ids.len() != self.observations.len()
        {
            return Err("invalid cohort schema, size or catalogue binding".into());
        }
        let mut ids = BTreeSet::new();
        let mut groups = BTreeSet::new();
        let mut missions = BTreeMap::<u64, usize>::new();
        let mut stations = BTreeMap::<u64, usize>::new();
        for row in &self.observations {
            let start = utc(&row.start)?;
            let end = utc(&row.end)?;
            let day = format!(
                "{}:{}",
                row.norad,
                chrono::DateTime::from_timestamp(start, 0)
                    .ok_or("date out of range")?
                    .format("%Y-%m-%d")
            );
            if row.id == 0
                || row.station == 0
                || !ids.insert(row.id)
                || row.baud != 9600
                || !self.protocol.missions.iter().any(|m| m.norad == row.norad)
                || start < utc(&self.protocol.start)?
                || start >= utc(&self.protocol.end)?
                || !(1..=1800).contains(&(end - start))
                || !transport::allowed_url(&row.audio_url)
                || !matches!(row.mode.as_str(), "FSK" | "GFSK" | "GMSK")
                || row.transmitter.trim().is_empty()
                || self.exclusions.observation_ids.contains(&row.id)
                || self.exclusions.excluded_mission_days.contains(&day)
            {
                return Err("invalid, duplicate or exposed cohort observation".into());
            }
            let group = self
                .group_ids
                .get(&row.id)
                .filter(|s| !s.is_empty())
                .ok_or("cohort group missing")?;
            if !groups.insert(group) {
                return Err("more than one selected recording in a pass group".into());
            }
            *missions.entry(row.norad).or_default() += 1;
            *stations.entry(row.station).or_default() += 1;
        }
        if missions.len() < self.protocol.minimum_missions
            || stations.len() < self.protocol.minimum_stations
            || missions.values().any(|n| *n > self.protocol.per_mission)
            || stations.values().any(|n| *n > self.protocol.per_station)
        {
            return Err("cohort diversity limits violated".into());
        }
        Ok(())
    }
}

/// A declared metadata-feasibility amendment, never an implicit relaxed gate.
/// Acquisition domain, ranking salt and decoder/resource policy cannot change.
pub fn amend(
    catalogue: &Path,
    protocol: &Path,
    reason: &str,
    output: &Path,
) -> Result<Value, String> {
    let mut original = Sealed::<Catalogue>::read(catalogue)?;
    if !original.content.pagination_complete
        || original.content.amendment.is_some()
        || reason.trim().len() < 20
        || reason.len() > 4096
    {
        return Err(
            "amendment requires complete original catalogue and an explicit bounded reason".into(),
        );
    }
    let revised: Protocol = read(protocol)?;
    revised.validate()?;
    let mut expected = original.content.protocol.clone();
    expected.target = revised.target;
    expected.per_mission = revised.per_mission;
    expected.per_station = revised.per_station;
    expected.minimum_missions = revised.minimum_missions;
    expected.minimum_stations = revised.minimum_stations;
    if digest(&expected)? != digest(&revised)? {
        return Err("amendment may change sample-size/diversity limits only, not input domain, salt, grouping or receiver policy".into());
    }
    let original_protocol = original.content.protocol_sha256.clone();
    original.content.protocol_sha256 = digest(&revised)?;
    original.content.protocol = revised;
    original.content.amendment = Some(json!({"parent_catalogue_sha256":original.sha256,
        "original_protocol_sha256":original_protocol,"declared_utc":transport::utc(),"reason":reason,
        "scope":"metadata feasibility only; caller must declare before waveform-outcome access"}));
    let sealed = Sealed::write(output, original.content)?;
    Ok(
        json!({"catalogue":output,"sha256":sealed.sha256,"original_preserved":true,"waveforms_downloaded":0}),
    )
}

pub fn select(catalogue: &Sealed<Catalogue>, exposures: Exposure) -> Result<Cohort, String> {
    let protocol = &catalogue.content.protocol;
    protocol.validate()?;
    if catalogue.content.schema != "framelift-archive-catalogue-v1"
        || !catalogue.content.pagination_complete
        || digest(protocol)? != catalogue.content.protocol_sha256
    {
        return Err("selection requires a complete protocol-bound catalogue".into());
    }
    if exposures.inventory_scope.trim().is_empty() || exposures.evidence.is_empty() {
        return Err("selection requires an explicit, evidenced exposure inventory".into());
    }
    let mut ids = BTreeSet::new();
    let mut candidates = Vec::new();
    let mut exposed_ids = BTreeSet::new();
    for row in &catalogue.content.observations {
        if !ids.insert(row.id) {
            return Err("duplicate catalogue ID".into());
        }
        let start = utc(&row.start)?;
        let end = utc(&row.end)?;
        if row.id == 0
            || row.station == 0
            || row.baud != 9600
            || row.transmitter.trim().is_empty()
            || !matches!(row.mode.as_str(), "FSK" | "GFSK" | "GMSK")
            || !protocol
                .missions
                .iter()
                .any(|m| m.norad == row.norad && m.baud == row.baud)
            || start < utc(&protocol.start)?
            || start >= utc(&protocol.end)?
            || !(1..=1800).contains(&(end - start))
            || !transport::allowed_url(&row.audio_url)
        {
            return Err("invalid projected observation".into());
        }
        let day = format!(
            "{}:{}",
            row.norad,
            chrono::DateTime::from_timestamp(start, 0)
                .ok_or("date out of range")?
                .format("%Y-%m-%d")
        );
        if exposures.observation_ids.contains(&row.id)
            || exposures.excluded_mission_days.contains(&day)
        {
            exposed_ids.insert(row.id);
        }
        candidates.push(row.clone());
    }
    // Connected temporal groups, not fixed UTC bins: adjacent observations
    // cannot land either side of a bin boundary and masquerade as independent.
    candidates.sort_by_key(|r| (r.norad, utc(&r.start).unwrap_or(i64::MIN), r.id));
    let mut groups = BTreeMap::new();
    let mut previous: Option<(u64, i64)> = None;
    let mut group = String::new();
    for row in &candidates {
        let time = utc(&row.start)?;
        if previous
            .is_none_or(|(norad, end)| norad != row.norad || time - end > protocol.pass_gap_seconds)
        {
            group = format!("{}:{}", row.norad, row.id);
        }
        previous = Some((
            row.norad,
            previous
                .filter(|(n, _)| *n == row.norad)
                .map_or(utc(&row.end)?, |(_, e)| e.max(utc(&row.end).unwrap())),
        ));
        groups.insert(row.id, group.clone());
    }
    // Build components before exclusion: removing a known-exposed observation
    // must not split its neighbors into apparently untouched passes.
    let exposed_groups: BTreeSet<_> = exposed_ids.iter().map(|id| groups[id].clone()).collect();
    let before = candidates.len();
    candidates.retain(|row| !exposed_groups.contains(&groups[&row.id]));
    let excluded = before - candidates.len();
    let mut ranked: Vec<_> = candidates
        .iter()
        .map(|r| {
            (
                digest(&(protocol.rank_salt.as_str(), r.id)).unwrap(),
                r.clone(),
            )
        })
        .collect();
    ranked.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.id.cmp(&b.1.id)));
    let mut missions = BTreeMap::<u64, usize>::new();
    let mut stations = BTreeMap::<u64, usize>::new();
    let mut selected_groups = BTreeSet::new();
    let mut observations = Vec::new();
    for (_, row) in ranked {
        if observations.len() == protocol.target {
            break;
        }
        if missions.get(&row.norad).copied().unwrap_or(0) >= protocol.per_mission
            || stations.get(&row.station).copied().unwrap_or(0) >= protocol.per_station
            || selected_groups.contains(&groups[&row.id])
        {
            continue;
        }
        *missions.entry(row.norad).or_default() += 1;
        *stations.entry(row.station).or_default() += 1;
        selected_groups.insert(groups[&row.id].clone());
        observations.push(row);
    }
    if observations.len() != protocol.target
        || missions.len() < protocol.minimum_missions
        || stations.len() < protocol.minimum_stations
    {
        return Err(format!(
            "cohort coverage gate failed: {}/{} observations, {} missions, {} stations; no outcome-based replacement",
            observations.len(),
            protocol.target,
            missions.len(),
            stations.len()
        ));
    }
    groups.retain(|id, _| observations.iter().any(|r| r.id == *id));
    Ok(Cohort {
        schema: "framelift-archive-cohort-v1".into(),
        protocol: protocol.clone(),
        catalogue_sha256: catalogue.sha256.clone(),
        independently_selected_within_inventory_scope: exposures.complete_inventory_attested,
        exclusions: exposures,
        observations,
        eligible_count: candidates.len(),
        excluded_count: excluded,
        group_ids: groups,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> (Sealed<Catalogue>, Exposure) {
        let mut protocol: Protocol = serde_json::from_str(include_str!(
            "../../config/archive-qualification-20260916.json"
        ))
        .unwrap();
        protocol.target = 4;
        protocol.per_mission = 2;
        protocol.minimum_missions = 2;
        protocol.minimum_stations = 2;
        protocol.missions.truncate(2);
        let mut observations = Vec::new();
        for mission in &protocol.missions {
            for i in 0..8 {
                observations.push(Observation {
                    id: mission.norad * 1000 + i,
                    norad: mission.norad,
                    station: 1 + i,
                    start: format!("2026-07-{:02}T01:00:00Z", 8 + i),
                    end: format!("2026-07-{:02}T01:10:00Z", 8 + i),
                    audio_url: "https://network.satnogs.org/fixture.ogg".into(),
                    transmitter: "fixture".into(),
                    mode: "GMSK".into(),
                    baud: 9600,
                    reported_signal: None,
                });
            }
        }
        let content = Catalogue {
            schema: "framelift-archive-catalogue-v1".into(),
            protocol_sha256: digest(&protocol).unwrap(),
            protocol,
            pagination_complete: true,
            observations,
            pages: vec![],
            rejected_metadata_rows: 0,
            amendment: None,
        };
        let catalogue = Sealed {
            sha256: digest(&content).unwrap(),
            content,
        };
        let exposures = Exposure {
            inventory_scope: "test fixture".into(),
            evidence: vec![input::Identity {
                path: "fixture".into(),
                sha256: "a".repeat(64),
                bytes: 0,
            }],
            ..Default::default()
        };
        (catalogue, exposures)
    }

    #[test]
    fn rank_is_outcome_blind_and_order_invariant() {
        let (mut catalogue, exposures) = fixture();
        let a = select(&catalogue, exposures.clone()).unwrap();
        catalogue.content.observations.reverse();
        for row in &mut catalogue.content.observations {
            row.reported_signal = Some(true);
        }
        let b = select(&catalogue, exposures).unwrap();
        assert_eq!(
            a.observations.iter().map(|r| r.id).collect::<Vec<_>>(),
            b.observations.iter().map(|r| r.id).collect::<Vec<_>>()
        );
        assert_eq!(a.observations.len(), 4);
        assert!(!a.independently_selected_within_inventory_scope);
    }

    #[test]
    fn partial_duplicate_and_malformed_catalogues_fail_closed() {
        let (mut catalogue, exposures) = fixture();
        catalogue.content.pagination_complete = false;
        assert!(select(&catalogue, exposures.clone()).is_err());
        catalogue.content.pagination_complete = true;
        catalogue
            .content
            .observations
            .push(catalogue.content.observations[0].clone());
        assert!(select(&catalogue, exposures.clone()).is_err());
        catalogue.content.observations.pop();
        catalogue.content.observations[0].start = "é".into();
        assert!(select(&catalogue, exposures).is_err());
    }

    #[test]
    fn frozen_cohort_rechecks_diversity_and_exposure() {
        let (catalogue, exposures) = fixture();
        let mut cohort = select(&catalogue, exposures).unwrap();
        assert!(cohort.validate().is_ok());
        let id = cohort.observations[0].id;
        cohort.exclusions.observation_ids.insert(id);
        assert!(cohort.validate().is_err());
        cohort.exclusions.observation_ids.clear();
        cohort.protocol.minimum_stations = cohort.observations.len() + 1;
        assert!(cohort.validate().is_err());
    }

    #[test]
    fn one_exposed_record_excludes_its_whole_connected_pass() {
        let (mut catalogue, mut exposures) = fixture();
        let mut adjacent = catalogue.content.observations[0].clone();
        let exposed = adjacent.id;
        adjacent.id += 100;
        adjacent.start = "2026-07-08T01:20:00Z".into();
        adjacent.end = "2026-07-08T01:25:00Z".into();
        let neighbor = adjacent.id;
        catalogue.content.observations.push(adjacent);
        exposures.observation_ids.insert(exposed);
        let selected = select(&catalogue, exposures).unwrap();
        assert_eq!(selected.excluded_count, 2);
        assert!(
            !selected
                .observations
                .iter()
                .any(|r| r.id == neighbor || r.id == exposed)
        );
    }

    #[test]
    fn station_field_projection_and_outcome_omission_are_explicit() {
        let (catalogue, _) = fixture();
        let p = &catalogue.content.protocol;
        let m = &p.missions[0];
        let mut raw = json!({"id":1,"norad_cat_id":m.norad,"ground_station":123,"station_id":999,
            "start":"2026-07-08T01:00:00Z","end":"2026-07-08T01:05:00Z","transmitter":"x",
            "transmitter_mode":"GFSK","transmitter_baud":9600,"payload":"https://network.satnogs.org/x.ogg","demoddata":["secret outcome"]});
        let a = project(&raw, m, p).unwrap().unwrap();
        assert_eq!(a.station, 123);
        raw["demoddata"] = json!([]);
        raw["status"] = json!("bad");
        assert_eq!(project(&raw, m, p).unwrap().unwrap(), a);
        raw["ground_station"] = Value::Null;
        assert!(project(&raw, m, p).unwrap().is_none());
    }

    #[test]
    fn pagination_cannot_add_outcome_filters_or_leave_endpoint() {
        let (catalogue, _) = fixture();
        let p = &catalogue.content.protocol;
        let m = &p.missions[0];
        let url = format!("{}&cursor=abc%3D", query(m, p));
        let header = format!("Link: <{url}>; rel=\"next\"\r\n");
        assert_eq!(next_link(&header, m, p).unwrap(), Some(url));
        assert!(next_link(&header.replace("cursor=", "status=good&cursor="), m, p).is_err());
        assert!(next_link(&header.replace("network.satnogs.org", "evil.example"), m, p).is_err());
    }
}

pub fn freeze(catalogue: &Path, exposures: &Path, output: &Path) -> Result<Value, String> {
    let catalogue = Sealed::<Catalogue>::read(catalogue)?;
    let exclusions: Exposure = read(exposures)?;
    for evidence in &exclusions.evidence {
        let actual = input::identity(Path::new(&evidence.path))?;
        if actual.sha256 != evidence.sha256 || actual.bytes != evidence.bytes {
            return Err("exposure evidence identity changed".into());
        }
    }
    let cohort = select(&catalogue, exclusions)?;
    let count = cohort.observations.len();
    let sealed = Sealed::write(output, cohort)?;
    Ok(json!({"cohort":output,"sha256":sealed.sha256,"count":count,"waveforms_downloaded":0}))
}

pub fn acquire_waveforms(cohort: &Path, output: &Path) -> Result<Value, String> {
    let cohort = Sealed::<Cohort>::read(cohort)?;
    cohort.content.validate()?;
    let root = input::existing_new_dir(output)?;
    let mut receipts = Vec::new();
    for row in &cohort.content.observations {
        reserve_space(&root, 128 * 1024 * 1024)?;
        let dir = root.join(row.id.to_string());
        fs::create_dir(&dir).map_err(|e| e.to_string())?;
        let result = transport::fetch(&row.audio_url, &dir.join("source.ogg"), 64 * 1024 * 1024);
        let receipt = json!({"id":row.id,"cohort_sha256":cohort.sha256,"success":result.is_ok(),"download":result.as_ref().ok(),"error":result.as_ref().err()});
        input::write_json_new(&dir.join("acquisition.json"), &receipt)?;
        receipts.push(receipt);
    }
    let result = json!({"schema":"framelift-archive-acquisition-v1","cohort_sha256":cohort.sha256,"observations":receipts});
    input::write_json_new(&root.join("acquisition.json"), &result)?;
    Ok(result)
}
