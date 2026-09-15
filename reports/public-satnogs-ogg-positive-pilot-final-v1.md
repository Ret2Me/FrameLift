# Pilotaż OGG SatNOGS — wynik końcowy, 7 września 2026

Nasza metoda odzyskuje poprawne ramki z publicznych, stratnych nagrań OGG.
Na dwóch celowo dobranych nagraniach CANVAS odzyskano **15 ramek / 2062 B PDU**.
Każde nagranie przetworzono dwukrotnie: pełne ramki, FCS, pochodzenie wyników
i zapisy wszystkich okien były identyczne między powtórzeniami.

| Obserwacja | W archiwum SatNOGS | gr-satellites z tego samego OGG | Nasza metoda z OGG |
|---|---:|---:|---:|
| [14366383](https://network.satnogs.org/observations/14366383/) | 6 | 2 | 5 |
| [14115025](https://network.satnogs.org/observations/14115025/) | 10 | 5 | 10 |
| Razem | 16 | 7 | 15 |

## Co jest rzeczywiście dodatkowe

- Nasza metoda odtworzyła **14 z 16** ramek obecnych w archiwum.
- Odzyskała również **jedną ramkę / 70 B** nieobecną w archiwalnym wyniku.
  Tę samą ramkę znalazł także istniejący dekoder porównawczy.
- Względem gr-satellites na identycznym audio: **9 ramek / 1392 B tylko naszą
  metodą**, ale także **jedna ramka / 56 B tylko dekoderem porównawczym**.
- Liczba ramek nieobecnych zarówno w archiwum, jak i w wyniku dekodera
  porównawczego, a znalezionych wyłącznie naszą metodą: **0**.
- Połączenie obu dekoderów daje 16 ramek. Połączenie archiwum i obu wyników
  audio daje 17 ramek / 2188 B. Powtórki i nakładające się okna nie zwiększają
  tych liczników. Jednej ramki archiwalnej nie odtworzył żaden tor audio.

## Kontrola poprawności

Każdą natywną ramkę zweryfikowano trzema ścieżkami CRC: dekoderem,
niezależną implementacją bitową i niezależnym sprawdzeniem opartym o
`binascii.crc_hqx` z odbiciem bitów. Sprawdzono również strukturę AX.25,
pełne bajty PDU i ich SHA-256. Wszystkie ramki mają opisowo źródło LASP-0
i cel CANVAS-0; nie był to dodatkowy filtr akceptacji dobrany po wyniku.

Gr-satellites 5.9.0 sprawdza FCS wewnętrznie i usuwa ją przed KISS.
Zachowano oryginalne KISS i logi. Powtórki mają identyczne PDU i kolejność,
a różnice w plikach KISS wynikają tylko ze znaczników czasu sterujących.

**40 testów i 34 podtesty przeszły.** Kontrole ciszy i szumu nie dały ramek,
ale są to tylko dwa unikalne wejścia po sześć sekund, ponownie używane w
kilku przebiegach. Nie stanowią pomiaru niskiej częstości fałszywych alarmów.

Pobrane dodatkowo ISS 14206235 zostało wykluczone z próby dodatniej:
jedyny opublikowany obiekt miał dwa bajty `ffff`, nie ramkę AX.25.
Ograniczony dalszy przegląd AFSK nie dostarczył kolejnej dodatniej próbki.
Nie sprawdzono więc rzeczywistego AFSK w tym pilotażu.

## Ograniczenia i dalsza decyzja

To dwa nagrania jednej misji, wybrane na podstawie wcześniejszych wyników;
nie jest to niezależny, ślepy test ani wskaźnik skuteczności całego archiwum.
Archiwalne wyniki pochodzą z pierwotnego odbioru; do porównania algorytmów
użyto dopiero obu dekoderów uruchomionych na identycznym PCM z OGG.
Odtworzenie historycznego środowiska stacji nie jest tu deklarowane.

Koszt pierwszego przebiegu: około **293–295 s na nagranie** naszą metodą
wobec **1,6–2,0 s** dekoderem porównawczym. Budżety poszukiwania są różne,
a pomiary wykonano przy współbieżnym, oddzielnym eksperymencie IQ.
Ten wynik wspiera sens dalszego rozwoju ścieżki audio, nie gotowość
produkcyjną ani ogólną przewagę publikacyjną.

Zgodnie z kolejną decyzją użytkownika **najpierw nastąpi dopracowanie
algorytmu i sprawdzenie na małych nowych próbach; test wielkoskalowy nie
został uruchomiony**. Stary pilotaż pozostaje zapisanym punktem odniesienia.

## Artefakty

- [Pełne porównanie i PDU](public-satnogs-ogg-positive-pilot-v1.json), SHA-256
  `157b60e1dbed16ec0c3a1da1b25b7e606b174afaa466a34f0f2fc45d77856bf4`.
- [Niezależna weryfikacja powtórek](/home/ubuntu/telemetry-yield/work/satnogs-ogg-pilot-20260907/verification/repeat-b-independent-verification.json), SHA-256
  `1ec36f2f999cd1cfcf37cd45a9a54eb0216ebbe2f294cc1d6302b9b1af5f6501`.
- [Zapis środowiska i źródeł](/home/ubuntu/telemetry-yield/work/satnogs-ogg-pilot-20260907/verification/provenance-and-review.json), SHA-256
  `8cc9c8a008b61c112089a4546b2bbbcd9c617235bc89d368c159d61c0cfe0579`.
- [Testy JUnit](/home/ubuntu/telemetry-yield/work/satnogs-ogg-pilot-20260907/verification/pilot-tests.xml), SHA-256
  `2a8940a8ff2e55927e59ad45a963b8337988bd78a9c5222a4462a92ff2fe56d2`.
- [Opis wejścia audio i odtwarzania pilotażu](/home/ubuntu/telemetry-yield/docs/audio-receiver-pilot.md).
