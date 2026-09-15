# Zamknięcie dopracowania OGG v2 — 7 września 2026

Wynik tego etapu: wersja nadaje się do rozpoczęcia kontrolowanej kampanii
badawczej na archiwalnych OGG. **Nie oznacza to gotowości produkcyjnej ani
gotowej publikacji.** Uruchomienie i wyniki kohorty tygodniowej mają osobny
raport; nie są częścią tej małej walidacji.

## Co zmieniono

Przyspieszono wykonanie NRZI, G3RUH i wyszukiwania flag bez zmiany
otrzymywanych bajtów ani kryteriów poprawności. Rozszerzono wybór zegarów
o zróżnicowane fazy / błędy prędkości, zachowując wszystkie dawne hipotezy.
Nie zmieniono zamrożonej kampanii IQ i nie uczono modelu AI.

## Co uzyskano

| Etap | Liczba obserwacji | Archiwum | gr-satellites na tym samym PCM | Natywny global | Natywny diverse |
|---|---:|---:|---:|---:|---:|
| Rozwój na znanych wynikach | 2 | 16 | 7 | 15 | 16 |
| Nowe nagrania, bez strojenia na wyniku | 2 | 87 | 66 | 85 | 85 |

Nowa walidacja dała **85 ramek / 22 440 B**, czyli **19 ramek / 5016 B
więcej** od komponentowego dekodera porównawczego; żadnej jego ramki nie
utracono. Wszystkie były już w archiwum. Dwie archiwalne ramki nadal są
nieodzyskane, w tym jedyna ramka obserwacji 14956101, która pozostaje
zerowym wynikiem obu dekoderów audio.

Na próbkach rozwojowych diverse dodał jedną wcześniej brakującą ramkę 56 B.
Nie dodał żadnej względem global na nowych nagraniach. W rozwoju ponowna
analiza znalazła jedną ramkę 70 B nieobecną w archiwalnej obserwacji, ale
znalazł ją także istniejący dekoder. W żadnym z czterech nagrań nie ma ramki
wyłącznie naszej, nieobecnej jednocześnie w archiwum i wyniku baseline'u.

Wektorowy wariant z dawnym bankiem miał obserwowany czas około 2,41 razy
krótszy od starego; rozszerzony bank około 1,8 razy krótszy. To pomiary na
współdzielonym hoście, nie izolowany benchmark ani porównanie równych
budżetów CPU z gr-satellites. Zbiorów rozwojowego i walidacyjnego nie
łączymy w pozorny procent skuteczności całej populacji.

## Kontrole

- 97 testów i 42 podtesty przeszły bez błędu; zapis JUnit zawiera 139
  przypadków, bo wlicza podtesty (`qualification-final-tests.xml`).
- Dwie powtórki pełnych nagrań rozwojowych dały identyczne ramki,
  oryginalne FCS i pochodzenie; wszystkie 163 wiersze dzienników są
  identyczne bajtowo. Różnica zapisu JSON 6 / 6.0 nie zmienia parametrów.
- Niezależny audyt nowych nagrań ponownie sprawdził oryginalne FCS,
  strukturę, pełne bajty i tożsamość audio: 714 kompletnych okien,
  zero błędów. Archiwum i KISS baseline'u nie zachowują tu FCS, więc
  niezależna kontrola odebranego FCS dotyczy wyników natywnych.
- 90 świeżych sześciosekundowych próbek: biały szum, szum skorelowany,
  zakłócenia tonowe z impulsową obwiednią. Zero ramek i zero błędów.
  Powtórka identyczna; unikalna ekspozycja to nadal tylko **540 sekund**.
  To kontrola podstawowa, nie wiarygodny pomiar niskiego FAR stacji.
- Audyt wykrył pominięcie `camras_replay.py` w nadrzędnej bramce hashy
  pierwszej kontroli. Plik był niezmieniony i figurował w wykonawczych
  planach v2. Bramkę rozszerzono o sumę zależności tych planów, kontrolę
  sprzecznych hashy oraz sprawdzenie przed i po próbie. Powtórka przeszła.
  Odbiornik i zachowane wyniki nie były zmieniane.

## Źródła wyników i dalszy etap

- Pełne PDU, bilanse i czasy: `public-satnogs-ogg-refinement-v2.json`.
- Audyty: `work/satnogs-ogg-refinement-20260907/verification/`.
- Powtórzona kontrola: `work/satnogs-ogg-refinement-20260907/null-smoke/repeat-b.json`.
- Odtworzenie odbiornika: `docs/audio-receiver-refinement-v2.md`.
- Ustalony przed nową kohortą dobór i liczniki:
  `reports/ogg-archive-week-preanalysis-v1.md`.

Dotychczasowe dane uzasadniają test archiwalny, ale nie dowodzą jeszcze
unikalnej przewagi nad wszystkimi dostępnymi dekoderami, wielu misjami,
oryginalnym torem RF SatNOGS ani bezpiecznej pracy produkcyjnej.
