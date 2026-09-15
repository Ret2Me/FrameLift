//! Lightweight, self-contained SVG figures for the README. No network fonts or scripts.
//! Called only after the paper bundle has passed checksum and arithmetic verification.

use super::{Result, count};
use serde_json::Value;
use std::fs;
use std::path::Path;

const INK: &str = "#152e43";
const MUTED: &str = "#526879";
const TEAL: &str = "#087e70";
const GREY: &str = "#7b8e9c";
const RUST: &str = "#a7462d";
const RULE: &str = "#dce5ea";

struct Metrics {
    observations: u64,
    ours: u64,
    baseline: u64,
    added: u64,
    lost: u64,
    added_bytes: u64,
    lost_bytes: u64,
    with_gain: u64,
}

impl Metrics {
    fn parse(v: &Value) -> Result<Self> {
        let m = Self {
            observations: count(&v["observations"])?,
            ours: count(&v["ours"])?,
            baseline: count(&v["baseline"])?,
            added: count(&v["added"])?,
            lost: count(&v["lost"])?,
            added_bytes: count(&v["added_pdu_bytes"])?,
            lost_bytes: count(&v["lost_pdu_bytes"])?,
            with_gain: count(&v["observations_with_gain"])?,
        };
        if m.observations == 0 || m.baseline == 0 || m.with_gain > m.observations {
            return Err("figure denominator or observation count is invalid".into());
        }
        let recovered_total = m.ours.checked_add(m.lost).ok_or("packet count overflow")?;
        let reference_total = m
            .baseline
            .checked_add(m.added)
            .ok_or("packet count overflow")?;
        if recovered_total != reference_total || m.ours < m.baseline || m.added_bytes < m.lost_bytes
        {
            return Err("this recovery figure requires consistent, nonnegative net gains".into());
        }
        Ok(m)
    }

    fn percent(&self) -> f64 {
        100.0 * (self.ours - self.baseline) as f64 / self.baseline as f64
    }
    fn gain_frequency(&self) -> f64 {
        100.0 * self.with_gain as f64 / self.observations as f64
    }
}

fn grouped(n: u64) -> String {
    let s = n.to_string();
    let mut out = String::new();
    for (i, c) in s.chars().enumerate() {
        if i != 0 && (s.len() - i).is_multiple_of(3) {
            out.push(',');
        }
        out.push(c);
    }
    out
}

fn xml(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

struct Svg(String);

impl Svg {
    fn new(height: u32, title: &str, description: &str) -> Self {
        Self(format!(
            r##"<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}" role="img" aria-labelledby="title desc">
<title id="title">{}</title>
<desc id="desc">{}</desc>
<rect x="0.5" y="0.5" width="1199" height="{}" rx="8" fill="#ffffff" stroke="{RULE}"/>
<g font-family="Arial, Helvetica, sans-serif" style="font-variant-numeric:tabular-nums">
"##,
            xml(title),
            xml(description),
            height - 1
        ))
    }

    fn text(&mut self, x: f64, y: f64, size: u32, fill: &str, weight: u32, content: &str) {
        self.0.push_str(&format!("<text x=\"{x}\" y=\"{y}\" font-size=\"{size}\" fill=\"{fill}\" font-weight=\"{weight}\">{}</text>\n",xml(content)));
    }

    fn rect(&mut self, x: f64, y: f64, width: f64, height: f64, fill: &str) {
        self.0.push_str(&format!(
            "<rect x=\"{x}\" y=\"{y}\" width=\"{width:.3}\" height=\"{height}\" fill=\"{fill}\"/>\n"
        ));
    }

    fn line(&mut self, x1: f64, y1: f64, x2: f64, y2: f64) {
        self.0.push_str(&format!(
            "<line x1=\"{x1}\" y1=\"{y1}\" x2=\"{x2}\" y2=\"{y2}\" stroke=\"{RULE}\"/>\n"
        ));
    }

    fn header(&mut self, label: &str, title: &str, subtitle: &str) {
        self.rect(48.0, 32.0, 5.0, 22.0, TEAL);
        self.text(66.0, 50.0, 19, TEAL, 700, label);
        self.text(48.0, 105.0, 36, INK, 700, title);
        self.text(48.0, 144.0, 22, MUTED, 400, subtitle);
    }

    fn bar(&mut self, label: &str, value: u64, maximum: u64, y: f64, color: &str) {
        self.text(
            48.0,
            y + 25.0,
            24,
            INK,
            if color == TEAL { 700 } else { 400 },
            label,
        );
        self.rect(310.0, y, 710.0, 36.0, "#f2f5f7");
        self.0.push_str(&format!("<rect data-role=\"value-bar\" data-value=\"{value}\" data-scale-max=\"{maximum}\" x=\"310\" y=\"{y}\" width=\"{:.3}\" height=\"36\" fill=\"{color}\"/>\n",710.0*value as f64/maximum as f64));
        self.text(1040.0, y + 26.0, 25, INK, 700, &grouped(value));
    }

    fn axis(&mut self, maximum: u64, ticks: &[u64], y: f64) {
        self.line(310.0, y, 1020.0, y);
        for &tick in ticks {
            let x = 310.0 + 710.0 * tick as f64 / maximum as f64;
            self.0.push_str(&format!("<path d=\"M{x} {y}v6\" stroke=\"{GREY}\"/>\n<text x=\"{x}\" y=\"{}\" text-anchor=\"middle\" font-size=\"19\" fill=\"{MUTED}\">{}</text>\n",y+30.0,grouped(tick)));
        }
    }

    fn finish(mut self) -> String {
        self.0.push_str("</g></svg>\n");
        self.0
    }
}

fn packet_recovery(s: &Value, m: &Metrics) -> Result<String> {
    let direwolf = count(&s["all"]["arms"]["direwolf"]["frames"])?;
    let grsat = count(&s["all"]["arms"]["gr_satellites"]["frames"])?;
    let max_value = [m.ours, m.baseline, direwolf, grsat]
        .into_iter()
        .max()
        .ok_or("no values")?;
    let maximum = max_value.div_ceil(1000) * 1000;
    let mut svg = Svg::new(
        740,
        "Packet recovery on the CANVAS historical cohort",
        &format!(
            "{} unique packets within observations for Telemetry Yield progressive versus {} in the tested Dire Wolf and gr-satellites union. {} added; {} missed; +{:.2}% net. {} exposed development recordings; substantially more compute, not an equal-compute test.",
            m.ours,
            m.baseline,
            m.added,
            m.lost,
            m.percent(),
            m.observations
        ),
    );
    svg.header(
        "01 / PACKET RECOVERY",
        "More telemetry. Same recordings.",
        "Unique AX.25 UI packets, counted within each observation",
    );
    svg.text(48.0, 242.0, 78, INK, 700, &grouped(m.ours));
    svg.text(
        48.0,
        275.0,
        20,
        MUTED,
        400,
        "RECOVERED BY THE PROGRESSIVE RECEIVER",
    );
    svg.text(
        780.0,
        238.0,
        62,
        TEAL,
        700,
        &format!("+{:.2}%", m.percent()),
    );
    svg.text(785.0, 275.0, 20, MUTED, 400, "NET GAIN VS REFERENCE UNION");
    svg.line(48.0, 303.0, 1152.0, 303.0);
    for (label, n, y, color) in [
        ("Dire Wolf", direwolf, 335.0, GREY),
        ("gr-satellites", grsat, 397.0, GREY),
        ("Reference union", m.baseline, 459.0, "#435f75"),
        ("Telemetry Yield", m.ours, 521.0, TEAL),
    ] {
        svg.bar(label, n, maximum, y, color);
    }
    svg.axis(maximum, &[0, 2000, 4000, 6000, maximum], 585.0);
    svg.text(
        48.0,
        655.0,
        24,
        INK,
        700,
        &format!(
            "+{} added packets   /   {} reference packets missed",
            grouped(m.added),
            m.lost
        ),
    );
    svg.text(48.0,691.0,20,MUTED,400,&format!("{} historical CANVAS recordings · exposed development cohort · tested configurations only",m.observations));
    svg.text(
        48.0,
        719.0,
        20,
        MUTED,
        400,
        "Same PCM input. Additional recovery costs substantially more computation.",
    );
    Ok(svg.finish())
}

fn data_recovery(m: &Metrics) -> Result<String> {
    if m.observations != 266 {
        return Err("the observation grid is sized for the frozen 266-recording cohort".into());
    }
    let mut svg = Svg::new(
        730,
        "Additional received bytes and observations with a gain",
        &format!(
            "{} bytes in additional PDUs minus {} bytes in missed PDUs equals {} net extra bytes. Headers included, FCS excluded; not application-only bytes. {} of {} observations have an added packet. The grid groups outcomes, not time; these are not independent trials.",
            m.added_bytes,
            m.lost_bytes,
            m.added_bytes - m.lost_bytes,
            m.with_gain,
            m.observations
        ),
    );
    svg.header(
        "02 / DATA RECOVERY",
        "Additional data, with losses accounted for.",
        "Bytes in observation-level PDUs · headers included · FCS excluded",
    );
    svg.text(48.0, 202.0, 19, TEAL, 700, "ADDED BYTES");
    svg.text(440.0, 202.0, 19, RUST, 700, "MISSED BYTES");
    svg.text(820.0, 202.0, 19, INK, 700, "NET EXTRA BYTES");
    svg.text(
        48.0,
        261.0,
        51,
        TEAL,
        700,
        &format!("+{}", grouped(m.added_bytes)),
    );
    svg.text(
        440.0,
        261.0,
        51,
        RUST,
        700,
        &format!("−{}", grouped(m.lost_bytes)),
    );
    svg.text(
        820.0,
        261.0,
        51,
        INK,
        700,
        &grouped(m.added_bytes - m.lost_bytes),
    );
    svg.text(
        48.0,
        297.0,
        22,
        MUTED,
        400,
        &format!("{} additional packets", grouped(m.added)),
    );
    svg.text(
        440.0,
        297.0,
        22,
        MUTED,
        400,
        &format!("{} reference packets", m.lost),
    );
    svg.text(820.0, 297.0, 22, MUTED, 400, "after subtracting misses");
    svg.line(48.0, 328.0, 1152.0, 328.0);
    svg.text(
        48.0,
        375.0,
        27,
        INK,
        700,
        &format!("Gain in {} of {} recordings", m.with_gain, m.observations),
    );
    svg.text(
        949.0,
        375.0,
        33,
        TEAL,
        700,
        &format!("{:.2}%", m.gain_frequency()),
    );
    for i in 0..m.observations {
        let gain = i < m.with_gain;
        svg.0.push_str(&format!("<rect data-role=\"observation\" data-gain=\"{gain}\" x=\"{}\" y=\"{}\" width=\"21\" height=\"21\" rx=\"2\" fill=\"{}\"/>\n",48+(i%38)*29,405+(i/38)*29,if gain {TEAL} else {"#e3eaf0"}));
    }
    svg.rect(48.0, 628.0, 17.0, 17.0, TEAL);
    svg.text(77.0, 643.0, 21, INK, 400, "At least one added packet");
    svg.rect(440.0, 628.0, 17.0, 17.0, "#e3eaf0");
    svg.text(469.0, 643.0, 21, INK, 400, "No added packet");
    svg.text(48.0,687.0,20,MUTED,400,"One square = one observation. Grouped by outcome, not time; gains can coexist with losses.");
    svg.text(
        48.0,
        715.0,
        20,
        MUTED,
        400,
        "PDU bytes are not application-only measurements or globally unique telemetry.",
    );
    Ok(svg.finish())
}

fn signal_recovery(m: &Metrics) -> String {
    let maximum = m.ours.max(m.baseline).div_ceil(50) * 50;
    let mut svg = Svg::new(
        590,
        "Recovery in the separately reported signal-labelled subset",
        &format!(
            "{} observations with frozen waterfall_status=with-signal metadata: {} progressive packets versus {} reference-union packets. {} added, {} missed, +{:.2}% net. Gain in {} observations. Exploratory subset of the same exposed historical cohort; not independent signal truth.",
            m.observations,
            m.ours,
            m.baseline,
            m.added,
            m.lost,
            m.percent(),
            m.with_gain
        ),
    );
    svg.header(
        "03 / SIGNAL-LABELLED SUBSET",
        "Visible signal. Measurable extra recovery.",
        &format!(
            "{} observations with a frozen “with-signal” archive label",
            m.observations
        ),
    );
    svg.text(48.0, 235.0, 64, TEAL, 700, &format!("+{:.2}%", m.percent()));
    svg.text(
        500.0,
        214.0,
        28,
        INK,
        700,
        &format!("{} added packets · {} missed", m.added, m.lost),
    );
    svg.text(
        500.0,
        251.0,
        23,
        MUTED,
        400,
        &format!("Gain in {} of {} observations", m.with_gain, m.observations),
    );
    svg.bar("Reference union", m.baseline, maximum, 305.0, "#435f75");
    svg.bar("Telemetry Yield", m.ours, maximum, 381.0, TEAL);
    svg.axis(maximum, &[0, 50, 100, 150, maximum], 453.0);
    svg.text(
        48.0,
        532.0,
        20,
        MUTED,
        400,
        "Exploratory metadata subgroup, not independent signal truth or spacecraft identification.",
    );
    svg.text(48.0,563.0,20,MUTED,400,"Subset of the same exposed CANVAS cohort, not an additional trial. Reference = tested union.");
    svg.finish()
}

pub(super) fn render(summary: &Value, output: &Path) -> Result<()> {
    let all = Metrics::parse(&summary["all"])?;
    let signal = Metrics::parse(&summary["with_signal"])?;
    let figures = [
        ("packet-recovery.svg", packet_recovery(summary, &all)?),
        ("data-recovery.svg", data_recovery(&all)?),
        ("signal-recovery.svg", signal_recovery(&signal)),
    ];
    fs::create_dir_all(output)?;
    for (name, figure) in figures {
        fs::write(output.join(name), figure)?;
    }
    println!(
        "Rendered three evidence-derived README figures to {}",
        output.display()
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn summary() -> Value {
        serde_json::from_str(include_str!(
            "../../publication/decoder-paper-v2/evidence/summary.json"
        ))
        .unwrap()
    }

    #[test]
    fn numbers_and_xml_are_safe() {
        assert_eq!(grouped(0), "0");
        assert_eq!(grouped(402192), "402,192");
        assert_eq!(grouped(1000000), "1,000,000");
        assert_eq!(xml("A&B <C>"), "A&amp;B &lt;C&gt;");
    }

    #[test]
    fn grid_counts_observations_not_packets() {
        let m = Metrics::parse(&summary()["all"]).unwrap();
        let svg = data_recovery(&m).unwrap();
        assert_eq!(svg.matches("data-role=\"observation\"").count(), 266);
        assert_eq!(svg.matches("data-gain=\"true\"").count(), 141);
        assert_eq!(svg.matches("data-gain=\"false\"").count(), 125);
        assert!(svg.contains("401,136"));
        assert!(svg.contains("1,056"));
    }

    #[test]
    fn primary_and_signal_denominators_stay_separate() {
        let s = summary();
        let m = Metrics::parse(&s["all"]).unwrap();
        let svg = packet_recovery(&s, &m).unwrap();
        assert!(svg.contains("+52.98%"));
        assert!(svg.contains("data-value=\"6220\""));
        let m = Metrics::parse(&s["with_signal"]).unwrap();
        let svg = signal_recovery(&m);
        assert!(svg.contains("+56.76%"));
        assert!(svg.contains("data-value=\"174\""));
        assert!(svg.contains("24 observations"));
        assert!(!svg.contains("6,220"));
    }

    #[test]
    fn invalid_metrics_do_not_create_positive_charts() {
        let mut s = summary();
        s["all"]["baseline"] = Value::from(0);
        assert!(Metrics::parse(&s["all"]).is_err());
        let mut s = summary();
        s["all"]["ours"] = Value::from(1);
        assert!(Metrics::parse(&s["all"]).is_err());
        let mut s = summary();
        s["all"]["ours"] = Value::from(u64::MAX);
        s["all"]["baseline"] = Value::from(u64::MAX);
        assert!(Metrics::parse(&s["all"]).is_err());
    }

    #[test]
    fn bar_lengths_use_the_same_zero_origin_scale() {
        let s = summary();
        let m = Metrics::parse(&s["all"]).unwrap();
        let svg = packet_recovery(&s, &m).unwrap();
        let bars: Vec<_> = svg
            .lines()
            .filter(|line| line.contains("data-role=\"value-bar\""))
            .collect();
        assert_eq!(bars.len(), 4);
        for (bar, count) in bars.iter().zip([4029, 2514, 4066, 6220]) {
            assert!(bar.contains("data-scale-max=\"7000\""));
            assert!(bar.contains("x=\"310\""));
            assert!(bar.contains(&format!("width=\"{:.3}\"", 710.0 * count as f64 / 7000.0)));
        }
    }

    #[test]
    fn checked_in_figures_match_verified_summary() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"));
        super::super::verify(&root.join("publication/decoder-paper-v2/evidence")).unwrap();
        let output = tempfile::tempdir().unwrap();
        render(&summary(), output.path()).unwrap();
        for name in [
            "packet-recovery.svg",
            "data-recovery.svg",
            "signal-recovery.svg",
        ] {
            assert_eq!(
                fs::read(output.path().join(name)).unwrap(),
                fs::read(root.join("docs/assets/recovery").join(name)).unwrap(),
                "{name} is stale; run paper_evidence render-readme"
            );
        }
    }

    #[test]
    fn assets_are_deterministic_and_standalone() {
        let a = tempfile::tempdir().unwrap();
        let b = tempfile::tempdir().unwrap();
        render(&summary(), a.path()).unwrap();
        render(&summary(), b.path()).unwrap();
        for name in [
            "packet-recovery.svg",
            "data-recovery.svg",
            "signal-recovery.svg",
        ] {
            let svg = fs::read_to_string(a.path().join(name)).unwrap();
            assert_eq!(svg, fs::read_to_string(b.path().join(name)).unwrap());
            assert!(svg.contains("<title"));
            assert!(svg.contains("<desc"));
            assert!(!svg.contains("<script"));
            assert!(!svg.contains("foreignObject"));
        }
    }
}
