# Kontrola walidacji przed startem kampanii

10 września 2026. Poniższe próby nie są wykonanymi odbiorami ani ukończonymi dniami kampanii. Nie zmieniono modelu, rejestracji, progów głównego audytu ani dziennika.

## Zgodność przedziału czasu

Test na zamrożonym, wdrożonym kodzie wykazał, że dopasowanie wymaga tej samej stacji, satelity i nadajnika, ale wystarczy dowolna dodatnia długość wspólnego czasu. W sztucznym przykładzie dwie obserwacje po 300 sekund, przesunięte o 299 sekund, zostały połączone mimo tylko jednej wspólnej sekundy. Brak wspólnego czasu został odrzucony. Testy opisują faktyczne zachowanie, a nie naprawiony mechanizm. Nie dowodzą, że taki przypadek już wystąpił w prawdziwych danych tej kampanii.

Zbiorcza etykieta jednej obserwacji nie musi być prawidłową etykietą innego, częściowo wspólnego przedziału. Sygnał lub ramka mogły wystąpić poza zaplanowanym oknem. Także negatywna ocena krótszego nagrania nie dowodzi braku sygnału w całym dłuższym oknie.

Dodany **audyt zgodności przedziałów** zachowuje oryginalne wyniki i oddzielnie zapisuje:

- długość części wspólnej i pokrycie obu przedziałów;
- zgodność początku i końca oraz zapis predykcji przed rozpoczęciem obu przedziałów;
- liczbę wyników przy pokryciu co najmniej 50%, 80%, 95% i 100% — wyłącznie jako diagnostykę;
- osobne liczebności etykiet dla dokładnie tego samego przedziału.

Dokładna zgodność jest rygorystycznym punktem odniesienia, nie automatycznym nowym kryterium odrzucenia całej kampanii. Różnice spowodowane precyzją znaczników czasu także będą widoczne. Żaden arbitralny procent wspólnego czasu nie jest gwarancją przenoszenia etykiety. Wyników opartych tylko na pokrywaniu się przelotów nie należy bez zastrzeżeń opisywać jako pomiaru odbioru wewnątrz dokładnego zaplanowanego okna.

Audyt sprawdza sumy zapisanych odpowiedzi źródłowych, tożsamość obserwacji, źródłowe etykiety, odwołanie do właściwej wersji predykcji i brak wielokrotnego użycia obserwacji. Nie zapisuje wyników do dziennika. Przy pustych danych nie zgłasza poprawnej zgodności. Pełne testy obejmują 14 przypadków, w tym przejście przez sztuczny dziennik i odrzucanie niepowiązanych lub zmienionych plików. Przed startem rzeczywistej kampanii liczba jej wyników wynosi zero.

To uzupełnienie diagnostyczne głównego audytu, **nie naprawa dotychczasowego dopasowywania ani potwierdzenie gotowości do publikacji**. Decyzję o ewentualnej zmianie reguły dopasowania lub dodatkowych prognozach dla z góry znanych okien zleceń trzeba jawnie opisać, bez wykorzystywania przyszłych etykiet do wyboru korzystniejszego wariantu.

## Koszt pobierania wyników

Dla pierwszego rzeczywistego planu sprawdzono scenariusz stałego harmonogramu, pustej historii uzgodnionych wyników i codziennego sprawdzania ostatnich 168 godzin. Maksimum wynosi 191 grup stacja–satelita–nadajnik. Przy odstępie 61 sekund sama przerwa między pierwszymi stronami odpowiedzi to 11 590 sekund, czyli około 3 godz. 13 min.

Gdyby każda grupa miała trzy strony, same przerwy wyniosłyby 34 892 sekundy, czyli około 9 godz. 42 min — więcej niż sześciogodzinny limit procesu. To scenariusz obciążenia, **nie zmierzony czas ani rzeczywista liczba stron**. Nie uwzględnia czasu HTTP, ponowień ani konkurowania o wspólny limit. Nie usunięto ograniczeń API ani nie założono, że wszystkie przyszłe zapytania zmieszczą się na jednej stronie.

## Korekta sposobu uruchamiania testów

Główny projekt ma ustawienie pytest `pythonpath = ["src"]`. Uruchomienie zewnętrznych testów z jego katalogu mogło więc wybrać kod główny pomimo ustawienia `PYTHONPATH` na kopię izolowaną. Wykryto to na błędzie importu nowego testu. Dotyczy to również wcześniejszego opisu samodzielnego uruchomienia 12 testów memoizacji jako „izolowanego”. Pierwotne raporty pozostawiono bez zmian.

Nowy uruchamiacz podaje konfigurację pytest jawnie i po testach sprawdza faktycznie załadowane moduły projektu. Ponowienie 12 testów na oryginalnym `runtime` zakończyło się poprawnie, z 28 modułami z właściwego katalogu i zerem z obcego. Sześć testów kontraktu dopasowania i obciążenia również przeszło na wdrożonej kopii, ze sprawdzeniem 34 modułów. Testy audytu przedziałów przechodzą taką samą kontrolę.

Nie podważa to pełnego zestawu 186 testów i 9 podtestów wdrożenia: uruchomiono go wewnątrz izolowanej kopii, a zawarty test wprost weryfikował jej katalog. Niezależne pomiary 500 prognoz i odtworzenie całego miesiąca były zwykłymi skryptami z właściwymi importami, a nie uruchomieniami pytest. Nie przeliczono ich wyników ani nie podmieniono zamrożonego modelu.
