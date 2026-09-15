# Optymalizacja aplikacji i ponowna kwalifikacja — 2026-09-12

Stan zapisany po pierwszej pełnej parze na rzeczywistym nagraniu, około
14:29 UTC. Zmiany aplikacji są wdrożone lokalnie; dalsze pary pomiarowe trwają.
Nie nadpisano binariów, sesji ani wyników zamrożonego badania 266 obserwacji.

## Co zmieniono

- `decode-progressive --cache-mib`: domyślnie 2048 MiB zamiast stałych 512 MiB,
  dopuszczalne 0–4096 MiB na proces. Cache nadal podlega dotychczasowemu
  oszacowaniu pamięci roboczej. Przy braku miejsca następuje przeliczenie,
  nie pominięcie pracy. Budżet można zmieniać przy wznowieniu tej samej wersji
  programu; nie należy wznawiać starej sesji innym binarium.
- Przygotowany frontend jest pożyczany do obliczeń adaptacyjnych, bez
  kopiowania jego tablicy próbek.
- `InnovationWorkspace` zachowuje bufory traceback i decyzji między
  hipotezami. Ścieżka audio używa również istniejącego `SequenceWorkspace`
  zamiast ponownego przydzielania buforów matched-white. Arytmetyka,
  walidacja, remisy i pełny bank hipotez pozostają takie same.
- Aplikację zbudowano normalnym profilem Cargo release, z thin LTO i jednym
  codegen unit. Używany w badaniu starszy artefakt miał bibliotekę DSP
  zbudowaną z opt-level=2 i codegen-units=8.
- Odczyt natywnych raportów benchmarku ma osobny limit 256 MiB i parser
  strumieniowy. Limit zwykłych metadanych/konfiguracji nadal wynosi 64 MiB.
  Kontrole pliku regularnego, zmiany tożsamości i odebranego FCS pozostają.

Nie wdrożono pomijania hipotez, obniżenia precyzji, fast-math, wcześniejszego
kończenia po znalezieniu ramki ani zmiany modeli i progów. Deduplikacja
równoważnych wywołań AR(0) pozostaje oddzielną możliwością, nie częścią tej zmiany.

## Weryfikacja

- Biblioteka: **395 PASS, 0 FAIL, 6 jawnie pominiętych**.
- CLI: **17 PASS, 0 FAIL**. Obejmuje przerwanie/wznowienie, zmianę budżetu cache
  oraz porównanie pełnych deterministycznych zadań przy cache 0 i domyślnym.
- Narzędzie benchmarku/raportów: **37 PASS, 0 FAIL, 1 pominięty replay**.
- Komparator innovations: **4 PASS, 0 FAIL**, w tym dwa powtórzone testy
  parsera raportów; nie są to cztery dodatkowe niezależne przypadki ponad wszystko.
- Nowy detektor sprawdzono w 1296 kombinacjach względem skopiowanej
  implementacji sprzed zmian: AR(0/1/2), różne długości, zera, losowe próbki,
  wzmocnienia i zmienne wagi. Dodatkowo sprawdzono błędy, zatrutą pamięć
  i zachowanie przy ponownym używaniu pojemności.

Logi i artefakty: `work/receiver-speed-20260912-v1/`.

## Zmierzone przyspieszenie — porównanie do zamrożonej wersji badania

Wartości poniżej obejmują pakiet zmian implementacji i sposobu kompilacji;
nie przypisujemy całego zysku samemu zwiększeniu cache ani wyłącznie zmianom
z dzisiejszego patcha. Starszy artefakt badania nie miał wcześniejszego cache
progresywnego, który znajdował się już w aktualnych źródłach projektu.

| Próba | Czas przed → po | Wyniki deterministyczne |
|---|---|---|
| Cztery syntetyczne kontrole progressive, suma | 64,417 → 40,619 s; **36,94% krócej** | Identyczne wszystkie zadania i ramki; po 35 zadań na kontrolę |
| Rzeczywiste 14967362, pierwsza para | 489,630 → 301,572 s; **38,41% krócej / 1,624×** | Identyczne 1596 zadań i 4 ramki z odebranym FCS |
| Cztery syntetyczne kontrole innovations, suma | 45,77 → 40,09 s; **12,41% krócej** | Identyczny pełny raport po usunięciu tylko pól czasu |

CPU progressive na rzeczywistym pliku: 676,98 → 410,01 s.
Maksymalny RSS w tej parze: 31,75 → około 785,95 MiB. To rzeczywista wymiana
pamięci za oszczędność przeliczania. Zestaw innovations obejmował łącznie
16 modeli źródłowych i 1920 prób dodatkowych; w obu wersjach zachowano tę
samą zawartość raportu, także na kontrolach bez docelowej transmisji.

Host jest współdzielony: 8 vCPU, trwające zamrożone badanie oraz inne zadania
użytkownika. Te czasy nie są pomiarem izolowanym ani gwarancją dla każdego pliku.
Porównanie na danych rzeczywistych obejmuje dwa stare pliki rozwojowe × dwa powtórzenia,
naprzemiennie AB/BA. W chwili zapisu **1/4 par ukończona**, pozostałe trwają.
Końcowy wynik zapisze sam sterownik w `real-pairs/summary.json`; nie mieszamy
ukończonej pierwszej pary z nieukończonymi jako rzekomym końcowym benchmarkiem.

## Naprawa odczytu dużego raportu

Obserwacja 14848596 miała pomyślnie zakończony proces innovations_v2, ale jego
około 65-MiB raport przekraczał limit dotychczasowego czytnika. Nowe narzędzie
`innovation_recover_large_report` sprawdziło powiązanie wejścia, programu,
logów procesu, parametrów i odebranych FCS. Potwierdziło **89 ramek/PDU** bez
ponownego uruchamiania DSP.

To odzyskanie poprawnie wygenerowanego **raportu**, nie 89 dodatkowych ramek
uzyskanych dzięki nowemu algorytmowi ani wynik przewagi nad SatNOGS.
Oryginalny wpis nadal jest zachowany jako incomplete. Osobny dowód korekty:
`work/receiver-speed-20260912-v1/obs-14848596-report-recovery.json`.
Nie dodano go po cichu do zamrożonej analizy; końcowe zestawienie wymaga jawnego
uwzględnienia tej poprawki przetwarzania wyników.

## Co dzieje się z dużym badaniem

Nie ma obecnie wykazanej regresji dekodowania, która wymagałaby porzucenia
wszystkich dotychczasowych wyników. Badanie 266 obserwacji działa dalej na
niezmienionych artefaktach; pomiar nowej implementacji jest osobnym porównaniem.
Współbieżne testy kwalifikacyjne są dodatkowym obciążeniem VM i muszą być
uwzględnione przy interpretacji kosztów czasowych starej kampanii.

To kwalifikacja optymalizacji, nie deklaracja uniwersalnej bezstratności,
gotowości całego systemu do publikacji czy potwierdzenie przewagi dla wszystkich
modulacji. Resztę wyników rzeczywistych należy sprawdzić przed końcowym wnioskiem
o szybkości i przed zmianą wersji używanej w kolejnej kampanii.

## Artefakty aplikacji

- Aktualna aplikacja: `target/release/telemetry-yield-rs`.
- Zachowana kopia: `work/receiver-speed-20260912-v1/after/telemetry-yield-rs`,
  SHA256 `cd2c8adcf9db34e5f177753357e7915ac29189816ce73e11e90814c0f3d96acf`.
- Innovations: `work/receiver-speed-20260912-v1/after/innovation_audio_probe`,
  SHA256 `c406199366ecccef6ce14d4c13600d8cd6e3019ac9a69921430d80e59424960b`.
- Migawka źródeł: `work/receiver-speed-20260912-v1/after/source.tar.gz`,
  SHA256 `10868f9ed060e7a31ab79921061a7c68940cb4dd280329297b2f6ef22babbc36`.
  Nie jest hermetycznym pakietem wszystkich zewnętrznych fixture'ów testowych.
- Poprzednia lokalna aplikacja zachowana w `before/current-cli`; zamrożonych
  wersji naukowych nie nadpisano.

Graphify skierował do starego kodu Python; implementację i wnioski oparto
na bezpośrednim przeglądzie Rust oraz rzeczywistych testach.
