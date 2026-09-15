# Uzupełnienie profili prywatnego SatNOGS

Stan wdrożenia: **12 dodatkowych profili oraz obsługa obu pasujących wariantów
Astrocast; ponowna analiza 207 nagrań uruchomiona 2026-09-11 o 00:14:24 UTC.**
To wynik rozszerzenia pokrycia konfiguracjami, nie wynik odzysku telemetrii.
Analiza nadal trwa.

Pierwsza kontrola po uruchomieniu, około 00:16 UTC: **6 rzeczywistych nagrań
zakończyło dostępne ramiona bez awarii**, w tym dwie obserwacje Astrocast
z równoczesnym sprawdzeniem obu wariantów framingu. Na tym wczesnym etapie
brak potwierdzonych ramek AX.25 UI; nie jest to wynik końcowy dla 207 nagrań.
Agregat zapisuje się partiami i może chwilowo pozostawać za wynikami pojedynczymi.

## Co oznaczał brak profilu

Profil opisuje, jaki odbiornik i format ramek zastosować: satelitę/NORAD,
częstotliwość, modulację, bitrate, framing i kodowanie. Dotychczasowy dispatcher
wymagał jednego zgodnego nadajnika w zainstalowanym katalogu gr-satellites.
Nie znalezienie go kończyło się `unsupported_profile`, bez dekodowania.
Nie oznaczało to braku sygnału ani zerowej skuteczności dekodera.

Były trzy rodzaje braków: nieobecne konfiguracje, stare/tymczasowe identyfikatory
satelitów oraz kilka prawidłowych wariantów formatu na tym samym kanale.
Osobną kategorią są protokoły wymagające niedostępnego dekodera; samo dodanie
nazwy satelity do katalogu nie zapewni ich obsługi.

## Pokrycie całej pobranej paczki

Audyt obejmuje wszystkie **538 oryginalnych nagrań**, nie tylko wcześniejszy
częściowy status 131 pominiętych obserwacji.

| Dobór konfiguracji | Nagrania |
|---|---:|
| Dopuszczone przez poprzednią politykę | 233 |
| Dodatkowo dopuszczone do prób odbioru | **207** |
| Razem po rozszerzeniu | **440 / 538 (81,8%)** |
| Nadal poza obsługą tego dispatchera | 98 |

**81,8% oznacza pokrycie konfiguracjami, nie skuteczność dekodowania.** W tej
liczbie znajdują się też jawnie oznaczone hipotezy odbioru ARICA-2 i SwissCube.
Przypisanie profilu nie potwierdza obecności sygnału, poprawności wszystkich
parametrów toru audio ani odzyskania choć jednej ramki.

| Satelita | Dodatkowe nagrania | Wprowadzona obsługa |
|---|---:|---|
| PCSAT | 23 | APRS AFSK1200 AX.25 |
| CUTE-1 | 24 | gałąź AFSK1200 AX.25; bez SRLL |
| CUTE-1.7+APD II | 5 | osobna gałąź CO-65 AFSK1200 AX.25; bez SRLL |
| AEPEX | 18 | FSK9600 G3RUH, powiązanie starego i aktualnego NORAD |
| COSMO | 18 | GMSK9600 / AX.25 G3RUH |
| KNACKSAT-2 | 13 | FSK9600 / AX.25 G3RUH na 400,630 MHz |
| KOSTKA | 16 | FSK9600 G3RUH, dewiacja 2400 Hz, powiązanie tymczasowego NORAD |
| LASARsat | 10 | gałąź pakietowa GFSK9600 G3RUH, nie CW |
| Marina | 16 | VHF145,925 MHz G3RUH, nie inny kanał UHF |
| FrontierSat | 14 | AX100 ASM+Golay / mode 5, randomizacja CCSDS i RS |
| ARICA-2 | 19 | hipoteza odbioru G3RUH4800, patrz zastrzeżenia |
| SwissCube | 12 | hipoteza BFSK1200 AX.25 dla audio po FM |
| Astrocast 0.1 | 19 | oba już zainstalowane warianty FX.25 NRZ i NRZ-I |

AEPEX ma dwa warianty prędkości w nowym profilu, 9600 i 19200; wybór wynika
z bitrate w metadanych. Łącznie dodane profile zawierają 13 nadajników.

## Źródła i kwalifikacja

Wartości i źródła każdego wpisu są zapisane w
`config/polyitan-supplemental-profiles-20260911.json`, a ich kopie z polem
`x-evidence` są zamrożone w katalogu wynikowym. Nie zamieniano dowolnych
identyfikatorów po podobieństwie nazwy.

- [PCSAT — operator APRS](https://www.aprs.org/pcsat.html) oraz [DB](https://db.satnogs.org/satellite/26931/).
- CUTE-1: [publikacja autorów misji](https://www.gov.br/inpe/pt-br/area-conhecimento/unidade-nordeste/conasat/documentacao/nano-satelites-pelo-mundo/cute-i-tokyo-institute-of-technology-japan/cute-i-sys-description.pdf). CUTE-II: [osobny wpis CO-65 w JARL](https://www.jarl.org/Japanese/7_Technical/satellite/japanese-amateur-satellite.htm). Obie misje mogą korzystać także z SRLL. Testowana jest gałąź AX.25; tony Bell202 stanowią standardowe założenie odbiorcze.
- AEPEX: [upstream SatYAML](https://raw.githubusercontent.com/daniestevez/gr-satellites/main/python/satyaml/AEPEX.yml) i [jawna zmiana NORAD 98864 → 68506](https://db.satnogs.org/satellite-reviewed-suggestions/11341).
- COSMO: [zgłoszenie operatora](https://gitlab.com/librespacefoundation/satnogs-ops/-/work_items/332). Zastosowano poprawione 9600, nie stare 19200.
- KNACKSAT-2: [zaakceptowany kontrakt operatora](https://db.satnogs.org/transmitter-reviewed-suggestions/11816).
- KOSTKA: [upstream SatYAML](https://raw.githubusercontent.com/daniestevez/gr-satellites/main/python/satyaml/KOSTKA.yml) i [powiązanie 98395 / 69935](https://db.satnogs.org/satellite/98395/).
- LASARsat: [kontrakt nadajnika](https://db.satnogs.org/satellite/62391/) i [dokumentacja modemu MURGAS](https://spacemanic.com/wp-content/uploads/2026/03/SM-MRG-DS-0001_3.1_Data-Sheet.pdf).
- Marina: [kontrakt VHF w DB](https://db.satnogs.org/satellite/FZQD-0263-2257-6241-5637/). Nie przypisano mu parametrów odmiennego kanału z upstream MARINA.yml.
- FrontierSat: [konfiguracja nadajnika opublikowana przez operatora](https://raw.githubusercontent.com/CalgaryToSpace/CTS-SAT-1-Ground-Station/main/docs/AX100_Config_Dump.txt). Surowe wyjście deframera CSP pozostaje kandydatem wymagającym osobnej walidacji; nie jest ramką AX.25 UI.
- Astrocast: [opis autora gr-satellites](https://destevez.net/2019/03/new-decoders-for-astrocast-0-1/) i zainstalowany `Astrocast_0_1.yml`. Testowane są oba zgodne warianty, bez arbitralnego wyboru jednego.

ARICA-2: [operator](https://sakamotolab.phys.aoyama.ac.jp/research/current_space/ARICA-2_en/amateur)
opisuje AX.25, NRZI i scrambling, ale bez jawnego wielomianu scramblera;
[JAMSAT](https://www.jamsat.or.jp/?page_id=2777) wskazuje zgodne narzędzia odbiorcze.
G3RUH jest więc oznaczoną hipotezą testową. Pakiety obrazu mogą mieć własny
nagłówek i FCS AX.25, bez pełnej struktury UI; surowych kandydatów nie odrzuca
się z archiwum, ale też nie zalicza do potwierdzonych UI.

SwissCube: [operator](https://swisscube.live/Home/RadioAmateurs) podaje BFSK1200
AX.25 i odbiór USB; [slajdy operatora, Main data downlink](https://www.hb9afo.ch/articles/swisscube/RA%20presentation%2003%20mar%2009.pdf)
podają dewiację 500 Hz. Metadane naszej obserwacji mówią AFSK, nie rozstrzygając,
jak przygotowano audio. Dodano wąsko przypisany do tego nadajnika test FSK dla
audio po FM. Dla audio po USB potrzebne mogą być inne tony. Nie użyto
parametrów Bell202 z dokumentacji uplinku jako dowodu dla downlinku.

## Co nadal wymaga pracy

98 nagrań pozostaje poza tym przebiegiem: FOX/DUV 31, PEARL-1B 17, RIDU GMSK
13, KOYO UHF 5, CW 16, SSTV 12, ISS FSK 2, METEOR M2-3 1 i TeikyoSat-4 FSK 1.
DUV wymaga właściwego dekodera FOX; CW/SSTV wymagają odpowiednich ścieżek audio,
z inną walidacją niż CRC ramki AX.25. Dla pozostałych nie ustalono w pełni
kontraktu właściwego kanału lub zgodności reprezentacji audio. Profile innych
nadajników tego samego satelity nie są automatycznie zamiennikami.

## Testy i uruchomienie

- Końcowy zoptymalizowany zestaw: **26/26 testów logiki PASS**.
- Osobny test integracyjny: **1/1 PASS**, obejmujący wszystkie **13 wariantów
  nadajników w 12 profilach**, rzeczywisty gr-satellites i sekundę ciszy. Wszystkie
  się uruchomiły, bez ramek z ciszy. To kontrola konstrukcji i negatywnego wejścia,
  nie pomiar czułości ani odsetka fałszywych detekcji na dużym zbiorze.
- Audyt routingu bez DSP: 207 nowo dopuszczonych nagrań, 440 łącznie, 98 poza obsługą.
- Niezależny przegląd wykrył błąd raportowania awarii dispatchera: naprawiono
  zachowywanie historii błędów, jawne liczenie brakujących wyników i niezerowy
  kod zakończenia takiego przebiegu. Dodano test regresji.
- Wznowienie natychmiast odświeża stan `terminal=false`; pole
  `all_selected_have_result` odróżnia koniec etapu od rozpatrzenia całej kolejki.

Uruchomiona usługa: `telemetry-polyitan-profile-extension-20260911-v1.service`.
2 równoległe nagrania × 2 wątki DSP, 900 s na dekoder, 8 GiB limitu całej usługi.
Wybór `--only-newly-routable` wynika wyłącznie z metadanych i porównania polityk
doboru profilu, nie z odzyskanych bajtów ani dotychczasowego sukcesu obserwacji.
Oryginalna usługa `telemetry-polyitan-audio-analysis-20260910-v1.service` nie
została zatrzymana lub podmieniona. Nie powtórzono jej już obsługiwanych nagrań.

Nowe pliki wynikowe w `/home/ubuntu/telemetry-yield/work/polyitan-profile-extension-20260911-v1/`:

- `analysis/summary.json` — bieżące wyniki 207 dodatkowych nagrań;
- `analysis/routing-coverage.json` — wszystkie 538 decyzji przed i po zmianie;
- `analysis/obs-ID/result.json` — wynik, profil, źródła i ograniczenia nagrania;
- `analysis/plan.json` — zamrożone wejścia, konfiguracje i identyfikatory;
- `build-and-launch.json` — kompilacja, testy, skróty i uruchomienie;
- `dispatcher-sources-v3.tar.gz` — źródła dispatchera, pomocniczych modułów i katalog.

Zamrożone źródła i katalog nie są pełnym archiwum wszystkich stron WWW ani
pełną reprodukcją systemu operacyjnego. Przebieg używa istniejącego, oznaczonego
SHA-256 modułu Rust i wcześniej zamrożonych binariów dekoderów. Nie zmieniano
ich rdzenia. Graphify posłużył do znalezienia dotychczasowej warstwy profili;
nową implementację i jej ograniczenia zweryfikowano w aktualnym kodzie Rust.
