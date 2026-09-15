# Pilotaż odzyskiwania ramek z publicznych OGG SatNOGS

Mała, celowo dobrana próba dodatnia. Nie jest testem ślepym ani dowodem szerokiej przewagi.

| Obserwacja | W archiwum | gr-satellites z OGG | Nasza metoda z OGG | Nasze dodatkowe wobec obu |
|---|---:|---:|---:|---:|
| 14366383 | 6 | 2 | 5 | 0 |
| 14115025 | 10 | 5 | 10 | 0 |

Każdy wynik natywny przeszedł ponowną bitową kontrolę oryginalnej FCS i struktury AX.25.
Porównanie metod używa identycznych plików PCM. Wynik archiwalny nie jest pełną prawdą o wszystkich nadanych ramkach.
Krótkie kontrole ciszy i szumu są tylko testem podstawowym, nie pomiarem niskiej częstości fałszywych alarmów.
Nie dopasowywano zdekodowanych bajtów do wzorca w procesie dekodowania. Wyniki archiwalne służą wyłącznie porównaniu.
Koszt obliczeń jest nierówny: metoda natywna przeszukuje bank hipotez; czas mierzono przy równoległym innym eksperymencie.
Pełne bajty, przyrosty, braki, SHA-256 oraz czasy są w towarzyszącym pliku JSON.
