# Dzisiejsze OGG SatNOGS: porównanie trzech dekoderów

Zakończono 20/20 porównań, 2026-09-10. Wszystkie 60 procesów dekoderów
zakończyło się poprawnie. Pobrano 146 464 093 bajty OGG, obejmujące 2,658 h audio.

## Wyniki końcowe

| Metryka | Nasz dekoder | Dire Wolf | gr-satellites |
|---|---:|---:|---:|
| Unikalne pary obserwacja–PDU, ścisłe AX.25 UI | 127 | 86 | 44 |
| Globalnie różne PDU, ścisłe AX.25 UI | 114 | 83 | 43 |
| Wszystkie unikalne pary obserwacja–PDU przed filtrem UI | 127 | 87 | 55 |
| Obserwacje z co najmniej jedną ścisłą ramką UI | 6/20 | 7/20 | 4/20 |
| Bajty PDU, suma po obserwacjach, bez FCS | 33 528 | 22 704 | 11 616 |
| Łączny czas procesów dekodowania | 865,93 s | 54,00 s | 42,69 s |
| Średni czas procesu na nagranie | 43,30 s | 2,70 s | 2,13 s |
| Łączny czas CPU | 3278,98 s | 52,52 s | 92,26 s |
| Największy RSS pojedynczego procesu | 511 320 KiB | 5888 KiB | 89 480 KiB |

W podstawowej wspólnej metryce nasz dekoder daje **47,7% więcej niż Dire Wolf**
i **188,6% więcej niż gr-satellites**. Odzyskał 42 pary obserwacja–PDU nieobecne
w wynikach obu konkurujących komponentów, ale pominął jedną ramkę Dire Wolfa
w obserwacji14967361. Zysk netto względem Dire Wolfa wynosi41 par.
Wszystkie44 ścisłe ramki gr-satellites są również w wyniku naszego dekodera.
Zysk wystąpił w6/20 obserwacji; nie uzyskaliśmy większej liczby obserwacji
z przynajmniej jedną ramką niż Dire Wolf. Nie jest to bezwarunkowa przewaga.

Po deduplikacji między wszystkimi nagraniami:114 różnych PDU, czyli30 096
bajtów u nas;32 PDU nieobecne globalnie w wyjściu Dire Wolfa i1 jego PDU
nieobecny u nas. „Nieobecne” dotyczy tego testu, nie całego archiwum SatNOGS.
Wynik kosztuje około16 razy więcej czasu procesu niż Dire Wolf i20 razy
więcej niż gr-satellites przy podanych ustawieniach. Czasy nie obejmują
wielokrotnie wolniejszego pobierania przez niestabilne DNS/TLS.

| Obserwacja | Nasz | Dire Wolf | gr-satellites |
|---|---:|---:|---:|
| 14967385 | 0 | 0 | 0 |
| 14966639 | 0 | 0 | 0 |
| 14967410 | 0 | 0 | 0 |
| 14967361 | 0 | 1 | 0 |
| 14967408 | 0 | 0 | 0 |
| 14967407 | 0 | 0 | 0 |
| 14963998 | 0 | 0 | 0 |
| 14967367 | 6 | 4 | 0 |
| 14967406 | 0 | 0 | 0 |
| 14967401 | 0 | 0 | 0 |
| 14967415 | 0 | 0 | 0 |
| 14967436 | 0 | 0 | 0 |
| 14967362 | 3 | 1 | 0 |
| 14967384 | 0 | 0 | 0 |
| 14967376 | 48 | 39 | 27 |
| 14959745 | 0 | 0 | 0 |
| 14967393 | 18 | 13 | 8 |
| 14967428 | 0 | 0 | 0 |
| 14967413 | 45 | 27 | 8 |
| 14967432 | 7 | 1 | 1 |

Tabela dotyczy ścisłego UI; dodatkowe PDU zewnętrznych dekoderów zachowano
w JSON. Przykład publicznego źródła: [obserwacja14967376](https://network.satnogs.org/observations/14967376/).

## Z góry ustalony dobór

20 najnowszych zakończonych obserwacji CANVAS (NORAD68635), GMSK9600,
z dostępnym publicznym OGG. Przedział: od2026-09-10T00:00:00Z do
2026-09-10T15:09:32Z; zarówno rozpoczęcie, jak i zakończenie muszą mieścić
się w kryteriach zapisanych w planie. Pełna paginacja API dała107 rekordów
na5 stronach,106 kwalifikujących się obserwacji. Wybrano20 według malejącego
czasu rozpoczęcia iID. Żadnego filtra na status obserwacji, waterfall ani
liczbę ramek. Lista została zamrożona o15:19:11UTC przed pobraniem audio.

To kontrolowane porównanie jednego wspólnie obsługiwanego profilu, nie
reprezentatywna próba wszystkich satelitów/protokołów SatNOGS. Stacje mogą
obserwować ten sam przelot;20 obserwacji nie oznacza20 niezależnych misji.

## Identyczne wejście i niezmieniony algorytm

Każdy cały OGG jest dekodowany dokładnie raz przez ffmpeg6.1.1 do mono
48kHz PCM signed16bit WAV. Bez resamplingu, normalizacji głośności i mieszania
kanałów. Wszystkie trzy programy dostają dokładnie ten sam plik, potwierdzony
SHA256 przed i po wykonaniu. Kwantyzacja do16bit jest wspólna; nie twierdzimy,
że jest bezstratna względem dowolnego float32. Pliki OGG też pozostają zachowane.

- Nasz dekoder: zamrożony zoptymalizowany Rust adaptive-sequence receiver,
  SHA256 `acf20e51e8cfb2b4e2264757fe8fe8ef61ae6de3d523ac3da6e02f63fb492a97`,
  ustawienia domyślne,9600baud,4 wątki. Bez strojenia na tej kohorcie.
- Dire Wolf1.7: `atest -B 9600 -F 0 -h`; domyślny modem G3RUH, bez naprawy bitów.
- gr-satellites5.9.0: standardowy komponent FSK9600/AX.25G3RUH, WAV bez `--iq`.
  To odtworzenie komponentu na audio, nie pełny historyczny łańcuch stacji
  gr-satnogs z wejścia IQ. Tak zinterpretowano nazwę „gr-stanogs” w zleceniu.

Dwa nagrania mogą być analizowane równolegle. Kolejność dekoderów rotuje
pomiędzy nagraniami. Mierzymy czas całego procesu dekodera, ze startem programu,
ale bez pobierania i konwersji. Host nie jest izolowany; nie jest to precyzyjny
benchmark wydajności na wyłączność. Żadne ramki nie są wysyłane do SatNOGS.

## Zasady liczenia

Podstawową jednostką jest unikalna para(obserwacja,PDU): identyczne bajty ramki
bez FCS, z usunięciem powtórzeń wewnątrz jednego nagrania. Osobno raportowane
są PDU unikalne globalnie. Porównanie podstawowe używa tej samej ścisłej
walidacji struktury AX.25UI dla wszystkich trzech programów. Dodatkowo
zachowujemy wszystkie PDU zwrócone przez dekodery zewnętrzne, także odrzucone
przez ten węższy filtr, żeby nie ukrywać różnic zakresu obsługi.

Nasz wynik zawiera odebrany FCS, sprawdzany niezależnie algorytmem bitowym.
Dire Wolf i gr-satellites weryfikują CRC wewnętrznie, ale usuwają odebrany FCS
przed wyjściem. Nie doklejamy wyliczonego CRC jako pozornego dowodu odbioru.
Błąd pobierania, WAV, procesu lub parsera nie jest wynikiem „zero ramek”.

Payloady z archiwum nie są używane do przeszukiwania sygnału ani do doboru
nagrań. „Dodatkowa ramka” oznacza brak w wyjściu porównywanych dekoderów
na tym nagraniu, a nie dowód, że nigdy nie trafiła do archiwum SatNOGS.

## Kontrole działania

14 testów runnera/parserów i8 testów akwizycji przeszło.
Oba zewnętrzne dekodery odtworzyły identyczny148-bajtowy PDU z wcześniejszego
złotego nagrania AALTO-1. Wcześniejszy CANVAS14936407 na wspólnym PCM16 dał
20/16/8 ścisłych unikalnych ramek(nasz/DireWolf/gr-satellites), przy czym
gr-satellites wyemitował10 PDU przed filtrem ścisłym. Nasze20 pełnych ramek
było identyczne z poprzednią analizą float32 tego samego nagrania.
Te kontrole nie wchodzą do dzisiejszej dwudziestki.

Zwykły WAV z ffmpeg może zawierać metadane LIST, odrzucane przez atest.
Wspólna konwersja stosuje `-flags:a +bitexact -fflags +bitexact`, co zostało
sprawdzone na prawdziwych logach obu dekoderów przed analizą dzisiejszej próbki.

## Artefakty i powtarzalność

Katalog badania: `/home/ubuntu/telemetry-yield/work/satnogs-today20-20260910-v1`.
`acquisition/cohort.json` zawiera pełne zamrożone metadane i20ID;
`acquisition/obs-ID/capture.ogg` oryginały oraz dzienniki pobierania;
`comparison/obs-ID/shared-pcm16.wav` identyczne wejście trzech dekoderów;
`comparison/obs-ID/result.json` pierwotne wyniki i czasy;
`verified/obs-ID/result.json` końcowe wyniki po ponownym odczycie logów;
`verified/summary.json` końcowe agregaty i różnice zbiorów.
`comparison/manifest.json` zawiera konfiguracje i hashe programów/komponentów.

Ostatni log Dire Wolfa zawierał trzy powtórzenia nietypowego25-bajtowego PDU
bez tekstowej deklaracji długości, a następnie poprawną264-bajtową ramkę UI.
Pierwotny parser odrzucił ten wariant tekstowego wydruku. Poprawka dotyczy
wyłącznie czytnika: dokładne kolumny HEX, znaczniki początku/końca i licznik
pakietów nadal są sprawdzane. Ponownie odczytano wszystkie40 wyjść zewnętrznych
dekoderów; wszystkie39 wcześniej zaakceptowanych wyników pozostało identycznych.
Żaden dekoder nie został ponownie uruchomiony ani dostrojony z tego powodu.
Pierwotny błąd pozostaje w `comparison/`; miarodajny jest katalog `verified/`.
Audyt: `verified/reader-audit.json`.

SHA256 końcowego `verified/summary.json`:
`9a6b09606942762705a3099c4c88e4f948d0ed400483e3d5b91f7330390714fe`.

Kod: `examples/satnogs_today20_acquire.rs`, `examples/satnogs_today20_run.rs`,
`examples/support/today20_baselines.rs`, `examples/satnogs_today20_reparse.rs`.
Pierwotne nieudane pobrania i kolejne
poprawki wyłącznie transportu są zachowane w katalogu akwizycji. Lista
obserwacji i konfiguracje dekoderów nie są przy tych poprawkach zmieniane.

Kontrole: `/home/ubuntu/telemetry-yield/work/today20-baseline-check-20260910-v1/README.md`.

## Ograniczenia

Mała kohorta jednej misji, częściowo wspólne przeloty, stratne audio OGG i
wspólna kwantyzacja16bit. Brak testów wszystkich modulacji, CCSDS i wejścia IQ.
Nie ukończono niezależnej walidacji nowych PDU ani oceny fałszywych alarmów
specyficznej dla tej kohorty. Sam ten test nie oznacza gotowości publikacyjnej
ani produkcyjnej i nie uzasadnia twierdzenia o rewolucyjności metody.
