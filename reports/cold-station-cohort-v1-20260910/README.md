# Osobna populacja do prospektywnej oceny nowych stacji

## Wynik tego etapu

Zarejestrowano **25 dostępnych stacji nieobecnych we wszystkich 47 039 rekordach użytych do uczenia zamrożonego modelu**. Niezależny audyt odtworzył tę listę z pełnych surowych danych: przecięcie ze zbiorem 532 stacji uczących wynosi zero. Rejestracja dotyczy populacji przyszłego, osobnego doświadczenia — **nie jest zakończoną walidacją, uruchomieniem kolektora prognoz ani nowym wynikiem trafności**.

Główna kampania, jej 13 stacji, model, dziennik i istniejące prognozy pozostały niezmienione. Nowej populacji nie dopisano do poprzednich wyników. Okres pozostaje pełny: 13 września–14 października 2026, te same 50 satelitów. Osiem z tych satelitów jest również nieobecnych w zamrożonej historii, ale samo wybranie ich identyfikatorów nie zapewnia uzyskania dostatecznej liczby przyszłych ocen.

## Reguła wyboru i rzeczywista chronologia

Regułę zapisano 10 września 2026 o **22:26:23.561203 UTC**, przed nowym pobraniem katalogu. Wykonano jedno anonimowe zapytanie GET do oficjalnego katalogu stacji SatNOGS. Odpowiedź HTTP 200 odebrano o **22:26:51.255458 UTC**. Populację zarejestrowano o **22:26:52.447149 UTC**. Zachowano dokładne bajty odpowiedzi, czas ich odebrania, bezpieczny podzbiór nagłówków i sumy kontrolne. Nie użyto pamięci podręcznej, tokena ani endpointu `/jobs/`.

Katalog zawierał 4466 stacji: 532 obecne w historii i 3934 nieobecne. Spośród tych drugich zakwalifikowano wszystkie 25 spełniających wcześniej ustaloną regułę. Pozostałe miały niespełnione wymagania połączenia, dostępności lub trybu testowego. Nie wykonano rankingu po skuteczności, liczbie wcześniejszych obserwacji, liczbie przyszłych zadań, właścicielu, nazwie ani opisie.

Wymagane są jawne wartości logiczne `is_connected=true`, `is_available=true`, `testing=false` oraz poprawne współrzędne i pozostałe wymagane dane lokalizacji. Jeśli opublikowano zakresy anteny, muszą być poprawne. Dwie stacje bez opcjonalnych danych antenowych pozostają w próbie; pozostałe 23 mają opublikowane zakresy częstotliwości. Nie wymyślono zysku anteny, temperatury szumowej ani charakterystyki kierunkowej. Takie pomiary nie zostały przez ten etap uzyskane.

Identyfikatory stacji: `77, 452, 984, 1031, 1168, 1330, 1441, 1697, 1946, 2032, 2925, 3258, 3289, 3442, 3652, 3794, 4600, 4755, 4801, 4917, 5069, 5115, 5120, 5153, 5157`.

„Nowa stacja” oznacza tutaj nieobecność identyfikatora w dokładnym zbiorze użytym do uczenia tego modelu, także w rekordach bez etykiet. Nie oznacza niedawno zainstalowanej stacji ani stwierdzenia, że nigdy nie pojawiła się w żadnych wcześniejszych badaniach projektu. Wiarygodna ocena nadal wymaga rzeczywistych prognoz zapisanych przed przyszłymi przelotami.

## Implementacja i odporność na przerwanie

Osobny [rejestrator populacji](/home/ubuntu/telemetry-yield/work/operations/cold_station_cohort_v1.py) korzysta z zamrożonego parsera stacji i klienta obsługującego wyłącznie GET. Przestrzega wspólnego odstępu co najmniej 61 sekund pomiędzy anonimowymi zapytaniami. Rejestrator nie wykonuje automatycznych ponowień HTTP. Ponowne wywołanie po sukcesie wyłącznie weryfikuje istniejącą rejestrację, bez pobierania nowego katalogu.

Jeśli proces przerwano po pełnym zarchiwizowaniu odpowiedzi, wybierana jest ta sama pierwsza kompletna odpowiedź, a nie nowszy katalog z dogodniejszą populacją. Niekompletne próby są zachowywane. Rejestracja nie przechodzi przy zbyt małej próbie, zmianie zbioru uczącego, źródeł, modelu, konfiguracji, nieprawidłowej chronologii, powtórzonych identyfikatorach, obcym źródle HTTP lub stronicowaniu udającym pełny katalog. Odrzucane są też niekanoniczne i dowiązane ścieżki dowodów.

Konsument nowej populacji powinien wywołać `checked(..., expected_sha256=...)` z sumą protokołu zapisaną we własnym, później zarejestrowanym doświadczeniu. Obecna suma protokołu to `9e2718c1cb1ba5ed420904a77e8ed83c2fb44d7bfd70605a4ad07347f233f5ce`. Nie należy edytować zamrożonego rejestratora, jego testów, protokołu ani listy stacji po rejestracji.

## Testy i niezależny audyt

Końcowy zestaw przeszedł **97 testów w 11,02 s**: 62 przypadki wyboru/rejestracji oraz 35 istniejących przypadków dokładnej walidacji obserwacji. Sprawdzono 35 załadowanych modułów projektu; wszystkie pochodziły z właściwego zamrożonego środowiska, którego tożsamość pozostała niezmieniona. Pierwszy przebieg miał jedną błędną asercję konkretnego komunikatu: niedozwolona ścieżka była poprawnie odrzucona, ale przez wcześniejszą kontrolę. Poprawiono tę asercję przed końcowym przebiegiem i rejestracją; wcześniejszy wynik zachowano.

Osobny [audytor](/home/ubuntu/telemetry-yield/work/operations/audit_cold_station_cohort_v1.py) używa wyłącznie biblioteki standardowej. Nie importuje selektora populacji, parsera stacji ani modelu. Ponownie czyta wszystkie identyfikatory zbioru uczącego, surowy katalog, współrzędne i pasma, odtwarza kwalifikację stacji oraz sprawdza zgodność zapisanej listy i metadanych. Jego **8 testów przeszło w 0,60 s**, w tym próby celowo błędnych, ale spójnie przeliczonych sum kontrolnych: usunięcia stacji z populacji, pominięcia stacji uczącej, zmiany współrzędnych i wymyślenia zysku anteny.

Rzeczywisty niezależny audyt zakończył się **22:31:06 UTC**: wszystkie 25 identyfikatorów i ich dane geometryczne/antenowe odtworzono zgodnie, bez powiązania z jakąkolwiek stacją uczącą. Audyt nie potwierdza fizycznej prawdziwości deklaracji operatorów ani całego środowiska modelu niezależnie; te zakresy są oznaczone osobno. Nie ocenił żadnego nowego odbioru.

Dowody: [protokół populacji](/home/ubuntu/telemetry-yield/work/cold-station-cohort-v1/registered-20260910/protocol.json), [reguła przed pobraniem](/home/ubuntu/telemetry-yield/work/cold-station-cohort-v1/registered-20260910/selection-rule.json), [lista i kwalifikacja](/home/ubuntu/telemetry-yield/work/cold-station-cohort-v1/registered-20260910/selection.json), [niezależny audyt](/home/ubuntu/telemetry-yield/reports/cold-station-cohort-v1-20260910/independent-audit.json), [97 testów](/home/ubuntu/telemetry-yield/reports/cold-station-cohort-v1-release-tests-20260910.xml), [8 testów audytora](/home/ubuntu/telemetry-yield/reports/cold-station-cohort-v1-auditor-tests-20260910.xml).

## Wymagany następny etap — nadal niewykonany

Trzeba wdrożyć i zarejestrować osobny kolektor dokładnych przyszłych obserwacji dla tej populacji, zapis prognoz przed przelotem, pobieranie późniejszych ocen i analizę obu wyników: sygnału oraz artefaktu demodulacji przy niezależnie ocenionym sygnale. Aktualny stan stacji musi być sprawdzany ponownie podczas prognozowania, ponieważ katalog z 10 września nie gwarantuje dostępności przez miesiąc. Nie wolno zmieniać w tym celu zamrożonej konfiguracji głównej kampanii ani omijać jej kontroli dozwolonych stacji.

Porównanie powinno zachować te same dane uczące i obecne punkty odniesienia, bez dołączania etykiet nowych stacji podczas testu. Puste lub nieocenione odpowiedzi nie stają się porażkami. Reguły doboru prognozy, czasu dostępności etykiet, liczebności, niepewności oraz rozdzielenia nowych satelitów muszą zostać zapisane przed oceną, tak jak w głównym porównaniu dokładnych obserwacji. Nie obniżono wymagań miesięcznej kampanii, aby tę populację uznać za zwalidowaną.

To nadal będzie ocena predyktora na naturalnie zaplanowanych obserwacjach SatNOGS. Wykonanie naszego harmonogramu i pomiar przyczynowego zysku liczby poprawnych próbek wymagają dodatkowo uprawnień i rzeczywistych kalendarzy operatorów; ta rejestracja ich nie zapewnia. Artefakt demodulacji nie dowodzi poprawnego CRC ani nowej unikatowej telemetrii.

Graphify pomógł odnaleźć `station_inventory.py`, `cohort.py`, `history.py` i `PredictionRecord`, a dalszą implementację oparto na bezpośrednim odczycie aktualnych źródeł i danych. Rozwinięcie użyło ośmiu rzeczywistych słów grafu: `station inventory cohort cold unseen prospective history prediction`. Zapytanie pokazało 42 z 487 znalezionych węzłów przy budżecie około 1500 tokenów. Nie wykonano nowej ekstrakcji LLM; jej koszt wyniósł zero tokenów, co nie oznacza zerowego kosztu całej sesji.
