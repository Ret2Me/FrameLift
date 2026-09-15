# RML24 blind-origin v3.2.4 — niezależny audyt formalny

## Werdykt

**FAIL_CLOSED.** Snapshot, sidecary, import traces, inwentarze native i reprodukcja wyniku przechodzą swoje kontrole, ale implementacja nie zamyka okna `mutate → use → restore`. Dwie kontrole hashujące stan przed i po obliczeniach nie dowodzą, że obliczenia użyły tych samych bajtów.

Rozstrzygający finding to **F-001 (critical)**. Pliki snapshotu mają tryb `0444`, katalogi `0555`, lecz należą do użytkownika uruchamiającego ocenę. Ten użytkownik może wykonać `chmod`, chwilowo zmienić plik użyty przez import/`numpy.load(..., mmap_mode="r")`, przywrócić zablokowane bajty i przejść oba postflighty. Nie ma read-only mount, fs-verity, flagi immutable, pinowania deskryptorów/inode'ów ani wiązania hasha z obiektem faktycznie użytym w całym okresie oceny.

Niezależny bounded probe użył wyłącznie pliku tymczasowego: poprawny preflight przeszedł, proces odczytał inne chwilowe bajty, przywrócono treść zablokowaną, po czym niezmodyfikowany `postflight()` z `run_blind_origin_v324.py` zwrócił `all_gates_pass=true`. To jest konstruktywny kontrprzykład dla formalnej gwarancji postflight.

## Chronologia

Wewnętrzna kolejność timestampów jest poprawna:

- prereg: `2026-09-02T22:33:00Z`;
- freeze start: `2026-09-02T23:34:04.939870Z`;
- lock created: `2026-09-02T23:34:55.996887Z`;
- freeze child finish: `2026-09-02T23:34:56.247149Z`;
- evaluate start: `2026-09-02T23:38:51.314798Z`;
- evaluation generated: `2026-09-03T00:11:49.208833Z`;
- evaluate child finish: `2026-09-03T00:11:49.397247Z`.

Prereg ctime (`2026-09-02T22:07:47.870237099Z`) poprzedza finalne mtimes runnera i launchera, więc plik istniał przed ich finalną postacią. Embedded `created_at_utc` jest jednak o `1512.129763 s` późniejszy od ctime; timestampy nie są podpisane ani zewnętrznie zakotwiczone. Audyt uznaje kolejność za wewnętrznie spójną, nie za trusted timestamp proof.

## Bramki, które przeszły

- Wszystkie sześć wymaganych sidecarów ma dokładny format `<sha256><dwie spacje><basename>\n` i zgadza się z artefaktem.
- Freeze lock `e27782c764d392b6291448931e944fa5af7ff3f6f0cb44e88bd17d7d5b8963e4` ma 21 plików kodu, 52 support i 120 shardów danych (`1,191,975,360` B). Wszystkie canonical inventory SHA zostały przeliczone dokładnie.
- Dziedziczenie v3.2.3 jest dokładne; bazowy lock ma SHA `aaf4eda410864edf8a3a354d982b3490ccac30331776bf64b8d4319532814ee8`.
- 120 plików danych to dokładnie 60 par IQ/truth z manifestu; brak błędów hash/size.
- Snapshot `/home/ubuntu/telemetry-yield/work/rml24/rml24-v324-full-e27782c764d3-pz5sjnuj` zawiera dokładny zestaw 193 locked code/support/data files plus lock i sidecar. Zweryfikowano `1,195,946,200` B, wszystkie hashe, tryby, 193 różne pary `(device,inode)` względem origin i 193 unikalne inode'y wewnątrz snapshotu. Inventory SHA to `870a8455810980f5c587c8186cc3463f5edc950f606c3e05e29c2768f2777c6f`.
- Freeze i evaluate trace mają dokładnie tę samą oczekiwaną transitive local source closure: 15 plików, bez braków i bez nadmiarów. `run_blind_origin_v321.py` występuje w obu. W pełnych trace'ach nie ma ani jednego `.pyc`/`__pycache__`.
- Obie komendy potomne użyły dokładnego prefiksu `/usr/bin/python3 -B -v`. Cache prefixes były różne, świeże i puste przed/po; pozostają puste.
- Native regular-executable-file inventories są równe lockowi: launcher 13 pozycji pre/post w freeze i evaluate; child 134 pozycje pre/post w evaluate. `mmap.cpython-312-x86_64-linux-gnu.so` jest obecny dokładnie raz z SHA `96a7d0c5c484d04f1e536ed0e0a25d39d242830d4893ff907b16a5403e2cbec9`. Granica: nie zachowano surowych `/proc/self/maps`, mapowań anonymous/special ani device/inode mapowanego obiektu.
- Finalny JSON jest bezpośrednim wynikiem `write_json(output, output_sha, result)` w childzie; launcher go nie przepisuje. SHA, rozmiar i tryb w attestation są dokładne: `abd0134cb58eb939de6eb7ebad39eee922e70e46a9fc103ca9b1660bf35eb06e`, `288438` B, `0444`.
- Dynamiczny test `O_EXCL` odrzucił istniejący target i zachował pierwotne bajty. `lsattr` potwierdza brak flagi immutable, zgodnie z ograniczonym claimem autora.
- Reprodukcja historical v3 przechodzi **7/7**. Canonical metric core SHA po obu stronach to `262e1830c0a532312edac77a9e2367dc4e41c6db88cbd7c83d2524610be61f61`.

Dokładne BER-y:

- transfer blind-origin v3: `0.3963538675090944`;
- holdout zero shift: `0.47758561889718554`;
- holdout acquisition v2: `0.47773818925904654`;
- holdout blind-origin v3: `0.3841437511966303`;
- holdout truth-aided shift 8: `0.45697515795519816`;
- holdout truth-aided shift 1024: `0.16680278336205245`.

Edge policies są identyczne z historycznym v3: brakujące truth bits liczą się jako błędy, nadmiar candidate bits nie wchodzi do BER, ale jest raportowany. Obecne są kontrole `wrong_record:*` i `random_candidate:*`; wszystkie zapisane success checks są true.

## Dodatkowy stan po ewaluacji

Origin nie odpowiada już lockowi w dwóch plikach support:

| plik | locked SHA/bytes | current SHA/bytes | current mtime UTC |
|---|---|---|---|
| `pyproject.toml` | `a65796a33fa2150aa5ecabf69004b55a77a9cbe4bacd8e37738b049be93b9958` / 972 | `652899b8b80b37789fec1b6789487135dc074b2327ca6cd2042ffe631d87f32e` / 1186 | `2026-09-03T00:13:46.245049138Z` |
| `uv.lock` | `ed29c2ce332e473c88dcd90cb945447bcfa8d381092563bf000a6e7ea8b21e60` / 171611 | `a9786a04e26b42ec10152c36ec98bb43b514c4eac6365bfba16c8f732fddb9e7` / 171775 | `2026-09-03T00:13:09.416752762Z` |

Obie zmiany nastąpiły po mtime evaluate attestation (`00:11:56.624200127Z`), więc nie są dowodem na błąd historycznego postflightu. Snapshot nadal ma dokładne locked copies. Aktualny origin nie przejdzie jednak `verify_lock()`.

## Granice claimów

Wynik potwierdza wyłącznie matematyczną zgodność wcześniej ujawnionego metric core. Nie jest to nowy holdout, fully blind BER, packet yield ani wynik porównywalny z SatNOGS. `independent_pass` pozostaje **false** ze względu na F-001.

Pełnego, około 33-minutowego DSP replay nie uruchomiono: nie może naprawić strukturalnej luki TOCTOU, a aktualny origin ma dwa późniejsze drifty support. Zamiast tego wykonano bounded niezależne przeliczenie wszystkich hashy/inode'ów/mode'ów, closure trace, native equality, historical metric-core 7/7 oraz dwa konstruktywne testy O_EXCL i TOCTOU.

## SHA-256 kluczowych artefaktów

```text
5b155afacd8cf4baf6b63c5502c2a56efacc26850c6d440abd5a15ffa5d95e7a  reports/rml24-blind-origin-v3.2.4-prereg.json
1c748e42f19fcabb61539000d2a745a5e215b72e57862cff72b587b8ee8eaad2  reports/rml24-blind-origin-v3.2.4-config.json
e27782c764d392b6291448931e944fa5af7ff3f6f0cb44e88bd17d7d5b8963e4  reports/rml24-blind-origin-v3.2.4-freeze-lock.json
59f4b502203af282f8e379ce5305d8fc1e5de73bc34ee0725898a179d102e596  reports/rml24-blind-origin-v3.2.4-freeze-import-trace.txt
e1ff96b529138f19b89d1a0066fc01d5d70e7add94b755b736131a73152042bc  reports/rml24-blind-origin-v3.2.4-freeze-launch-attestation.json
313108b5f6c0a5dd6e3e8fbe24f2660f054afea962d301cf65fd43f189b1ea3d  reports/rml24-blind-origin-v3.2.4-evaluate-import-trace.txt
dc8039f42db3cc6eea71541f2e86e86dbf1002fcf863c4851e687208b621e888  reports/rml24-blind-origin-v3.2.4-evaluate-launch-attestation.json
abd0134cb58eb939de6eb7ebad39eee922e70e46a9fc103ca9b1660bf35eb06e  reports/rml24-blind-origin-v3.2.4-evaluation.json
2bd02b0ea00bf627a9629c1b6eeb787e39962099e2e03d46f3a6799d97b47991  work/rml24/launch_blind_origin_v324.py
c55edcdf2d579a5967969d220dac5a83ba8057669946102b22b9e95b262789cb  work/rml24/run_blind_origin_v324.py
b75a1e691f0eff6e036eb917f3f0779c12b7d4e9e7f86731d9bd5f76a679e233  work/rml24/audit_blind_origin_v324_independent.py
fcd51f18c4eab4d0850411182e5b51e734bc3a0e536e39aa9a97d513c778a640  reports/rml24-blind-origin-v3-evaluation.json
21ac28f7dac5ab614c106607eecdd191fe4f1764e9daa5d99d92f16e4e6f2abf  reports/rml24-blind-origin-v3.2-prereg.json
e47997f0f2b3780f9ad51ba4d5e80610599aabf8ec6bb153cd570cd3456da7a0  reports/rml24-blind-origin-v3.2-config.json
aaf4eda410864edf8a3a354d982b3490ccac30331776bf64b8d4319532814ee8  reports/rml24-blind-origin-v3.2.3-freeze-lock.json
dba5fd0b840c9835967963179ba2065baefad9a32985ac33a2f6b2b63e05a2ef  work/nature-dataset/shards/manifest.json
```

## Komendy rozstrzygające

```bash
rtk sha256sum -c rml24-blind-origin-v3.2.4-freeze-lock.json.sha256 rml24-blind-origin-v3.2.4-freeze-import-trace.txt.sha256 rml24-blind-origin-v3.2.4-freeze-launch-attestation.json.sha256 rml24-blind-origin-v3.2.4-evaluate-import-trace.txt.sha256 rml24-blind-origin-v3.2.4-evaluate-launch-attestation.json.sha256 rml24-blind-origin-v3.2.4-evaluation.json.sha256
rtk python3 -B work/rml24/audit_blind_origin_v324_independent.py | rtk jq '{verdict,decisive_finding,toctou_probe,reproduction,native,snapshot}'
rtk sha256sum pyproject.toml uv.lock work/rml24/rml24-v324-full-e27782c764d3-pz5sjnuj/pyproject.toml work/rml24/rml24-v324-full-e27782c764d3-pz5sjnuj/uv.lock
rtk stat -c '%n|%s|%a|%i|%y|%z' reports/rml24-blind-origin-v3.2.4-prereg.json reports/rml24-blind-origin-v3.2.4-freeze-lock.json reports/rml24-blind-origin-v3.2.4-evaluation.json reports/rml24-blind-origin-v3.2.4-evaluate-launch-attestation.json
rtk lsattr reports/rml24-blind-origin-v3.2.4-freeze-lock.json reports/rml24-blind-origin-v3.2.4-freeze-import-trace.txt reports/rml24-blind-origin-v3.2.4-freeze-launch-attestation.json reports/rml24-blind-origin-v3.2.4-evaluate-import-trace.txt reports/rml24-blind-origin-v3.2.4-evaluate-launch-attestation.json reports/rml24-blind-origin-v3.2.4-evaluation.json work/rml24/rml24-v324-full-e27782c764d3-pz5sjnuj/work/rml24/run_blind_origin_v324.py
rtk rg -n '(\.pyc|__pycache__)' reports/rml24-blind-origin-v3.2.4-freeze-import-trace.txt reports/rml24-blind-origin-v3.2.4-evaluate-import-trace.txt
rtk find work/rml24/rml24-v324-pycache-80oeul02 work/rml24/rml24-v324-pycache-lib33kgh -mindepth 1 -print
```
