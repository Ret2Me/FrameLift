# Plan analizy zamkniętego tygodnia OGG — wersja 1

Plan zapisany po rozwoju na 14366383 / 14115025 i małej walidacji na
14956101 / 14936397, przed wynikami nowej kohorty. Nie nazywamy tych
wcześniejszych obserwacji ślepym testem. Kod odbiornika po tej walidacji
nie jest dalej strojony na potrzeby kohorty.

## Dobór i zakres

- Zamknięty przedział UTC: 2026-08-31 00:00:00 włącznie do
  2026-09-07 00:00:00 wyłącznie, według początku obserwacji.
- CANVAS, NORAD 68635, nadajnik GCmN6RULea8dAT7Qoat8z2,
  deklarowane GMSK 9600. Pierwsza kohorta dotyczy jednego trybu jednej
  misji; nie reprezentuje wszystkich satelitów ani protokołów.
- Maksymalnie 100 obserwacji uporządkowanych malejąco po początku i ID.
  Wykluczamy dokładnie cztery wyżej wymienione, już analizowane ID.
- Dobór wyłącznie według metadanych, bez filtru statusu, liczby ramek,
  wyglądu waterfall lub wyniku odbiornika. Mniej niż 100 dostępnych
  obserwacji nie powoduje rozszerzenia dat ani wyboru łatwiejszej misji.
- Brak OGG, niedostępne referencje, nieobsługiwany format i błąd procesu
  mają osobne statusy. Nie są zerowym wynikiem dekodowania. Nie zastępujemy
  ich następnymi obserwacjami w celu podwyższenia skuteczności.
- Odpowiedzi API, wybór ID, pełne metadane, pliki referencyjne i oryginalne
  OGG zachowujemy z SHA-256. Publiczne archiwum jest migawką, nie pełną
  prawdą o wszystkich nadanych ramkach ani wszystkich archiwach na świecie.

## Niezmienny eksperyment

Wersja odbiornika: szybki AX.25, bank addytywnie zróżnicowany, 6-sekundowe
okna co 3 sekundy, 32 fazy i błędy zegara -500 / -100 / 0 / 100 / 500 ppm,
stare 16 hipotez plus do dwóch rozdzielonych fazowo reprezentantów na błąd
zegara. Nie osłabiamy FCS i nie uzupełniamy bajtów ze znanych ramek.

gr-satellites 5.9.0 z FSK 9600 / AX.25 G3RUH pracuje na identycznym WAV,
otrzymanym z OGG jako mono float32 bez zmiany częstotliwości próbkowania.
To odniesienie komponentowe, nie dokładne odtworzenie historycznej stacji.
Jest tańsze obliczeniowo od przeszukującej bank metody badanej; porównanie
nie oznacza równego budżetu CPU. Nie wysyłamy telemetrii do żadnej bazy.

Warunkiem startu jest zgodność hashy użytych źródeł, zaliczona mała
walidacja, testy regresji, niezależny audyt i kontrola techniczna runnera.
Kontrola na świeżych syntetycznych zakłóceniach musi zakończyć się bez
zaakceptowanych ramek i bez błędów. Jest to test podstawowy, a nie
potwierdzenie niskiego współczynnika fałszywych akceptacji w realnej stacji.

## Liczniki i wnioski

Jednostką jest cały PDU AX.25, łącznie z adresami, bez dwóch bajtów
FCS. Dla wyniku natywnego zachowujemy i niezależnie sprawdzamy odebrane
bajty FCS przed ich usunięciem. Archiwum i KISS dekodera porównawczego
nie przechowują tu FCS: ich niezależna kontrola dotyczy struktury PDU,
a dla baseline'u dodatkowo zachowanej konfiguracji sprawdzania FCS.
Raportujemy zarówno unikalność w obrębie
obserwacji, jak i dodatkowo po pełnych bajtach w całej kohorcie. Powtórki
dekodowania, hipotez i okien nie zwiększają wyniku.

Podstawowy wynik użytkowy to liczba i bajty ramek odbiornika nieobecnych
w migawce archiwalnej danej obserwacji. Osobno raportujemy:

1. Dodatkowe ramki względem istniejącego dekodera na tym samym audio.
2. Ramki dodatkowe względem archiwum, które znajduje także istniejący
   dekoder — korzyść ponownej analizy, nie wyłączna przewaga algorytmu.
3. Ramki nieobecne zarówno w archiwum, jak i wyniku tego dekodera.
4. Ramki archiwalne i porównawcze, których metoda nie odzyskała.
5. Obserwacje z co najmniej jedną dodatkową ramką, wszystkie dostępne
   mianowniki, czas obliczeń, błędy i kompletność pobrania referencji.

Wniosek o nieobecności ramki w archiwum wymaga kompletnego pobrania
referencji wymienionych w migawce. Brak referencji nie oznacza dowodu
braku transmisji: takie obserwacje są nieoznaczone, nie kontrolnie ujemne.
Ramka z poprawną FCS i strukturą AX.25 pozostaje kandydatem do analizy
pochodzenia: sygnał innego nadajnika nie jest nową telemetrią CANVAS.
Każdy wynik dodatkowy wymaga niezależnej kontroli oryginalnej FCS,
powtórnego odtworzenia z zachowanego audio i oceny adresów / pochodzenia.

Nie wybieramy podgrup ani kolejnej wersji na podstawie uzyskanych zysków.
Wynik zerowy lub regres pozostaje wynikiem. Dalsza zmiana algorytmu
tworzy nowy etap rozwojowy i wymaga nowego zbioru potwierdzającego.

## Eksploatacja i ograniczenia publikacyjne

Runner ma przetwarzać sekwencyjnie i wznawiać po przerwaniu bez mieszania
wersji oraz bez nadpisywania prób. OGG i wyniki pozostają na dysku.
Wyłącznie własny, odtwarzalny WAV może zostać usunięty po zapisaniu
porównania i sprawdzeniu hashy; zatrzymujemy jego tożsamość i polecenie
konwersji. Ochrona wolnego miejsca ma zatrzymać pracę zamiast niszczyć dane.

Kohorta retrospektywna jednej misji oraz syntetyczny test podstawowy
nie wystarczają do deklaracji wdrożenia produkcyjnego, szerokiej przewagi
ani gotowej publikacji. Mogą dostarczyć mierzalnego, odtwarzalnego wyniku
do dalszej pracy i uzasadnić rozszerzenie na inne misje oraz realne
kontrole zakłóceń. Na tym etapie test wielkoskalowy jeszcze nie wystartował.
