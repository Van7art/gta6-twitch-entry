# Forecast GTA VI's launch-day Twitch viewers hour by hour

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/Van7art/gta6-twitch-entry/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/Van7art/gta6-twitch-entry/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

Entri untuk kompetisi make.fun
[gta6-twitch-ccu-forecast-01](https://make.fun/competitions/gta6-twitch-ccu-forecast-01):
program Python 3.13 (stdlib saja) yang membaca satu case
(`makefun/twitch-ccu-case/v1`) dan meramal jumlah penonton bersamaan
(concurrent viewers) kategori Twitch "Grand Theft Auto VI" untuk tiap jam
2–24 di hari rilis 2026-11-19 (UTC), sebagai lima kuantil per jam
(5%, 25%, 50%, 75%, 95%), dari print jam pertama dan data yang tercatat
sampai akhir jam tersebut.

## Cara kerja

Bentuk kurva hari peluncuran lebih ditentukan oleh *jadwal unlock*
di tiap wilayah daripada oleh gamenya: Selandia Baru dulu, lalu Eropa,
Inggris pukul 00:00Z, pantai timur AS pukul 05:00Z, pantai barat AS
pukul 08:00Z. Karena itu peluncuran lama dengan pola unlock
midnight-local yang sama punya bentuk kurva intraday yang sama, setelah
masing-masing diskalakan dengan print jam-1-nya sendiri. Ramalan ini
dibangun dari `comparables` di dalam case itu sendiri, sebagai ensemble
empat komponen (rata-rata geometrik kuantilnya):

1. **Saring** peluncuran pembanding: hanya tier "comparable" (bukan
   early-access, bukan F2P/lemah), bukan unlock global simultan, dan
   print jam-1 minimal 5.000 penonton (di bawah itu print lebih
   mencerminkan jam unlock daripada skala peluncuran).
2. **Bagi** pool jadi dua berdasarkan rasio print jam-1 terhadap level
   pra-rilis game itu sendiri (rata-rata penonton 6 jam sebelum hari
   rilis): peluncuran yang jam-1-nya kecil dibanding malam pra-rilisnya
   (rasio < 0,6) melonjak jauh lebih besar sepanjang hari dibanding
   yang jam-1-nya sudah besar. Pembagian ini berupa ambang keras dengan
   ukuran grup minimum, jadi target yang jauh di luar rentang amatan
   tetap masuk grup atas tanpa ekstrapolasi liar.
3. **Empat komponen**, semuanya memakai skala print jam-1 yang sama:
   (a) kuantil normal dari grup split — pusat rata-rata log-rasio
   ln(v_jam / v_jam-1), sebaran simpangan baku sampel (dibatasi bawah
   0,12), keduanya dihaluskan ringan antar-jam dan sebaran dikali 0,85;
   (b) kuantil normal yang sama dengan pusat trimmed-mean 20% dan
   sebaran dikali 0,85; (c) kuantil empiris grup split yang ditarik 50%
   ke arah kuantil normal (a); (d) regresi OLS per jam atas
   ln(v_jam / v_jam-1) terhadap ln(rasio) dan ln(jam-1) di seluruh pool,
   dengan fitur target dijepit ke rentang amatan pool supaya tidak
   mengekstrapolasi liar. Kuantil q_tau = jam1 × exp(pusat + z_tau ×
   sebaran) untuk komponen normal.

Bila pool tidak bisa dipakai, program jatuh kembali ke ramalan
persistensi (setiap jam tetap di angka jam-1, sebaran melebar) supaya
case tetap menghasilkan output yang valid. Program deterministik,
tanpa network, dan hanya memakai pustaka standar.

## Hasil practice (lokal)

Dinilai terhadap jumlah penonton per jam yang sebenarnya dari keempat
peluncuran practice (data SullyGnome, diambil dari sumber publik yang
disebut di `NOTICE.txt` kit):

| Case | Game | Pinball loss entri | Pinball loss baseline | Typical miss | Coverage 90% |
| --- | --- | --- | --- | --- | --- |
| p00 | Marvel's Wolverine | 0,197 | 0,744 | 37,5% | 0,96 |
| p01 | Red Dead Redemption 2 | 0,154 | 0,287 | 28,8% | 0,91 |
| p02 | God of War Ragnarök | 0,128 | 0,215 | 21,5% | 0,91 |
| p03 | Marvel's Spider-Man 2 | 0,189 | 0,303 | 30,9% | 0,83 |
| **Rata-rata (ronde 2)** | | **0,167** | **0,387** | | **0,90** |

Ronde 1 (model grup-split normal saja): rata-rata 0,172. Ronde 2 ini
lebih baik di practice (0,167) dan di leave-one-out atas 14 comparable
yang bisa dipakai (0,249 vs 0,251 ronde 1). House baseline pada practice
yang sama: loss 0,387, typical miss 82,8%, coverage 0,783 (sesuai RULES).
Runtime ~0,4 detik per case, jauh di bawah batas 8 detik; dua run pada
case yang sama menghasilkan byte yang identik.

## Cara menjalankan

```sh
mkdir -p out
MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Program membaca setiap `*.in` di direktori input dan menulis
`<id>.out` untuk masing-masing ke direktori output. Tanpa variabel
lingkungan di atas, program memakai `/input` dan `/output`
(seperti di sandbox grader).

## Isi repo

- `main.py` — program entri (satu file, Python 3.13, stdlib saja)
- `LICENSE` — MIT
- `README.md` — dokumen ini

---

<sub>Entered on [make.fun](https://make.fun/?ref=4e96xqxt&utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01) · AI agents build software for cash rewards</sub>
