# Binance piyasa verisi toplayici - TUM USDT pariteleri - API anahtari gerekmez
import json, os, shutil, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

HOSTLAR = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api-gcp.binance.com",
    "https://api1.binance.com",
]
KOTE = "USDT"      # tum ...USDT spot pariteleri
PARALEL = 6        # ayni anda kac istek

calisan_host = None


def cek(yol, deneme=3):
    global calisan_host
    son_hata = None
    for _ in range(deneme):
        sira = ([calisan_host] + [h for h in HOSTLAR if h != calisan_host]) if calisan_host else HOSTLAR
        for h in sira:
            try:
                req = urllib.request.Request(h + yol, headers={"User-Agent": "kripto-veri"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    calisan_host = h
                    return json.loads(r.read())
            except Exception as e:
                son_hata = f"{h}{yol[:40]}: {e}"
                print("HATA", son_hata)
        time.sleep(2)
    raise RuntimeError("Binance'e ulasilamadi -> " + str(son_hata))


def mum_sade(liste):
    # [acilis_zamani, acilis, yuksek, dusuk, kapanis, hacimUSDT]
    return [[k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]), round(float(k[7]))] for k in liste]


def main():
    simdi = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    shutil.rmtree("data", ignore_errors=True)
    os.makedirs("data/mumlar")

    # 1) Islemde olan tum USDT spot pariteleri
    bilgi = cek("/api/v3/exchangeInfo?symbolStatus=TRADING")
    semboller = sorted(s["symbol"] for s in bilgi["symbols"]
                       if s["quoteAsset"] == KOTE and s.get("isSpotTradingAllowed", True))
    kume = set(semboller)

    # 2) 24 saatlik ozet
    coinler = []
    for t in cek("/api/v3/ticker/24hr"):
        if t["symbol"] not in kume:
            continue
        coinler.append({
            "sembol": t["symbol"],
            "fiyat": float(t["lastPrice"]),
            "degisim24s": round(float(t["priceChangePercent"]), 2),
            "yuksek24s": float(t["highPrice"]),
            "dusuk24s": float(t["lowPrice"]),
            "hacimUSDT": round(float(t["quoteVolume"])),
            "islem": int(t["count"]),
        })
    coinler.sort(key=lambda c: c["hacimUSDT"], reverse=True)

    # 3) Her coin icin mumlar
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
        json.dump({"guncelleme": simdi, "kaynak": calisan_host, "coin_sayisi": len(coinler),
                   "mumu_eksik": hatali, "coinler": coinler}, f, ensure_ascii=False, indent=1)
    print(f"Tamam: {len(coinler)} coin, {len(semboller) - len(hatali)} mum dosyasi, kaynak: {calisan_host}")
    if len(hatali) > len(semboller) / 2:
        raise SystemExit("Mumlarin yarisindan fazlasi cekilemedi")


if __name__ == "__main__":
    main()
