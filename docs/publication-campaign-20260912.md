# Kampania publikacyjna — decyzja o grupie i warunki wykonania

Stan dokumentu: 12 września 2026, 11:59 UTC, przed pobraniem nowych falowych danych tej kampanii. Na wyraźne polecenie użytkownika kampania dotyczy wyłącznie istniejących nagrań: nie czeka na przyszłe obserwacje. To plan wykonawczy, nie raport skuteczności ani deklaracja gotowości publikacji. Wcześniejsze protokoły pozostają zachowane.

## Główna grupa badawcza

Publiczne archiwalne obserwacje SatNOGS satelity CANVAS (NORAD 68635), wskazany nadajnik GMSK 9600, AX.25/G3RUH, oryginalne mono OGG/Vorbis 48 kHz. Wykorzystujemy wcześniej ustaloną listę 500 kandydatów, wybraną deterministycznie według stacji i dnia z dostępnych metadanych, oraz jej cały dopuszczony podzbiór. Nie wybieramy według liczby ramek, wyglądu waterfallu ani oceny powodzenia odbioru.

Źródłowa kohorta: `work/innovation-benchmark-20260910-v2/acquisition-corrected-v4-final/cohort.json`, SHA256 `dc473c071d98974ddaba15d518a3048dcff87f46fe73e0088aeefd874e0abb1a`. Kontrola aktualności z 12 września 11:53:27 UTC nie zmieniła proponowanych identyfikatorów ani ich kolejności. Zamrożenie odbiorników zakończono 11 września o 10:27:39 UTC. Dzisiejszy kandydat13 rozszerzeń PSK nie zastępuje tych odbiorników w trakcie tego eksperymentu.

Zmiana wobec wstępnego planu tego zadania: prospektywny tydzień 12–19 września **nie jest aktywną kampanią**. Jego oryginalne dokumenty pozostają niezmienione, ale nie blokują terminu badania historycznego. Nie przedstawimy wyników archiwalnych jako wykonania tamtej rejestracji prospektywnej. Nie utworzono usługi ani harmonogramu oczekującego na przyszłe nagrania.

## Dopuszczenie grupy archiwalnej

Istnieje zamrożona lista 500 kandydatów historycznych; po dotychczasowych wyłączeniach została propozycja 266 nagrań z 161 stacji i 15 dni (11 sierpnia — 8 września). Reguła 120 minut tworzy w niej 44 połączone grupy czasowe. Nie są to 266 niezależnych prób ani 44 zmierzone niezależne przeloty.

Ta grupa pozostaje **niedopuszczona**, dopóki nie zostaną rozliczone wcześniejsze źródła danych i nowa kontrola lokalnego użycia. Nie usuwamy niejasnych artefaktów, żeby wymusić dopuszczenie. Uzasadniony zbiór możliwych źródeł można wykluczyć konserwatywnie w całości, ale nie można dopisać wymyślonego pochodzenia.

Jeżeli kontrola zakończy się pomyślnie, zostanie wykonany cały dopuszczony podzbiór w pierwotnej kolejności rang, bez uzupełniania braków wygodnymi nagraniami. Wyniki będą osobnym testem historycznym o lokalnie sprawdzonym użyciu danych, nie zamiennikiem prospektywnego tygodnia i nie dowodem globalnego braku wcześniejszej ekspozycji. Główna miara, porównania i reguły braków będą identyczne jak poniżej. Zamrożony analizator wyłącza niepasujące siedmiodniowe podsumowanie dla tej grupy; nie wolno opisać tych 15 dni jako tygodnia prospektywnego.

## Porównanie

Każde całe OGG przechodzi jedną wspólną konwersję do PCM16 bez zmiany częstotliwości próbkowania, mieszania kanałów czy ręcznej regulacji wzmocnienia. Pięć zamrożonych ramion:

1. Pełny dekoder progressive — główny oceniany odbiornik.
2. Wariant innovation-v2 korzystający dodatkowo z informacji o kodowaniu OGG.
3. Zamrożony wariant innovation-v1 bez tej informacji.
4. Dire Wolf z wyłączoną naprawą bitów.
5. gr-satellites ze wspólnym profilem FSK9600/G3RUH.

Główna miara to dodatkowe **minus utracone** dokładne PDU na nagranie względem sumy zbiorów wyników Dire Wolf i gr-satellites. Raport obejmie też bajty PDU, globalne powtórzenia, odsetek nagrań z zyskiem i stratą, czas CPU/wall oraz pamięć. Dodatkowej ramki względem dwóch uruchomień dekoderów nie nazywamy automatycznie nową ramką w całej bazie SatNOGS.

Każdy natywny wynik wymaga sprawdzenia odebranej sumy FCS, struktury AX.25 i zakończenia pełnego zestawu zadań. Eksporty porównawczych dekoderów nie zawierają FCS; ich integralność jest poświadczona ustawieniami dekoderów, a nie dopisaniem nowej sumy kontrolnej. Wariant innovation-v2 otrzymuje dodatkową informację OGG — wspólne PCM nie oznacza identycznej informacji dodatkowej.

## Braki, niepewność i koszty

- Wszystkie wybrane nagrania pozostają w zestawieniu. Błąd pobierania, nieobsługiwany format i timeout nie są zerowym wynikiem dekodowania.
- Podajemy wynik na kompletnych parach i jego ograniczenia oraz pełny bilans braków. Nie imputujemy brakujących wyników.
- Główne przedziały: 10 000 sparowanych losowań całych grup 120-minutowych ze stałym ziarnem; grupy 0 i 30 minut wyłącznie jako analizy wrażliwości. Poniżej 10 wnoszących dane grup przedział pozostaje niedostępny. Liczba 10 nie gwarantuje poprawności statystycznej.
- Liczba 500 jest limitem zakresu, nie obliczeniem mocy badania. Nie przedłużamy zbioru, aby wymusić korzystny wynik.
- Cztery równoległe obserwacje, dwa wątki natywnego DSP, limit bezpieczeństwa 900 s na ramię. Nie jest to porównanie przy równym koszcie obliczeń ani pomiar izolowanej latencji. Wniosek dotyczy odzysku offline i jego kosztu, nie uniwersalnej przewagi algorytmu.

## Kolejność wykonania i audyt

1. Ponowne sprawdzenie zamrożonych plików i testów narzędzi.
2. Dopuszczenie konkretnej kohorty na podstawie metadanych, pochodzenia i aktualnej kontroli ekspozycji.
3. Pobranie z weryfikacją integralności i ograniczeń formatu; pełne pięcioramienne przetwarzanie z możliwością wznowienia.
4. Niezależne sprawdzenie zbiorów ramek, mianowników, kompletności, strat i kosztów; dopiero potem interpretacja i materiały do publikacji.

Testy narzędzi wykonane 12 września: 99 zwykłych zaliczonych oraz 1 osobno uruchomiony test istniejącego raportu rozwojowego, zero błędów. To nie jest 100 przetestowanych obserwacji ani nowy wynik odzysku danych. Szczegóły: `work/publication-execution-20260912-v1/control-tests/CONTROL_REPORT.md`.

Żaden zapis tego dokumentu nie uruchamia automatycznie procesu w przyszłości ani nie wystawia zgody na użycie danych. Stan faktycznie działających procesów, dopuszczenia i zakończonych testów musi być raportowany oddzielnie.
