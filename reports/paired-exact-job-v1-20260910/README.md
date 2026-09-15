# Porównanie prognoz na tych samych przyszłych odbiorach

Zapisano dwie rzeczywiste prognozy porównawcze przed przelotami. Nie ma jeszcze wyników odbiorów ani nowego pomiaru skuteczności. To pilotaż poprawności prospektywnego porównania, nie zakończona walidacja miesięczna.

## Populacja i czas

Oryginalny pilot: `work/exact-job-v1/pilot-20260910-1435`. Porównanie: `work/paired-exact-job-v1/pilot-20260910`. Stacja 766, NORAD 68635, obserwacje 14967355 i 14967447; okna 10 września 2026 odpowiednio 19:03:25–19:08:34 i 22:19:45–22:25:04 UTC. Nowe prognozy zapisano 16:20:45.087769 UTC, ponad godzinę przed każdym oknem. Pierwotne prognozy i ich czasy nie zostały zmienione.

Wszystkie metody korzystają z tych samych 47 039 wierszy historii i informacji dostępnych najpóźniej przy odbiorze pierwotnego wejścia o 14:32:45.037784 UTC. Ten czas jest konserwatywnym potwierdzeniem dostępności całego pliku, nie datą pierwszego udostępnienia każdej etykiety. Porównanie nie dodaje nowych wyników i nie udaje, że powstało już o 14:32.

SHA-256 historii: `491d10f985b7e4de290ddfcc334487a6dfabcaf3f08f035c7f12959842fdf702`. Obie tożsamości występowały już w kwalifikowanej historii: nie jest to test nowych satelitów ani stacji.

## Metody i granice interpretacji

Porównywane są niezmieniony `frozen_logit`, główny punkt odniesienia `link_last10_2`, `last10_2` oraz częstość globalna. Metody historyczne łączą ostatnie wyniki łącza z ostrożnym oszacowaniem z historii satelity i stacji. Implementacje są importowane z zamrożonego środowiska kampanii. To nie jest porównanie z wcześniejszą, mocną hybrydą HGB/blend75.

Każda metoda otrzymuje tę samą etykietę tej samej obserwacji, przy zgodnych identyfikatorach i dokładnych granicach czasowych. Brak oceny nie jest porażką. Dla warunkowego wyniku demodulacji oceniane są wyłącznie przypadki z ręcznie potwierdzonym sygnałem. Raport zawiera błąd prawdopodobieństwa Brier, poprawność przy progu 50%, precyzję pozytywnych wskazań i czułość. Różnica Brier to model minus główna metoda historyczna; ujemna wartość sprzyja modelowi. Przedział z losowania całych dni wymaga co najmniej 10 dni; dwa przeloty nie pozwalają oszacować wiarygodnej przewagi.

Nie zmieniono wdrożonego modelu, planu ani zasad etykietowania. Nie wysłano zleceń do SatNOGS. To naturalnie zaplanowane obserwacje: wynik nie dowodzi przyczynowego zwiększenia uzysku planera ani poprawności CRC/unikalności telemetrii. Dane pogodowe/Kp dla tych przelotów są jawnie brakujące; nie zastąpiono ich późniejszą pogodą. Profil anteny zawiera rzeczywisty typ/pasmo, nie zmierzony zysk lub temperaturę szumową.

## Istotna hipoteza dotycząca etykiet

Historia zawiera 19 274 oceny sygnału i 13 588 ocen demodulacji, z czego 9163 dodatnie. Aż 8018 dodatnich artefaktów nie ma ręcznej oceny sygnału. Jest to świadoma zasada istniejącego normalizatora: artefakt może być dowodem demodulacji, ale nie zmienia niezależnej etykiety waterfallu.

Po ograniczeniu do potwierdzonego sygnału zostaje 5570 ocen demodulacji, z czego 1145 dodatnich. Udziały dodatnich wynoszą więc około 67,4% w szerokiej historii i 20,6% w ocenionym podzbiorze. Nie oznacza to, że stare dane są uszkodzone lub że wykazano przyczynę błędów modelu. Różnica może wynikać także z innych satelitów, stacji i sposobu ręcznej selekcji. Wskazuje jednak na potrzebę osobnego eksperymentu z identyczną populacją docelową podczas budowania historii i oceny.

W tym pilocie wszystkie historyczne metody zachowują dotychczasowe 13 588 etykiet; nie usunięto 8018 dodatnich przykładów ani nie zmieniono wcześniej zapisanych prognoz. Kandydat wykorzystujący tylko podzbiór z ocenionym sygnałem wymaga nowej wersji i chronologicznego porównania.

## Weryfikacja i wykonanie

Łącznie 70 testów przeszło w 39,37 s: 12 nowych przypadków oraz regresja oryginalnego pilota i bramki dokładnych okien. Kontrola importów potwierdziła 37 modułów projektu wyłącznie z zamrożonego środowiska oraz niezmienność źródeł. Raport: `reports/paired-exact-job-v1-combined-tests-20260910.xml`; kontrola: `test-import-guard-combined.json`.

Pierwsze uruchomienie dało 10 sukcesów i jeden błąd: nowa kontrola błędnie odrzucała dopuszczone artefakty bez ręcznej oceny. Naprawiono wyłącznie tę kontrolę przed wystawieniem prognoz, zachowano raport pierwszej porażki, dodano regresję. Kolejne niezależne uruchomienie dało 12 sukcesów. Nie sumujemy powtórnych uruchomień jako nowych przypadków.

Rzeczywisty wczesny scoring: `early-score/results.json`, zero dopasowanych wyników, dwa oczekujące, wszystkie miary skuteczności puste. Nie było dodatkowego żądania HTTP.

Jednorazowy timer porównania jest skonfigurowany na 11 września 2026 o 23:00 UTC. Korzysta z wyniku wcześniej skonfigurowanego pobrania etykiet o 22:30 UTC, sam nie pobiera danych. Wymaga ukończonego, zweryfikowanego wyniku nadrzędnego. Jeśli tamto pobranie nie powiedzie się lub pliki ulegną zmianie, zakończy się widocznym błędem, nie wymyśli etykiet ani nie nadpisze wcześniejszych plików. `tooling.sha256` przypina implementację, jednostki i zapisane prognozy.
