# Plan dopracowania odbiornika audio przed dużą kampanią

Plan zapisany w trakcie rozwoju, po poznaniu wyników dwóch dodatnich
nagrań CANVAS. Nie jest rejestracją ślepego eksperymentu ani zmianą
zamrożonej kampanii IQ.

## Punkt odniesienia

Pilotaż OGG: obserwacje 14366383 i 14115025, 15 ramek natywnych wobec
7 ramek dekodera porównawczego uruchomionego na tym samym audio. Jedna
ramka występuje tylko w wyniku dekodera porównawczego. W archiwum jest
16 ramek, z czego natywny tor odtwarza 14; dodatkowe 70 B względem
archiwum odzyskują obie metody. Liczba ramek wyłącznie natywnych wobec
archiwum i dekodera porównawczego wynosi zero.

## Dwie rozdzielone zmiany

1. Wektoryzacja NRZI, G3RUH i wyszukiwania flag AX.25 bez zmiany FCS,
   struktury, limitu długości, kolejności ani otrzymywanych bajtów.
   Sprawdzamy dokładną zgodność na danych syntetycznych, uszkodzonych,
   szumie i na obu pełnych nagraniach.
2. Zróżnicowanie banku zegara: zachowanie oryginalnych 16 hipotez oraz
   dodanie maksymalnie dwóch rozdzielonych fazowo hipotez dla każdego
   błędu zegara. Cały bank 160 ustawień nadal jest oceniany bez użycia
   treści ramek; maksymalnie 26 zamiast 16 przechodzi do dekodowania.
   Nie stosujemy uzupełniania danych ze znanej ramki ani osłabienia CRC.

Mała diagnostyka okien 156, 159 i 162 s w 14366383 jest jawnie
analizą po poznaniu wyniku. Wybrano je według przybliżonego czasu
opublikowanego pliku ramki. Znaczniki czasu KISS są czasem obliczeń,
nie pozycją audio, i nie służą do tego przypisania.

## Weryfikacja przed szerokim przeglądem

- Testy różnicowe szybkiego dekodera i integracyjne zachowania
  wszystkich dotychczasowych hipotez i poprawnych ramek.
- Pełne przebiegi starego banku z szybkim dekoderem oraz banku
  rozszerzonego; żaden błąd wykonania nie jest liczony jako zero ramek.
- Niezależna kontrola FCS i pełnych bajtów oraz powtarzalność wyników.
- Mała dodatkowa próba dodatnia, której natywne wyniki nie były
  wcześniej użyte do strojenia. Dobór według publicznych metadanych
  i rzeczywistych ramek referencyjnych; `ffff` i podobne atrapy nie
  kwalifikują obserwacji. Ta sama misja nie daje dowodu między-misyjnego.
- Jawny bilans ramek nowych, brakujących i kosztu obliczeń. Czasy
  współbieżnych zadań są pomiarami operacyjnymi, nie izolowanym
  benchmarkiem CPU.

O wyborze większej próby, obsługiwanych trybów, kontrolach fałszywych
alarmów i zamknięciu wersji decydujemy przed jej przetwarzaniem.
Nie uruchamiamy wielkoskalowych testów w trakcie tych poprawek i nie
pożyczamy statystyk fałszywych alarmów ze starej kampanii IQ dla
nowej ścieżki audio.
