# Eksperyment do pracy o odzysku archiwalnej telemetrii

Stan sprawdzony 2026-09-08 o 19:51 UTC. To zapis uruchomienia i stanu pośredniego,
nie końcowe wyniki niezależnego testu ani deklaracja gotowości do publikacji.

Aktualizacja po zakończeniu kontroli, około 19:57 UTC: **64/64 ukończone, zero
błędów, zero zaakceptowanych fałszywych ramek w 3840 sekundach syntetycznego
szumu** (po 32 nagrania obu rodzajów). Jednostka zakończyła proces kodem 0.
To nie jest estymacja fałszywych alarmów dla populacji prawdziwych stacji.
Główny holdout nadal czeka na zakończenie przerwy API o 20:32:25 UTC.

## Pytanie i zakres

Czy zamrożony zestaw czterech ścieżek demodulacji odzyskuje dodatkowe poprawne
ramki z prawdziwych nagrań CANVAS w SatNOGS — i ile kosztuje taki odzysk?
Porównujemy osobno ponownie uruchomiony gr-satellites oraz zachowane ramki
archiwalne. Nie zakładamy z góry rewolucyjności ani przewagi nad wszystkimi
dekoderami. Wielościeżkowe dekodowanie ma istniejące odpowiedniki.

[Protokół przed analizą](satnogs-holdout-protocol-20260908-v1.json) określa:

- Daty 2026-08-23 włącznie do 2026-09-06 wyłącznie; CANVAS, GMSK/FSK 9600,
  AX.25/G3RUH. Jeden satelita, nie badanie wszystkich protokołów.
- Do 336 obserwacji: dwie najniższe wartości deterministycznego hasha ID w każdym
  dwugodzinnym przedziale rozpoczęcia obserwacji, osobno dla każdego dnia.
- Brak dobierania nagrań pod sukces dekodera, brak zastępowania niedostępnych OGG,
  wykluczenie rozpoznanych wcześniejszych nagrań rozwojowych.
- Cztery wcześniej ustalone ścieżki i ich suma zbiorowa. Brak napraw bitów,
  przeszukiwania z użyciem znanych ramek i zmian parametrów po wynikach holdoutu.
- Identyczny zachowany PCM float32/48 kHz dla naszego dekodera i gr-satellites.
- Przyrosty i straty, unikalne PDU w obserwacji i w całym wybranym zbiorze,
  bajty PDU, niekompletne referencje, czasy/CPU/RAM i analizy wkładu ścieżek.
- Przedziały niepewności z grupowaniem według dat, z analizą dwudniowych bloków.
  Identyczne próbki występujące w różnych dniach wyłączają takie przedziały;
  osobne stacje nie są automatycznie niezależnymi transmisjami.

To lokalne ustalenie protokołu przed analizą nagrań, a nie zewnętrzna prerejestracja.
Zysk względem skończonego archiwum wybranych obserwacji nie oznacza nowości względem
całej światowej bazy SatNOGS. Szkic artykułu zachowuje te ograniczenia:
[manuscript](../docs/paper-satnogs-archive-recovery-v1.md).

## Co faktycznie wykonano

Powstały cztery narzędzia w Rust: zbieranie i zamrażanie metadanych, wykonywanie
porównań, niezależny audyt/statystyka oraz kontrola na szumie. Sam dekoder DSP
pozostaje wcześniejszym zamrożonym programem. Obce kodeki i gr-satellites są
oddzielnie wskazanymi zależnościami, nie naszym kodem Python.

Weryfikacja końcowego kodu narzędzi:

- `cargo test --release --example satnogs_holdout_analyze --example satnogs_holdout_run --example satnogs_holdout_acquire --example satnogs_holdout_controls -j 2 -- --test-threads=2`: **44/44 testy**, bez błędów.
- Clippy tych czterech przykładów z `-D warnings`: poprawnie.
- `systemd-analyze --user verify` jednostek porównania i kontroli: poprawnie.
- Zamrożono 769 plików środowiska dekodera referencyjnego, 204930346 bajtów;
  ich aktualne sumy zgadzają się z zapisami wcześniejszego środowiska.

Przegląd drugiego agenta wykrył i doprowadził do naprawienia przed uruchomieniem
holdoutu m.in. pomijania referencji obserwacji bez audio, niewystarczającego
sprawdzania surowych artefaktów, przedwczesnego stanu końcowego oraz mylenia
braku sprawdzenia z potwierdzoną nieobecnością w danych rozwojowych.

Graphify służyło wyłącznie odnalezieniu starszych komponentów. Wyniki osobnego
projektu planowania obserwacji nie zostały użyte jako dowód skuteczności odbiornika.

## Rzeczywisty stan wykonania w chwili zapisu

1. **Metadane:** zachowano 60 poprawnie pobranych stron i trzy odpowiedzi HTTP429.
   Na stronie numer 60, licząc od zera, SatNOGS nakazał `Retry-After` do
   **2026-09-08 20:32:25 UTC**. Nie obchodzimy limitu. Proces wznowienia sprawdza
   zachowane strony i czeka na ten termin. Manifest wybranych ID nie jest jeszcze
   zamrożony i nie pobrano ani nie zdekodowano nowych nagrań holdout.
2. **Kontrole:** uruchomiono identyczny pełny dekoder na 64 nowych syntetycznych
   nagraniach po 60 sekund: 32 biały szum i 32 szum AR(1). O 19:51 zakończono
   **14/64**, bez błędów i bez zaakceptowanych ramek, obejmując 840 sekund szumu.
   To wyłącznie wynik pośredni; nie rzeczywisty współczynnik fałszywych alarmów stacji.
3. **Porównanie i raport:** jednostka wykonania jest przygotowana, ale jeszcze
   nie działa. Dokładnie jeden callback uruchomi ją automatycznie dopiero po
   poprawnym zakończeniu pobierania i zamrożeniu całego zbioru. Audyt i zapis
   raportu nastąpią po zakończeniu przetwarzania.

Ograniczenie przeglądu wcześniejszych danych: spis rozpoznał 107 wcześniejszych ID
i 101 plików OGG, ale pominął nieczytelny katalog ochronny środowiska
`work/golden/.env.guard-v3-x1ztap28` oraz nie śledził dowiązań. Ograniczenie jest
zapisane w śladzie doboru; nie twierdzimy, że każdy plik całej maszyny był czytelny.

Pierwsza wygenerowana seria `control-inputs/` nie została użyta do dekodowania:
jej manifest wskazywał zmienny plik generatora w `target/`. Zamiast nadpisywać
manifest, wygenerowano taką samą deterministyczną serię w `control-inputs-v2/`
zamrożonym generatorem. Obie serie i historia pozostały lokalnie.

## Wznawialność i pliki wynikowe

Katalog kampanii: `work/satnogs-holdout-20260908-v1/`.

- `cohort/`: niezmienne strony, odpowiedzi błędów, protokół i operacyjne wznowienie;
  `cohort.json` i `freeze.json` powstaną dopiero po pełnej paginacji.
- `controls-run/summary.json`: aktualne wyniki kontroli.
- `run/summary.json`: powstanie po uruchomieniu nagrań; wszystkie próby, także
  nieudane, zachowują surowe OGG, referencje, PCM, pełne ramki/FCS oraz logi.
- `analysis/analysis.json` i `analysis/results.md`: powstaną po końcowym audycie.
  Zawierają rzeczywiste wyniki, nie uzupełniają automatycznie brakujących wyników
  w artykule ani nie ogłaszają gotowości publikacyjnej.

Jednostki użytkownika:

- `telemetry-yield-holdout-metadata-resume-20260908.service` — oczekiwanie/wznowienie;
  proces 2577796 w chwili kontroli. Stara jednostka `...metadata-20260908.service`
  ma stan failed po HTTP429 i nie jest aktywnym kolektorem.
- `telemetry-yield-holdout-controls-20260908.service` — kontrole, proces 2580248
  w chwili kontroli.
- `telemetry-yield-holdout-run-20260908.service` — automatyczny następny etap,
  2 pracowników po 2 wątki, limit 14 GiB, rezerwa dyskowa 20 GiB.

Stan live trzeba odczytać z jednostek i podsumowań; ten dokument jest migawką.
Nie dodano automatycznego restartowania porównań ani usuwania wejść. W razie błędu
artefakty pozostają, a wznowienie wymaga tej samej zamrożonej wersji i jawnego
`--resume`. Udane wyniki zerowe nie są ponawiane w poszukiwaniu sukcesu.

## Zamrożone tożsamości

W `release-v1/` znajdują się zachowane programy oraz `source.tar.gz`.

| Artefakt | SHA-256 |
|---|---|
| Protokół | `ddfe8c45f2c0adf86571eda3603c0796a4c7937db6902f1b076e35cc09607c28` |
| Dekoder czterech ścieżek, wcześniejszy tracking-v1 | `455a9622ba7c839fbba1c575b8139502a9b24c2b3f99421adb2e9ab74bb37ab3` |
| Kolektor wznowienia w `acquire-v3-resume/` | `9eeecfb569d9301646d52ea35d317437cca5bb410f65466dd6bb7689bf2b7b78` |
| `satnogs_holdout_run` | `1a7e5f23d0c4fef03572649890b5729cb3345979b60613c02f84d64f1a009061` |
| `satnogs_holdout_analyze` | `29c426700794c73b7af26ab4819cfbdc5fa98ea56d5cc3f786538dcf58a5f549` |
| `satnogs_holdout_controls` | `04ea1c04a1a98909f678f2839703a3b16b6077521cdf6324cd831f352116534d` |
| `source.tar.gz` | `d15bf3722c4b236caabe58d3e1d80a81620d9cec83772976e319b02785a71f76` |
| `baseline-runtime-v1.json` | `47ebfe89a7fcfa9eea5f4ca6e338930b9234fc8dd35c29389787109a1fa623e8` |
| `control-inputs-v2/manifest.json` | `380bfc7d54ecab2bf3dd38affa64eda3ef39c783374702a5a6812b67e800f7e5` |

## Co nadal będzie potrzebne do mocnej publikacji

Zakończony holdout z oceną brakujących danych, audyt i ponowne odtworzenie wszystkich
kandydatów dodatkowego odzysku, mocniejsze porównanie z istniejącymi wielościeżkowymi
modemami, przegląd najbliższej literatury i odtworzenie przez niezależnego badacza.
Dire Wolf jest dostępny, ale jego `atest` nie czyta głównego float32 PCM;
porównanie na wspólnym skwantowanym PCM pozostaje niewykonane.
Żadnego wyniku kontroli ani wcześniejszego przyrostu 5,24% względem własnego dekodera
nie przedstawiamy jako gotowego wyniku niezależnego testu lub 5,24% przewagi nad
historycznym SatNOGS.
