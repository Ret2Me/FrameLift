# Wrażliwość na opóźnienie nowych wyników odbioru

Eksperyment rozwojowy. Ten sam model i początkowa historia; zmieniamy tylko dostępność nowych wyników po treningu.
Wariant modelu i próg pomocniczy pochodzą z marcowej walidacji. Poniżej poprawność decyzji przy stałym progu 50%.

| Zadanie / scenariusz | Liczba prognoz | Bez nowych wyników | Po 24 h | Po 72 h | Po 7 dniach |
|---|---:|---:|---:|---:|---:|
| Sygnał / znane | 1099 | 79.7% | 84.6% | 83.8% | 82.1% |
| Sygnał / nowy satelita z adaptacją | 2115 | 65.8% | 78.0% | 75.6% | 73.2% |
| Sygnał / nowa stacja z adaptacją | 2115 | 69.8% | 79.8% | 76.5% | 73.6% |
| Plik po demodulacji przy sygnale / znane | 810 | 74.1% | 78.8% | 76.7% | 75.8% |
| Plik po demodulacji przy sygnale / nowy satelita z adaptacją | 1640 | 55.9% | 70.6% | 67.3% | 62.0% |
| Plik po demodulacji przy sygnale / nowa stacja z adaptacją | 1640 | 54.1% | 70.7% | 67.9% | 62.7% |

## Ograniczenia

- Czerwiec był już używany rozwojowo: nie jest nowym niezależnym testem końcowym.
- Opóźnienia 24/72/168 h są scenariuszami, nie pomiarem terminów publikacji w SatNOGS.
- Początkowy zbiór treningowy i jego założenie 24 h pozostają stałe. To test opóźnienia NOWYCH wyników, nie całej historii.
- Prognozy dotyczą przelotów za 2–26 godzin, nie skuteczności nieruchomego planu miesięcznego.
- Nowe grupy mogą korzystać z wcześniejszych wyników w okresie odtwarzania. Nie jest to test bez jakiejkolwiek historii.
- Plik po demodulacji nie oznacza potwierdzonych poprawnych pakietów ani kontroli CRC.
- Wyniki według liczby wcześniejszych odbiorów pary stacja–satelita są opisowe; przeloty mogą zmieniać grupę między scenariuszami.
- results.json zawiera także błąd prawdopodobieństw i sparowane przedziały z losowania bloków dni. Zależności między satelitami/stacjami mogą pozostawać.
- Nie wdrażano nowych modeli i nie zmieniano zamrożonej kampanii V4.
