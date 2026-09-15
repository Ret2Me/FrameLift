# Wdrożenie przyspieszenia — 10 września 2026

O 13:46 UTC przełączono planer, monitor i audyt danych na osobną, zweryfikowaną kopię `work/prospective-v4h/runtime-memo-v1`. Oryginalny katalog `runtime`, model, historia oraz pierwszy zapisany plan pozostały nienaruszone. Zastąpiono wyłącznie trzy dowiązania definicji usług; poprzednie pliki definicji są zachowane. Nie zmieniono rejestracji, terminów 13 września–14 października, dziennika, stacji, celów, ograniczeń ani progów jakości.

Warunkiem przełączenia były następujące dowody:

- 186 testów i 9 podtestów całego izolowanego programu, powiązanych z nową sumą kodu;
- pełne odtworzenie wcześniejszego miesięcznego planu: 39 841 możliwości i 12 202 wybrane obserwacje, dokładnie identyczny dokument JSON;
- 20 testów audytu, w tym odrzucanie podmienionych i niepowiązanych plików poprzedniego runtime;
- brak zmian modelu, danych historycznych, rejestracji i istniejącego dziennika podczas przygotowania i przełączenia.

Odtworzenie miesiąca zajęło 15 min 9 s. Obejmuje geometrię, prognozy i optymalizację, ale nie pobieranie danych ani serializację wyników. Nie porównujemy tego wprost jako kontrolowanego przyspieszenia z wcześniejszymi 73 min obejmującymi także pobranie TLE i zapis. Test 500 samych prognoz dał 7,39-krotne przyspieszenie z początkowo pustą pamięcią, przy dokładnie identycznych odpowiedziach.

Stan zweryfikowany o 13:47:29 UTC: nowy proces główny i proces planowania działają z `runtime-memo-v1`; pobrano 3744 godziny prognozy pogody, zapisano 12 profili sprzętowych oraz 25 odpowiedzi źródłowych. Audyt zakończył się poprawnie i sprawdził 103 powiązania plików. Wszystkie trzy zegary pozostają aktywne. **Pierwszy cykl po wdrożeniu jest jeszcze w trakcie**, więc jego rzeczywisty czas i nowy wynik harmonogramu nie są jeszcze potwierdzone.

Nowa suma kodu to `7cc358e8be8cb6450363cccd678ed201f68c852ed4f1f8268f05561aa8723213`; stara pozostaje odrębna i zachowana. Poprzednie zobowiązania planera nie zostały przepisane. Audyt dopuszcza dawny plik wejściowy wyłącznie przy zgodności zarówno z jego oryginalnym zdarzeniem dziennika, jak i przypiętą wersją pierwotnego kodu/danych.

To wdrożona poprawa kosztu obliczeń, **nie wzrost trafności prognoz**. Kampania nadal działa bez wysyłania obserwacji do SatNOGS. Nie upłynęło 30 dni; brakujące pomiary anten i rzeczywiste potwierdzenia uprawnień do publikacji pozostają odrębnymi wymaganiami. Gotowość do publikacji pozostaje fałszywa.

Maszynowy zapis wdrożenia: `compute-amendment-v1-activation.json`. Pełny test zgodności i jego manifest: `reports/prospective-month-memo-replay-v1-20260910`. Historyczny raport walidacyjny w `reports/prospective-history-memo-v1-20260910` opisuje stan przed aktywacją i celowo nie został po niej zmieniony.
