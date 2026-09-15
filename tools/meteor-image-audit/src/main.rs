//! Pixel-preserving comparison of unchanged SatDump MSU-MR products.
//! Timestamp-verified integer translation only: no resize, interpolation,
//! enhancement or generative image manipulation.
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File, OpenOptions},
    io::{BufReader, BufWriter, Write},
    path::Path,
};
type E = Box<dyn std::error::Error>;
fn hash(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}
fn read_cbor(path: &Path) -> Result<Value, E> {
    Ok(ciborium::from_reader(BufReader::new(File::open(path)?))?)
}
fn png_read(path: &Path) -> Result<(usize, usize, Vec<u8>), E> {
    let mut r = png::Decoder::new(BufReader::new(File::open(path)?)).read_info()?;
    let size = r.output_buffer_size().ok_or("PNG extent overflow")?;
    if size > 100_000_000 {
        return Err("PNG too large".into());
    }
    let mut data = vec![0; size];
    let i = r.next_frame(&mut data)?;
    if i.color_type != png::ColorType::Grayscale || i.bit_depth != png::BitDepth::Eight {
        return Err("requires unmodified grayscale8 product".into());
    }
    data.truncate(i.buffer_size());
    Ok((i.width as usize, i.height as usize, data))
}
fn png_write(path: &Path, w: usize, h: usize, data: &[u8]) -> Result<(), E> {
    let f = OpenOptions::new().write(true).create_new(true).open(path)?;
    let mut e = png::Encoder::new(BufWriter::new(f), w as u32, h as u32);
    e.set_color(png::ColorType::Grayscale);
    e.set_depth(png::BitDepth::Eight);
    e.write_header()?.write_image_data(data)?;
    Ok(())
}

// Coverage outside a decoder's output is alpha=0, never invented black
// observations. Inside the extent R=G=B=the original byte, alpha=255.
fn coverage_pixels(
    w: usize,
    rows: usize,
    original: &[u8],
    offset: usize,
    crop_y: usize,
    crop_h: usize,
) -> Vec<u8> {
    let mut rgba = vec![0; w * crop_h * 4];
    for row in 0..crop_h {
        let y = crop_y + row;
        if y < offset || y >= offset + rows {
            continue;
        }
        for x in 0..w {
            let value = original[(y - offset) * w + x];
            rgba[(row * w + x) * 4..(row * w + x + 1) * 4]
                .copy_from_slice(&[value, value, value, 255]);
        }
    }
    rgba
}

fn coverage_detail(
    out: &Path,
    stem: &str,
    b: (usize, usize, &[u8]),
    n: (usize, &[u8]),
    delta: isize,
    requested_rows: usize,
) -> Result<Value, E> {
    let (w, bh, bp) = b;
    let (nh, np) = n;
    let union_start = 0.min(-delta * 8);
    let bo = (-union_start) as usize;
    let no = (-delta * 8 - union_start) as usize;
    let union_height = (bo + bh).max(no + nh);
    let extra: Vec<_> = (no..no + nh)
        .filter(|&y| {
            (y < bo || y >= bo + bh) && np[(y - no) * w..(y - no + 1) * w].iter().any(|p| *p != 0)
        })
        .collect();
    if extra.is_empty() {
        return Ok(Value::Null);
    }
    let crop_h = requested_rows.min(union_height);
    let mut crop_y = 0;
    let mut best_count = 0;
    for &y in &extra {
        let start = y.saturating_sub(crop_h / 2).min(union_height - crop_h) / 8 * 8;
        let count = extra
            .iter()
            .filter(|&&yy| yy >= start && yy < start + crop_h)
            .count();
        if count > best_count {
            best_count = count;
            crop_y = start;
        }
    }
    let mut files = Vec::new();
    for (label, rows, pixels, offset) in [("baseline", bh, bp, bo), ("candidate", nh, np, no)] {
        let name = format!("{stem}-{label}-coverage-detail.png");
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(out.join(&name))?;
        let mut encoder = png::Encoder::new(BufWriter::new(file), w as u32, crop_h as u32);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        let rgba = coverage_pixels(w, rows, pixels, offset, crop_y, crop_h);
        encoder.write_header()?.write_image_data(&rgba)?;
        files.push(json!({"arm":label,"file":name,"file_sha256":hash(&fs::read(out.join(&name))?),"rgba_sha256":hash(&rgba)}));
    }
    Ok(
        json!({"width":w,"height":crop_h,"crop_y_in_union":crop_y,"baseline_y_in_union":bo,"candidate_y_in_union":no,"union_height":union_height,"extra_nonempty_rows":extra.len(),"extra_nonempty_rows_in_detail":best_count,"baseline_extent_in_detail":[bo as isize-crop_y as isize,(bo+bh) as isize-crop_y as isize],"candidate_extent_in_detail":[no as isize-crop_y as isize,(no+nh) as isize-crop_y as isize],"files":files,"display_rule":"Source samples copied exactly to RGB; alpha=0 only OUTSIDE original output extent. Transparency means no decoded coverage, not black received pixels. Must be visibly labelled in the gallery.","selection":"Selected area maximizes newly covered nonempty rows; not representative whole-recording quality."}),
    )
}
fn times(image: &Value) -> Result<Vec<f64>, E> {
    image["timestamps"]
        .as_array()
        .ok_or("missing channel times")?
        .iter()
        .map(|t| t.as_f64().ok_or_else(|| "invalid timestamp".into()))
        .collect()
}
fn main() -> Result<(), E> {
    let args: Vec<_> = std::env::args().collect();
    if args.len() == 6 && args[1] == "--packet-compare" {
        let values: Vec<Value> = args[2..5]
            .iter()
            .map(|p| Ok::<Value, E>(serde_json::from_slice(&fs::read(p)?)?))
            .collect::<Result<_, _>>()?;
        let sets: Vec<BTreeSet<String>> = values
            .iter()
            .map(|v| {
                v["packets"]
                    .as_array()
                    .ok_or("missing packets")?
                    .iter()
                    .map(|p| {
                        p["sha256"]
                            .as_str()
                            .map(str::to_string)
                            .ok_or("missing packet hash")
                    })
                    .collect()
            })
            .collect::<Result<_, _>>()?;
        let mut rows = Vec::new();
        for apid in 64..=70 {
            let counts: Vec<_> = values
                .iter()
                .map(|v| {
                    v["packets"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .filter(|p| p["apid"] == apid)
                        .count()
                })
                .collect();
            rows.push(json!({"apid":apid,"baseline":counts[0],"native":counts[1],"hybrid":counts[2],"hybrid_net_gain":counts[2] as i64-counts[0] as i64}));
        }
        let report = json!({"schema":"meteor-complete-packet-comparison-v1","baseline":sets[0].len(),"native":sets[1].len(),"hybrid":sets[2].len(),"native_only":sets[1].difference(&sets[0]).count(),"baseline_only":sets[0].difference(&sets[1]).count(),"hybrid_only":sets[2].difference(&sets[0]).count(),"hybrid_losses":sets[0].difference(&sets[2]).count(),"by_apid":rows,"source_report_sha256":args[2..5].iter().map(|p|Ok::<_,E>(hash(&fs::read(p)?))).collect::<Result<Vec<_>,_>>()?,"independent_crc":false});
        OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&args[5])?
            .write_all(&serde_json::to_vec_pretty(&report)?)?;
        println!("{}", serde_json::to_string_pretty(&report)?);
        return Ok(());
    }
    if args.len() == 3 && args[1] == "--inspect" {
        println!(
            "{}",
            serde_json::to_string_pretty(&read_cbor(Path::new(&args[2]))?)?
        );
        return Ok(());
    }
    if args.len() != 4 && args.len() != 5 {
        return Err(
            "usage: meteor-image-audit BASELINE_MSU_MR_DIR CANDIDATE_MSU_MR_DIR NEW_OUTPUT_DIR [DETAIL_ROWS_MULTIPLE_OF_8]"
                .into(),
        );
    }
    let bdir = Path::new(&args[1]);
    let ndir = Path::new(&args[2]);
    let out = Path::new(&args[3]);
    let requested_rows = args
        .get(4)
        .map(|s| s.parse::<usize>())
        .transpose()?
        .unwrap_or(256);
    if requested_rows == 0 || requested_rows > 2048 || requested_rows % 8 != 0 {
        return Err("detail rows must be a positive multiple of 8, at most 2048".into());
    }
    if out.exists() {
        return Err("output must not exist".into());
    }
    let bc = read_cbor(&bdir.join("product.cbor"))?;
    let nc = read_cbor(&ndir.join("product.cbor"))?;
    let bi = bc["images"].as_array().ok_or("baseline has no images")?;
    let ni = nc["images"].as_array().ok_or("candidate has no images")?;
    fs::create_dir(out)?;
    let mut channels = Vec::new();
    for base in bi {
        let file = base["file"].as_str().ok_or("image file missing")?;
        if Path::new(file).components().count() != 1 {
            return Err("unsafe product filename".into());
        }
        let native = ni
            .iter()
            .find(|i| i["file"] == file)
            .ok_or("candidate channel missing")?;
        if base["ifov_y"] != 8 || native["ifov_y"] != 8 {
            return Err("expected eight-line segments".into());
        }
        let bt = times(base)?;
        let nt = times(native)?;
        let mut lookup = BTreeMap::<u64, Vec<usize>>::new();
        for (j, t) in nt.iter().enumerate() {
            if *t > 0.0 {
                lookup.entry(t.to_bits()).or_default().push(j);
            }
        }
        let mut offsets = BTreeMap::<isize, usize>::new();
        for (i, t) in bt.iter().enumerate() {
            if let Some(js) = lookup.get(&t.to_bits()) {
                if js.len() == 1 {
                    *offsets.entry(js[0] as isize - i as isize).or_default() += 1;
                }
            }
        }
        if offsets.len() != 1 || offsets.values().next().copied().unwrap_or(0) < 20 {
            return Err(format!("{file}: cannot prove a constant alignment: {offsets:?}").into());
        }
        let delta = *offsets.keys().next().unwrap();
        let (w, bh, bp) = png_read(&bdir.join(file))?;
        let (nw, nh, np) = png_read(&ndir.join(file))?;
        if w != 1568 || nw != w || bh != bt.len() * 8 || nh != nt.len() * 8 {
            return Err("product geometry/timestamp mismatch".into());
        }
        let bs = (-delta).max(0) as usize * 8;
        let ns = delta.max(0) as usize * 8;
        let height = (bh - bs).min(nh - ns);
        let b = &bp[bs * w..(bs + height) * w];
        let n = &np[ns * w..(ns + height) * w];
        let mut gained = Vec::new();
        let mut lost = Vec::new();
        let mut changed_nonzero = 0;
        let mut new_nonzero = 0;
        for (&x, &y) in b.iter().zip(n) {
            if x == 0 && y != 0 {
                new_nonzero += 1;
            }
            if x != 0 && y != 0 && x != y {
                changed_nonzero += 1;
            }
        }
        for y in (0..height).step_by(8) {
            for x in (0..w).step_by(112) {
                let b_empty =
                    (y..y + 8).all(|yy| b[yy * w + x..yy * w + x + 112].iter().all(|p| *p == 0));
                let n_empty =
                    (y..y + 8).all(|yy| n[yy * w + x..yy * w + x + 112].iter().all(|p| *p == 0));
                if b_empty && !n_empty {
                    gained.push((x, y));
                }
                if !b_empty && n_empty {
                    lost.push((x, y));
                }
            }
        }
        let crop_height = height.min(requested_rows);
        let crop_y = gained
            .iter()
            .map(|&(_, y)| y.saturating_sub(crop_height / 2).min(height - crop_height) / 8 * 8)
            .max_by_key(|start| {
                gained
                    .iter()
                    .filter(|(_, y)| *y >= *start && *y < start + crop_height)
                    .count()
            })
            .unwrap_or(0);
        let stem = file.trim_end_matches(".png");
        png_write(&out.join(format!("{stem}-baseline.png")), w, height, b)?;
        png_write(&out.join(format!("{stem}-candidate.png")), w, height, n)?;
        png_write(
            &out.join(format!("{stem}-baseline-detail.png")),
            w,
            crop_height,
            &b[crop_y * w..(crop_y + crop_height) * w],
        )?;
        png_write(
            &out.join(format!("{stem}-candidate-detail.png")),
            w,
            crop_height,
            &n[crop_y * w..(crop_y + crop_height) * w],
        )?;
        let coverage = coverage_detail(out, stem, (w, bh, &bp), (nh, &np), delta, requested_rows)?;
        channels.push(json!({"file":file,"baseline_sha256":hash(&fs::read(bdir.join(file))?),"candidate_sha256":hash(&fs::read(ndir.join(file))?),"baseline_height":bh,"candidate_height":nh,"common_height":height,"native_minus_baseline_scan_index":delta,"alignment_matches":offsets.values().next(),"baseline_crop_start_y":bs,"candidate_crop_start_y":ns,"gained_112x8_zero_to_nonzero_tiles":gained.len(),"lost_112x8_nonzero_to_zero_tiles":lost.len(),"gained_tile_coordinates_common_grid":gained,"lost_tile_coordinates_common_grid":lost,"zero_to_nonzero_pixels":new_nonzero,"changed_nonzero_pixels":changed_nonzero,"detail_crop_y":crop_y,"detail_crop_height":crop_height,"coverage_detail":coverage,"pixel_note":"Tile presence is an image-output measure, not pixel ground truth or a substitute for packet checks"}));
    }
    let report = json!({"schema":"meteor-pixel-preserving-image-audit-v1","baseline_products":bdir,"candidate_products":ndir,"renderer":"same unchanged SatDump 1.2.2 MSU-MR instrument module; fill_missing=false","alignment":"single constant integer scan offset supported by every uniquely matched timestamp; common extent only","changes":"copy/crop only; original 8-bit pixel values retained","selection":"detail region selected for most recovered empty tiles; not an unbiased overview","channels":channels});
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(out.join("report.json"))?
        .write_all(&serde_json::to_vec_pretty(&report)?)?;
    println!(
        "{}",
        json!({"channels":channels.len(),"report":out.join("report.json")})
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn coverage_distinguishes_missing_extent_from_decoded_black_pixels() {
        let pixels = coverage_pixels(2, 2, &[0, 17, 34, 255], 1, 0, 4);
        assert_eq!(&pixels[..8], &[0; 8]);
        assert_eq!(&pixels[8..16], &[0, 0, 0, 255, 17, 17, 17, 255]);
        assert_eq!(&pixels[16..24], &[34, 34, 34, 255, 255, 255, 255, 255]);
        assert_eq!(&pixels[24..], &[0; 8]);
        assert_eq!(
            coverage_pixels(2, 2, &[0, 17, 34, 255], 1, 2, 1),
            pixels[16..24]
        );
    }
}
