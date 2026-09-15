# Plan poprawy — 7 września 2026

## Dlaczego zbiór był niewielki

Pierwotna kohorta to świadomie ograniczony panel 50 satelitów: cztery
14-dniowe okresy sezonowe oraz sierpień do testowania. Nie był to limit
archiwum SatNOGS. Ten projekt pilotażowy nie dawał dostatecznej różnorodności
do uzasadnienia skuteczności dla całej sieci.

Z 55 870 rekordów bazowych powstało 47 039 poprawnie znormalizowanych.
19 274 ma jednoznaczną etykietę obecności sygnału; 27 765 takiej etykiety nie ma.
13 588 kwalifikuje się do osobnego zadania przewidywania pliku demodulacji.
Następnie oddzielane są daty, satelity i stacje przeznaczone do walidacji/testów.
Liczby rzędu 3870 i 5042 w ostatnim raporcie były liczebnościami testów,
nie pełnego zbioru uczącego. Rozszerzone ostatnie dopasowanie korzystało z
15 840 etykiet sygnału i 6280 etykiet warunkowego pliku demodulacji.

Zwiększenie liczby danych nie gwarantuje poprawy. Brak oceny nie jest
nieudanym odbiorem. Plik demodulacji nie jest potwierdzeniem ramki z poprawnym CRC.

## Wdrożone teraz

1. Oddzielny kolektor całej sieci, bez listy 50 satelitów: 77 deterministycznie
   wybranych pełnych dni, po 7 na miesiąc od września 2025 do lipca 2026.
   Nie filtruje po sukcesie, porażce, nadajniku ani stacji. Pierwsza partia
   ma limit 12 000 odpowiedzi / do 300 000 zwróconych rekordów. Przekroczenie
   budżetu jest jawnie niepełnym pobraniem, a nie kompletną kohortą.
2. Atomowe transakcje danych i kursorów, surowe odpowiedzi skompresowane
   z sumami kontrolnymi, deduplikacja, zachowanie Retry-After po restarcie,
   ograniczenie wielkości odpowiedzi i rezerwa co najmniej 4 GB na dysku.
   Używany jest istniejący prywatny token, wyłącznie do żądań GET.
   Kolejka kończy po jednym dniu na miesiąc, zanim otworzy kolejny, aby nie
   rozproszyć budżetu wyłącznie na niepełne dni. Zwiększenie limitu czasu
   odpowiedzi z 30 do 120 s i zmiana kolejności są zapisanymi poprawkami
   transportowymi; poprzednie protokoły i kod zachowano. Zestaw dni,
   populacja, limit zapytań i etykiety nie zmieniły się.
   Limit pojedynczej odpowiedzi podniesiono z 5 do 100 MB (20-krotnie),
   z oddzielną poprawką protokołu i archiwum poprzedniego kodu oraz testów.
   Większa odpowiedź zatrzymuje kolektor z jawną przyczyną, bez zapętlenia
   ponowień i bez pomijania strony. Limit RAM usługi 3 GiB, rezerwa dysku,
   całkowity budżet pobierania i tempo zapytań pozostają zachowane.
3. Osobny trening na dotychczasowych danych, niezależny od pobierania.
   Trzy warianty: bazowy, rozszerzony, rozszerzony z kontekstem.
   Kontekst obejmuje historyczną pogodę, parametry orbitalne z TLE,
   dostępne i oznaczone czasem ustawienia odbiornika oraz istniejące
   udokumentowane cechy anteny. Nie uzupełniamy brakujących zysków anten
   wymyślonymi wartościami ani dzisiejszym profilem sprzętu.
4. Walidacja czasowa na marcu i czerwcu, z trzema oddzielnymi grupami
   satelitów lub stacji wyłączanych z uczenia. Łączymy przewidywania ze
   wszystkich grup zamiast rezygnować po wylosowaniu kilku małych satelitów.
   Przykłady testowe są identyczne pomiędzy porównywanymi wariantami.
5. Historia dostępna dopiero dobę po zakończeniu odbioru — jawne założenie
   testu odporności, nie potwierdzony czas publikacji etykiet SatNOGS.
   Raport obejmuje także czułość, fałszywe alarmy i prostą regułę większościową.
   Próg decyzji dobrany wyłącznie na marcu sprawdzamy następnie na czerwcu.
6. Osobny proces sprawdzający przyrost danych co 30 minut. Kolejne treningi
   uruchamia po przekroczeniu 5000, 20 000 i 100 000 nowych kwalifikujących
   się rekordów z w całości pobranych dni. Eksporty i protokoły są niezmienne,
   istniejące rekordy nie są liczone ponownie. Dwa treningi nie startują naraz.

## Ograniczenia przepustowości

W zweryfikowanym kodzie Network strona ma 25 rekordów, a domyślny limit
uwierzytelnionych żądań obserwacji wynosi 240/h. Nasz odstęp 15,25 s daje
maksymalnie około 5900 rekordów/h przed błędami, pustymi stronami i innym
ruchem. 300 tys. rekordów wymaga więc co najmniej około 51 godzin;
milion — około 7 dni. Serwer może narzucić dłuższe przerwy.

Źródła: [paginacja](https://gitlab.com/librespacefoundation/satnogs/satnogs-network/-/raw/master/network/api/pagination.py),
[limit zapytań](https://gitlab.com/librespacefoundation/satnogs/satnogs-network/-/commit/ddb322cf).

Sprawdzono też alternatywę dla API. W [dyskusji z sierpnia 2026](https://community.libre.space/t/bulk-access-to-network-observation-history-for-station-health-research/15221)
użytkownicy zgłaszają ten sam problem dostępu zbiorczego; nie wskazano tam
gotowego eksportu. Znaleziony [zewnętrzny zbiór 178 092 obserwacji](https://community.libre.space/t/database-download/5516)
pochodzi z grudnia 2019 i obejmuje też duże pliki multimedialne. Nie pobrano
go ani nie dodano do obecnego eksperymentu dotyczącego lat 2025–2026.

## Jeszcze nie osiągnięto

- Nie ma jeszcze dowodu, że nowe warianty poprawiają wynik końcowy. Wyniki
  rozwojowe nie będą opisywane jako niezależny test publikacyjny.
- Nie mamy prawdziwych etykiet CRC dla całej kohorty ani historycznej
  specyfikacji każdej anteny. To wymaga osobnej akwizycji/uzgodnienia danych.
- Miesięczna kampania porównawcza 8 września–9 października pozostaje
  bez zmian. Nowe modele nie zastępują zamrożonego V4 i nie sterują stacjami.
- Sierpień i zarezerwowany wrzesień nie są oceniane przez ten eksperyment.
  Końcowa weryfikacja nowego modelu wymaga oddzielnego zamrożenia wyboru.
- Pobranie całości milionowego archiwum ani uzgodnienie eksportu zbiorczego
  z operatorami SatNOGS nie zostało wykonane. Żadnych wiadomości z taką prośbą
  automatycznie nie wysyłamy.

## Miejsca z bieżącym stanem

- `reports/network-scale-20260907/progress.json`: rzeczywisty postęp pobierania.
- `reports/generalization-v3-20260907/local/progress.json`: rzeczywisty etap treningu.
- `reports/generalization-v3-20260907/local/results.json`: pojawi się po zakończeniu.
- `reports/generalization-v3-20260907/local/integrity-audit.json`: kontrole podziałów i pochodzenia.

Mapę projektu graphify wykorzystano do odnalezienia mechanizmów kohorty,
normalizacji, historii i oceny. Liczby i implementację sprawdzono w źródłach
oraz plikach wynikowych, a nie wywnioskowano z samej mapy.
