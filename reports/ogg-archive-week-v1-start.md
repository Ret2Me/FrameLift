# Pierwsza większa kohorta OGG — uruchomiona 2026-09-07

**Stan tego dokumentu: start badania, nie wynik zakończonej kampanii.**
Przed uruchomieniem dopracowano odbiornik, wykonano małą walidację,
kontrole syntetyczne, testy niezawodności oraz niezależne audyty.
Dotychczasowe wyniki: [zamknięcie wersji v2](audio-refinement-v2-qualification.md).

## Zamrożony wybór

Przejrzano pełny zwrócony przez API tydzień początków obserwacji
[2026-08-31, 2026-09-07) UTC: 47 stron, 1161 rekordów, 1160 różnych ID.
Jedna identyczna duplikacja została usunięta, a wcześniej analizowane ID
wykluczono. Spośród 1159 kwalifikowanych ID wybrano dokładnie 100
najnowszych według wcześniej zapisanej reguły.

Wynik doboru: **100 obserwacji CANVAS GMSK 9600 z 83 stacji**, rzeczywisty
zakres 2026-09-06 16:16:48–23:43:42 UTC. Jest to zatem jedna większa partia
z jednego dnia, **nie pełne pokrycie tygodnia, losowa próba populacji ani
100 niezależnych przelotów**. Referencje do OGG ma 94 obserwacji; pozostałe
6 pozostaje w mianowniku jako brak audio, bez dobierania zamienników.
Dostępność każdego odnośnika będzie dopiero sprawdzana podczas pobrania.

Status, liczba opublikowanych ramek i obecność audio nie wpływały na
wybór. Nie zmieniamy doboru ani odbiornika po uzyskaniu nowych wyników.

## Uruchomienie i postęp

Usługa użytkownika `telemetry-yield-ogg-archive-week-v1.service` wystartowała
2026-09-07 o 21:23:36 UTC. Zweryfikowana jako aktywna; pierwsze OGG zostało
pobrane, przekształcone do PCM i weszło do natywnego dekodowania bez błędów
w pierwszych sprawdzonych oknach. To nie jest jeszcze końcowy wynik obserwacji.

Kontrola po starcie: pierwsza obserwacja 14939959 zakończyła się poprawnie
około 21:26 UTC, oba dekodery dały 0 ramek, migawka archiwalna także była
pusta. Utworzono commit i kompaktowy checkpoint, usunięto wyłącznie własny
odtwarzalny WAV i rozpoczęto następną pozycję. Jedna obserwacja nie daje
podstaw do wnioskowania o skuteczności całej serii.

- Invocation ID: `88f5d2dacaed4404ba5f77126a74542f`.
- Katalog: `/home/ubuntu/telemetry-yield/work/satnogs-ogg-archive-week-20260831-v1`.
- `observations/<ID>/attempt-NNN/final.json`: ukończone wyniki lub jawne błędy.
- `summaries/*.json`: zwarte checkpointy po każdym ID oraz pełne
  podsumowanie końcowe / przerwania. Wybrać najnowszy po `recorded_utc`,
  nie po losowej nazwie pliku.
- OGG, referencje, odebrane FCS, surowy KISS, plany i wyniki pozostają.
  Tylko własny odtwarzalny WAV jest usuwany po zatwierdzeniu porównania.
- Wykonanie sekwencyjne, własna cgroup, limit pamięci 6 GiB, procesów 256,
  rezerwa dysku 1,5 GiB i limit artefaktów 3 GiB. Zmiana źródeł, niska
  przestrzeń lub niewyjaśniony żywy potomek zatrzymują pracę, nie dają zera.
- Limit pojedynczego uruchomienia usługi: 24 godziny. Ewentualny restart
  używa zachowanych commitów i nowych numerowanych prób, bez nadpisywania.
- Nie jest ustawiony automatyczny restart i nie ma wysyłki telemetrii.
  Kontrolowane zatrzymanie lub awaria wymagają odczytania przyczyny przed
  wznowieniem; raport startowy nie gwarantuje późniejszej aktywności usługi.

## Sprawdzenia przed startem

- Mała walidacja: 85 ramek natywnych wobec 66 baseline'u, wszystkie
  dodatkowe już obecne w archiwum. Bez strojenia na tych nowych wynikach.
- Świeże syntetyczne kontrole: 90 różnych okien / 540 s, zero ramek i
  błędów, dokładna powtórka. Nie jest to kwalifikacja niskiego FAR stacji.
- Runner: 77 testów autora i niezależnego audytora oraz dodatkowe
  24 wykonania timeout/cleanup przeszły. Wcześniejsze nieudane przebiegi
  i poprawki są zachowane, nie zostały nadpisane.
- Rzeczywisty test 6 s starego rozwojowego OGG w odrębnej cgroup przeszedł
  całą ścieżkę ffprobe → ffmpeg → native → baseline → walidacja identycznego
  PCM. Zakończył się 21:14:25 UTC. Nie jest nową próbką skuteczności.
- Niezależnie sprawdzono wszystkie strony, globalne sortowanie, wykluczenia,
  hashe oraz 3342 tożsamości plików kodu/runtime przed startem.

SHA-256 zamrożonych artefaktów:

| Artefakt | SHA-256 |
|---|---|
| runner | `902d2ce03090e053bd3ce7e5ca2aee5de2900b967adc9c9df0f8d1f80b8435f6` |
| plan.json | `88205a49780a304fb188c4f1f56e74b9e01f5a7820ccbffa8a04e680c2ec28bb` |
| cohort.json | `c7b25894600f011fd6a84026faab606e380c418c4045583ab7b075da47fa7a95` |
| freeze.json | `e1fe6aa105dced7a400c5bda8b741ee45216297369f547b8b7ded288242a62fe` |

Audyt kodu, rzeczywistego canary oraz kohorty:
`work/satnogs-ogg-refinement-20260907/verification/archive-runner-corrected-b-independent-audit.md`,
`archive-runner-live-canary-independent-audit.md`,
`archive-frozen-cohort-independent-audit.md`.

## Jak czytać przyszły wynik

Oddzielamy dodatkowe ramki względem migawki archiwum, względem istniejącego
dekodera na identycznym PCM oraz nieobecne w obu źródłach. Deduplikujemy
pełne bajty per obserwacja i osobno globalnie. Brak dowolnej referencji
blokuje twierdzenie o nieobecności ramki w archiwum tej obserwacji.

Dodatkowe ramki są kandydatami wymagającymi powtórnego odtworzenia z OGG
i audytu pochodzenia. Poprawna FCS / AX.25 oraz wybór obserwacji CANVAS
nie dowodzą samodzielnie, który nadajnik wysłał ramkę. Wynik tej jednej
misji i trybu nie ustanawia powszechnej przewagi ani gotowości
publikacyjnej / produkcyjnej.
