# Zakończona seria rozwojowa — 7 września 2026

Wykonano wszystkie 84 dopasowania walidacyjne i 6 końcowych dopasowań
modeli badawczych. Kontrola odtworzyła przynależność przykładów dla 84
podziałów i sprawdziła 54 297 zapisów przewidywań. To liczba zapisów
powtarzanych między modelami, **nie** liczba niezależnych obserwacji.

Nie oceniano sierpnia ani zarezerwowanego września. To walidacja rozwojowa
na marcu i czerwcu, nie niezależny test końcowy. Żaden model nie został wdrożony.

## Wynik porównania

Podstawą wyboru był średni błąd prognoz prawdopodobieństwa w marcu i czerwcu
(Brier; mniej znaczy lepiej). Identyczne panele dla wszystkich wariantów.

| Zadanie i sytuacja | Bazowy błąd | Błąd wybranego wariantu | Wniosek |
|---|---:|---:|---|
| Sygnał, znane grupy | 0,1469 | 0,1469 | Pozostał model bazowy |
| Sygnał, nowe satelity | 0,3366 | 0,3343 | Praktycznie brak poprawy; nadal zły wynik |
| Sygnał, nowe stacje | 0,2285 | 0,2187 | Umiarkowana poprawa błędu prawdopodobieństw |
| Plik demodulacji, znane grupy | 0,1613 | 0,1603 | Minimalna poprawa |
| Plik demodulacji, nowe satelity | 0,3844 | 0,3527 | Poprawa, ale poziom nadal nieakceptowalny |
| Plik demodulacji, nowe stacje | 0,2623 | 0,2623 | Pozostał model bazowy; nadal słabo |

Dla orientacji stała prognoza 50% w każdym przypadku ma błąd 0,25. Trzy
wybrane modele grupowe są gorsze nawet od tego punktu odniesienia. Ten
kontrolny wniosek nie zmienia po fakcie zamrożonej reguły wyboru ani nie
stanowi testu istotności. Wyraźnie wyklucza jednak ogłaszanie tych modeli
jako gotowych do wdrożenia.

Nie porównujemy powyższych liczb bezpośrednio z wcześniejszym testem
sierpniowym: zmieniły się okresy, podziały grupowe i założenie opóźnienia
dostępności historii. W badaniu nowych satelitów historia tego satelity
i jego identyfikator są wyłączone.

Dobór progu na marcu nie przyniósł stabilnej poprawy w czerwcu. Przykładowo
w wybranym modelu sygnału nowych satelitów próg 0,8 daje wysoką trafność
samych alarmów dodatnich, ale wykrywa tylko 6,4% rzeczywistych sygnałów.
Nie przedstawiamy takiego wyniku jako wysokiej skuteczności systemu.

## Co faktycznie zawierały nowe wejścia

Rozszerzona pula przed sierpniem, po ochronie grup testowych i uwzględnieniu
doby opóźnienia etykiet: 32 201 rekordów, 60 satelitów, 431 stacji.
15 832 rekordy mają etykietę sygnału; 6279 — etykietę warunkowego pliku
demodulacji. Poszczególne modele bazowe używają mniejszej, pierwotnej puli.

- Temperatura: dostępna w 25 538 rekordach.
- Zapisany w metadanych sterownik odbiornika: 31 318 rekordów.
- Zapisane wzmocnienie odbiornika: 27 244 rekordy.
- Udokumentowany historyczny typ anteny i zysk antenowy: **0 rekordów**.

Nie mylimy wzmocnienia odbiornika ze zyskiem anteny. Braku parametrów
anteny nie uzupełniono domysłami. Nowy wariant potrafi przyjąć te parametry,
ale obecna seria nie dowodzi ich przydatności, skoro brakuje wartości.

## Dalsze działanie

Kolektor całej sieci pracuje niezależnie od zakończonego treningu. Jego
pierwszych rekordów nie użyto w powyższej serii. Kolejne badania uruchomi
proces przyrostowy dopiero po zgromadzeniu nowych, unikalnych, dozwolonych
przykładów z w całości pobranych dni. Poziomy uruchomienia: 5000, 20 000,
100 000 rekordów. Brakujące etykiety nie staną się porażkami.

Źródła lokalne: `local/results.json`, `local/cohort.json`,
`local/integrity-audit.json`, `local/protocol.json`.
