# Pierwszy rzeczywisty plugin fizyczny RML24

## Co działa

Plugin obsługuje teraz `BPSK`, `QPSK`, `OQPSK`, `GMSK` oraz zwykłe binarne `2FSK/FSK`. Nie udaje obsługi wszystkich klas: `SOQPSK-TG`, `FQPSK`, `ARTM`, `8PSK`, QAM/APSK, FM/PM i wszystkie warianty złożone `PCM-*` są raportowane jako `unsupported`, a nie jako błędnie zdemodulowane.

BPSK odzyskuje CFO z nachylenia fazy sygnału podniesionego do kwadratu, fazę nośnej modulo π i fazę zegara w ograniczonej siatce. QPSK/OQPSK używa ograniczonego pamięciowo, nadpróbkowanego periodogramu czwartej potęgi do CFO i fazy modulo π/2. QPSK szuka wspólnej fazy zegara I/Q. OQPSK zachowuje dwa wyznaczone bez prawdy warianty przesunięcia gałęzi o pół symbolu, bo nierozstrzygnięta rotacja π/2 zamienia I z Q. GMSK/2FSK używa dyskryminatora fazowego, ślepego dwuklastrowego oszacowania tonów i CFO, krótkiego integrate-and-dump oraz ograniczonego przeszukania fazy zegara.

## Polityka prawdy referencyjnej

Etykieta modulacji i nominalna szybkość symbolowa służą wyłącznie do wybrania profilu testowego. To nie jest wynik AMR.

Prawdziwe bity nie są dostępne dla odzysku nośnej, CFO, zegara ani slicera. Są używane dopiero po wygenerowaniu niezmiennego ciągu kandydackiego, do:

- wyboru jednej globalnej polaryzacji dla BPSK/FSK;
- dla QPSK/OQPSK: jednej z czterech rotacji kwadratowej konstelacji, opcjonalnego odbicia IQ i — w OQPSK — jednego z dwóch wariantów gałęzi opóźnionej;
- przesunięcia całkowitego maksymalnie o ±8 bitów, a dla QPSK/OQPSK wyłącznie całych symboli;
- policzenia BER.

Brakujące bity są liczone jako błędy. Test izolacyjny potwierdza, że podmiana prawdy bitowej przy identycznym IQ nie zmienia wyniku `demodulate_unaligned`.

## Self-test syntetyczny

To tylko kontrola implementacji, nie wynik badawczy:

Przypadki QPSK i OQPSK zawierają odpowiednio niecałkowite przesunięcia zegara 3,35 i 2,25 próbki; OQPSK dodatkowo ma fizyczne przesunięcie Q o pół symbolu.

| Modulacja | CFO | SNR | Wynik |
|---|---:|---:|---:|
| BPSK, 100 kBd | +2,3 kHz | 9 dB | 0/205 błędów |
| 2FSK, 250 kBd | +1,7 kHz | 8 dB | 12/512, BER 2,34% |
| Gaussian CPFSK/GMSK, 250 kBd | −1,3 kHz | 9 dB | 23/512, BER 4,49% |
| QPSK, 100 kBd | +1,9 kHz | 10 dB | 2/408, BER 0,49% |
| OQPSK, 100 kBd | −2,1 kHz | 10 dB | 2/408, BER 0,49% |

Testy całego toru RML24: `32 passed`.

## Uruchomienie po kompletnym pobraniu

```bash
telemetry-yield benchmark-rml24-physical \
  work/nature-dataset/shards/manifest.json \
  --output reports/rml24-physical-ber-v1.json
```

CLI odmawia pracy, jeśli manifest konwersji nie ma `complete=true`. W raporcie zapisze osobno liczbę wspieranych rekordów, klasy niewspierane, politykę alignmentu i BER. Nie uruchomiłem go na niepełnym archiwum.

To nadal nie jest synchronizator produkcyjny: syntetyczne testy używają kwadratowej konstelacji i prostego kształtu impulsu. Rzeczywisty BER RML24 może ujawnić konieczność filtru RRC, pętli śledzącej CFO/fazę i dokładniejszego odzysku zegara. Rozstrzygnięcie symetrii konstelacji oraz wariantu stagger OQPSK z użyciem prawdy jest dozwolone wyłącznie jako jawny etap ewaluacyjny, nie jako część odbiornika.
