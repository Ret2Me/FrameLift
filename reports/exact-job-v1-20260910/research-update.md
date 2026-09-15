# Wiarygodna poprawa prognoz odbioru satelitarnego

## Wnioski

Najbardziej uzasadniony kierunek pozostaje dwuczęściowy: adaptacyjna historia dla konkretnego satelity, stacji i nadajnika oraz weryfikacja na rzeczywiście przyszłych odbiorach, jednoznacznie powiązanych z prognozą. Przegląd nie daje podstaw do obietnicy dużego wzrostu poprawności przez samo zastąpienie obecnej metody siecią neuronową lub RL. Nowe publikacje z 2026 r. potwierdzają wartość kontekstu i historii, ale dotyczą innych celów pomiarowych albo symulowanego planowania.

Pełny [raport metod i eksperymentów](/home/ubuntu/telemetry-yield/reports/reception-revision-v1-20260910/deep-research.md) zawiera analizę 15 źródeł pierwotnych, pięć zaimplementowanych wariantów i porównania z silnymi metodami historycznymi. Niniejsze uzupełnienie dodaje trzy aktualne prace, decyzje wdrożeniowe i rzeczywisty pilotaż prognoz wystawionych przed przelotem. Żaden opisany wynik nie uprawnia jeszcze do twierdzenia o zwiększeniu miesięcznej liczby unikalnych próbek telemetrii.

## Nowe, istotne badania

### Modele drzewiaste i kontekst geograficzny

Horizon, opublikowane w PACM on Measurement and Analysis of Computing Systems w czerwcu 2026 r., łączy pomiary Starlink z pogodą i informacją orbitalną. Wykorzystuje mieszankę Random Forest i gradientowego wzmacniania drzew, a nie RL. Obejmuje 11 miesięcy danych z ponad 90 krajów i osobną tygodniową walidację czasową. Przedmiotem prognozy są opóźnienie i przepustowość Internetu, nie obecność sygnału ani ramki z konkretnego nanosatelity.[^1]

Przydatna inspiracja dotyczy różnorodności lokalizacji i interpretowalnej mieszanki modeli. Istotne ograniczenie przeniesienia to filtracja wartości odstających oraz tworzenie zagregowanego wyniku odniesienia z pomiarów w otoczeniu czasowym. W tym projekcie nie można kopiować takiej procedury do binarnych sukcesów i porażek: usunięcie trudnych odbiorów zmieniłoby pytanie badawcze. Wyniki błędu przepustowości nie przekładają się też na oczekiwany procent poprawnych odpowiedzi tak/nie. Autorzy udostępniają kod, jednak repozytorium zaznacza, że duże pliki danych nie są do niego dołączone.[^1][^2]

### Prognozowanie na różnych skalach czasu

Hu i współautorzy przedstawili na HotMobile 2026 prognozowanie przyszłych połączeń satelitarnych i parametrów łącza w różnych skalach czasu. Opis instytucjonalny wskazuje rozdzielenie okien połączenia i zmian jakości wewnątrz okna, z wykorzystaniem geometrii i telemetrii Starlink. Zweryfikowany został abstrakt oraz metadane publikacji; pełny tekst nie został pozyskany, dlatego nie przypisuje się tej pracy konkretnej architektury ani nieporównywalnej liczby określanej jako „accuracy”.[^3]

Wniosek projektowy jest własną interpretacją: plan miesięczny i korekta na godzinę przed odbiorem mogą potrzebować odrębnych prognoz. Do prognozy krótkoterminowej można włączyć nowszą historię, TLE i pogodę, ale trzeba zachować wcześniejszą wersję. Nie wolno oceniać miesięcznej prognozy tak, jakby od początku znała informacje z ostatniej godziny.

### RL rzeczywiście występuje w badaniach nad planowaniem

Dobariya i Hobiger opisują w CEAS Space Journal, w publikacji z 29 maja 2026 r., planowanie za pomocą policy-gradient RL i transformera typu pointer network. Badanie wykorzystuje syntetyczne scenariusze oparte na widoczności z TLE, a elewacja stanowi przybliżenie jakości łącza. Autorzy jawnie wskazują brak modelowania awarii pogodowych, awarii anten i niepewności czasu wykonania. Nie jest to zatem dowód lepszego przewidywania rzeczywistych sukcesów SatNOGS.[^4]

RL może być konkurentem dla optymalizacji harmonogramu, gdy rozmiar problemu i częstotliwość przeliczania uzasadnią koszt uczenia. W obecnym systemie nie ma jednak powodu zastępować nim modelu prawdopodobieństwa tylko dlatego, że uzyskał dobre wyniki dla innej funkcji nagrody. Najpierw trzeba wykazać przewagę predyktora przy wspólnych danych i osobno przewagę planera przy wspólnych prognozach.

## Dotychczasowy wynik implementacji metod predykcyjnych

Rozszerzenie historii wygenerowało 68 862 prognozy poprzedzające etykietę sygnału i 49 073 prognozy poprzedzające warunkową etykietę demodulacji. Są to dwa częściowo nakładające się zadania, nie suma rozłącznych obserwacji. Cała kohorta eksperymentu obejmuje 154 547 unikalnych obserwacji, z których tylko część ma etykiety i mieści się przed daną granicą czasową.

Poniższe wyniki dotyczą czerwcowego panelu rozwojowego. Kandydat został wybrany na marcu. Czerwiec był już wcześniej oglądany, więc nie jest to nowy, nietknięty test końcowy.

| Zadanie | Punkt odniesienia | Nowa historia kontekstowa | Ocena |
|---|---:|---:|---|
| Obecność sygnału, 1099 odbiorów | Najlepsza wcześniejsza hybryda: 87,17% | 85,26% | Brak poprawy |
| Artefakt demodulacji przy sygnale, 810 odbiorów | Wcześniejsza hybryda: 80,74% | 81,11% | Mała poprawa punktowa |
| Błąd prawdopodobieństwa demodulacji, niższy lepszy | 0,129811 | 0,125808 | Spadek około 3,1%, bez pewnej przewagi |

Poprawność oznacza zgodność decyzji przy progu 50% z etykietą. Nie jest precyzją pozytywnych wskazań: dla demodulacji ta druga wielkość w porównaniu pełnej historii spadła z 87,12% do 86,77%, przy wzroście czułości. Różnica błędu prawdopodobieństwa ma przedział od około −0,01002 do +0,00245, dopuszczający również brak poprawy. Modelu kampanii nie podmieniono. Dane liczbowe i decyzje są zapisane w [wynikach rozszerzenia](/home/ubuntu/telemetry-yield/reports/reception-revision-full-history-v1-20260910/results.json).

## Problem etykiety przypisanej do innego okna

Dotychczasowe uzgadnianie naturalnych obserwacji z planem dopuszczało dowolne dodatnie nakładanie się przedziałów. Test syntetyczny potwierdził, że dwa pięciominutowe okna stykające się przez jedną sekundę mogły zostać dopasowane. Pozytywny wynik całego rzeczywistego odbioru nie dowodzi jednak, że sygnał był obecny w tej jednej sekundzie. Także 95% pokrycia nie gwarantuje przeniesienia binarnej etykiety.

To wynik audytu implementacji, a nie zmierzona częstość błędnych dopasowań w przyszłej kampanii. Kampania rozpoczyna się 13 września i obecnie nie ma prospektywnych etykiet. Jej dotychczasowy rejestr i licznik dopasowań pozostają nienaruszone. Wyniki częściowego pokrycia należy przedstawiać jako przybliżone etykiety, nie potwierdzoną skuteczność dokładnie zaplanowanego wycinka.

## Wdrożona weryfikacja dokładnych zleceń

Oficjalny kod SatNOGS Network 1.132 potwierdza możliwość filtrowania obserwacji według stacji i przedziału czasowego. Filtr `start` oznacza początek nie wcześniejszy niż podana data, a `end` — koniec nie późniejszy niż podana data. API umożliwia także odczyt konkretnych ID. Publiczne odpowiedzi dla przyszłych obserwacji zawierają ich okna i TLE.[^5]

Osobny moduł odczytuje przyszłe obserwacje, oblicza geometrię z otrzymanego TLE na dokładnie tym przedziale i wystawia prognozę za pomocą niezmienionego, zamrożonego modelu. Zapisuje surową odpowiedź, faktyczny czas zakończenia pobierania, konfigurację stacji, cechy, prawdopodobieństwa i sumy kontrolne. Prognoza musi zostać trwale zapisana z co najmniej godzinnym wyprzedzeniem. Zmienione ID, stacja, satelita, nadajnik lub granice przedziału nie otrzymują poprzedniej etykiety.

Odczyt wyniku następuje nie wcześniej niż 24 godziny po zakończeniu okna. Jest to jawna reguła dojrzałości, nie gwarancja, że operator nigdy później nie poprawi oceny. Wynik nieznany lub brak rekordu nie staje się porażką. Etykieta demodulacji w analizie warunkowej jest oceniana tylko przy potwierdzonym sygnale. Kolejne pobrania mogą zachować późniejsze wersje etykiet w odrębnych katalogach; nie są automatycznie liczone jako nowe obserwacje.

Integracja używa anonimowego GET endpointu `observations`, a nie uwierzytelnionego GET `jobs`. W oficjalnej implementacji ten drugi może aktualizować `last_seen` stacji właściciela. Nie wykonano takiej operacji. Nie wysłano żadnego zlecenia obserwacji. Wspólny lokalny limit 61 sekund chroni publiczne API również przy innych działających kolektorach.[^6]

## Rzeczywisty pilotaż

Pilotaż rozpoczęto 10 września 2026 o 14:31:37 UTC, po zapisaniu protokołu doboru danych. Wybrano pierwszą stację z zarejestrowanej listy, 766, i wszystkie jej kwalifikujące się obserwacje z najbliższych 24 godzin dla zarejestrowanych 50 satelitów. API zwróciło 13 obserwacji: 11 dotyczyło satelitów poza zarejestrowanym zestawem, a dwie otrzymały prognozy. Nie wybierano ich według prognozowanego prawdopodobieństwa ani wyniku.

| ID SatNOGS | NORAD | Dokładne okno UTC, 10 września | Czas utrwalenia prognozy UTC |
|---|---:|---|---|
| 14967355 | 68635 | 19:03:25–19:08:34 | 14:32:45 |
| 14967447 | 68635 | 22:19:45–22:25:04 | 14:32:45 |

To test integracji na dwóch przyszłych odbiorach, nie trening na dwóch obserwacjach ani miarodajny pomiar skuteczności. Próba ocenienia ich od razu zwróciła poprawnie: zero ocenionych wyników, dwa zbyt wczesne przypadki, brak wartości accuracy i Brier. Najwcześniejsza wspólna ocena zgodna z protokołem przypada po 11 września, 22:25:04 UTC.

W pilotażu użyto rzeczywistego profilu anteny Turnstile i opublikowanych zakresów częstotliwości. Nie ma pomiaru jej zysku ani temperatury szumowej, więc te wartości pozostają puste. Archiwum prognoz głównej kampanii zaczyna się 13 września: nie pokrywa wrześniowych okien pilotażu z dnia 10. Pogoda i Kp zostały zatem jawnie oznaczone jako brakujące, a nie przeniesione z innej daty. Mechanizm dołączenia dostępnej pogody jest przetestowany, lecz ten pilotaż nie stanowi dowodu poprawy dzięki pogodzie. Co więcej, zamrożony model nie dopuścił pogody do aktywnych cech; pozostaje ona materiałem do odrębnego porównania.

## Testy, odtwarzalność i dalsze decyzje

Nowy moduł przeszedł 35 testów. Łączny zestaw z audytem przedziałów i testem starego dopasowania przeszedł 55 testów; kontrola importów potwierdziła 35 modułów pochodzących wyłącznie z zamrożonego środowiska i jego niezmienność. Testy obejmują rzeczywisty zamrożony predyktor z syntetycznym transportem HTTP, geometrię SGP4, brak anteny, dostępne/brakujące środowisko, błędne ID, przesunięcie o sekundę, opóźnione wyniki, nieznane etykiety, zmodyfikowane pliki i odmowę pobrania wyników przed terminem.

Zachowano także wcześniejszy nieudany przebieg: 25 testów przeszło, dwa ujawniły nieujednolicony typ wyjątku TLE i błędne oczekiwanie dostępności pogody poza zakresem archiwum. Poprawiono obsługę wyjątku i test granicy dostępności. Osobny omyłkowo zbyt szeroki wybór katalogu testów został przerwany przed wynikiem i nie jest zaliczany. Końcowe 35/55 zaliczeń pochodzą z jawnie wskazanych plików testowych, nie z tego przerwanego uruchomienia.

Kolejny eksperyment powinien porównywać silną historię i nowe warianty na tych samych przyszłych ID, z uprzednio zapisanym wyborem metod. Dwuobserwacyjny pilotaż nie spełnia tego warunku porównawczego: sprawdza tylko niezmieniony model i integralność przepływu danych. Skalowanie takiej walidacji wymaga osobnego protokołu, odpowiedniej liczby stacji i satelitów oraz analizy selekcji przez rzeczywisty harmonogram SatNOGS.

Walidacja predyktora nie zastępuje wykonania miesięcznego planu. Dokładność okien API nie jest niezależnym dowodem, że odbiornik rzeczywiście słuchał przez cały zadany przedział. Nie wykazano jeszcze zwiększenia liczby poprawnych, unikalnych ramek, przewagi nowych metod na niewidzianych grupach ani gotowości publikacyjnej całego systemu. Poprawa obejmuje obecnie mechanizm oceny i odtwarzalność, a nie potwierdzony nowy procent skuteczności.

### Artefakty

- [Implementacja](/home/ubuntu/telemetry-yield/work/operations/exact_job_validation_v1.py), [testy](/home/ubuntu/telemetry-yield/work/operations/test_exact_job_validation_v1.py).
- [Protokół rzeczywistego pilotażu](/home/ubuntu/telemetry-yield/work/exact-job-v1/pilot-20260910-1435/protocol.json), [potwierdzenie ukończenia](/home/ubuntu/telemetry-yield/work/exact-job-v1/pilot-20260910-1435/completed.json).
- [Kontrola 55 testów i importów](/home/ubuntu/telemetry-yield/reports/exact-job-v1-20260910/test-import-guard-combined.json), [próba zbyt wczesnej oceny](/home/ubuntu/telemetry-yield/reports/exact-job-v1-20260910/early-score-real/completed.json).

## Przypisy

[^1]: Benghe C., Graure V., Shreedhar T., Mohan N., [Horizon: Understanding and Predicting Global Starlink Performance](https://www.nitindermohan.com/documents/2026/pubs/sigmetrics26-horizon.pdf), PACM MACS 10(2), artykuł 41, czerwiec 2026, DOI 10.1145/3805639; sekcje 3.4–3.5 i 4.1. Pełna wersja autorska.
[^2]: SPEAR Research Lab, [Horizon-Predicting-Starlink-Performance](https://github.com/SPEAR-Research-Lab/Horizon-Predicting-Starlink-Performance), repozytorium autorów, dostęp 10 września 2026; opis danych, modeli i udostępnionych plików.
[^3]: Hu B., Qian F., Hassan A., Mao Z.M., Zhang Z.-L., [Predicting Connectivity and Link Performance in Dynamic LEO Satellite Networks](https://experts.umn.edu/en/publications/predicting-connectivity-and-link-performance-in-dynamic-leo-satel/), ACM HotMobile 2026, s. 79–84, publikacja 2 marca 2026, DOI 10.1145/3789514.3792058; abstrakt i metadane instytucjonalne.
[^4]: Dobariya R., Hobiger T., [Automatic scheduling of satellite tracking tasks by means of policy-gradient reinforcement learning and transformer-based pointer networks](https://link.springer.com/article/10.1007/s12567-026-00726-y), CEAS Space Journal, 29 maja 2026, DOI 10.1007/s12567-026-00726-y; sekcja 2.1.1 i ograniczenia.
[^5]: Libre Space Foundation, SatNOGS Network, [network/api/filters.py, wersja 1.132](https://gitlab.com/librespacefoundation/satnogs/satnogs-network/-/blob/1.132/network/api/filters.py), odczyt 10 września 2026; `ObservationViewFilter`. Kod tagowanej wersji i rzeczywisty odczyt API sprawdzono oddzielnie; tag nie jest dowodem identyczności całego wdrożenia serwera.
[^6]: Libre Space Foundation, SatNOGS Network, [network/api/views.py, wersja 1.132](https://gitlab.com/librespacefoundation/satnogs/satnogs-network/-/blob/1.132/network/api/views.py), odczyt 10 września 2026; `ObservationView` i `JobView.list`.

## Sources

1. Benghe, Graure, Shreedhar, Mohan. [Horizon](https://doi.org/10.1145/3805639). PACM MACS, 2026. Modele drzewiaste, geografia, pogoda i granice przenoszenia ewaluacji.
2. SPEAR Research Lab. [Repozytorium Horizon](https://github.com/SPEAR-Research-Lab/Horizon-Predicting-Starlink-Performance). Dostęp 10 września 2026. Publiczna implementacja i ograniczona dostępność dużych plików danych.
3. Hu, Qian, Hassan, Mao, Zhang. [Predicting Connectivity and Link Performance in Dynamic LEO Satellite Networks](https://experts.umn.edu/en/publications/predicting-connectivity-and-link-performance-in-dynamic-leo-satel/). HotMobile, 2026. Abstrakt; różne skale czasu.
4. Dobariya, Hobiger. [Automatic scheduling of satellite tracking tasks](https://link.springer.com/article/10.1007/s12567-026-00726-y). CEAS Space Journal, 2026. RL planera, syntetyczna ewaluacja i ograniczenia.
5. Libre Space Foundation. [SatNOGS API filters, 1.132](https://gitlab.com/librespacefoundation/satnogs/satnogs-network/-/blob/1.132/network/api/filters.py). Dostęp 10 września 2026. Filtry przedziałów i ID.
6. Libre Space Foundation. [SatNOGS API views, 1.132](https://gitlab.com/librespacefoundation/satnogs/satnogs-network/-/blob/1.132/network/api/views.py). Dostęp 10 września 2026. Skutki uwierzytelnionego GET jobs i alternatywny odczyt observations.
