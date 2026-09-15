# Drugi eksploracyjny trening następcy — 4 września 2026

## Status

Drugi trening pośredni zakończono na fizycznie oddzielonym i zweryfikowanym
snapshotcie trwającej akwizycji. Snapshot zawiera 1718 odpowiedzi API i 39 984
rekordy przed deduplikacją; po normalizacji pozostało 32 300 obserwacji.
Wszystkie wyniki w tym katalogu są eksploracyjne i mają wymuszone flagi
`confirmatory_eligible=false`, `publication_eligible=false` oraz
`deployment_eligible=false`.

W porównaniu z pierwszym shadow runem liczba rekordów przed deduplikacją wzrosła
z 18 627 do 39 984. Zbiór zawiera 14 057 obserwacji z etykietą obecności sygnału
z 33 satelitów i 402 stacji oraz 7232 obserwacje dekodowania warunkowego z 28
satelitów i 245 stacji.

## Test czasowy na przyszłym okresie

Modele wybierano na czerwcowym oknie walidacyjnym, a wyniki poniżej pochodzą z
późniejszego, sierpniowego okna testowego.

| Zadanie i model | Testy | Trafność przy progu 0,5 | Brier | AUROC | ECE |
|---|---:|---:|---:|---:|---:|
| Sygnał — model logistyczny | 4302 | 72,34% | 0,1967 | 0,7871 | 0,0839 |
| Sygnał — model logistyczny + pogoda | 4302 | 72,15% | 0,1952 | 0,7870 | 0,0774 |
| Sygnał — wybrany HGB | 4302 | 72,27% | **0,1867** | **0,7947** | **0,0403** |
| Dekodowanie po sygnale — model logistyczny | 2435 | **77,29%** | **0,1485** | 0,8566 | **0,0591** |
| Dekodowanie po sygnale — model logistyczny + pogoda | 2435 | 77,41% | 0,1492 | 0,8538 | 0,0562 |
| Dekodowanie po sygnale — wybrany HGB | 2435 | 75,65% | 0,1496 | **0,8673** | 0,0678 |

Dla sygnału wybrano `hgb_extended_wide`. Względem modelu logistycznego Brier
spadł o 5,1%, a ECE o 52,0%. Grupowany bootstrap różnicy Brier dał estymatę
−0,00757, przedział 95% od −0,03053 do +0,01152 i 74,55% replikacji na korzyść
następcy. Jest to poprawa obiecująca, ale przedział przecina zero, więc nie jest
jeszcze potwierdzona statystycznie.

Dla dekodowania wybrany `hgb_extended_shallow` nie poprawił Brier względem
modelu logistycznego. Estymata grupowanej różnicy wynosi +0,00068, a przedział
95% od −0,01979 do +0,02064. Do obecnego kandydata wdrożeniowego dla znanych
satelitów i stacji należy więc nadal zaliczać prostszy model logistyczny.

Kalibracja wielookresowa wybrała `identity` dla obu zadań. Dodatkowa kalibracja
Platta, isotoniczna ani beta nie była stabilniejsza w kolejnych foldach.

## Rzeczywiste warunki środowiskowe i sprzęt

Do wzbogaconego zbioru dołączono 29 206 godzin historycznej pogody naziemnej i
2921 przedziałów trzygodzinnego indeksu Kp. Raport pochodzenia wiąże użyty zbiór
z 912 surowymi odpowiedziami źródłowymi. Pełny wektor pogody jest dostępny dla
31 952 z 32 300 obserwacji, a Kp dla wszystkich 32 300.

Sama pogoda nie zwiększyła AUROC. Dla sygnału nieznacznie poprawiła Brier i ECE;
dla dekodowania efekt Brier był lekko ujemny. Dane te pozostają użyteczne jako
cechy kandydackie, lecz nie wolno przypisywać im potwierdzonego efektu.

Nie użyto retrospektywnych parametrów anten, ponieważ dla dawnych obserwacji nie
ma wiarygodnych, obowiązujących w danym czasie konfiguracji. Współczesnej
specyfikacji anteny nie przypisuje się do przeszłości. Dane antenowe są
przewidziane dla kampanii prospektywnej, gdzie konfiguracja będzie znana przed
obserwacją.

## Nowe satelity i stacje — tryb cold-start

Tryb cold-start usuwa identyfikator i historię grupy, która ma być niewidziana,
a następnie wybiera model w zagnieżdżonej walidacji grupowej.

| Przypadek | Testy / grupy | Baseline Brier | Cold-start Brier | Zmiana | Trafność baseline → cold-start | Ocena |
|---|---:|---:|---:|---:|---:|---|
| Sygnał, nowy satelita | 950 / 7 | **0,2477** | 0,2498 | +0,8% | 63,47% → 57,05% | brak poprawy |
| Sygnał, nowa stacja | 758 / 26 | 0,2034 | **0,1767** | **−13,1%** | 70,71% → **71,37%** | obiecujące |
| Dekodowanie, nowy satelita | 571 / 4 | 0,2910 | **0,2283** | **−21,6%** | 44,66% → **61,47%** | mocny efekt, za mało grup |
| Dekodowanie, nowa stacja | 555 / 23 | **0,2101** | 0,2114 | +0,6% | 66,49% → 61,08% | brak poprawy |

Dla sygnału na nowych stacjach grupowana różnica Brier wynosi −0,02673,
przedział 95% od −0,05087 do +0,00045, a 97,35% replikacji przemawia za
cold-start. Efekt jest silny, ale przedział minimalnie przecina zero.

Dla dekodowania nowych satelitów różnica wynosi −0,06270 z przedziałem 95% od
−0,08995 do −0,03150; wszystkie replikacje przemawiają za cold-start. Ten panel
obejmuje jednak tylko cztery informatywne satelity, podczas gdy protokół wymaga
co najmniej pięciu, dlatego wynik pozostaje formalnie niewystarczający.

## Wniosek inżynieryjny

Aktualne wyniki uzasadniają architekturę routowaną, a nie jeden model globalny:

1. znany satelita i znana stacja — HGB jest kandydatem dla sygnału, ale model
   logistyczny pozostaje kandydatem dla dekodowania;
2. nowa stacja — osobny model grupowy ma przewagę dla sygnału;
3. nowy satelita — osobny model grupowy jest obiecujący dla dekodowania, lecz
   wymaga piątej informatywnej grupy;
4. jeśli odpowiednia bramka nie jest spełniona, system musi wrócić do modelu
   prostego albo zwrócić szerszą niepewność zamiast zawyżać pewność predykcji.

## Kontrole i pozostałe bramki

- Audyt testu czasowego przeszedł 11/11 kontroli.
- Audyt cold-start przeszedł 25/25 kontroli; nie wykryto przecieku grup.
- Protokoły, dane, skrypty, zależności i pliki predykcji są związane sumami
  SHA-256.
- Nadal brakuje pełnej reprezentacji 50 celów, co najmniej 40 satelitów z
  etykietą sygnału oraz piątego informatywnego satelity w zewnętrznym teście
  dekodowania.
- Wyniki trzeba powtórzyć na kompletnej kohorcie oraz potwierdzić w trwającej co
  najmniej miesiąc kampanii prospektywnej z realnymi blokerami i znanymi
  konfiguracjami anten.

## Artefakty

- `interim-snapshot-audit.json` i `snapshot-manifest-interim.json` — granica i
  integralność snapshotu;
- `covariate-evidence.json` — pochodzenie pogody naziemnej i Kp;
- `enriched-training/evaluation.json` — ablacja liniowa;
- `successor-benchmark-v1/` — test czasowy oraz audyt;
- `successor-calibration-v1/` — kalibracja wielookresowa;
- `successor-external-v1/` — diagnoza zwykłego modelu na nowych grupach;
- `group-robust-successor-v1/` — modele cold-start i audyt grupowy.
