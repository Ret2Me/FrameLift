//! Fail-closed normalization of offline external decoder outputs.
//!
//! Neither format carries the received FCS: CRC validity is attested by the
//! configured external decoder, not independently reconstructed by this parser.
//! `strict_ui_payloads` uses precisely the native receiver's structural filter;
//! `all_payloads` also retains externally decoded non-UI/noncanonical AX.25.

use crate::{formats, protocol};
use serde::Serialize;
use std::collections::BTreeSet;

const MAX_OUTPUT_BYTES: usize = 64 * 1024 * 1024;
const MAX_FRAME_BYTES: usize = 1024 * 1024;

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct BaselinePdus {
    pub emitted_count: usize,
    pub all_payloads: BTreeSet<String>,
    pub strict_ui_payloads: BTreeSet<String>,
    pub crc_evidence: &'static str,
    pub received_fcs_present: bool,
    pub independent_fcs_verified: bool,
}

fn normalize(frames: Vec<Vec<u8>>, evidence: &'static str) -> BaselinePdus {
    let mut all_payloads = BTreeSet::new();
    let mut strict_ui_payloads = BTreeSet::new();
    for frame in &frames {
        let value = hex::encode(frame);
        if protocol::parse_ax25_ui(frame).is_some() {
            strict_ui_payloads.insert(value.clone());
        }
        all_payloads.insert(value);
    }
    BaselinePdus {
        emitted_count: frames.len(),
        all_payloads,
        strict_ui_payloads,
        crc_evidence: evidence,
        received_fcs_present: false,
        independent_fcs_verified: false,
    }
}

fn strip_ansi(text: &str) -> Result<String, String> {
    let mut bytes = text.bytes().peekable();
    let mut out = Vec::with_capacity(text.len());
    while let Some(value) = bytes.next() {
        if value != 0x1b {
            out.push(value);
            continue;
        }
        match bytes.next() {
            Some(b'[') => {
                let mut closed = false;
                for next in bytes.by_ref() {
                    if (0x40..=0x7e).contains(&next) {
                        closed = true;
                        break;
                    }
                    if !(0x20..=0x3f).contains(&next) {
                        return Err("invalid ANSI CSI in atest output".into());
                    }
                }
                if !closed {
                    return Err("truncated ANSI CSI in atest output".into());
                }
            }
            Some(b']') => {
                let mut closed = false;
                while let Some(next) = bytes.next() {
                    if next == 7 || (next == 0x1b && bytes.next() == Some(b'\\')) {
                        closed = true;
                        break;
                    }
                }
                if !closed {
                    return Err("truncated ANSI OSC in atest output".into());
                }
            }
            _ => return Err("unsupported ANSI escape in atest output".into()),
        }
    }
    String::from_utf8(out).map_err(|_| "invalid UTF-8 after stripping ANSI".into())
}

fn decimal(text: &str, what: &str) -> Result<usize, String> {
    if text.is_empty() || !text.bytes().all(|x| x.is_ascii_digit()) {
        return Err(format!("invalid {what}: {text:?}"));
    }
    text.parse().map_err(|_| format!("overflow in {what}"))
}

/// Parse `atest -B 9600 -F 0 -h input.wav` stdout. Read logs as bytes and pass
/// `String::from_utf8_lossy(&bytes)`: monitor text may contain non-UTF8 payloads,
/// while the authoritative fixed-column hex dump is ASCII.
pub fn parse_direwolf_atest(output: &str) -> Result<BaselinePdus, String> {
    if output.len() > MAX_OUTPUT_BYTES {
        return Err("atest log exceeds 64 MiB parser limit".into());
    }
    let clean = strip_ansi(output)?;
    let mut frames = Vec::new();
    let mut current = Vec::new();
    let mut declared: Option<usize> = None;
    let mut undeclared_dump = false;
    let mut decoded_markers = 0;
    let mut summary = None;
    let mut fix_bits = None;
    for line in clean.lines() {
        let line = line.trim_start();
        // atest omits the length/type line for some CRC-valid noncanonical
        // AX.25 packets. Their complete dump is still delimited by ------.
        if line == "------" && undeclared_dump {
            if current.is_empty() {
                return Err("empty undeclared atest dump".into());
            }
            frames.push(std::mem::take(&mut current));
            undeclared_dump = false;
        }
        if let Some(rest) = line.strip_prefix("Fix Bits level = ")
            && fix_bits
                .replace(decimal(rest.trim(), "Fix Bits level")?)
                .is_some()
        {
            return Err("duplicate atest Fix Bits declaration".into());
        }
        if let Some(rest) = line.strip_prefix("DECODED[") {
            if declared.is_some() || !current.is_empty() {
                return Err("new atest packet before prior hexdump completed".into());
            }
            let (count, _) = rest.split_once(']').ok_or("malformed DECODED marker")?;
            decoded_markers += 1;
            if decimal(count, "DECODED marker")? != decoded_markers
                || frames.len() + 1 != decoded_markers
            {
                return Err("atest packet marker count is not contiguous".into());
            }
        }
        if let Some((count, _)) = line.split_once(" packets decoded in ")
            && summary.replace(decimal(count, "decoded total")?).is_some()
        {
            return Err("multiple atest summary lines".into());
        }
        if let Some((_, length)) = line.rsplit_once(", length = ") {
            if declared.is_some() || decoded_markers != frames.len() + 1 {
                return Err("unexpected/duplicate atest packet length declaration".into());
            }
            let length = decimal(length.trim(), "frame length")?;
            if length == 0 || length > MAX_FRAME_BYTES {
                return Err("atest frame length is zero or exceeds 1 MiB".into());
            }
            declared = Some(length);
        }
        let Some((offset, body)) = line.split_once(':') else {
            continue;
        };
        if !(3..=8).contains(&offset.len()) || !offset.bytes().all(|x| x.is_ascii_hexdigit()) {
            continue;
        }
        if declared.is_none() {
            if decoded_markers != frames.len() + 1 {
                return Err("atest hex row without a decoded packet marker".into());
            }
            undeclared_dump = true;
        }
        let offset = usize::from_str_radix(offset, 16).map_err(|_| "invalid hex offset")?;
        if offset != current.len() {
            return Err(format!("non-contiguous atest hexdump at {offset}"));
        }
        let body = body.trim_start().as_bytes();
        let count = if let Some(length) = declared {
            (length - current.len()).min(16)
        } else {
            if body.len() < 48 || current.len() % 16 != 0 || current.len() >= MAX_FRAME_BYTES {
                return Err("invalid undeclared atest dump bounds or row continuation".into());
            }
            let mut count = 0;
            let mut padding = false;
            for cell in body[..48].as_chunks::<3>().0 {
                if cell == b"   " {
                    padding = true;
                } else if !padding
                    && cell[0].is_ascii_hexdigit()
                    && cell[1].is_ascii_hexdigit()
                    && cell[2] == b' '
                {
                    count += 1;
                } else {
                    return Err("invalid undeclared fixed-column atest hex row".into());
                }
            }
            if count == 0 {
                return Err("empty atest hex row".into());
            }
            count
        };
        if body.len() < count * 3 - 1 {
            return Err("truncated atest hex row".into());
        }
        for i in 0..count {
            let pos = i * 3;
            if !body[pos].is_ascii_hexdigit()
                || !body[pos + 1].is_ascii_hexdigit()
                || (i > 0 && body[pos - 1] != b' ')
            {
                return Err("malformed fixed-column atest hex byte".into());
            }
            let pair = std::str::from_utf8(&body[pos..pos + 2]).unwrap();
            current.push(u8::from_str_radix(pair, 16).unwrap());
        }
        // Dire Wolf pads its final row to 16 octets, then emits ASCII. Reject
        // extra hex octets; never mistake an ASCII column such as `ab` for data.
        let hex_end = count * 3 - 1;
        if body
            .get(hex_end..48)
            .is_none_or(|padding| padding.iter().any(|b| *b != b' '))
        {
            return Err("atest hex padding missing or contains unexpected octets".into());
        }
        if declared == Some(current.len()) {
            frames.push(std::mem::take(&mut current));
            declared = None;
        }
    }
    if declared.is_some() || !current.is_empty() {
        return Err("truncated atest packet hexdump".into());
    }
    if fix_bits != Some(0) {
        return Err("atest did not confirm Fix Bits level = 0".into());
    }
    if summary != Some(frames.len()) || decoded_markers != frames.len() {
        return Err("atest summary/markers disagree with complete hex dumps".into());
    }
    Ok(normalize(
        frames,
        "Dire Wolf internal FCS check, Fix Bits level 0; received FCS stripped",
    ))
}

/// Parse gr-satellites `--kiss_out` from an AX.25 G3RUH profile whose HDLC
/// deframer enables FCS checking. File identity and decoder success are the
/// caller's responsibility; an absent file must not become an empty slice.
pub fn parse_gr_satellites_kiss(bytes: &[u8]) -> Result<BaselinePdus, String> {
    if !bytes.is_empty() && bytes.first() != Some(&0xc0) {
        return Err("KISS stream has bytes before first FEND".into());
    }
    let parsed = formats::parse_kiss(bytes)?;
    if parsed.malformed_records != 0 {
        return Err(format!(
            "KISS stream has {} malformed records",
            parsed.malformed_records
        ));
    }
    if !parsed.other_commands.is_empty() {
        return Err("unexpected command in gr-satellites KISS output".into());
    }
    Ok(normalize(
        parsed
            .data_frames
            .into_iter()
            .map(|frame| frame.payload)
            .collect(),
        "gr-satellites AX.25 HDLC internal FCS check; received FCS stripped",
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ui() -> Vec<u8> {
        hex::decode("94a662b2a0826094a662b29eb2e103f06162").unwrap()
    }

    fn dump(frame: &[u8], index: usize) -> String {
        let mut text = format!(
            "DECODED[{index}] 0:01.000\nU frame UI: p/f=0, length = {}\n",
            frame.len()
        );
        for (index, row) in frame.chunks(16).enumerate() {
            text.push_str(&format!("  {:03x}:  ", index * 16));
            for i in 0..16 {
                text.push_str(
                    &row.get(i)
                        .map(|x| format!("{x:02x} "))
                        .unwrap_or_else(|| "   ".into()),
                );
            }
            text.push_str(" ab cd ef\n");
        }
        text
    }

    fn atest(frames: &[Vec<u8>]) -> String {
        let mut text = "\x1b[38;2;0;192;0mFix Bits level = 0\n".to_string();
        for (i, frame) in frames.iter().enumerate() {
            text.push_str(&dump(frame, i + 1));
        }
        text.push_str(&format!(
            "{} packets decoded in 0.1 seconds. 1 x realtime\n",
            frames.len()
        ));
        text
    }

    #[test]
    fn direwolf_preserves_exact_bytes_and_separates_emissions_from_unique() {
        let value = parse_direwolf_atest(&atest(&[ui(), ui()])).unwrap();
        assert_eq!(value.emitted_count, 2);
        assert_eq!(value.all_payloads, BTreeSet::from([hex::encode(ui())]));
        assert_eq!(value.strict_ui_payloads, value.all_payloads);
        assert!(!value.received_fcs_present && !value.independent_fcs_verified);
    }

    #[test]
    fn direwolf_rejects_truncation_offsets_extra_octets_and_missing_summary() {
        let valid = atest(&[ui()]);
        for bad in [
            valid.replace("010:", "020:"),
            valid.replace("length = 18", "length = 17"),
            valid.replace("length = 18", "length = 19"),
            valid.replace("1 packets decoded", "2 packets decoded"),
            valid
                .lines()
                .filter(|line| !line.contains("packets decoded"))
                .collect::<Vec<_>>()
                .join("\n"),
            valid.replace("Fix Bits level = 0", "Fix Bits level = 1"),
            valid.replace("DECODED[1]", "DECODED[2]"),
        ] {
            assert!(parse_direwolf_atest(&bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn direwolf_zero_requires_success_summary_and_no_repair() {
        assert_eq!(parse_direwolf_atest(&atest(&[])).unwrap().emitted_count, 0);
        assert!(parse_direwolf_atest("Couldn't open file for read\n").is_err());
        assert!(parse_direwolf_atest("").is_err());
    }

    #[test]
    fn noncanonical_ax25_retained_without_falsely_becoming_strict_ui() {
        let mut unusual = ui();
        unusual[6] &= !0x60;
        let parsed = parse_direwolf_atest(&atest(&[unusual.clone()])).unwrap();
        assert_eq!(parsed.all_payloads, BTreeSet::from([hex::encode(unusual)]));
        assert!(parsed.strict_ui_payloads.is_empty());
    }

    #[test]
    fn noncanonical_dump_without_declared_length_is_delimited_and_exact() {
        let raw = hex::decode("9882a6a040406086829cac82a61800c00000050000d7069b11").unwrap();
        let dump = dump(&raw, 1)
            .lines()
            .filter(|s| !s.contains("length ="))
            .collect::<Vec<_>>()
            .join("\n");
        let valid =
            format!("Fix Bits level = 0\n{dump}\n------\n1 packets decoded in 1 seconds.\n");
        let parsed = parse_direwolf_atest(&valid).unwrap();
        assert_eq!(parsed.all_payloads, BTreeSet::from([hex::encode(raw)]));
        assert!(parsed.strict_ui_payloads.is_empty());
        assert!(parse_direwolf_atest(&valid.replace("------\n", "")).is_err());
        assert!(parse_direwolf_atest(&valid.replace("010:", "020:")).is_err());
        assert!(parse_direwolf_atest(&valid.replace("DECODED[1]", "unframed")).is_err());
    }

    #[test]
    fn kiss_unescapes_and_deduplicates_but_never_invents_fcs() {
        let mut frame = ui();
        frame.extend([0xc0, 0xdb]);
        let mut stream = formats::encode_kiss_record(0, 9, &123_u64.to_be_bytes()).unwrap();
        stream.extend(formats::encode_kiss_record(0, 0, &frame).unwrap());
        stream.extend(formats::encode_kiss_record(0, 0, &frame).unwrap());
        let value = parse_gr_satellites_kiss(&stream).unwrap();
        assert_eq!(value.emitted_count, 2);
        assert_eq!(
            value.strict_ui_payloads,
            BTreeSet::from([hex::encode(frame)])
        );
        assert!(!value.independent_fcs_verified && !value.received_fcs_present);
    }

    #[test]
    fn kiss_rejects_malformed_truncated_unknown_or_unframed() {
        for bad in [
            vec![0xc0, 0, 0xdb, 0xc0],
            vec![0xc0, 0, 1],
            vec![0, 1, 0xc0],
            vec![0xc0, 1, 9, 0xc0],
            vec![0xc0, 0, 0xdb, 42, 0xc0],
        ] {
            assert!(parse_gr_satellites_kiss(&bad).is_err());
        }
        assert_eq!(parse_gr_satellites_kiss(&[]).unwrap().emitted_count, 0);
    }

    #[test]
    fn malformed_ansi_is_an_error_not_silent_data_loss() {
        for suffix in ["\x1b[12", "\x1b]unfinished", "\x1bX"] {
            assert!(parse_direwolf_atest(&(atest(&[]) + suffix)).is_err());
        }
    }
}
