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
<rect width="1200" height="{height}" fill="#ffffff"/>
<g font-family="Arial, Helvetica, sans-serif" style="font-variant-numeric:tabular-nums">
"##,
            xml(title),
            xml(description)
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

    fn header(&mut self, title: &str, subtitle: &str) {
        self.text(48.0, 64.0, 36, INK, 700, title);
        self.text(48.0, 104.0, 25, MUTED, 400, subtitle);
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
            self.0.push_str(&format!("<path d=\"M{x} {y}v6\" stroke=\"{GREY}\"/>\n<text x=\"{x}\" y=\"{}\" text-anchor=\"middle\" font-size=\"24\" fill=\"{MUTED}\">{}</text>\n",y+34.0,grouped(tick)));
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
        638,
        "Packet recovery on the CANVAS historical cohort",
        &format!(
            "{} unique packets within observations for FrameLift (Telemetry Yield progressive) versus {} in the tested Dire Wolf and gr-satellites union. {} added; {} missed; +{:.2}% net. {} exposed development recordings; substantially more compute, not an equal-compute test.",
            m.ours,
            m.baseline,
            m.added,
            m.lost,
            m.percent(),
            m.observations
        ),
    );
    svg.header(
        "Recovered packets",
        &format!("{} CANVAS recordings", m.observations),
    );
    svg.text(48.0, 194.0, 72, INK, 700, &grouped(m.ours));
    svg.text(48.0, 230.0, 25, MUTED, 400, "FrameLift");
    svg.text(
        780.0,
        194.0,
        62,
        TEAL,
        700,
        &format!("+{:.2}%", m.percent()),
    );
    svg.text(
        785.0,
        230.0,
        25,
        MUTED,
        400,
        "Net increase over reference union",
    );
    for (label, n, y, color) in [
        ("Dire Wolf", direwolf, 285.0, GREY),
        ("gr-satellites", grsat, 345.0, GREY),
        ("Reference union", m.baseline, 405.0, "#435f75"),
        ("FrameLift", m.ours, 465.0, TEAL),
    ] {
        svg.bar(label, n, maximum, y, color);
    }
    svg.axis(maximum, &[0, 2000, 4000, 6000, maximum], 530.0);
    svg.text(
        48.0,
        608.0,
        25,
        INK,
        400,
        &format!(
            "{} additional packets; {} reference packets missed",
            grouped(m.added),
            m.lost
        ),
    );
    Ok(svg.finish())
}

fn data_recovery(m: &Metrics) -> String {
    let mut svg = Svg::new(
        520,
        "Additional received bytes and observations with a gain",
        &format!(
            "{} bytes in additional PDUs minus {} bytes in missed PDUs equals {} net extra bytes. Headers included, FCS excluded; not application-only bytes. {} of {} observations have an added packet. Additions can coexist with missed packets; these are not independent trials.",
            m.added_bytes,
            m.lost_bytes,
            m.added_bytes - m.lost_bytes,
            m.with_gain,
            m.observations
        ),
    );
    svg.header(
        "Additional received data",
        "Protocol bytes (including headers; excluding FCS)",
    );
    svg.text(48.0, 165.0, 25, TEAL, 400, "Added bytes");
    svg.text(440.0, 165.0, 25, RUST, 400, "Missed bytes");
    svg.text(820.0, 165.0, 25, INK, 400, "Net extra bytes");
    svg.text(
        48.0,
        228.0,
        51,
        TEAL,
        700,
        &format!("+{}", grouped(m.added_bytes)),
    );
    svg.text(
        440.0,
        228.0,
        51,
        RUST,
        700,
        &format!("−{}", grouped(m.lost_bytes)),
    );
    svg.text(
        820.0,
        228.0,
        51,
        INK,
        700,
        &grouped(m.added_bytes - m.lost_bytes),
    );

    svg.text(
        48.0,
        320.0,
        27,
        INK,
        700,
        "Recordings with additional packets",
    );
    svg.text(
        48.0,
        385.0,
        46,
        INK,
        700,
        &format!("{} / {}", m.with_gain, m.observations),
    );
    svg.text(
        949.0,
        385.0,
        40,
        TEAL,
        700,
        &format!("{:.2}%", m.gain_frequency()),
    );

    // Both segments use the same observation denominator, never packet counts.
    let width = 1104.0;
    let gained_width = width * m.with_gain as f64 / m.observations as f64;
    for (gain, count, x, segment_width, color) in [
        (true, m.with_gain, 48.0, gained_width, TEAL),
        (
            false,
            m.observations - m.with_gain,
            48.0 + gained_width,
            width - gained_width,
            "#e3eaf0",
        ),
    ] {
        svg.0.push_str(&format!(
            "<rect data-role=\"observation-share\" data-gain=\"{gain}\" data-value=\"{count}\" data-total=\"{}\" x=\"{x:.3}\" y=\"418\" width=\"{segment_width:.3}\" height=\"32\" fill=\"{color}\"/>\n",
            m.observations
        ));
    }
    svg.text(
        48.0,
        490.0,
        25,
        INK,
        400,
        &format!("{} with additions", m.with_gain),
    );
    svg.text(
        820.0,
        490.0,
        25,
        MUTED,
        400,
        &format!("{} without additions", m.observations - m.with_gain),
    );
    svg.finish()
}

fn signal_recovery(m: &Metrics) -> String {
    let maximum = m.ours.max(m.baseline).div_ceil(50) * 50;
    let mut svg = Svg::new(
        545,
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
        "Recordings labelled with-signal",
        &format!("{} observations from the same CANVAS study", m.observations),
    );
    svg.text(48.0, 195.0, 64, TEAL, 700, &format!("+{:.2}%", m.percent()));
    svg.text(
        48.0,
        233.0,
        25,
        MUTED,
        400,
        "Net increase over reference union",
    );
    svg.text(
        600.0,
        183.0,
        28,
        INK,
        700,
        &format!("{} additional packets", m.added),
    );
    svg.text(
        600.0,
        224.0,
        25,
        MUTED,
        400,
        &format!("{} reference packets missed", m.lost),
    );
    svg.bar("Reference union", m.baseline, maximum, 292.0, "#435f75");
    svg.bar("FrameLift", m.ours, maximum, 368.0, TEAL);
    svg.axis(maximum, &[0, 50, 100, 150, maximum], 435.0);
    svg.text(
        48.0,
        515.0,
        25,
        INK,
        400,
        &format!(
            "Additional packets in {} of {} recordings",
            m.with_gain, m.observations
        ),
    );
    svg.finish()
}

pub(super) fn render(summary: &Value, output: &Path) -> Result<()> {
    let all = Metrics::parse(&summary["all"])?;
    let signal = Metrics::parse(&summary["with_signal"])?;
    let figures = [
        ("packet-recovery.svg", packet_recovery(summary, &all)?),
        ("data-recovery.svg", data_recovery(&all)),
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
    fn observation_bar_counts_recordings_not_packets() {
        let m = Metrics::parse(&summary()["all"]).unwrap();
        let svg = data_recovery(&m);
        assert_eq!(svg.matches("data-role=\"observation-share\"").count(), 2);
        assert!(svg.contains("data-gain=\"true\" data-value=\"141\" data-total=\"266\""));
        assert!(svg.contains("data-gain=\"false\" data-value=\"125\" data-total=\"266\""));
        assert!(svg.contains(&format!("width=\"{:.3}\"", 1104.0 * 141.0 / 266.0)));
        assert!(svg.contains("53.01%"));
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
            assert!(!svg.contains('·'));
            assert!(!svg.contains(" / PACKET"));
            assert!(!svg.contains("<circle"));
            assert!(!svg.contains("rx=\""));
            for text in svg.split("font-size=\"").skip(1) {
                let size: u32 = text.split('"').next().unwrap().parse().unwrap();
                assert!(size >= 24, "small decorative text has returned");
            }
        }
    }
}
