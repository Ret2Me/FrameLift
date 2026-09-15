"""Satellite-neutral receiver orchestration with pluggable DSP and protocols.

The receiver deliberately knows nothing about SatNOGS, NORAD identifiers, or
one mission.  A search plan binds waveform hypotheses to protocol adapters;
new modulations and framing schemes can be registered without changing the
orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol, Sequence

from .ax25_validation import parse_ax25_ui
from .camras_replay import bits_to_bytes
from .ccsds_validation import CCSDSPrimaryHeader, parse_ccsds_space_packet
from .ccsds_tm_validation import (
    TMIntegrityValidator,
    TMTransferFrameConfig,
    validate_tm_transfer_frame,
)
from .clock_recovery import (
    decode_ax25_levels,
    deterministic_direct_phase_fsk_demodulate,
    deterministic_fsk_demodulate,
    recover_timing_hypotheses,
    soft_symbols_with_hypothesis,
)


BINARY_SOFT_SYMBOLS = "binary_soft_symbols"


@dataclass(frozen=True, slots=True)
class DemodulatorCapabilities:
    """Machine-readable contract advertised by one demodulator plugin.

    A modulation family is a physical-layer claim, not a mission name.  The
    symbol kind describes the boundary exposed to protocol adapters after
    the receiver's timing bank.
    """

    modulation_families: tuple[str, ...]
    output_symbol_kind: str = BINARY_SOFT_SYMBOLS
    features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.modulation_families or any(
            not item.strip() for item in self.modulation_families
        ):
            raise ValueError("at least one non-empty modulation family is required")
        if not self.output_symbol_kind.strip() or any(
            not item.strip() for item in self.features
        ):
            raise ValueError("symbol kind and feature names must be non-empty")


@dataclass(frozen=True, slots=True)
class ProtocolCapabilities:
    """Input requirements and validation layers of a protocol plugin."""

    accepted_symbol_kinds: tuple[str, ...]
    framing: str
    protocol_stack: tuple[str, ...]
    validation_layers: tuple[str, ...]
    required_demodulator_features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        groups = (
            self.accepted_symbol_kinds,
            (self.framing,),
            self.protocol_stack,
            self.validation_layers,
        )
        if any(
            not group or any(not item.strip() for item in group) for group in groups
        ):
            raise ValueError("protocol capability fields must be non-empty")
        if any(not item.strip() for item in self.required_demodulator_features):
            raise ValueError("required feature names must be non-empty")


@dataclass(frozen=True, slots=True)
class ReceiverCapabilities:
    """Snapshot of the currently registered plugin contracts."""

    demodulators: Mapping[str, DemodulatorCapabilities]
    protocols: Mapping[str, ProtocolCapabilities]


@dataclass(frozen=True, slots=True)
class WaveformHypothesis:
    """One bounded physical-layer hypothesis, independent of satellite ID."""

    hypothesis_id: str
    demodulator_id: str
    symbol_rate: float
    decimation: int
    rate_errors_ppm: tuple[float, ...] = (0.0,)
    phase_bins: int = 32
    top_timing_hypotheses: int = 8
    parameters: Mapping[str, float | int | str | bool] = field(default_factory=dict)
    modulation_family: str | None = None

    def __post_init__(self) -> None:
        if not self.hypothesis_id.strip() or not self.demodulator_id.strip():
            raise ValueError("hypothesis and demodulator IDs must be non-empty")
        if self.symbol_rate <= 0 or self.decimation < 1:
            raise ValueError("symbol_rate and decimation must be positive")
        if not self.rate_errors_ppm:
            raise ValueError("rate_errors_ppm must not be empty")
        if self.phase_bins < 4 or self.top_timing_hypotheses < 1:
            raise ValueError("invalid timing-search budget")
        if self.modulation_family is not None and not self.modulation_family.strip():
            raise ValueError("modulation_family must be non-empty when supplied")


@dataclass(frozen=True, slots=True)
class ReceiverHypothesis:
    """Pair one waveform with one protocol interpretation."""

    waveform: WaveformHypothesis
    protocol_id: str

    def __post_init__(self) -> None:
        if not self.protocol_id.strip():
            raise ValueError("protocol_id must be non-empty")


@dataclass(frozen=True, slots=True)
class DemodulatedSignal:
    """Protocol-neutral real-valued symbol waveform."""

    samples: tuple[float, ...]
    samples_per_symbol: float
    metrics: Mapping[str, float | int | str]
    symbol_kind: str = BINARY_SOFT_SYMBOLS

    def __post_init__(self) -> None:
        if not self.symbol_kind.strip():
            raise ValueError("symbol_kind must be non-empty")


@dataclass(frozen=True, slots=True)
class FrameCandidate:
    """A validated frame plus complete hypothesis provenance."""

    payload: bytes
    protocol_id: str
    waveform_hypothesis_id: str
    timing_rank: int
    timing_score: float
    validation: str


@dataclass(frozen=True, slots=True)
class ReceiverResult:
    """Deterministic output and bounded-search accounting."""

    frames: tuple[FrameCandidate, ...]
    attempted_waveforms: int
    attempted_timing_hypotheses: int
    failures: tuple[str, ...]


class Demodulator(Protocol):
    """Convert complex IQ into a protocol-neutral real-valued waveform."""

    @property
    def capabilities(self) -> DemodulatorCapabilities: ...

    def demodulate(
        self,
        iq: Sequence[complex],
        *,
        sample_rate_hz: float,
        hypothesis: WaveformHypothesis,
    ) -> DemodulatedSignal: ...


class ProtocolDecoder(Protocol):
    """Interpret soft symbols and return only validated byte frames."""

    protocol_id: str

    @property
    def capabilities(self) -> ProtocolCapabilities: ...

    def decode(
        self, soft_symbols: Sequence[float], *, threshold: float
    ) -> tuple[tuple[bytes, str], ...]: ...


@dataclass(frozen=True, slots=True)
class PhaseFskDemodulator:
    """Legacy binary-FSK path: phase difference before real filtering."""

    demodulator_id: str = "phase_fsk"

    @property
    def capabilities(self) -> DemodulatorCapabilities:
        return DemodulatorCapabilities(
            modulation_families=("fsk", "gfsk", "gmsk"),
            output_symbol_kind=BINARY_SOFT_SYMBOLS,
            features=(
                "binary_threshold_timing_bank",
                "phase_discriminator_before_real_filter",
                "configurable_sample_rate",
                "configurable_symbol_rate",
                "configurable_decimation",
                "configurable_post_discriminator_cutoff",
            ),
        )

    def demodulate(
        self,
        iq: Sequence[complex],
        *,
        sample_rate_hz: float,
        hypothesis: WaveformHypothesis,
    ) -> DemodulatedSignal:
        configured_cutoff = hypothesis.parameters.get(
            "post_discriminator_cutoff_hz",
            0.75 * hypothesis.symbol_rate,
        )
        if isinstance(configured_cutoff, bool) or not isinstance(
            configured_cutoff, (float, int)
        ):
            raise ValueError("post_discriminator_cutoff_hz must be numeric")
        cutoff_hz = float(configured_cutoff)
        samples, metrics = deterministic_direct_phase_fsk_demodulate(
            iq,
            sample_rate_hz=int(round(sample_rate_hz)),
            baudrate=hypothesis.symbol_rate,
            decimation=hypothesis.decimation,
            post_discriminator_cutoff_hz=cutoff_hz,
        )
        samples_per_symbol = sample_rate_hz / (
            hypothesis.decimation * hypothesis.symbol_rate
        )
        return DemodulatedSignal(
            samples=tuple(float(value) for value in samples),
            samples_per_symbol=float(samples_per_symbol),
            metrics={
                "carrier_offset_hz": metrics.carrier_offset_hz,
                "normalization_scale": metrics.normalization_scale,
                "input_complex_samples": metrics.input_complex_samples,
                "output_real_samples": metrics.output_real_samples,
                "post_discriminator_cutoff_hz": cutoff_hz,
            },
        )


@dataclass(frozen=True, slots=True)
class ChannelConditionedPhaseFskDemodulator:
    """Carrier-corrected, channel-filtered binary FSK phase discriminator.

    The input sample rate is supplied by :meth:`GenericReceiver.decode_iq`;
    symbol rate and decimation are supplied by the selected
    :class:`WaveformHypothesis`.  Keeping those values in the typed receiver
    plan makes the frontend reusable without mission- or recording-specific
    constants.
    """

    demodulator_id: str = "channel_conditioned_phase_fsk"

    @property
    def capabilities(self) -> DemodulatorCapabilities:
        return DemodulatorCapabilities(
            modulation_families=("fsk",),
            output_symbol_kind=BINARY_SOFT_SYMBOLS,
            features=(
                "binary_threshold_timing_bank",
                "carrier_offset_estimation",
                "complex_channel_filter",
                "phase_discriminator_after_channel_filter",
                "configurable_sample_rate",
                "configurable_symbol_rate",
                "configurable_decimation",
            ),
        )

    def demodulate(
        self,
        iq: Sequence[complex],
        *,
        sample_rate_hz: float,
        hypothesis: WaveformHypothesis,
    ) -> DemodulatedSignal:
        effective_sample_rate_hz = int(round(sample_rate_hz))
        samples, metrics = deterministic_fsk_demodulate(
            iq,
            sample_rate_hz=effective_sample_rate_hz,
            baudrate=hypothesis.symbol_rate,
            decimation=hypothesis.decimation,
        )
        samples_per_symbol = sample_rate_hz / (
            hypothesis.decimation * hypothesis.symbol_rate
        )
        return DemodulatedSignal(
            samples=tuple(float(value) for value in samples),
            samples_per_symbol=float(samples_per_symbol),
            metrics={
                "frontend": "carrier_correct_channel_filter_phase_difference",
                "configured_sample_rate_hz": effective_sample_rate_hz,
                "configured_symbol_rate_baud": float(hypothesis.symbol_rate),
                "configured_decimation": hypothesis.decimation,
                "carrier_offset_hz": metrics.carrier_offset_hz,
                "normalization_scale": metrics.normalization_scale,
                "input_complex_samples": metrics.input_complex_samples,
                "output_real_samples": metrics.output_real_samples,
            },
        )


@dataclass(frozen=True, slots=True)
class Bell202AfskDemodulator:
    """Independent FM-audio Bell-202 energy demodulator.

    This is a normal waveform plugin: it emits binary soft symbols and knows
    neither satellite identities nor AX.25.  Framing remains an independently
    routed protocol adapter.
    """

    demodulator_id: str = "bell202_afsk"

    @property
    def capabilities(self) -> DemodulatorCapabilities:
        return DemodulatorCapabilities(
            modulation_families=("afsk",),
            output_symbol_kind=BINARY_SOFT_SYMBOLS,
            features=(
                "binary_threshold_timing_bank",
                "fm_audio_discriminator",
                "noncoherent_mark_space_energy",
                "configurable_sample_rate",
                "configurable_symbol_rate",
                "configurable_bell202_tones",
            ),
        )

    def demodulate(
        self,
        iq: Sequence[complex],
        *,
        sample_rate_hz: float,
        hypothesis: WaveformHypothesis,
    ) -> DemodulatedSignal:
        from .afsk1200_plugin import Afsk1200Config, bell202_soft_discriminator

        effective_rate = int(round(sample_rate_hz))
        if abs(float(sample_rate_hz) - effective_rate) > 1e-9:
            raise ValueError("Bell-202 plugin requires an integer IQ sample rate")
        if effective_rate % hypothesis.decimation:
            raise ValueError("AFSK decimation must divide the IQ sample rate")

        def numeric_parameter(name: str, default: float) -> float:
            value = hypothesis.parameters.get(name, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric")
            return float(value)

        mark_hz = numeric_parameter("mark_hz", 1_200.0)
        space_hz = numeric_parameter("space_hz", 2_200.0)
        audio_sample_rate_hz = effective_rate // hypothesis.decimation
        config = Afsk1200Config(
            sample_rate_hz=effective_rate,
            baudrate=float(hypothesis.symbol_rate),
            mark_hz=mark_hz,
            space_hz=space_hz,
            audio_sample_rate_hz=audio_sample_rate_hz,
            timing_rate_errors_ppm=tuple(hypothesis.rate_errors_ppm),
            timing_phase_bins=hypothesis.phase_bins,
            timing_top_n=hypothesis.top_timing_hypotheses,
        )
        soft = bell202_soft_discriminator(iq, config)
        return DemodulatedSignal(
            samples=tuple(float(value) for value in soft),
            samples_per_symbol=audio_sample_rate_hz / hypothesis.symbol_rate,
            metrics={
                "frontend": "fm_discriminator_noncoherent_bell202_energy",
                "configured_sample_rate_hz": effective_rate,
                "configured_audio_sample_rate_hz": audio_sample_rate_hz,
                "configured_symbol_rate_baud": float(hypothesis.symbol_rate),
                "mark_hz": mark_hz,
                "space_hz": space_hz,
                "input_complex_samples": len(iq),
                "output_real_samples": len(soft),
            },
        )


@dataclass(frozen=True, slots=True)
class Ax25ProtocolDecoder:
    """Strict AX.25 UI adapter, optionally trying plain and G3RUH paths."""

    protocol_id: str = "ax25"
    g3ruh_modes: tuple[bool, ...] = (False, True)

    def __post_init__(self) -> None:
        if not self.protocol_id.strip() or not self.g3ruh_modes:
            raise ValueError("protocol ID and at least one G3RUH mode are required")

    @property
    def capabilities(self) -> ProtocolCapabilities:
        return ProtocolCapabilities(
            accepted_symbol_kinds=(BINARY_SOFT_SYMBOLS,),
            framing="hdlc",
            protocol_stack=("ax25",),
            validation_layers=("crc16_x25", "ax25_ui"),
        )

    def decode(
        self, soft_symbols: Sequence[float], *, threshold: float
    ) -> tuple[tuple[bytes, str], ...]:
        levels = tuple(int(float(value) >= threshold) for value in soft_symbols)
        output: list[tuple[bytes, str]] = []
        seen: set[bytes] = set()
        for g3ruh in self.g3ruh_modes:
            for frame in decode_ax25_levels(levels, g3ruh=g3ruh):
                if parse_ax25_ui(frame[:-2]) is not None and frame not in seen:
                    seen.add(frame)
                    output.append((frame, "crc16_x25+ax25_ui"))
        return tuple(output)


@dataclass(frozen=True, slots=True)
class FixedSyncFrameDecoder:
    """Generic fixed-length frame adapter with strict caller-supplied validation.

    This covers many syncword-based satellite links, including CCSDS-like
    layouts.  FEC/derandomization can be supplied as ``transform`` and the
    mission CRC as ``validator``; candidates are never accepted on syncword
    resemblance alone.
    """

    protocol_id: str
    syncword: tuple[int, ...]
    frame_bits: int
    validator: Callable[[bytes], bool]
    transform: Callable[[tuple[int, ...]], tuple[int, ...]] | None = None
    lsb_first: bool = False
    maximum_sync_hamming: int = 0
    validation_name: str = "custom"

    def __post_init__(self) -> None:
        if not self.protocol_id.strip() or not self.validation_name.strip():
            raise ValueError("protocol and validation names must be non-empty")
        if not self.syncword or any(bit not in (0, 1) for bit in self.syncword):
            raise ValueError("syncword must contain binary bits")
        if self.frame_bits < len(self.syncword) or self.frame_bits % 8:
            raise ValueError("frame_bits must include syncword and be byte-aligned")
        if not 0 <= self.maximum_sync_hamming <= len(self.syncword):
            raise ValueError("invalid syncword Hamming bound")

    @property
    def capabilities(self) -> ProtocolCapabilities:
        return ProtocolCapabilities(
            accepted_symbol_kinds=(BINARY_SOFT_SYMBOLS,),
            framing="fixed_syncword",
            protocol_stack=(self.protocol_id,),
            validation_layers=(self.validation_name,),
        )

    def decode(
        self, soft_symbols: Sequence[float], *, threshold: float
    ) -> tuple[tuple[bytes, str], ...]:
        bits = tuple(int(float(value) >= threshold) for value in soft_symbols)
        if self.transform is not None:
            bits = tuple(self.transform(bits))
        output: list[tuple[bytes, str]] = []
        seen: set[bytes] = set()
        stop = len(bits) - self.frame_bits + 1
        for start in range(max(0, stop)):
            observed = bits[start : start + len(self.syncword)]
            distance = sum(a != b for a, b in zip(observed, self.syncword, strict=True))
            if distance > self.maximum_sync_hamming:
                continue
            frame = bits_to_bytes(
                bits[start : start + self.frame_bits],
                lsb_first=self.lsb_first,
            )
            if frame not in seen and self.validator(frame):
                seen.add(frame)
                output.append((frame, self.validation_name))
        return tuple(output)


@dataclass(frozen=True, slots=True)
class CcsdsTmTransferFrameDecoder:
    """Adapter for exact-length CCSDS Version-1 TM Transfer Frames.

    The configured sync marker is an acquisition boundary and is not returned
    as part of the Transfer Frame.  This adapter expects channel decoding and
    derandomization (when applicable) to have happened upstream.  It implements
    neither AOS nor USLP framing, and cannot be instantiated without an
    integrity path that can accept frames.
    """

    config: TMTransferFrameConfig
    sync_marker: tuple[int, ...]
    integrity_validator: TMIntegrityValidator | None = None
    integrity_name: str | None = None
    maximum_sync_hamming: int = 0
    invert_modes: tuple[bool, ...] = (False,)
    protocol_id: str = "ccsds_tm_transfer_frame"

    def __post_init__(self) -> None:
        if not isinstance(self.config, TMTransferFrameConfig):
            raise TypeError("config must be TMTransferFrameConfig")
        if not self.protocol_id.strip():
            raise ValueError("protocol_id must be non-empty")
        if not self.sync_marker or any(bit not in (0, 1) for bit in self.sync_marker):
            raise ValueError("sync_marker must contain binary bits")
        if not 0 <= self.maximum_sync_hamming <= len(self.sync_marker):
            raise ValueError("invalid sync marker Hamming bound")
        if not self.invert_modes or any(
            not isinstance(mode, bool) for mode in self.invert_modes
        ):
            raise ValueError("at least one boolean polarity mode is required")
        if (self.integrity_validator is None) != (self.integrity_name is None):
            raise ValueError(
                "integrity validator and validation name must be supplied together"
            )
        if self.integrity_name is not None and not self.integrity_name.strip():
            raise ValueError("integrity_name must be non-empty when supplied")
        if self.integrity_validator is not None and not callable(
            self.integrity_validator
        ):
            raise TypeError("integrity_validator must be callable")
        if not self.config.fecf_present and self.integrity_validator is None:
            raise ValueError(
                "TM frames without a configured FECF require an integrity validator"
            )

    @property
    def capabilities(self) -> ProtocolCapabilities:
        layers = ["ccsds_tm_primary_header", "ccsds_tm_exact_frame_length"]
        if self.config.fecf_present:
            layers.append("ccsds_tm_fecf_crc16")
        if self.integrity_name is not None:
            layers.append(self.integrity_name)
        return ProtocolCapabilities(
            accepted_symbol_kinds=(BINARY_SOFT_SYMBOLS,),
            framing="configured_sync_marker+fixed_length_ccsds_tm_transfer_frame",
            protocol_stack=("ccsds_tm_transfer_frame",),
            validation_layers=tuple(layers),
        )

    def decode(
        self, soft_symbols: Sequence[float], *, threshold: float
    ) -> tuple[tuple[bytes, str], ...]:
        sliced = tuple(int(float(value) >= threshold) for value in soft_symbols)
        frame_bits = self.config.frame_length_bytes * 8
        candidate_bits = len(self.sync_marker) + frame_bits
        output: list[tuple[bytes, str]] = []
        seen: set[bytes] = set()
        for invert in dict.fromkeys(self.invert_modes):
            bits = tuple(1 - bit for bit in sliced) if invert else sliced
            stop = len(bits) - candidate_bits + 1
            for start in range(max(0, stop)):
                observed = bits[start : start + len(self.sync_marker)]
                distance = sum(
                    left != right
                    for left, right in zip(
                        observed, self.sync_marker, strict=True
                    )
                )
                if distance > self.maximum_sync_hamming:
                    continue
                frame_start = start + len(self.sync_marker)
                candidate = bits_to_bytes(
                    bits[frame_start : frame_start + frame_bits],
                    lsb_first=False,
                )
                if candidate in seen:
                    continue
                validation = validate_tm_transfer_frame(
                    candidate,
                    config=self.config,
                    integrity_validator=self.integrity_validator,
                    integrity_name=self.integrity_name,
                )
                if not validation.accepted:
                    continue
                seen.add(candidate)
                output.append(
                    (candidate, "+".join(validation.validation_layers))
                )
        return tuple(output)


@dataclass(frozen=True, slots=True)
class RawCcsdsSpacePacketDecoder:
    """Recover raw CCSDS Space Packets with an explicit integrity policy.

    A CCSDS primary header has no checksum.  Consequently this adapter
    requires a caller-supplied validator and never treats a plausible APID or
    length field as telemetry on its own.  The validator can represent a
    packet error-control field, an authenticated field, or a strict
    mission-independent envelope supplied by a higher layer.
    """

    packet_validator: Callable[[bytes, CCSDSPrimaryHeader], bool]
    validation_name: str
    protocol_id: str = "ccsds_space_packet"
    allowed_apids: tuple[int, ...] | None = None
    bit_offsets: tuple[int, ...] = tuple(range(8))
    invert_modes: tuple[bool, ...] = (False, True)
    maximum_packet_bytes: int = 65_542

    def __post_init__(self) -> None:
        if not self.protocol_id.strip() or not self.validation_name.strip():
            raise ValueError("protocol and validation names must be non-empty")
        if not callable(self.packet_validator):
            raise TypeError("packet_validator must be callable")
        if not self.bit_offsets or any(
            not 0 <= value <= 7 for value in self.bit_offsets
        ):
            raise ValueError("bit offsets must be in the inclusive range 0..7")
        if not self.invert_modes:
            raise ValueError("at least one polarity mode is required")
        if self.maximum_packet_bytes < 7:
            raise ValueError("maximum_packet_bytes must fit a CCSDS packet")
        if self.allowed_apids is not None and (
            not self.allowed_apids
            or any(not 0 <= apid <= 0x07FF for apid in self.allowed_apids)
        ):
            raise ValueError("allowed APIDs must be in the range 0..2047")

    @property
    def capabilities(self) -> ProtocolCapabilities:
        return ProtocolCapabilities(
            accepted_symbol_kinds=(BINARY_SOFT_SYMBOLS,),
            framing="ccsds_space_packet_byte_alignment_search",
            protocol_stack=("ccsds_space_packet",),
            validation_layers=("ccsds_primary_header", self.validation_name),
        )

    def decode(
        self, soft_symbols: Sequence[float], *, threshold: float
    ) -> tuple[tuple[bytes, str], ...]:
        sliced = tuple(int(float(value) >= threshold) for value in soft_symbols)
        output: list[tuple[bytes, str]] = []
        seen: set[bytes] = set()
        allowed_apids = None if self.allowed_apids is None else set(self.allowed_apids)
        for invert in dict.fromkeys(self.invert_modes):
            bits = tuple(1 - bit for bit in sliced) if invert else sliced
            for bit_offset in dict.fromkeys(self.bit_offsets):
                available_bits = len(bits) - bit_offset
                octet_bits = available_bits - available_bits % 8
                if octet_bits < 56:
                    continue
                stream = bits_to_bytes(
                    bits[bit_offset : bit_offset + octet_bits],
                    lsb_first=False,
                )
                for start in range(len(stream) - 5):
                    encoded_length = int.from_bytes(
                        stream[start + 4 : start + 6], "big"
                    )
                    packet_bytes = 7 + encoded_length
                    if (
                        packet_bytes > self.maximum_packet_bytes
                        or start + packet_bytes > len(stream)
                    ):
                        continue
                    packet = stream[start : start + packet_bytes]
                    header = parse_ccsds_space_packet(packet)
                    if header is None:
                        continue
                    if allowed_apids is not None and header.apid not in allowed_apids:
                        continue
                    if packet in seen or not self.packet_validator(packet, header):
                        continue
                    seen.add(packet)
                    output.append(
                        (
                            packet,
                            f"ccsds_primary_header+{self.validation_name}",
                        )
                    )
        return tuple(output)


@dataclass(frozen=True, slots=True)
class Ax25CcsdsProtocolDecoder:
    """Validate an AX.25 UI frame carrying one complete CCSDS Space Packet."""

    protocol_id: str = "ax25_ccsds_space_packet"
    g3ruh_modes: tuple[bool, ...] = (False, True)
    allowed_apids: tuple[int, ...] | None = None
    packet_validator: Callable[[bytes, CCSDSPrimaryHeader], bool] | None = None
    validation_name: str | None = None

    def __post_init__(self) -> None:
        if not self.protocol_id.strip() or not self.g3ruh_modes:
            raise ValueError("protocol ID and at least one G3RUH mode are required")
        if self.allowed_apids is not None and (
            not self.allowed_apids
            or any(not 0 <= apid <= 0x07FF for apid in self.allowed_apids)
        ):
            raise ValueError("allowed APIDs must be in the range 0..2047")
        if self.packet_validator is not None and not callable(self.packet_validator):
            raise TypeError("packet_validator must be callable")
        if self.validation_name is not None and not self.validation_name.strip():
            raise ValueError("validation_name must be non-empty when supplied")
        if (self.packet_validator is None) != (self.validation_name is None):
            raise ValueError(
                "custom validator and validation name must be supplied together"
            )

    @property
    def capabilities(self) -> ProtocolCapabilities:
        layers = ["crc16_x25", "ax25_ui", "ccsds_primary_header"]
        if self.validation_name is not None:
            layers.append(self.validation_name)
        return ProtocolCapabilities(
            accepted_symbol_kinds=(BINARY_SOFT_SYMBOLS,),
            framing="hdlc",
            protocol_stack=("ax25", "ccsds_space_packet"),
            validation_layers=tuple(layers),
        )

    def decode(
        self, soft_symbols: Sequence[float], *, threshold: float
    ) -> tuple[tuple[bytes, str], ...]:
        outer = Ax25ProtocolDecoder(g3ruh_modes=self.g3ruh_modes)
        allowed_apids = None if self.allowed_apids is None else set(self.allowed_apids)
        output: list[tuple[bytes, str]] = []
        for frame, _ in outer.decode(soft_symbols, threshold=threshold):
            parsed_ax25 = parse_ax25_ui(frame[:-2])
            if parsed_ax25 is None:
                continue
            packet = parsed_ax25.information
            header = parse_ccsds_space_packet(packet)
            if header is None:
                continue
            if allowed_apids is not None and header.apid not in allowed_apids:
                continue
            if self.packet_validator is not None and not self.packet_validator(
                packet, header
            ):
                continue
            output.append((frame, "+".join(self.capabilities.validation_layers)))
        return tuple(output)


class GenericReceiver:
    """Run a bounded plugin bank without catalogue or ground-network coupling."""

    def __init__(self) -> None:
        self._demodulators: dict[str, Demodulator] = {}
        self._protocols: dict[str, ProtocolDecoder] = {}
        self._demodulator_capabilities: dict[str, DemodulatorCapabilities] = {}
        self._protocol_capabilities: dict[str, ProtocolCapabilities] = {}

    @property
    def capabilities(self) -> ReceiverCapabilities:
        """Return a detached registry snapshot suitable for plan builders."""

        return ReceiverCapabilities(
            demodulators=dict(self._demodulator_capabilities),
            protocols=dict(self._protocol_capabilities),
        )

    def register_demodulator(
        self,
        demodulator_id: str,
        demodulator: Demodulator,
        *,
        capabilities: DemodulatorCapabilities | None = None,
    ) -> None:
        if not demodulator_id.strip() or demodulator_id in self._demodulators:
            raise ValueError("demodulator ID must be non-empty and unique")
        declared = capabilities
        if declared is None:
            declared = getattr(demodulator, "capabilities", None)
        if not isinstance(declared, DemodulatorCapabilities):
            raise TypeError("demodulator must declare DemodulatorCapabilities")
        self._demodulators[demodulator_id] = demodulator
        self._demodulator_capabilities[demodulator_id] = declared

    def register_protocol(
        self,
        decoder: ProtocolDecoder,
        *,
        capabilities: ProtocolCapabilities | None = None,
    ) -> None:
        if not decoder.protocol_id.strip() or decoder.protocol_id in self._protocols:
            raise ValueError("protocol ID must be non-empty and unique")
        declared = capabilities
        if declared is None:
            declared = getattr(decoder, "capabilities", None)
        if not isinstance(declared, ProtocolCapabilities):
            raise TypeError("protocol adapter must declare ProtocolCapabilities")
        self._protocols[decoder.protocol_id] = decoder
        self._protocol_capabilities[decoder.protocol_id] = declared

    def _compatibility_error(self, item: ReceiverHypothesis) -> str | None:
        demodulator_id = item.waveform.demodulator_id
        demodulator = self._demodulator_capabilities.get(demodulator_id)
        if demodulator is None:
            return f"unknown demodulator {demodulator_id}"
        protocol = self._protocol_capabilities.get(item.protocol_id)
        if protocol is None:
            return f"unknown protocol adapter {item.protocol_id}"
        modulation = item.waveform.modulation_family
        if (
            modulation is not None
            and modulation.casefold()
            not in {item.casefold() for item in demodulator.modulation_families}
        ):
            return (
                f"demodulator {demodulator_id} does not advertise modulation "
                f"family {modulation}"
            )
        if demodulator.output_symbol_kind not in protocol.accepted_symbol_kinds:
            return (
                f"symbol kind {demodulator.output_symbol_kind} from {demodulator_id} "
                f"is incompatible with protocol {item.protocol_id}"
            )
        missing_features = set(protocol.required_demodulator_features) - set(
            demodulator.features
        )
        if missing_features:
            return (
                f"demodulator {demodulator_id} lacks required features: "
                f"{', '.join(sorted(missing_features))}"
            )
        return None

    def validate_plan(self, plan: Sequence[ReceiverHypothesis]) -> None:
        """Reject incompatible plugin combinations before touching IQ data."""

        if not plan:
            raise ValueError("plan must not be empty")
        failures = [
            f"{item.waveform.hypothesis_id}: {error}"
            for item in plan
            if (error := self._compatibility_error(item)) is not None
        ]
        if failures:
            raise ValueError("incompatible receiver plan: " + "; ".join(failures))

    def decode_demodulated(
        self,
        signal: DemodulatedSignal,
        *,
        waveform: WaveformHypothesis,
        protocol_id: str,
    ) -> ReceiverResult:
        decoder = self._protocols.get(protocol_id)
        if decoder is None:
            raise KeyError(f"unknown protocol adapter: {protocol_id}")
        protocol_capabilities = self._protocol_capabilities[protocol_id]
        if signal.symbol_kind not in protocol_capabilities.accepted_symbol_kinds:
            raise ValueError(
                f"signal symbol kind {signal.symbol_kind} is incompatible with "
                f"protocol {protocol_id}"
            )
        hypotheses = recover_timing_hypotheses(
            signal.samples,
            nominal_samples_per_symbol=signal.samples_per_symbol,
            rate_errors_ppm=waveform.rate_errors_ppm,
            phase_bins=waveform.phase_bins,
            top_n=waveform.top_timing_hypotheses,
        )
        frames: list[FrameCandidate] = []
        seen: set[tuple[bytes, str]] = set()
        for rank, timing in enumerate(hypotheses):
            soft = soft_symbols_with_hypothesis(signal.samples, timing)
            for payload, validation in decoder.decode(soft, threshold=timing.threshold):
                key = (payload, protocol_id)
                if key in seen:
                    continue
                seen.add(key)
                frames.append(
                    FrameCandidate(
                        payload=payload,
                        protocol_id=protocol_id,
                        waveform_hypothesis_id=waveform.hypothesis_id,
                        timing_rank=rank,
                        timing_score=timing.score,
                        validation=validation,
                    )
                )
        return ReceiverResult(
            frames=tuple(frames),
            attempted_waveforms=1,
            attempted_timing_hypotheses=len(hypotheses),
            failures=(),
        )

    def decode_iq(
        self,
        iq: Sequence[complex],
        *,
        sample_rate_hz: float,
        plan: Sequence[ReceiverHypothesis],
    ) -> ReceiverResult:
        if sample_rate_hz <= 0 or not plan:
            raise ValueError("positive sample rate and a non-empty plan are required")
        frames: list[FrameCandidate] = []
        seen: set[tuple[bytes, str]] = set()
        failures: list[str] = []
        timing_attempts = 0
        for item in plan:
            compatibility_error = self._compatibility_error(item)
            if compatibility_error is not None:
                failures.append(
                    f"{item.waveform.hypothesis_id}: {compatibility_error}"
                )
                continue
            demodulator = self._demodulators.get(item.waveform.demodulator_id)
            assert demodulator is not None
            try:
                signal = demodulator.demodulate(
                    iq,
                    sample_rate_hz=sample_rate_hz,
                    hypothesis=item.waveform,
                )
                expected_kind = self._demodulator_capabilities[
                    item.waveform.demodulator_id
                ].output_symbol_kind
                if signal.symbol_kind != expected_kind:
                    raise ValueError(
                        f"demodulator declared {expected_kind} but returned "
                        f"{signal.symbol_kind}"
                    )
                result = self.decode_demodulated(
                    signal,
                    waveform=item.waveform,
                    protocol_id=item.protocol_id,
                )
            except (KeyError, ValueError) as exc:
                failures.append(f"{item.waveform.hypothesis_id}: {exc}")
                continue
            timing_attempts += result.attempted_timing_hypotheses
            for frame in result.frames:
                key = (frame.payload, frame.protocol_id)
                if key not in seen:
                    seen.add(key)
                    frames.append(frame)
        return ReceiverResult(
            frames=tuple(frames),
            attempted_waveforms=len(plan),
            attempted_timing_hypotheses=timing_attempts,
            failures=tuple(failures),
        )


# Compatibility alias for callers of the original prototype API.
GenericSatelliteReceiver = GenericReceiver


def default_receiver() -> GenericReceiver:
    """Return the network-neutral receiver with conservative built-ins."""

    receiver = GenericReceiver()
    receiver.register_demodulator("phase_fsk", PhaseFskDemodulator())
    receiver.register_demodulator(
        "channel_conditioned_phase_fsk",
        ChannelConditionedPhaseFskDemodulator(),
    )
    receiver.register_demodulator("bell202_afsk", Bell202AfskDemodulator())
    receiver.register_protocol(Ax25ProtocolDecoder())
    receiver.register_protocol(
        Ax25ProtocolDecoder(protocol_id="ax25_plain", g3ruh_modes=(False,))
    )
    receiver.register_protocol(Ax25CcsdsProtocolDecoder())
    return receiver
