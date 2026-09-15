# Nocny etap badań — 12 września 2026

Zapisano i sprawdzono checkpoint **candidate13**. Ten etap napraw i testów jest
zakończony; nie oznacza to zakończenia całego programu badawczego ani wdrożenia.
Nie zmieniano zamrożonego badania FSK, chronionych zbiorów CANVAS/RML24 ani stacji.

## Najważniejsze wyniki

| Sprawdzenie | Wynik | Co to rzeczywiście oznacza |
|---|---|---|
| K2SAT, sprzętowy nadajnik testowy QPSK | Stary tryb: 0; nowy: 88/88 ramek referencyjnych; 179 872 unikalne bajty treści pakietów | Nowy dobór częstotliwości naprawił ten konkretny odbiór. Dorównaliśmy referencji, nie odzyskaliśmy 88 ramek ponad nią. To nie odbiór z orbity. |
| Duży test syntetyczny | 9689→9862 poprawnych przypadków na 20 736; 173 zyski, 0 strat | Wzrost 46,73%→47,56% na celowo trudnej siatce zakłóceń. Nie jest to skuteczność stacji ani 173 nowe unikalne ramki. |
| Końcowy program | 392 testy biblioteki i 17 testów CLI zaliczone; 0 błędów; 6 osobnych testów/pomocników pominiętych z zachowanymi powodami | Gotowy, odtwarzalny checkpoint do dalszych kontrolowanych eksperymentów. |
| Brak regresji po naprawie wejścia | 288 wariantów analizy, 236 480 strumieni i 628 556 346 wartości PSK identycznych bitowo; 2 499 328 wartości WAV zgodnych ze wzorem | Naprawa OGG/WAV nie zmieniła wyników na sprawdzonym zbiorze regresyjnym. |

Nowy tryb K2SAT sprawdzono na wszystkich 271 nakładających się oknach tego samego
nagrania i 24 dodatkowych próbkach szumu. Oba tryby wykonały cały zadeklarowany
zakres: 590 prób bez odrzuceń zasobowych. Nie pojawiły się ramki spoza referencji
ani ramki w szumie. To 24 kontrole, nie oszacowanie częstości fałszywych alarmów
na całej stacji. Dekodowanie korekcyjne i składanie K2SAT w tym eksperymencie
pozostają zewnętrzne; nie nazywamy tego własnym kompletnym dekoderem K2SAT.

## Naprawy i koszt

Dodano opcjonalne próbowanie dodatkowych częstotliwości wybranych z widma sygnału,
bez używania wyniku CRC do ich wyboru. Domyślny tryb i jego strumienie pozostały.
Na K2SAT pełny nowy tryb kosztował około 2,96× więcej czasu łącznie z zewnętrzną
weryfikacją. Analiza zapisanych wyników wskazuje, że ostatnia dodatkowa ścieżka
nie wniosła tam ramek. Nie jest to jednak wykonany benchmark szybszego trybu
ani gwarancja bezstratnego pominięcia tej ścieżki na innych nagraniach.

Naprawiono też błąd, przez który zmiana pliku WAV mogła przejść niezauważona,
jeśli znaczniki czasu pliku się nie zmieniły. Odczytywane bloki są teraz związane
z zamrożonym hashem nagrania. Konwersja OGG używa prywatnej, zweryfikowanej kopii.
Nieudany candidate12 i błędne założenie pierwszego przeglądu o uprawnieniach
katalogu zachowano oraz jawnie skorygowano; candidate13 przeszedł nowe testy.
Kontrola integralności ma zmierzony narzut — nie przedstawiamy jej jako przyspieszenia.

## Czy można już robić ostateczne testy do publikacji?

**Nie dla szerokiej deklaracji skuteczności wszystkich nowych trybów PSK.**
Można prowadzić dalsze odtwarzalne testy oraz opisać dotychczasowe wyniki
inżynierskie, ale nie ma podstaw do deklarowania rewolucji lub ogólnej przewagi
nad SatNOGS/gr-satellites.

Pozostają trzy konkretne etapy:

1. Naprawić i niezależnie potwierdzić trudne przypadki przy 8 próbkach na symbol.
   Nadal jest 99 porażek przy wysokim SNR. Analiza przyczyn odzyskała do 83 z nich
   jako sumę wyników osobnych interwencji; gotowy połączony algorytm nie został
   wdrożony ani zakwalifikowany.
2. Zweryfikować QPSK/OQPSK na niezależnych nagraniach orbitalnego IQ. Symulacja,
   sprzętowy nadajnik laboratoryjny i FM-demodulowane OGG nie zastępują tych danych.
3. Zamrozić porównanie na nowym zbiorze wybranym przed poznaniem wyników: te same
   wejścia, okna, definicja poprawnej ramki, jawne koszty, zyski i straty oraz
   analiza statystyczna na poziomie obserwacji, nie pojedynczych hipotez demodulatora.

## Dowody i odtwarzanie

- [Końcowy checkpoint, hashe i testy](../work/psk-repair-20260912-v1/candidate13/BUILD.md).
- [Pełny raport napraw i wszystkich zachowanych wyników](psk-demodulator-repair-20260912.md).
- [Warunki dopuszczenia ostatecznego badania](psk-publication-readiness-20260912.md).
- [K2SAT: wyniki, audyt i ograniczenia](../work/psk-repair-20260912-v1/k2sat-hybrid-v1/RESULTS.md).
- [Krzywe czułości i pełny eksperyment syntetyczny](../work/psk-repair-20260912-v1/sensitivity-v1-candidate06-09/RESULTS.md).

Graphify pomógł zlokalizować wcześniejsze powiązania projektu, ale jego graf nie
obejmował nowej integracji Rust. Dlatego wnioski o niej oparto na bezpośrednim
przeglądzie kodu, zamrożonych artefaktach i niezależnych kontrolach liczbowych.
