# Generic receiver — milestone v1

Stan końcowy: **complete: false**. Inwentaryzacja, pobieranie i przetwarzanie są raportowane oddzielnie.

## Pokrycie danych

- Inventory kompletne: tak; katalog: 704 obserwacji, 257 z IQ.
- Live bucket: 290 IQ, w tym 33 nowych względem katalogu.
- Download katalogowy: próby 208/208, poprawne: 207, błędy: 1, complete: false.
- Download live-delta: próby 33/33, poprawne: 33, błędy: 0, complete: true.
- Processing jawnego G3RUH: 28/28, complete: true.
- Audyt protokołu G3RUH: 28/28, complete: true.
- Triage sygnałowy live-delta: 10 rekomendowanych nagrań; nie jest to demodulacja ani audyt protokołu.
- Routing SatYAML: 19/19 do istniejących profili; to zgodność metadanych, nie dekodowanie.
- Próba CW/narrowband: 10 analiz, 0 kandydatów pending; tekst bez walidatora nie jest telemetrią.
- RML24: 1323000 krótkich rekordów, 2.048 ms każdy; benchmark modulacji/BER, nie liczby ramek.

## Możliwości i braki

Aktualny tor obejmuje phase-FSK oraz jawnie konfigurowane AX.25/G3RUH, walidację CCSDS i stos AX.25+CCSDS. Ogólne FSK nie implikuje AX.25.

Wymagane pluginy dla dostępnych IQ: afsk_demodulator (23), sstv_decoder (12), ax100_mode5_protocol (7), cw_decoder (5), apt_decoder (3), bpsk_demodulator (2).

## Wyniki i poziom zaufania

Bieżąca kampania G3RUH: kandydaci CRC-valid: **9**; zaufane ramki AX.25 UI: **0**; odrzucone kolizje CRC: **9**; ramki z dokładnym wewnętrznym CCSDS: **0**. Sam CRC nie jest liczony jako telemetria.

Kandydaty tekstowe CW mają validation=pending i nie zwiększają licznika zaufanej telemetrii. RML24 mierzy warstwę modulacji i BER; nie jest benchmarkiem end-to-end ramek.

Znany benchmark same-IQ: SatNOGS 22, sam phase-first 16, unia po walidacji protokołu 24 (+2 względem SatNOGS).
