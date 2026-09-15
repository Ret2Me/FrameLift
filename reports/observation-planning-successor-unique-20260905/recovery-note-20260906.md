# Naprawa procesu i kontrolowane rozszerzenie badań — 6 września 2026

## Przyczyna postoju i naprawa

Trening lokalny zakończył się 5 września o 19:35 UTC, a trening po pobraniu nowych obserwacji o 22:24 UTC. Następny etap przerwała kontrola dwóch wartości wilgotności 102% w odpowiedzi Open-Meteo. Automatyczne ponawianie nie mogło usunąć deterministycznego błędu tych samych zapisanych danych. Poprzednie odpowiedzi w zadaniu raportowały ten postój zamiast go usuwać.

Nowy wariant `weather-recovery-v1`:

- oznacza pojedyncze nieprawidłowe wartości jako braki, bez przycinania do 100%, wymyślania wartości ani wyrzucania całej obserwacji;
- zachowuje surowe bajty, datę pobrania i sumę SHA-256; każda zmiana ma osobny rejestr możliwy do odtworzenia;
- nadal zatrzymuje się na błędach jednostek, tablic, znaczników czasu i integralności danych;
- wykorzystuje wcześniejsze odpowiedzi bez ponownego pobierania; nowe pobrania mają ograniczenie tempa i honorują `Retry-After`;
- zapisuje postęp oddzielnie dla każdej stacji i zakresu dat: 594 zadania, maksymalnie dwa jednocześnie;
- odróżnia przejściowy błąd sieci od błędu wymagającego poprawki, aby nie powtarzać bez końca tej samej awarii;
- uzupełnia tylko dopuszczone historyczne rekordy treningowe; odłożone obserwacje pozostają niezmienione;
- korzysta z już zweryfikowanego archiwum GFZ Kp, jeśli pokrywa wszystkie potrzebne historyczne przedziały.

Usługę wznowiono 6 września około 17:07 UTC. 14 testów naprawy przeszło, w tym test odtworzony na oryginalnej wadliwej odpowiedzi. W nowym pobraniu wykryto także 101% wilgotności — obsługa nie zatrzymała procesu. Nie zmieniono zamrożonego kodu, wyników ani modelu V4.

O 17:17 UTC zakończono wszystkie 594 zadania pogodowe. Spośród 6565 dodatkowych rekordów dopuszczonych do przynajmniej jednego zadania treningowego liczba rekordów z temperaturą wzrosła ze 183 do 6535 (99,54%). Trzy nieprawidłowe komórki pogodowe oznaczono jako braki. Wszystkie odłożone rekordy, etykiety, daty i identyfikatory grup pozostały bez zmian. Następnie rzeczywiście rozpoczęło się dopasowanie modeli na uzupełnionych danych.

## Dlaczego dodatkowe dane wymagają osobnego eksperymentu

W dopuszczonej części treningowej do przewidywania sygnału:

- wcześniejszy zbiór: 9730 przykładów, 42,95% pozytywnych, 99,12% z temperaturą;
- dodatek: 6110 przykładów, 23,04% pozytywnych, tylko 2,62% z temperaturą przed uzupełnieniem;
- 3176 dodatkowych przykładów dotyczy satelity 20442, a 1337 satelity 69015.

Dla wskaźnika dekodowania poprzednia część treningowa miała 59,43% pozytywnych wyników, dodatek 29,92%. Aż 1023 z 1658 dodatkowych przykładów dotyczyły jednego satelity (69015).

To zmiana składu próby, a nie samo równomierne zwiększenie liczby danych. Nie dowodzi jeszcze przyczyny całego pogorszenia. Nowy `robust-expansion-v2` sprawdza hipotezę w kontrolowany sposób:

1. porównuje oryginalny zbiór, pełny dodatek oraz dodatek ograniczony do 50 obserwacji na satelitę i miesiąc, wybranych bez używania wyników odbioru;
2. wybiera wariant na dwóch okresach rozwojowych — marcu i czerwcu — zamiast na jednym;
3. osobno uczy model dla znanych grup i model dla nieznanego satelity, bez jego identyfikatora i własnej historii;
4. zachowuje prostszy model bez dodatkowych danych wśród kandydatów;
5. nie wykorzystuje wrześniowego testu do strojenia ani automatycznej oceny.

Osiem testów nowego eksperymentu przeszło, w tym niezależność próbkowania od etykiet oraz rzeczywiste usunięcie identyfikatorów i historii nieznanego satelity z wejścia modelu. Oba eksperymenty działają jako osobne procesy i zapisują kompletne wyniki dopiero po zakończeniu.

## Ograniczenia

Nowe wyniki będą eksploracyjne. Sierpień był już oglądany; wrześniowy test pozostaje odłożony. Historyczna pogoda nie jest prognozą na miesiąc naprzód. Wskaźnik dekodowania oznacza plik demodulacji, nie zweryfikowane CRC. Opóźnienia historycznego oceniania i przesyłania wyników nadal wymagają osobnego audytu przed wdrożeniem. Nie ma automatycznej podmiany modelu miesięcznej kampanii.

## Bieżące artefakty

- `weather-recovery-v1/progress.json` — postęp uzupełniania pogody;
- `weather-recovery-v1/amendment.json` — zasady naprawy i pochodzenie;
- `weather-recovery-v1/results.json` — wynik po ukończeniu tego treningu;
- `robust-expansion-v2/protocol.json` — zasady drugiego eksperymentu;
- `robust-expansion-v2/*-development.json` — zakończone porównania rozwojowe;
- `robust-expansion-v2/results.json` — wynik po ukończeniu drugiego eksperymentu;
- `recovery-integrity-audit.json` — audyt dostępnych danych i rejestrów uczenia; pole `completion` odróżnia kontrolę częściową od pełnej.
