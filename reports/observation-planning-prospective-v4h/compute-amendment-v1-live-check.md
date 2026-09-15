# Pierwszy cykl po wdrożeniu zakończony

Sprawdzono 10 września 2026 po zakończeniu usługi.

Cykl rozpoczęty o 13:46:03,817 UTC zakończył się o 14:00:35,644 UTC — po 14 min 31,826 s. Sama faza planowania od jej rozpoczęcia do zapisu zobowiązania zajęła 850,242 s, czyli 14 min 10,242 s. Nowy plan zapisano o 14:00:23,665 UTC jako wersję 2 tego samego planu i kampanii.

Wynik: **12 208 obserwacji**, 39 837 możliwości, wszystkie **50 satelitów**, 12 dostępnych stacji. Solver zwrócił `optimal` i lukę optymalności 0. Usługa zakończyła się kodem sukcesu, a monitor potwierdził zdrowy stan oczekiwania na kolejne uruchomienie.

To rzeczywisty cykl obejmujący pobranie danych, nie tylko pomiar 500 wywołań modelu. Wykorzystał nowe TLE i pogodę; nie jest więc kontrolowanym porównaniem czasu na identycznych wejściach z wcześniejszym cyklem. Różnicy 6 wybranych obserwacji nie należy przypisywać poprawie modelu. Osobny test odtworzenia przy identycznych wejściach dał wcześniej identyczny dokument planu.

Zachowano rejestrację kampanii (SHA-256 `f07c157858b85c55bb25b62df50695bd93678a8b9fa16c80b717279903c53977`). Dziennik został prawidłowo rozszerzony o nową wersję planu; jego suma po cyklu wynosi `a7f5370ff6e844ef980c104d795f93c5bc468e059516ef83956d4002f7a57722`. Nowy plan ma sumę `112f7703b989ba622720b0ad72ae6b47fc9dab43f5b814989eca13f95e5701b4` i znajduje się w `work/prospective-v4h/plans/plan-20260910T134603817441Z.json`.

Nie wysłano zleceń obserwacji. Kampania rozpoczyna się 13 września: nadal ma **0 ukończonych dni i 0 uzgodnionych wyników**. Wcześniejsze notatki o trwającym cyklu opisują stan w chwili ich utworzenia i nie zostały przepisane.

Przed startem wykonano dodatkową kontrolę walidacji. Ujawniono, że obecne dopasowanie akceptuje nawet sekundę wspólnego czasu różnych przedziałów. Wdrożono osobny, godzinowy audyt ich zgodności, bez zmiany modelu ani starego dziennika. Audyt nie jest naprawą reguły dopasowania; ma ujawnić ograniczenia przy interpretowaniu późniejszych wyników. Opis i testy znajdują się w `reports/prospective-shadow-preflight-v1-20260910/README.md`.

Gotowość do publikacji pozostaje fałszywa. Szybsze działanie nie zastępuje miesięcznej walidacji, rzeczywistych danych antenowych ani kontroli tego, co dokładnie oznacza etykieta odbioru.
