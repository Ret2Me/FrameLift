# Jawne powiązanie ścieżek dowodów kampanii v4h

Przeniesienie programu do `work/prospective-v4h/runtime` odizolowało jego kod od dalszych eksperymentów. Historyczna ewaluacja zachowała jednak oryginalne, absolutne ścieżki do plików dowodowych. Archiwum pogodowe ma ścieżkę względną, lecz nie zostało skopiowane do odizolowanego katalogu. Bieżące plany i odpowiedzi źródłowe są natomiast zapisywane obok katalogu programu. Oryginalny audyt dopuszcza tylko pliki znajdujące się wewnątrz katalogu kodu, więc poprawne dowody w takim układzie katalogów zostają odrzucone.

Dodany audyt to jawna poprawka lokalizacji plików, nie zmiana eksperymentu. Uruchamia oryginalną, zamrożoną funkcję oceny dwukrotnie i zachowuje oba wyniki: bez poprawki oraz z listą dokładnych, sprawdzanych sumami SHA-256 powiązań. Nie zmienia żadnej wartości zwracanych bramek po ich obliczeniu. Nie zmienia modelu, warunków dopuszczenia, etykiet, rejestracji kampanii ani jej dziennika.

Każdy wyjątek od pierwotnego katalogu musi mieć uprzednie potwierdzenie integralności:

- archiwum pogody — sumę zapisaną w zamrożonym manifeście zbioru;
- historyczne predykcje i losowania statystyczne — parę ścieżka/suma w zamrożonej ewaluacji;
- plan, dane wejściowe i wyniki prospektywne — sumę zapisaną w zweryfikowanym łańcuchu zdarzeń tej samej kampanii.

Dozwolone są wyłącznie konkretne podkatalogi tego badania. Inne badania, pliki bez powiązania, zmienione treści i podmienione dowiązania są odrzucane. Suma jest sprawdzana ponownie przy użyciu pliku. Jeśli rejestracja, dziennik albo zamrożone zależności zmienią się podczas audytu, wynik nie zostaje opublikowany jako aktualny.

Pierwsze wykonanie przywróciło możliwość sprawdzenia dwóch wymagań historycznych: pochodzenia pogody oraz porównania modeli z pogodą i bez niej. Pozostałe wymagania nie zostały uznane za spełnione. Oryginalny raport z błędami ścieżek pozostaje dostępny w każdym katalogu audytu.

## Granice dowodów

Historyczna kohorta ma 47 039 obserwacji wszystkich 50 zaplanowanych satelitów. Posiada pogodę dla 46 471 obserwacji o potwierdzonej lokalizacji odbioru oraz Kp dla wszystkich 47 039; integralność źródeł opiera się na 1229 zachowanych odpowiedziach. Walidacje na wyłączonych z uczenia satelitach i stacjach mają zapisane predykcje i spełniają wcześniejsze wymagania liczebności. Przejście bramki oznacza wykonanie danego porównania, nie wykazanie przewagi modelu ani przyczynowego efektu pogody.

Konfiguracja odbiornika nie jest charakterystyką anteny. Historyczna kohorta ma **zero potwierdzonych konfiguracji antenowych**; 39 350 zapisów wzmocnienia RF opisuje ustawienie odbiornika, nie zysk anteny. Prospektywne odpowiedzi SatNOGS dostarczają rzeczywistych typów anten i obsługiwanych pasm. Nie zastępują pomiarów zysku kierunkowego, polaryzacji ani temperatury szumowej. Wymagania dotyczącego rzeczywistych danych antenowych nie wolno uznać za w pełni zamknięte na podstawie samej konfiguracji odbiornika.

Kampania v4h jest zarejestrowana na 13 września–14 października 2026. W chwili tego audytu nie ma zakończonych dni kampanii ani uzgodnionych wyników. Wymagane pozostają m.in. co najmniej 30 dni, 25 dni zapisanych kompletnych planów, 300 wyników sygnału i 100 warunkowych wyników demodulacji, wraz z ustaloną różnorodnością satelitów, stacji i klas wyniku. Kampania działa w trybie obserwacyjnym bez wysyłania zleceń: nie dowiedzie przyczynowego wzrostu uzysku przez wykonanie własnego harmonogramu.

Gotowość do publikacji pozostaje fałszywa. Ponadto publiczne udostępnienie wymaga rzeczywistych potwierdzeń licencji i uznań źródeł; audyt nie nadaje ich automatycznie.

## Zweryfikowany stan wdrożenia

Rozszerzony test narzędzia zakończył się wynikiem 15/15. Obejmuje m.in. obce kampanie, niedozwolone podkatalogi, zmienione sumy, konflikty przypisań, podmianę dowiązań i przyszłe ścieżki planów oraz źródeł wyników. Narzędzie, test, jednostki uruchomieniowe i wynik testu są przypięte w `audit-tooling.sha256`; sprawdzenie tego pliku jest warunkiem uruchomienia usługi.

Pierwsze wykonanie usługi zakończyło się poprawnie 10 września 2026 o 12:52:41 UTC. Zweryfikowało 86 powiązań plików. Dodatkowy zegar uruchamia audyt po starcie systemu i następnie co godzinę; nie uruchamia ani nie restartuje planera. Przy niezmienionym zestawie niespełnionych wymagań narzędzie nie wypisuje kolejnego podsumowania. Wyniki i manifesty pozostają w `bound-audits`, a sprawdzany sumą wskaźnik ostatniego wyniku w `bound-audit-latest.json`.

Odczyt zamrożonego modelu potwierdził, że oba zadania używają obecnie rodziny `operational_logit`, bez dodatkowej rodziny pogodowej. Pogoda została rzeczywiście zebrana i porównana w eksperymencie, lecz nie wykazała wymaganej przewagi pozwalającej włączyć ją do tej kampanii. Zapisanych pól pogodowych nie należy przedstawiać jako aktywnego wpływu pogody na jej prawdopodobieństwa.

| Wymaganie celu | Stan dowodów |
|---|---|
| Kohorta wszystkich 50 satelitów | 47 039 obserwacji; komplet reprezentacji, lecz etykiety sygnału dostępne dla 49, a warunkowej demodulacji dla 42 satelitów. |
| Rzeczywista pogoda | Źródła i porównanie modeli zweryfikowane; nie wykazano wymaganej korzyści i nie włączono tej rodziny do zamrożonej predykcji. |
| Rzeczywiste dane antenowe | Częściowe: 12 bieżących profili anten i pasm. Brak historycznych konfiguracji i zmierzonych charakterystyk; ustawienia RF nie wypełniają tego braku. |
| Walidacja na niewidzianych satelitach i stacjach | Zapisana, odrębna od treningu, ze zweryfikowanymi plikami; sama obecność walidacji nie oznacza przewagi nad konkurentem. |
| Co najmniej 30 dni kampanii z bramkami jakości | Niespełnione: kampania jeszcze się nie rozpoczęła; pierwszy plan jest w trakcie obliczeń. |
| Publiczne udostępnienie | Niespełnione: wymagane rzeczywiste potwierdzenia licencji i uznań źródeł. |

Nie zgłoszono ukończenia pełnego celu. Poprzednie, zerowe dni kampanii nie są zaliczane do nowej rejestracji.
