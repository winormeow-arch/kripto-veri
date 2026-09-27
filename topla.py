# Binance TR'de listeli coinlerin piyasa verisi - API anahtari gerekmez
# Coin listesi Binance TR'den, fiyat/mum verisi Binance global'in USDT paritesinden alinir.
import json, os, shutil, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

TR_LISTE_URL = "https://www.binance.tr/open/v1/common/symbols"
ESKI_LISTE_URL = os.environ.get("ESKI_LISTE_URL", "")   # onceki basarili TR listesi (yedek)
HOSTLAR = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api-gcp.binance.com",
    "https://api1.binance.com",
]
PARALEL = 6
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) kripto-veri", "Accept": "application/json"}

calisan_host = None


def url_cek(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def cek(yol, deneme=3):
    global calisan_host
    son_hata = None
    for _ in range(deneme):
        sira = ([calisan_host] + [h for h in HOSTLAR if h != calisan_host]) if calisan_host else HOSTLAR
        for h in sira:
            try:
                sonuc = url_cek(h + yol)
                calisan_host = h
                return sonuc
            except Exception as e:
                son_hata = f"{h}{yol[:40]}: {e}"
                print("HATA", son_hata)
        time.sleep(2)
    raise RuntimeError("Binance'e ulasilamadi -> " + str(son_hata))


def tr_ayikla(d):
    """Binance TR sembol cevabindan {'BTC': ['TRY','USDT'], ...} cikarir"""
    veri = d.get("data", d) if isinstance(d, dict) else d
    liste = veri.get("list", []) if isinstance(veri, dict) else veri
    coinler = {}
    for s in liste or []:
        if not isinstance(s, dict):
            continue
        b, q = s.get("baseAsset"), s.get("quoteAsset")
        if not b and "_" in s.get("symbol", ""):
            b, q = s["symbol"].split("_", 1)
        if b:
            coinler.setdefault(b.upper(), set()).add((q or "").upper())
    if len(coinler) < 20:
        raise ValueError(f"liste cok kisa ({len(coinler)})")
    return {k: sorted(v) for k, v in coinler.items()}


def tr_coinleri():
    # 1) Canli: Binance TR (GitHub'in ABD sunucularini genelde engelliyor)
    try:
        c = tr_ayikla(url_cek(TR_LISTE_URL))
        print("Binance TR listesi canli alindi:", len(c), "coin")
        return c, "canli"
    except Exception as e:
        print("Binance TR canli liste alinamadi:", e)

    # 2) Depoya senin yukledigin Binance TR sembol dosyasi (ana dizindeki herhangi bir .json)
    for ad in sorted(os.listdir(".")):
        if not ad.lower().endswith(".json"):
            continue
        try:
            with open(ad, encoding="utf-8") as f:
                c = tr_ayikla(json.load(f))
            print(f"Binance TR listesi depodaki '{ad}' dosyasindan alindi:", len(c), "coin")
            return c, "dosya: " + ad
        except Exception as e:
            print(f"'{ad}' uygun degil:", e)

    # 3) Onceki basarili listeyi kullan
    if ESKI_LISTE_URL:
        try:
            eski = url_cek(ESKI_LISTE_URL)
            print("Onceki TR listesi kullaniliyor:", len(eski["coinler"]), "coin")
            return eski["coinler"], "onceki (" + eski.get("guncelleme", "?") + ")"
        except Exception as e:
            print("Onceki liste de yok:", e)
    raise SystemExit("Binance TR coin listesi yok. Telefondan sembol dosyasini indirip depoya yukle.")


def mum_sade(liste):
    # [acilis_zamani, acilis, yuksek, dusuk, kapanis, hacimUSDT]
    return [[k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]), round(float(k[7]))] for k in liste]


def main():
    simdi = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    shutil.rmtree("data", ignore_errors=True)
    os.makedirs("data/mumlar")

    tr, tr_kaynak = tr_coinleri()
    with open("data/tr_coinler.json", "w") as f:
        json.dump({"guncelleme": simdi, "kaynak": tr_kaynak, "coinler": tr}, f, indent=1)

    # Global'de islemde olan USDT pariteleri
    bilgi = cek("/api/v3/exchangeInfo?symbolStatus=TRADING")
    global_usdt = {s["baseAsset"]: s["symbol"] for s in bilgi["symbols"] if s["quoteAsset"] == "USDT"}

    semboller = sorted(global_usdt[b] for b in tr if b in global_usdt)
    bulunamayan = sorted(b for b in tr if b not in global_usdt and b not in ("USDT", "TRY"))
    kume = set(semboller)

    # USD/TL kuru (TL fiyatini hesaplamak icin)
    try:
        usdttry = float(cek("/api/v3/ticker/price?symbol=USDTTRY")["price"])
    except Exception:
        usdttry = None

    coinler = []
    for t in cek("/api/v3/ticker/24hr"):
        if t["symbol"] not in kume:
            continue
        fiyat = float(t["lastPrice"])
        coinler.append({
            "sembol": t["symbol"],
            "coin": t["symbol"][:-4],
            "tr_pariteler": tr.get(t["symbol"][:-4], []),
            "fiyat": fiyat,
            "fiyatTL": round(fiyat * usdttry, 8) if usdttry else None,
            "degisim24s": round(float(t["priceChangePercent"]), 2),
            "yuksek24s": float(t["highPrice"]),
            "dusuk24s": float(t["lowPrice"]),
            "hacimUSDT": round(float(t["quoteVolume"])),
            "islem": int(t["count"]),
        })
    coinler.sort(key=lambda c: c["hacimUSDT"], reverse=True)

    hatali = []

    def mum_yaz(s):
        try:
            m15 = cek(f"/api/v3/klines?symbol={s}&interval=15m&limit=96")   # son 24 saat
            s1 = cek(f"/api/v3/klines?symbol={s}&interval=1h&limit=168")    # son 7 gun
            with open(f"data/mumlar/{s}.json", "w") as f:
                json.dump({"guncelleme": simdi, "sembol": s,
                           "alan": ["zaman", "acilis", "yuksek", "dusuk", "kapanis", "hacimUSDT"],
                           "m15": mum_sade(m15), "s1": mum_sade(s1)}, f, separators=(",", ":"))
        except Exception as e:
            hatali.append(s)
            print("MUM HATA", s, e)

    with ThreadPoolExecutor(PARALEL) as ex:
        list(ex.map(mum_yaz, semboller))

    with open("data/ozet.json", "w") as f:
        json.dump({"guncelleme": simdi, "kaynak": calisan_host, "tr_liste": tr_kaynak,
                   "usdttry": usdttry, "coin_sayisi": len(coinler),
                   "tr_de_olup_globalde_usdt_paritesi_olmayan": bulunamayan,
                   "mumu_eksik": hatali, "coinler": coinler}, f, ensure_ascii=False, indent=1)
    print(f"Tamam: TR'de {len(tr)} coin, {len(coinler)} tanesinin verisi cekildi, kaynak: {calisan_host}")
    if bulunamayan:
        print("Global USDT paritesi olmayanlar:", ", ".join(bulunamayan))
    if len(hatali) > len(semboller) / 2:
        raise SystemExit("Mumlarin yarisindan fazlasi cekilemedi")


if __name__ == "__main__":
    main()
