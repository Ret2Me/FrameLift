//! CLI orchestration. DSP and durable state live in the library.

mod args;
mod diagnostics;
pub(crate) mod io;

use args::{Cli, Commands};
use clap::Parser;
use diagnostics::{bounded_score_work, null_smoke};
use io::{load_metadata, stdin_json};
use serde_json::{Value, json};
use sha2::Digest;
use std::io::Write;
use std::time::Instant;
use telemetry_yield_rs::{
    adaptive, afsk_legacy, audit, backends, benchmark, campaign, cli_limits, clipping,
    clipping_file, compat, compute, cw, dsp, fec, formats, generic, input, ledger, physical,
    progressive, protocol, receiver, soft, space_link, triage,
};

pub(super) fn run() -> Result<Value, String> {
    let cli = Cli::parse();
    let compute_options = cli.compute_options();
    compute_options.validate()?;
    // Never initialize a CUDA context in a parent about to fork/exec a worker.
    if !matches!(
        &cli.command,
        Commands::DecodeProgressive { .. }
            | Commands::ProgressiveWorker { .. }
            | Commands::BenchmarkComputePair { .. }
    ) {
        compute::initialize(&compute_options)?;
    }
    match cli.command {
        Commands::ComputeInfo => Ok(compute::current().report()),
        Commands::BenchmarkComputePair {
            input,
            output,
            candidate,
            candidate_threads,
            threads,
            repeats,
        } => compute::qualification::benchmark_pair(
            &input,
            &output,
            candidate,
            candidate_threads,
            threads,
            repeats,
            &compute_options,
        ),
        Commands::AuditComputePair {
            cpu_session,
            candidate_session,
            output,
        } => progressive::compute_audit::run(&cpu_session, &candidate_session, &output),
        Commands::QualifyCompute {
            input,
            repeats,
            output,
        } => compute::qualification::run(input.as_deref(), repeats, &output),
        Commands::AssembleGeoscan {
            input: path,
            output,
            kiss,
        } => {
            let raw = input::read_bytes_bounded(&path, 64 * 1024 * 1024)?;
            let mut pdus = Vec::new();
            if kiss {
                let parsed = formats::parse_kiss(&raw)?;
                if parsed.malformed_records != 0 {
                    return Err("malformed KISS records".into());
                }
                for frame in parsed.data_frames {
                    pdus.push(frame.payload);
                }
            } else {
                let value: Value = serde_json::from_slice(&raw).map_err(|e| e.to_string())?;
                for frame in value["frames"].as_array().ok_or("frames array required")? {
                    let bytes =
                        hex::decode(frame["frame_hex"].as_str().ok_or("frame_hex required")?)
                            .map_err(|e| e.to_string())?;
                    if !telemetry_yield_rs::geoscan::valid_frame(&bytes) {
                        return Err("native frame failed received Geoscan CRC".into());
                    }
                    pdus.push(bytes[..64].to_vec());
                }
            }
            let image = telemetry_yield_rs::geoscan::assemble(&pdus)?;
            let out = input::existing_new_dir(&output)?;
            let name = if image.all_bytes_received_through_eoi {
                "recovered.jpg"
            } else {
                "recovered.partial.jpg"
            };
            let mut file = std::fs::OpenOptions::new()
                .create_new(true)
                .write(true)
                .open(out.join(name))
                .map_err(|e| e.to_string())?;
            file.write_all(&image.bytes).map_err(|e| e.to_string())?;
            input::write_json_new(&out.join("coverage.json"), &image.received)?;
            let report = json!({"schema":"geoscan-image-v1","input_sha256":hex::encode(sha2::Sha256::digest(&raw)),"integrity":if kiss {"external_decoder_crc_not_rechecked"}else{"received_crc16_cc11xx_rechecked"},"jpeg":name,"bytes":image.bytes.len(),"received_bytes":image.received.iter().filter(|&&b|b).count(),"missing_ranges":image.missing_ranges,"used_packets":image.used_packets,"rejected_packets":image.rejected_packets,"base_address":image.base_address,"has_received_eoi":image.has_received_eoi,"all_bytes_received_through_eoi":image.all_bytes_received_through_eoi,"missing_byte_fill":0,"jpeg_semantics_validated":false});
            input::write_json_new(&out.join("report.json"), &report)?;
            Ok(report)
        }
        Commands::ParseSpaceLink => {
            let value = stdin_json()?;
            let config: space_link::SpaceLinkConfig =
                serde_json::from_value(value["config"].clone()).map_err(|e| e.to_string())?;
            let bytes = hex::decode(value["frame_hex"].as_str().ok_or("frame_hex required")?)
                .map_err(|e| e.to_string())?;
            let frame = config.decode(&bytes)?;
            Ok(json!({"schema":"rust-space-link-frame-v1","frame":frame,
                "integrity_verified":config.has_integrity_check(),"structural_only":!config.has_integrity_check()}))
        }
        Commands::DecodeCodeword => {
            let value = stdin_json()?;
            let code: fec::FrameCode =
                serde_json::from_value(value["code"].clone()).map_err(|e| e.to_string())?;
            let soft: Vec<f64> =
                serde_json::from_value(value["soft"].clone()).map_err(|e| e.to_string())?;
            let length = usize::try_from(
                value["decoded_bytes"]
                    .as_u64()
                    .ok_or("decoded_bytes required")?,
            )
            .map_err(|e| e.to_string())?;
            let decoded = code.decode(&soft, length)?;
            Ok(
                json!({"schema":"rust-frame-codeword-v1","decoded":decoded,"telemetry_validated":false}),
            )
        }
        Commands::FecProfile {
            profile,
            interleaving,
            shortening,
        } => {
            let code = match profile.as_str() {
                "ccsds-rs255-223" => fec::FrameCode::ReedSolomon {
                    config: fec::ReedSolomonConfig::ccsds_rs255_223(interleaving, shortening),
                },
                "ccsds-tc128" | "ccsds-tc512" => {
                    if interleaving != 1 || shortening != 0 {
                        return Err(
                            "TC LDPC profiles do not accept RS interleaving/shortening options"
                                .into(),
                        );
                    }
                    fec::FrameCode::Ldpc {
                        config: if profile == "ccsds-tc128" {
                            fec::LdpcConfig::ccsds_tc128()
                        } else {
                            fec::LdpcConfig::ccsds_tc512()
                        },
                    }
                }
                _ => return Err("unknown native FEC profile".into()),
            };
            code.validate()?;
            serde_json::to_value(code).map_err(|e| e.to_string())
        }
        Commands::DecodeProgressive {
            input,
            output,
            mode,
            budget_ms,
            threads,
            cache_mib,
            scheduler,
            baud,
            resume,
            no_blind,
            no_multi_anchor,
        } => progressive::supervise(
            &input,
            &output,
            &progressive::Options {
                compute: compute_options,
                mode,
                budget_ms,
                threads,
                cache_mib,
                scheduler,
                baud,
                no_blind,
                no_multi_anchor,
            },
            resume,
        ),
        Commands::ProgressiveWorker {
            input,
            output,
            options,
            scratch,
            lock_fd,
        } => {
            let invocation = options
                .parent()
                .ok_or("worker options require invocation directory")?;
            let options: progressive::Options =
                serde_json::from_value(input::read_json(&options)?).map_err(|e| e.to_string())?;
            progressive::worker(&input, &output, &options, lock_fd, invocation, &scratch)
        }
        Commands::DecodeAfskLegacy {
            input: path,
            format,
            config,
            output,
            start_sample,
            sample_count,
        } => {
            let value = input::read_json(&config)?;
            if value.get("sample_rate_hz").is_none() {
                return Err("legacy AFSK requires explicit sample_rate_hz".into());
            }
            let config: afsk_legacy::Afsk1200Config =
                serde_json::from_value(value).map_err(|e| e.to_string())?;
            config.validate()?;
            let format: generic::InputFormat =
                serde_json::from_value(json!(format)).map_err(|e| e.to_string())?;
            let source = input::identity(&path)?;
            let out = input::existing_new_dir(&output)?;
            let iq = generic::read_iq_window(&path, &format, start_sample, sample_count)?;
            let result = afsk_legacy::decode_afsk1200_iq(&iq, &config, start_sample as u64)?;
            if input::identity(&path)?.sha256 != source.sha256 {
                return Err("IQ changed during legacy AFSK decode".into());
            }
            let report = json!({"schema":"rust-legacy-afsk-window-v1","status":"complete",
                "source":source,"format":format,"config":config,"start_sample":start_sample,
                "sample_count":sample_count,"result":result,"reference_truth_used_for_recovery":false,
                "rejected_frames_are_validated_telemetry":false,"python_runtime_required":false});
            input::write_json_new(&out.join("result.json"), &report)?;
            Ok(report)
        }
        Commands::InspectCw {
            input: path,
            config,
            output,
        } => {
            let value = input::read_json(&config)?;
            if value.get("sample_rate_hz").is_none() {
                return Err("CW inspection requires explicit sample_rate_hz".into());
            }
            let config: cw::CwProbeConfig =
                serde_json::from_value(value).map_err(|e| e.to_string())?;
            let source = input::identity(&path)?;
            let result = cw::extract_ci16(&path, &config)?;
            if input::identity(&path)?.sha256 != source.sha256 {
                return Err("IQ changed during CW inspection".into());
            }
            let report = json!({"schema":"rust-cw-candidate-inspection-v1","status":"complete",
                "source":source,"config":config,"capabilities":cw::capabilities(),"result":result,
                "native_trusted":false,"telemetry_validation":false,"automatic_decoder_pruning":false});
            input::write_json_new(&output, &report)?;
            Ok(report)
        }
        Commands::CompatPdus { stage, max_length } => {
            let data = stdin_json()?;
            let bits: Vec<u8> =
                serde_json::from_value(data["bits"].clone()).map_err(|e| e.to_string())?;
            if bits.len() > 4_194_304 || !(1..=1_048_576).contains(&max_length) {
                return Err("compatibility extraction requires at most4194304 bits and max_length in1..=1048576".into());
            }
            let candidates = if stage == "decoded-bits" {
                compat::extract_gr_satellites_hdlc_pdus(&bits, max_length)?
            } else {
                compat::decode_gr_satellites_g3ruh_levels(&bits, max_length)?
            };
            Ok(json!({"schema":"rust-gnu-hdlc-compatibility-candidates-v1",
                "stage":stage,"candidates":candidates,"native_trusted":false,
                "note":"GNU-compatible CRC-valid PDUs may be malformed or undersized; not automatically validated telemetry"}))
        }
        Commands::InspectIq {
            input: path,
            config,
            operation,
            output,
        } => {
            let config = input::read_json(&config)?;
            if config.get("sample_rate_hz").is_none() {
                return Err("explicit sample_rate_hz is required for IQ diagnostics".into());
            }
            let source = input::identity(&path)?;
            let result = match operation.as_str() {
                "triage-ci16" => triage::triage_ci16le_file(
                    &path,
                    &serde_json::from_value(config.clone()).map_err(|e| e.to_string())?,
                )?,
                "route-ci16" => triage::route_ci16le_waveform(
                    &path,
                    &serde_json::from_value(config.clone()).map_err(|e| e.to_string())?,
                )?,
                "burst-windows-ci16" => triage::select_protocol_neutral_ci16_windows(
                    &path,
                    &serde_json::from_value(config.clone()).map_err(|e| e.to_string())?,
                )?,
                "phase-windows-cf32" => triage::select_phase_windows_cf32(
                    &path,
                    &serde_json::from_value(config.clone()).map_err(|e| e.to_string())?,
                )?,
                _ => return Err("unsupported diagnostic".into()),
            };
            if input::identity(&path)?.sha256 != source.sha256 {
                return Err("IQ changed during diagnostics".into());
            }
            let report = json!({"schema":"rust-iq-diagnostic-v1","status":"complete","operation":operation,"config":config,
                "source":source,"result":result,"telemetry_validation":false,"automatic_decoder_pruning":false});
            input::write_json_new(&output, &report)?;
            Ok(report)
        }
        Commands::BaselineGrSatellites {
            config,
            segment,
            output,
            plan_only,
        } => {
            let config: backends::GrSatellitesBackend =
                serde_json::from_value(input::read_json(&config)?).map_err(|e| e.to_string())?;
            let segment: backends::CaptureSegment =
                serde_json::from_value(input::read_json(&segment)?).map_err(|e| e.to_string())?;
            let source = input::identity(&segment.path)?;
            let out = input::existing_new_dir(&output)?;
            let planned_argv = config.build_argv(&segment, &out.join("planned-output.kiss"))?;
            input::write_json_new(
                &out.join("plan.json"),
                &json!({"schema":"rust-external-baseline-plan-v1","config":config,"segment":segment,"source":source,
                "planned_argv":planned_argv,"temporary_kiss_path_may_differ_at_execution":true,"plan_only":plan_only,"network_submission":false}),
            )?;
            if plan_only {
                return Ok(json!({"status":"planned","argv":planned_argv,"executed":false}));
            }
            let decoded = config.run(&segment);
            if input::identity(&segment.path)?.sha256 != source.sha256 {
                return Err("baseline input changed during external decode".into());
            }
            let result = json!({"schema":"rust-external-baseline-result-v1","status":if decoded.succeeded(){"complete"}else{"failed"},
                "source":source,"result":decoded,"telemetry_validation":false,"network_submission":false});
            input::write_json_new(&out.join("result.json"), &result)?;
            Ok(result)
        }
        Commands::DecodeMetadata {
            metadata,
            kind,
            plan,
            output,
            threads,
            resume,
        } => {
            let metadata = load_metadata(&metadata, &kind)?;
            let plan: generic::Plan =
                serde_json::from_value(input::read_json(&plan)?).map_err(|e| e.to_string())?;
            generic::decode_metadata_resumable(&metadata, &output, &plan, threads, resume)
        }
        Commands::InspectMetadata { metadata, kind } => Ok(
            json!({"schema":"rust-iq-metadata-inspection-v1","metadata":load_metadata(&metadata,&kind)?,"telemetry_validation":false}),
        ),
        Commands::ParseKiss { input: path } => {
            let source = input::identity(&path)?;
            let bytes = input::read_bytes_bounded(&path, 64 * 1024 * 1024)?;
            if hex::encode(sha2::Sha256::digest(&bytes)) != source.sha256 {
                return Err("KISS source changed".into());
            }
            Ok(
                json!({"schema":"rust-kiss-transport-inspection-v1","source":source,"records":formats::parse_kiss(&bytes)?,"telemetry_validation":false}),
            )
        }
        Commands::InspectSatyaml { paths } => Ok(
            json!({"schema":"rust-satyaml-inspection-v1","registry":formats::build_satyaml_registry(&paths,4*1024*1024)?,"telemetry_validation":false}),
        ),
        Commands::DecodeClippedCi16 {
            input: path,
            config,
            output,
            selected_windows,
            resume,
            maximum_scratch_bytes,
        } => {
            let config_json = input::read_json(&config)?;
            if config_json.get("sample_rate_hz").is_none() || config_json.get("baudrate").is_none()
            {
                return Err("explicit CI16 sample_rate_hz and baudrate required".into());
            }
            let config: clipping::BlindPhaseFskConfig =
                serde_json::from_value(config_json).map_err(|e| e.to_string())?;
            let selected: Option<Vec<clipping::PhaseWindowCandidate>> = selected_windows
                .as_deref()
                .map(|p| {
                    input::read_json(p)
                        .and_then(|v| serde_json::from_value(v).map_err(|e| e.to_string()))
                })
                .transpose()?;
            let source = input::identity(&path)?;
            clipping_file::run_blind_phase_fsk_file(
                &path,
                &output,
                &clipping_file::BlindPhaseFskRunConfig {
                    receiver: config,
                    expected_input_size_bytes: source.bytes,
                    expected_input_sha256: source.sha256,
                    maximum_scratch_bytes,
                },
                selected.as_deref(),
                resume,
            )
        }
        Commands::DeclipCi16 {
            input: path,
            output,
            sample_rate_hz,
            bandlimit_hz,
            start_sample,
            sample_count,
            checkpoints,
        } => {
            let source = input::identity(&path)?;
            let out = input::existing_new_dir(&output)?;
            let iq = generic::read_iq_window(
                &path,
                &generic::InputFormat::Ci16Le,
                start_sample,
                sample_count,
            )?;
            let components: Vec<_> = iq.iter().map(|s| [s.re as i16, s.im as i16]).collect();
            let results = clipping::projected_bandlimited_declipping(
                &components,
                sample_rate_hz,
                bandlimit_hz,
                &checkpoints,
            )?;
            let mut artifacts = Vec::new();
            for (iteration, checkpoint) in results {
                let path = out.join(format!("iteration-{iteration}.cf64_le"));
                let file = std::fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&path)
                    .map_err(|e| e.to_string())?;
                let mut writer = std::io::BufWriter::new(file);
                for sample in &checkpoint.reconstruction {
                    for component in sample {
                        writer
                            .write_all(&component.to_le_bytes())
                            .map_err(|e| e.to_string())?;
                    }
                }
                writer.flush().map_err(|e| e.to_string())?;
                writer.get_ref().sync_all().map_err(|e| e.to_string())?;
                artifacts.push(json!({"identity":input::identity(&path)?,"metrics":checkpoint.metrics,"representation":checkpoint.representation}));
            }
            if input::identity(&path)?.sha256 != source.sha256 {
                return Err("CI16 source changed during projection".into());
            }
            let result = json!({"schema":"rust-declipping-reconstruction-v1","status":"complete","source":source,"sample_rate_hz":sample_rate_hz,
                "bandlimit_hz":bandlimit_hz,"start_sample":start_sample,"sample_count":sample_count,"artifacts":artifacts,
                "original_iq":false,"telemetry_validation":false});
            input::write_json_new(&out.join("result.json"), &result)?;
            Ok(result)
        }
        Commands::BenchmarkAudio {
            input,
            output,
            workers,
            repetitions,
            first_seconds,
            settings,
        } => benchmark::audio(
            &input,
            &output,
            &settings.config(),
            &workers,
            repetitions,
            first_seconds,
        ),
        Commands::CandidateLedger => {
            let data = stdin_json()?;
            let detections: Vec<ledger::DetectionRecord> =
                serde_json::from_value(data["detections"].clone()).map_err(|e| e.to_string())?;
            ledger::build_candidate_ledger(&detections)
        }
        Commands::PhysicalDecode {
            input: path,
            format,
            config,
            output,
            start_sample,
            sample_count,
        } => {
            let value = input::read_json(&config)?;
            if value.get("sample_rate_hz").is_none()
                || value.get("symbol_rate_hz").is_none()
                || value.get("modulation").is_none()
            {
                return Err("physical CLI requires explicit sample_rate_hz, symbol_rate_hz and modulation metadata".into());
            }
            let config: physical::PhysicalConfig =
                serde_json::from_value(value).map_err(|e| e.to_string())?;
            let format: generic::InputFormat =
                serde_json::from_value(json!(format)).map_err(|e| e.to_string())?;
            let source = input::identity(&path)?;
            let out = input::existing_new_dir(&output)?;
            let start = Instant::now();
            let iq = generic::read_iq_window(&path, &format, start_sample, sample_count)?;
            let bits = physical::demodulate_unaligned(&iq, &config)?;
            if input::identity(&path)?.sha256 != source.sha256 {
                return Err("IQ changed during physical decode".into());
            }
            let result = json!({"schema":"rust-physical-unaligned-v1","status":"complete","source":source,"config":config,
                "start_sample":start_sample,"sample_count":sample_count,"receiver_output":bits,
                "elapsed_seconds":start.elapsed().as_secs_f64(),"reference_truth_used_for_recovery":false,
                "validated_telemetry_frames_claimed":false,"python_runtime_required":false});
            input::write_json_new(&out.join("result.json"), &result)?;
            Ok(result)
        }
        Commands::ScoreBits => {
            let data = stdin_json()?;
            let truth: Vec<u8> =
                serde_json::from_value(data["truth"].clone()).map_err(|e| e.to_string())?;
            let policy: physical::AlignmentPolicy =
                serde_json::from_value(data["policy"].clone()).map_err(|e| e.to_string())?;
            let scored = if data["qpsk"] == true {
                let candidate: physical::UnalignedBits =
                    serde_json::from_value(data["candidate"].clone()).map_err(|e| e.to_string())?;
                bounded_score_work(
                    &candidate
                        .bit_variants
                        .iter()
                        .map(Vec::len)
                        .collect::<Vec<_>>(),
                    &truth,
                    &policy,
                    true,
                )?;
                if candidate.bit_variants.iter().flatten().any(|b| *b > 1) {
                    return Err("scorer candidate bits must be0/1".into());
                }
                physical::align_qpsk_for_ber(&candidate, &truth, &policy)?
            } else {
                let candidate: Vec<u8> =
                    serde_json::from_value(data["candidate"].clone()).map_err(|e| e.to_string())?;
                bounded_score_work(&[candidate.len()], &truth, &policy, false)?;
                if candidate.iter().any(|b| *b > 1) {
                    return Err("scorer candidate bits must be0/1".into());
                }
                physical::align_for_ber(&candidate, &truth, &policy)?
            };
            Ok(
                json!({"schema":"rust-development-ber-score-v1","score":scored,"reference_truth_used_only_in_scorer":true,"validated_telemetry_frames_claimed":false}),
            )
        }
        Commands::SoftDecodeSymbols { method } => {
            let data = stdin_json()?;
            let symbols: Vec<f64> =
                serde_json::from_value(data["soft"].clone()).map_err(|e| e.to_string())?;
            if symbols.len() > 1_000_000 {
                return Err("soft-symbol input exceeds one million symbols".into());
            }
            let threshold = data["threshold"].as_f64().ok_or("threshold required")?;
            let budget: soft::SoftListBudget =
                serde_json::from_value(data["budget"].clone()).map_err(|e| e.to_string())?;
            let options: soft::SoftDecodeOptions =
                serde_json::from_value(data["options"].clone()).map_err(|e| e.to_string())?;
            let resource_policy =
                cli_limits::validate_soft(symbols.len(), &budget, &options, &method)?;
            let largest_cost = symbols
                .iter()
                .map(|value| (value - threshold).abs())
                .fold(0.0, f64::max);
            if !((largest_cost + budget.error_unit_penalty) * (budget.maximum_flips as f64 + 18.0))
                .is_finite()
            {
                return Err("soft values/threshold/penalty cause non-finite search costs".into());
            }
            let mut decoded = if method == "syndrome" {
                soft::protocol_constrained_syndrome_decode(&symbols, threshold, &budget, &options)?
            } else {
                soft::protocol_constrained_list_decode(&symbols, threshold, &budget, &options)?
            };
            // The historical list oracle checks HDLC/FCS but ignores its UI
            // option. Enforce the CLI contract independently without changing
            // that oracle, and disclose rejected candidates explicitly.
            if decoded.frames.len() > budget.maximum_output_frames {
                return Err("soft candidate output exceeds the declared maximum_output_frames; nothing was published".into());
            }
            let emitted = decoded.frames.len();
            decoded.frames.retain(|frame| {
                let bytes = &frame.frame_with_fcs;
                bytes.len() >= 2
                    && protocol::valid_ax25_fcs(bytes)
                    && protocol::valid_ax25_ui(&bytes[..bytes.len() - 2])
            });
            let rejected_by_cli_integrity = emitted - decoded.frames.len();
            Ok(
                json!({"schema":"rust-soft-repair-candidates-v1","method":method,"result":decoded,"native_trusted":false,
                "resource_policy":resource_policy,
                "cli_validation_layers":["crc16_x25","ax25_ui"],"rejected_by_cli_integrity":rejected_by_cli_integrity,
                "note":"CRC-constrained repaired outputs are candidates, not automatically trusted telemetry"}),
            )
        }
        Commands::Decode {
            input: source,
            plan,
            output,
            threads,
            resume,
        } => {
            let plan: generic::Plan =
                serde_json::from_value(input::read_json(&plan)?).map_err(|e| e.to_string())?;
            generic::decode_file_resumable(&source, &output, &plan, threads, resume)
        }
        Commands::DecodeAdaptiveAudio {
            input,
            output,
            observation_id,
            threads,
            baud,
        } => adaptive::decode_file(
            &input,
            &output,
            observation_id,
            &adaptive::AdaptiveConfig { baud, threads },
        ),
        Commands::DecodeAudio {
            input,
            output,
            observation_id,
            settings,
        } => receiver::decode_file(&input, &output, observation_id, &settings.config()),
        Commands::BatchAudio {
            reference_summary,
            output,
            observation_id,
            limit,
            resume,
            settings,
        } => campaign::run_local(
            &reference_summary,
            &output,
            &settings.config(),
            &campaign::CampaignOptions {
                threads: settings.threads,
                observation_id,
                limit,
                resume,
            },
        ),
        Commands::AuditArchiveCandidates {
            reference_summary,
            replay_root,
            output,
        } => {
            let result = audit::candidates::inventory(&reference_summary, &replay_root)?;
            input::write_json_new(&output, &result)?;
            Ok(json!({"status":"complete", "output":output,
                "completed_observations_verified":result["completed_observations_verified"],
                "global_archive_absent_pdus":result["global_archive_absent_pdus"],
                "global_archive_absent_bytes":result["global_archive_absent_bytes"],
                "mission_attribution_verified":false,"publication_ready":false}))
        }
        Commands::CompareCampaign {
            reference_summary,
            results,
            output,
        } => {
            let result = audit::compare_campaign(&reference_summary, &results)?;
            if let Some(path) = output {
                input::write_json_new(&path, &result)?;
            }
            Ok(result)
        }
        Commands::CompareObservation {
            reference_summary,
            observation_id,
            result_dir,
            output,
        } => {
            let result =
                audit::compare_observation(&reference_summary, observation_id, &result_dir)?;
            if let Some(path) = output {
                input::write_json_new(&path, &result)?;
            }
            Ok(result)
        }
        Commands::DecodeAx25Symbols => {
            let data = stdin_json()?;
            let soft: Vec<f64> =
                serde_json::from_value(data["soft"].clone()).map_err(|e| e.to_string())?;
            let threshold = data["threshold"].as_f64().ok_or("threshold required")?;
            let modes: Vec<bool> =
                serde_json::from_value(data["g3ruh_modes"].clone()).map_err(|e| e.to_string())?;
            Ok(json!(
                protocol::decode_ax25(&soft, threshold, &modes)?
                    .iter()
                    .map(hex::encode)
                    .collect::<Vec<_>>()
            ))
        }
        Commands::InspectDsp => {
            let data = stdin_json()?;
            let pcm: Vec<f64> =
                serde_json::from_value(data["pcm"].clone()).map_err(|e| e.to_string())?;
            let config: dsp::DspConfig = if data.get("config").is_some() {
                serde_json::from_value(data["config"].clone()).map_err(|e| e.to_string())?
            } else {
                dsp::DspConfig::default()
            };
            let rate = u32::try_from(data["sample_rate"].as_u64().ok_or("sample_rate required")?)
                .map_err(|e| e.to_string())?;
            let front = dsp::frontend(&pcm, rate, &config)?;
            let bank = dsp::timing_bank(&front, &config)?;
            Ok(
                json!({"samples":front.samples,"samples_per_symbol":front.samples_per_symbol,"timing":bank}),
            )
        }
        Commands::NullSmoke {
            output,
            per_kind,
            settings,
        } => null_smoke(&output, per_kind, &settings.config()),
        Commands::Capabilities => Ok(diagnostics::capabilities()),
    }
}
