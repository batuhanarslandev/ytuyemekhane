from flask import Flask, render_template, request, redirect, url_for, flash, session
from functools import wraps
import sqlite3
import os
import re
from datetime import date, datetime, timedelta
import pandas as pd
from werkzeug.utils import secure_filename
app = Flask(__name__)
app.secret_key = "yemekhane_gizli_anahtar_2026"
app.config['TEMPLATES_AUTO_RELOAD'] = True

# ==========================================
# 🧠 AKILLI SİSTEM KONTROL VE BİLDİRİM MOTORU
# ==========================================
@app.context_processor
def inject_notifications():
    if 'kullanici_id' not in session: return dict(global_bildirimler=[])

    conn = get_db_connection()
    bildirimler = []
    bugun_str = date.today().strftime('%Y-%m-%d')
    mevcut_ay = str(date.today().month)
    mevcut_yil = date.today().year
    k_id = session.get('kullanici_id')
    k_adi = session.get('kullanici_adi')

    if session.get('rol') == 'admin':
        bekleyen_mal = conn.execute("SELECT urun_adi FROM mal_kabul_log WHERE onay_durumu='Bekliyor'").fetchall()
        for b in bekleyen_mal:
            bildirimler.append({'mesaj': f"📥 Mal Kabul Onayı: {b['urun_adi']}", 'url': '/mal-kabul', 'renk': 'text-orange-400 bg-orange-50 border border-orange-200'})

        bekleyen_cikis = conn.execute("SELECT malzeme_adi FROM depo_cikis WHERE onay_durumu='Bekliyor'").fetchall()
        for b in bekleyen_cikis:
            bildirimler.append({'mesaj': f"📤 Çıkış Onayı: {b['malzeme_adi']}", 'url': '/depo', 'renk': 'text-indigo-500 bg-indigo-50 border border-indigo-200'})

        etkinlikler = conn.execute("SELECT adi FROM etkinlikler WHERE tarih=? AND durum='Planlandı'", (bugun_str,)).fetchall()
        for e in etkinlikler:
            bildirimler.append({'mesaj': f"⚠️ ÜCRET GİRİLMEDİ: '{e['adi']}'", 'url': '/satis', 'renk': 'text-red-500 bg-red-50 border border-red-200'})

        dis_hizmetler = conn.execute("SELECT firma_adi, hizmet_turu FROM dis_hizmetler WHERE tarih=?", (bugun_str,)).fetchall()
        for d in dis_hizmetler:
            bildirimler.append({'mesaj': f"🎪 Dış Hizmet (Bugün): {d['firma_adi']}", 'url': '/dis-hizmetler', 'renk': 'text-teal-500 bg-teal-50 border border-teal-200'})

        bakimlar = conn.execute("SELECT id, ekipman_adi, bakim_aylari FROM periyodik_bakim").fetchall()
        for b in bakimlar:
            if b['bakim_aylari'] and mevcut_ay in b['bakim_aylari'].split(','):
                log = conn.execute("SELECT id FROM periyodik_bakim_log WHERE bakim_id=? AND yil=? AND ay=?", (b['id'], mevcut_yil, mevcut_ay)).fetchone()
                if not log:
                    bildirimler.append({'mesaj': f"🔧 Bakım Eksik: '{b['ekipman_adi']}'", 'url': f'/periyodik-bakim?yil={mevcut_yil}&ay={mevcut_ay}', 'renk': 'text-purple-500 bg-purple-50 border border-purple-200'})

        bugun_kisi = conn.execute("SELECT id FROM gunluk_istatistik WHERE tarih=? AND ogun='Öğle Yemeği'", (bugun_str,)).fetchone()
        if not bugun_kisi:
            bildirimler.append({'mesaj': "📊 Veri Girişi: Bugünün öğle yemeği sayıları henüz girilmedi!", 'url': '/uretim', 'renk': 'text-rose-600 bg-rose-50 border border-rose-200'})

    ajanda_notlari_raw = conn.execute("SELECT * FROM ajanda").fetchall()

    tamamlanlar_db = conn.execute("SELECT ajanda_id, tarih FROM ajanda_tamamlananlar WHERE tarih=?", (bugun_str,)).fetchall()
    biten_set = {str(t['ajanda_id']) for t in tamamlanlar_db}

    for row in ajanda_notlari_raw:
        n = dict(row)

        if session.get('rol') == 'depocu':
            if n['atanan_kisi'] != k_adi and n['olusturan'] != k_adi: continue
        else:
            if n['atanan_kisi'] != 'Tümü' and n['atanan_kisi'] != k_adi and n['olusturan'] != k_adi: continue

        start_date = datetime.strptime(n['tarih'], '%Y-%m-%d').date()
        periyot = n.get('periyot', 'Tek Seferlik')
        bitis_str = n.get('bitis_tarihi', '')

        if periyot == 'Tek Seferlik' or not bitis_str:
            final_date = get_next_workday(start_date.strftime('%Y-%m-%d'))
            if final_date == bugun_str:
                if str(n['id']) in biten_set: continue
                ref_id = f"{n['id']}_0"
                okundu = conn.execute("SELECT id FROM okunan_bildirimler WHERE kullanici_id=? AND bildirim_turu='ajanda' AND referans_id=? AND islem_tarihi=?", (k_id, ref_id, bugun_str)).fetchone()
                if not okundu:
                    bildirimler.append({'mesaj': f"🎯 Görev: {n['not_icerik']}", 'url': f'/bildirim-oku?tur=ajanda&ref={ref_id}&git=/ajanda', 'renk': 'text-emerald-500 bg-emerald-50 border border-emerald-200'})
            continue

        bitis_date = datetime.strptime(bitis_str, '%Y-%m-%d').date()
        cur_date = start_date
        i = 0

        while cur_date <= bitis_date and i < 500:
            final_date = get_next_workday(cur_date.strftime('%Y-%m-%d'))
            if final_date == bugun_str:
                if str(n['id']) in biten_set: break
                ref_id = f"{n['id']}_{i}"
                okundu = conn.execute("SELECT id FROM okunan_bildirimler WHERE kullanici_id=? AND bildirim_turu='ajanda' AND referans_id=? AND islem_tarihi=?", (k_id, ref_id, bugun_str)).fetchone()
                if not okundu:
                    bildirimler.append({'mesaj': f"🎯 Görev: {n['not_icerik']}", 'url': f'/bildirim-oku?tur=ajanda&ref={ref_id}&git=/ajanda', 'renk': 'text-emerald-500 bg-emerald-50 border border-emerald-200'})
                break
            if final_date > bugun_str: break
            if periyot == 'Haftalık': cur_date += timedelta(weeks=1)
            else:
                m_add = {'Aylık': 1, '2 Aylık': 2, '3 Aylık': 3, '6 Aylık': 6, 'Yıllık': 12}.get(periyot, 1)
                m = cur_date.month - 1 + m_add
                y = cur_date.year + m // 12
                m = m % 12 + 1
                try: cur_date = date(y, m, start_date.day)
                except ValueError: cur_date = date(y, m, 28)
            i += 1

    conn.close()
    return dict(global_bildirimler=bildirimler)


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'kullanici_id' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('rol') != 'admin':
            flash("⚠️ Bu sayfaya erişim yetkiniz yok!", "error")
            return redirect(url_for('depo'))
        return f(*args, **kwargs)
    return decorated_function

def get_next_workday(date_str):
    d = datetime.strptime(date_str, '%Y-%m-%d')
    while d.weekday() >= 5: d += timedelta(days=1)
    return d.strftime('%Y-%m-%d')

def get_db_connection():
    db_path = os.path.join(os.getcwd(), 'yemekhane.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS kullanicilar (id INTEGER PRIMARY KEY AUTOINCREMENT, kullanici_adi TEXT UNIQUE, sifre TEXT, rol TEXT, isim TEXT)''')
    kullanicilar = c.execute("SELECT * FROM kullanicilar").fetchall()
    if not kullanicilar:
        c.execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, isim) VALUES ('diyetisyen', '1234', 'admin', 'Diyetisyen (Yönetici)')")
        c.execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, isim) VALUES ('tekniker', '1234', 'admin', 'Gıda Teknikeri')")
        c.execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, isim) VALUES ('depocu', '1234', 'depocu', 'Depo Sorumlusu')")
        c.execute('''CREATE TABLE IF NOT EXISTS okunan_bildirimler (id INTEGER PRIMARY KEY AUTOINCREMENT, kullanici_id INTEGER, bildirim_turu TEXT, referans_id TEXT, islem_tarihi DATE)''')
        c.execute('''CREATE TABLE IF NOT EXISTS ajanda (id INTEGER PRIMARY KEY AUTOINCREMENT, tarih DATE, not_icerik TEXT, renk_kodu TEXT DEFAULT '#3b82f6', url TEXT DEFAULT "", atanan_kisi TEXT DEFAULT 'Tümü', periyot TEXT DEFAULT 'Tek Seferlik', olusturan TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS fire_kayitlari (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        kategori TEXT,
                        urun_adi TEXT,
                        miktar REAL,
                        birim TEXT,
                        tarih TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        kullanici TEXT,
                        aciklama TEXT
                    )''')
    try: c.execute('ALTER TABLE ajanda ADD COLUMN url TEXT DEFAULT ""'); c.execute("ALTER TABLE ajanda ADD COLUMN atanan_kisi TEXT DEFAULT 'Tümü'"); c.execute("ALTER TABLE ajanda ADD COLUMN periyot TEXT DEFAULT 'Tek Seferlik'"); c.execute("ALTER TABLE ajanda ADD COLUMN olusturan TEXT DEFAULT 'Sistem'")
    except: pass
    try: c.execute("ALTER TABLE ajanda ADD COLUMN bitis_tarihi DATE DEFAULT ''")
    except: pass
    try: c.execute("ALTER TABLE ajanda ADD COLUMN durum TEXT DEFAULT 'Bekliyor'")
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS ajanda_tamamlananlar (id INTEGER PRIMARY KEY AUTOINCREMENT, ajanda_id INTEGER, tarih DATE)''')
    c.execute('''CREATE TABLE IF NOT EXISTS depo (id INTEGER PRIMARY KEY AUTOINCREMENT, kategori TEXT, urun_adi TEXT UNIQUE, miktar REAL DEFAULT 0, birim TEXT)''')
    c.execute("INSERT OR IGNORE INTO depo (kategori, urun_adi, miktar, birim) VALUES ('İçecek', 'Ayran / Meyve Suyu', 0, 'ADET')")

    c.execute('''CREATE TABLE IF NOT EXISTS mal_kabul_log (id INTEGER PRIMARY KEY AUTOINCREMENT, kategori TEXT, urun_adi TEXT, marka TEXT, tedarikci TEXT, miktar REAL, birim TEXT, skt TEXT, onay_durumu TEXT, kabul_eden TEXT, notlar TEXT, tarih TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    try: c.execute("ALTER TABLE mal_kabul_log ADD COLUMN lot_damga_no TEXT DEFAULT ''")
    except: pass
    try: c.execute("ALTER TABLE mal_kabul_log ADD COLUMN takim_sayisi REAL DEFAULT 0")
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS stok_lotlari (id INTEGER PRIMARY KEY AUTOINCREMENT, urun_adi TEXT, marka TEXT, lot_damga_no TEXT, baslangic_miktar REAL, kalan_miktar REAL, birim TEXT, skt TEXT, takim_sayisi REAL DEFAULT 0, durum TEXT DEFAULT 'Aktif', tarih TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute('''CREATE TABLE IF NOT EXISTS uretim (id INTEGER PRIMARY KEY AUTOINCREMENT, tarih DATE, ogun TEXT, yemek_adi TEXT, planlanan_kisi INTEGER DEFAULT 0, gerceklesen_kisi INTEGER DEFAULT 0, dondurucudan_alinan INTEGER DEFAULT 0, durum TEXT DEFAULT 'Planlandı', kalan_porsiyon INTEGER DEFAULT 0, kategori TEXT DEFAULT 'Normal')''')
    try: c.execute('ALTER TABLE uretim ADD COLUMN dondurucudan_alinan INTEGER DEFAULT 0'); c.execute("ALTER TABLE uretim ADD COLUMN durum TEXT DEFAULT 'Planlandı'"); c.execute("ALTER TABLE uretim ADD COLUMN kalan_porsiyon INTEGER DEFAULT 0"); c.execute("ALTER TABLE uretim ADD COLUMN kategori TEXT DEFAULT 'Normal'")
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS receteler (id INTEGER PRIMARY KEY AUTOINCREMENT, yemek_adi TEXT, malzeme_adi TEXT, miktar REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS depo_cikis (id INTEGER PRIMARY KEY AUTOINCREMENT, tarih DATE, ogun TEXT DEFAULT 'Öğle Yemeği', yemek_adi TEXT, malzeme_adi TEXT, miktar REAL, birim TEXT, onay_durumu TEXT DEFAULT 'Onaylandı')''')
    try: c.execute('ALTER TABLE depo_cikis ADD COLUMN ogun TEXT DEFAULT "Öğle Yemeği"'); c.execute("ALTER TABLE depo_cikis ADD COLUMN onay_durumu TEXT DEFAULT 'Onaylandı'")
    except: pass
    try: c.execute('ALTER TABLE depo_cikis ADD COLUMN aciklama TEXT DEFAULT ""')
    except: pass
    try: c.execute("ALTER TABLE depo_cikis ADD COLUMN marka TEXT DEFAULT ''")
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS gunluk_istatistik (id INTEGER PRIMARY KEY AUTOINCREMENT, tarih DATE, ogun TEXT, personel_sayisi INTEGER DEFAULT 0, ogrenci_sayisi INTEGER DEFAULT 0, yemek_yetmedi INTEGER DEFAULT 0, alternatif_detay TEXT DEFAULT '')''')
    try: c.execute("ALTER TABLE gunluk_istatistik ADD COLUMN yemek_yetmedi INTEGER DEFAULT 0"); c.execute("ALTER TABLE gunluk_istatistik ADD COLUMN alternatif_detay TEXT DEFAULT ''")
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS yedek_stok (id INTEGER PRIMARY KEY AUTOINCREMENT, yemek_adi TEXT UNIQUE, porsiyon INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS dis_paydas_satis (id INTEGER PRIMARY KEY AUTOINCREMENT, tarih DATE, ogun TEXT, turu TEXT, adi TEXT, kisi_sayisi INTEGER, tarife TEXT, odeme_yontemi TEXT, toplam_tutar REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS etkinlikler (id INTEGER PRIMARY KEY AUTOINCREMENT, tarih DATE, adi TEXT, kisi_sayisi INTEGER, notlar TEXT, durum TEXT DEFAULT 'Planlandı')''')
    c.execute('''CREATE TABLE IF NOT EXISTS kumanya (id INTEGER PRIMARY KEY AUTOINCREMENT, tarih DATE, kulup_adi TEXT, kisi_sayisi INTEGER, kumanya_tipi TEXT, icerik_detay TEXT, durum TEXT DEFAULT 'Planlandı')''')
    c.execute('''CREATE TABLE IF NOT EXISTS kumanya_malzemeler (id INTEGER PRIMARY KEY AUTOINCREMENT, kumanya_id INTEGER, malzeme_adi TEXT, miktar REAL, birim TEXT)''')

    c.execute('''CREATE TABLE IF NOT EXISTS et_isleme_log (id INTEGER PRIMARY KEY AUTOINCREMENT, tarih TIMESTAMP DEFAULT CURRENT_TIMESTAMP, kaynak TEXT, harcanan REAL, detay TEXT)''')
    try: c.execute("ALTER TABLE et_isleme_log ADD COLUMN lot_id INTEGER")
    except: pass

# app.py içindeki init_db fonksiyonuna eklenecekler:
    try: c.execute("ALTER TABLE dis_paydas_satis ADD COLUMN kampus TEXT DEFAULT 'Davutpaşa Merkez'")
    except: pass
    try: c.execute("ALTER TABLE dis_paydas_satis ADD COLUMN tahsilat_durumu TEXT DEFAULT '✅ Ödendi (Nakit/Pos)'")
    except: pass
    try: c.execute("ALTER TABLE dis_paydas_satis ADD COLUMN firma_odeme_durumu TEXT DEFAULT '⏳ Hak Edişe Eklenecek (Bekliyor)'")
    except: pass
    try: c.execute("ALTER TABLE etkinlikler ADD COLUMN bitis_tarihi DATE")
    except: pass
    try: c.execute("ALTER TABLE etkinlikler ADD COLUMN kampus TEXT DEFAULT 'Davutpaşa Merkez'")
    except: pass
    try: c.execute("ALTER TABLE etkinlikler ADD COLUMN ogun TEXT DEFAULT 'Öğle Yemeği'")
    except: pass
    try: c.execute("ALTER TABLE personeller ADD COLUMN izin_hakki INTEGER DEFAULT 14")
    except: pass
    
    try: c.execute("ALTER TABLE receteler ADD COLUMN birim TEXT DEFAULT 'Gr'")
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS ihtarlar (id INTEGER PRIMARY KEY AUTOINCREMENT, firma_adi TEXT, konu TEXT, ihtar_tarihi DATE, son_tarih DATE, durum TEXT DEFAULT 'Bekliyor')''')
    c.execute('''CREATE TABLE IF NOT EXISTS tutanaklar (id INTEGER PRIMARY KEY AUTOINCREMENT, tarih DATE, yer TEXT, firma_adi TEXT, konu TEXT, detay TEXT, bagli_ihtar_id INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS dis_hizmetler (id INTEGER PRIMARY KEY AUTOINCREMENT, tarih DATE, firma_adi TEXT, hizmet_turu TEXT, hijyen_belgesi TEXT DEFAULT 'Bekliyor', tarim_belgesi TEXT DEFAULT 'Bekliyor', ytu_izni TEXT DEFAULT 'Bekliyor', notlar TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS periyodik_bakim (id INTEGER PRIMARY KEY AUTOINCREMENT, ekipman_adi TEXT, bakim_aylari TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS periyodik_bakim_log (id INTEGER PRIMARY KEY AUTOINCREMENT, bakim_id INTEGER, yil INTEGER, ay TEXT, islem_tarihi TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS gunluk_denetim (id INTEGER PRIMARY KEY AUTOINCREMENT, tarih DATE UNIQUE, denetmen TEXT, depo_durum TEXT DEFAULT 'Bekliyor', depo_not TEXT, tadim_durum TEXT DEFAULT 'Bekliyor', tadim_not TEXT, benmari_durum TEXT DEFAULT 'Bekliyor', benmari_not TEXT, numune_durum TEXT DEFAULT 'Bekliyor', hijyen_durum TEXT DEFAULT 'Bekliyor', ufak_sorunlar TEXT, son_guncelleme TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS yapilacaklar (id INTEGER PRIMARY KEY AUTOINCREMENT, gorev TEXT, durum TEXT DEFAULT 'Bekliyor', olusturan TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS personeller (id INTEGER PRIMARY KEY AUTOINCREMENT, ad_soyad TEXT, sicil_no TEXT UNIQUE, gorev TEXT, mesai_baslangic TEXT DEFAULT '08:00', mesai_bitis TEXT DEFAULT '17:00', durum TEXT DEFAULT 'Aktif')''')
    c.execute('''CREATE TABLE IF NOT EXISTS personel_izinler (id INTEGER PRIMARY KEY AUTOINCREMENT, personel_id INTEGER, baslangic_tarihi DATE, bitis_tarihi DATE, izin_turu TEXT, aciklama TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS pdks_log (id INTEGER PRIMARY KEY AUTOINCREMENT, tarih DATE, personel_id INTEGER, giris_saati TEXT, cikis_saati TEXT, durum TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS firma_talepleri (id INTEGER PRIMARY KEY AUTOINCREMENT, tarih DATE, firma_adi TEXT, konu TEXT, detay TEXT, durum TEXT DEFAULT 'Bekliyor', hatirlat_tarih DATE)''')

    conn.commit()
    conn.close()

@app.route('/bildirim-oku')
@login_required
def bildirim_oku():
    tur = request.args.get('tur')
    ref = request.args.get('ref')
    git = request.args.get('git', '/')
    bugun_str = date.today().strftime('%Y-%m-%d')

    conn = get_db_connection()
    if tur == 'ajanda':
        mevcut = conn.execute("SELECT id FROM okunan_bildirimler WHERE kullanici_id=? AND bildirim_turu=? AND referans_id=? AND islem_tarihi=?", (session['kullanici_id'], tur, ref, bugun_str)).fetchone()
        if not mevcut:
            conn.execute("INSERT INTO okunan_bildirimler (kullanici_id, bildirim_turu, referans_id, islem_tarihi) VALUES (?, ?, ?, ?)", (session['kullanici_id'], tur, ref, bugun_str))
            conn.commit()
    conn.close()
    return redirect(git)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM kullanicilar WHERE kullanici_adi=? AND sifre=?", (request.form['kullanici_adi'], request.form['sifre'])).fetchone()
        conn.close()
        if user:
            session['kullanici_id'] = user['id']; session['kullanici_adi'] = user['kullanici_adi']; session['rol'] = user['rol']; session['isim'] = user['isim']
            flash(f"Hoş geldin, {user['isim']}!", "success")
            return redirect(url_for('index'))
        flash("Hatalı giriş!", "error")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear(); flash("Çıkış yapıldı.", "success"); return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/ajanda')
@login_required
def ajanda():
    conn = get_db_connection()
    bugun = date.today(); bugun_str = bugun.strftime('%Y-%m-%d'); mevcut_yil = bugun.year
    events = []

    for y in [mevcut_yil - 1, mevcut_yil, mevcut_yil + 1]:
        sabit_tatiller = [
            (f"{y}-01-01", "🇹🇷 Yılbaşı"),
            (f"{y}-04-23", "🇹🇷 23 Nisan Ulusal Eg."),
            (f"{y}-05-01", "👷 1 Mayıs İşçi Bayramı"),
            (f"{y}-05-19", "🇹🇷 19 Mayıs Gençlik B."),
            (f"{y}-07-15", "🇹🇷 15 Temmuz Demokrasi Günü"),
            (f"{y}-08-30", "🇹🇷 30 Ağustos Zafer B."),
            (f"{y}-10-29", "🇹🇷 29 Ekim Cumhuriyet B.")
        ]
        for t_tarih, t_isim in sabit_tatiller:
            events.append({'id': f"tatil_{t_tarih}", 'title': t_isim, 'start': t_tarih, 'color': '#f43f5e', 'display': 'block'})

    ajanda_notlari_raw = conn.execute('SELECT * FROM ajanda').fetchall()

    tamamlanlar_db = conn.execute("SELECT ajanda_id, tarih FROM ajanda_tamamlananlar").fetchall()
    biten_set = {f"{t['ajanda_id']}_{t['tarih']}" for t in tamamlanlar_db}

    filtreli_notlar = []
    k_adi = session.get('kullanici_adi')
    for row in ajanda_notlari_raw:
        n = dict(row)
        if session.get('rol') == 'depocu':
            if n['atanan_kisi'] != k_adi and n['olusturan'] != k_adi: continue
        else:
            if n['atanan_kisi'] != 'Tümü' and n['atanan_kisi'] != k_adi and n['olusturan'] != k_adi: continue

        filtreli_notlar.append(n)

        start_date = datetime.strptime(n['tarih'], '%Y-%m-%d').date()
        periyot = n.get('periyot', 'Tek Seferlik')
        bitis_str = n.get('bitis_tarihi', '')

        def add_to_calendar(cur_date_obj, index):
            d_str = cur_date_obj.strftime('%Y-%m-%d')
            is_finished = f"{n['id']}_{d_str}" in biten_set

            durum_renk = n['renk_kodu'] if not is_finished else '#9ca3af'
            durum_class = 'tamamlandi-cizik' if is_finished else ''

            title = n['not_icerik']
            if n['atanan_kisi'] != 'Tümü': title = f"👤 [{n['atanan_kisi'].upper()}] {title}"
            else: title = f"🌍 [GENEL] {title}"

            event = {
                'id': f"{n['id']}_{index}",
                'real_id': n['id'],
                'title': title,
                'start': d_str,
                'color': durum_renk,
                'className': durum_class,
                'durum': 'Tamamlandı' if is_finished else 'Bekliyor'
            }
            if n.get('url'): event['url'] = n['url']
            events.append(event)

        if periyot == 'Tek Seferlik' or not bitis_str:
            final_date = datetime.strptime(get_next_workday(start_date.strftime('%Y-%m-%d')), '%Y-%m-%d').date()
            add_to_calendar(final_date, 0)
            continue

        bitis_date = datetime.strptime(bitis_str, '%Y-%m-%d').date()
        cur_date = start_date
        i = 0
        while cur_date <= bitis_date and i < 500:
            final_date = datetime.strptime(get_next_workday(cur_date.strftime('%Y-%m-%d')), '%Y-%m-%d').date()
            add_to_calendar(final_date, i)

            if periyot == 'Haftalık': cur_date += timedelta(weeks=1)
            else:
                m_add = {'Aylık': 1, '2 Aylık': 2, '3 Aylık': 3, '6 Aylık': 6, 'Yıllık': 12}.get(periyot, 1)
                m = cur_date.month - 1 + m_add
                y = cur_date.year + m // 12
                m = m % 12 + 1
                try: cur_date = date(y, m, start_date.day)
                except ValueError: cur_date = date(y, m, 28)
            i += 1

    if session.get('rol') == 'admin':
        for ek in conn.execute("SELECT * FROM kumanya WHERE tarih=? AND durum='Planlandı'", (bugun_str,)).fetchall():
            events.append({'id': f"alert_kumanya_{ek['id']}", 'title': f"⚠️ ÜRETİM: {ek['kulup_adi']} Reçete Bekliyor!", 'start': bugun_str, 'color': '#ef4444', 'url': '/kumanya'})
        for di in conn.execute("SELECT * FROM ihtarlar WHERE durum='Bekliyor' AND son_tarih <= ?", (bugun_str,)).fetchall():
            events.append({'id': f"alert_ihtar_{di['id']}", 'title': f"⚖️ İHTAR DOLDU: {di['firma_adi']} - Tutanak Tut!", 'start': bugun_str, 'color': '#991b1b', 'url': '/ihtar-tutanak'})
        bakimlar = conn.execute("SELECT * FROM periyodik_bakim").fetchall()
        for ay_no in range(1, 13):
            ay_str = str(ay_no); bekleyen_bakim_var = False
            for b in bakimlar:
                if b['bakim_aylari'] and ay_str in b['bakim_aylari'].split(','):
                    if not conn.execute("SELECT id FROM periyodik_bakim_log WHERE bakim_id=? AND yil=? AND ay=?", (b['id'], mevcut_yil, ay_str)).fetchone():
                        bekleyen_bakim_var = True; break
            if bekleyen_bakim_var:
                events.append({'id': f"alert_bakim_{ay_no}", 'title': f"🔧 Bakım İşle!", 'start': get_next_workday(f"{mevcut_yil}-{ay_no:02d}-01"), 'color': '#7e22ce', 'url': f'/periyodik-bakim?yil={mevcut_yil}&ay={ay_str}'})

    tum_kullanicilar = conn.execute("SELECT kullanici_adi, isim FROM kullanicilar").fetchall()
    yapilacaklar = conn.execute("SELECT * FROM yapilacaklar WHERE olusturan=? ORDER BY durum ASC, id DESC", (session.get('kullanici_adi'),)).fetchall()

    conn.close()
    return render_template('ajanda.html', events=events, kullanicilar=tum_kullanicilar, todos=yapilacaklar, ajanda_notlari=filtreli_notlar)

@app.route('/ajanda-durum', methods=['POST'])
@login_required
def ajanda_durum():
    conn = get_db_connection()
    a_id = request.form['ajanda_id']
    target_date = request.form['tarih']

    mevcut = conn.execute("SELECT id FROM ajanda_tamamlananlar WHERE ajanda_id=? AND tarih=?", (a_id, target_date)).fetchone()
    if mevcut:
        conn.execute("DELETE FROM ajanda_tamamlananlar WHERE id=?", (mevcut['id'],))
    else:
        conn.execute("INSERT INTO ajanda_tamamlananlar (ajanda_id, tarih) VALUES (?,?)", (a_id, target_date))

    conn.commit()
    conn.close()
    return redirect(url_for('ajanda'))

@app.route('/todo-islem', methods=['POST'])
@login_required
def todo_islem():
    conn = get_db_connection()
    islem = request.form.get('islem_tipi')
    k_adi = session.get('kullanici_adi')

    if islem == 'ekle':
        gorev = request.form.get('gorev')
        if gorev: conn.execute("INSERT INTO yapilacaklar (gorev, olusturan) VALUES (?,?)", (gorev, k_adi))
    elif islem == 'tamamla':
        t_id = request.form.get('todo_id')
        conn.execute("UPDATE yapilacaklar SET durum='Tamamlandı' WHERE id=? AND olusturan=?", (t_id, k_adi))
    elif islem == 'sil':
        t_id = request.form.get('todo_id')
        conn.execute("DELETE FROM yapilacaklar WHERE id=? AND olusturan=?", (t_id, k_adi))

    conn.commit(); conn.close()
    return redirect(url_for('ajanda'))

@app.route('/not-kaydet', methods=['POST'])
@login_required
def not_kaydet():
    conn = get_db_connection()
    tarih = request.form['tarih']
    icerik = request.form['not_icerik']
    renk = request.form['renk_kodu']

    if session.get('rol') == 'admin':
        atanan = request.form.get('atanan_kisi', session.get('kullanici_adi'))
    else:
        atanan = session.get('kullanici_adi')

    periyot = request.form.get('periyot', 'Tek Seferlik')
    bitis = request.form.get('bitis_tarihi', '')
    olusturan = session.get('kullanici_adi')

    conn.execute('INSERT INTO ajanda (tarih, not_icerik, renk_kodu, url, atanan_kisi, periyot, olusturan, bitis_tarihi) VALUES (?, ?, ?, "", ?, ?, ?, ?)',
                 (tarih, icerik, renk, atanan, periyot, olusturan, bitis))
    conn.commit(); conn.close()

    flash(f"Görev/Not başarıyla eklendi. ({periyot})", "success")
    return redirect(url_for('ajanda'))

@app.route('/ajanda-sil', methods=['POST'])
@login_required
def ajanda_sil():
    conn = get_db_connection()
    a_id = request.form['ajanda_id']

    if session.get('rol') != 'admin':
        mevcut = conn.execute("SELECT id FROM ajanda WHERE id=? AND olusturan=?", (a_id, session['kullanici_adi'])).fetchone()
        if not mevcut:
            flash("Bunu silmeye yetkiniz yok!", "error")
            return redirect(url_for('ajanda'))

    conn.execute("DELETE FROM ajanda WHERE id=?", (a_id,))
    conn.execute("DELETE FROM okunan_bildirimler WHERE referans_id LIKE ?", (f"{a_id}_%",))
    conn.execute("DELETE FROM ajanda_tamamlananlar WHERE ajanda_id=?", (a_id,))
    conn.commit()
    conn.close()
    flash("Görev ve gelecekteki tüm tekrarları sistemden kökten silindi.", "success")
    return redirect(url_for('ajanda'))

@app.route('/mal-kabul', methods=['GET', 'POST'])
@login_required
def mal_kabul():
    conn = get_db_connection()
    if request.method == 'POST':
        islem = request.form.get('islem_tipi')

        if islem == 'ekle':
            kategori = request.form.get('kategori')
            urun_raw = request.form.get('urun_adi', '').strip()

            u_upper = urun_raw.upper()
            if 'AYRAN' in u_upper or 'MEYVE SUYU' in u_upper or 'MEYVESUYU' in u_upper:
                urun = 'Ayran / Meyve Suyu'; kategori = 'İçecek'; birim = 'ADET'
            else:
                urun = urun_raw.title(); birim = request.form.get('birim')

            miktar = float(request.form.get('miktar', 0))
            form_onay = request.form.get('onay_durumu')
            notlar = request.form.get('notlar', '')
            lot_no = request.form.get('lot_damga_no', '').strip()
            takim_raw = request.form.get('takim_sayisi')
            takim = float(takim_raw) if takim_raw and str(takim_raw).strip() else 0.0
            marka = request.form.get('marka', '').strip()
            skt = request.form.get('skt', '')

            onay_durumu = 'Bekliyor' if session['rol'] == 'depocu' else (form_onay if form_onay else 'Onaylandı')

            if session['rol'] == 'admin' and onay_durumu in ['Onaylandı', 'Şartlı Kabul', 'Red (İade)']:
                islem_notu = request.form.get('islem_notu', '').strip()
                ek_str = f" (Sebep: {islem_notu})" if islem_notu and onay_durumu != 'Onaylandı' else ""
                notlar = f"{notlar} | {onay_durumu} Yapan: {session['isim']}{ek_str}".strip(" |")

            conn.execute('INSERT INTO mal_kabul_log (kategori, urun_adi, marka, tedarikci, miktar, birim, skt, onay_durumu, kabul_eden, notlar, lot_damga_no, takim_sayisi) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                         (kategori, urun, marka, request.form.get('tedarikci'), miktar, birim, skt, onay_durumu, session['isim'], notlar, lot_no, takim))

            if onay_durumu in ['Onaylandı', 'Şartlı Kabul']:
                mevcut = conn.execute("SELECT id, urun_adi FROM depo WHERE urun_adi COLLATE NOCASE = ?", (urun,)).fetchone()
                if mevcut:
                    conn.execute('UPDATE depo SET miktar = miktar + ? WHERE id = ?', (miktar, mevcut['id']))
                else:
                    conn.execute('INSERT INTO depo (kategori, urun_adi, miktar, birim) VALUES (?, ?, ?, ?)', (kategori, urun, miktar, birim))

                conn.execute('INSERT INTO stok_lotlari (urun_adi, marka, lot_damga_no, baslangic_miktar, kalan_miktar, birim, skt, takim_sayisi) VALUES (?,?,?,?,?,?,?,?)',
                             (urun, marka, lot_no, miktar, miktar, birim, skt, takim))

            conn.commit()
            if session['rol'] == 'depocu': flash("Mal kabul talebi oluşturuldu. Onay bekleniyor.", "success")
            elif onay_durumu == 'Red (İade)': flash("Mal reddedildi. Arşive işlendi.", "success")
            else: flash(f"✅ {urun} depoya ve Marka Takip sistemine eklendi ({onay_durumu}).", "success")

        elif islem == 'onayla' and session['rol'] == 'admin':
            log = conn.execute("SELECT * FROM mal_kabul_log WHERE id=?", (request.form['log_id'],)).fetchone()
            if log and log['onay_durumu'] == 'Bekliyor':
                mevcut = conn.execute("SELECT id, urun_adi FROM depo WHERE urun_adi COLLATE NOCASE = ?", (log['urun_adi'],)).fetchone()
                if mevcut: conn.execute('UPDATE depo SET miktar = miktar + ? WHERE id = ?', (log['miktar'], mevcut['id']))
                else: conn.execute('INSERT INTO depo (kategori, urun_adi, miktar, birim) VALUES (?, ?, ?, ?)', (log['kategori'], log['urun_adi'], log['miktar'], log['birim']))

                conn.execute('INSERT INTO stok_lotlari (urun_adi, marka, lot_damga_no, baslangic_miktar, kalan_miktar, birim, skt, takim_sayisi) VALUES (?,?,?,?,?,?,?,?)',
                             (log['urun_adi'], log['marka'], log['lot_damga_no'], log['miktar'], log['miktar'], log['birim'], log['skt'], log['takim_sayisi']))

                yeni_not = f"{log['notlar']} | Onaylayan: {session['isim']}".strip(" |")
                conn.execute("UPDATE mal_kabul_log SET onay_durumu='Onaylandı', notlar=? WHERE id=?", (yeni_not, request.form['log_id']))
                conn.commit(); flash(f"{log['urun_adi']} başarıyla stoğa eklendi.", "success")

        elif islem == 'sartli_kabul' and session['rol'] == 'admin':
            islem_notu = request.form.get('islem_notu', '').strip()
            log = conn.execute("SELECT * FROM mal_kabul_log WHERE id=?", (request.form['log_id'],)).fetchone()
            if log and log['onay_durumu'] == 'Bekliyor':
                mevcut = conn.execute("SELECT id, urun_adi FROM depo WHERE urun_adi COLLATE NOCASE = ?", (log['urun_adi'],)).fetchone()
                if mevcut: conn.execute('UPDATE depo SET miktar = miktar + ? WHERE id = ?', (log['miktar'], mevcut['id']))
                else: conn.execute('INSERT INTO depo (kategori, urun_adi, miktar, birim) VALUES (?, ?, ?, ?)', (log['kategori'], log['urun_adi'], log['miktar'], log['birim']))

                conn.execute('INSERT INTO stok_lotlari (urun_adi, marka, lot_damga_no, baslangic_miktar, kalan_miktar, birim, skt, takim_sayisi) VALUES (?,?,?,?,?,?,?,?)',
                             (log['urun_adi'], log['marka'], log['lot_damga_no'], log['miktar'], log['miktar'], log['birim'], log['skt'], log['takim_sayisi']))

                eski_not = log['notlar'] if log['notlar'] else ""
                yeni_not = f"{eski_not} | Şartlı Kabul: {session['isim']} (Sebep: {islem_notu})".strip(" |")
                conn.execute("UPDATE mal_kabul_log SET onay_durumu='Şartlı Kabul', notlar=? WHERE id=?", (yeni_not, request.form['log_id']))
                conn.commit(); flash(f"{log['urun_adi']} Şartlı Kabul ile stoğa eklendi.", "success")

        elif islem == 'reddet' and session['rol'] == 'admin':
            islem_notu = request.form.get('islem_notu', '').strip()
            log = conn.execute("SELECT notlar FROM mal_kabul_log WHERE id=?", (request.form['log_id'],)).fetchone()
            eski_not = log['notlar'] if log and log['notlar'] else ""
            yeni_not = f"{eski_not} | Reddeden: {session['isim']} (Sebep: {islem_notu})".strip(" |")
            conn.execute("UPDATE mal_kabul_log SET onay_durumu='Reddedildi', notlar=? WHERE id=?", (yeni_not, request.form['log_id']))
            conn.commit(); flash("Talep reddedildi.", "success")

        return redirect(url_for('mal_kabul'))

    secilen_ay = request.args.get('ay', date.today().strftime('%Y-%m'))
    bekleyenler = conn.execute("SELECT * FROM mal_kabul_log WHERE onay_durumu='Bekliyor' ORDER BY tarih DESC").fetchall()
    loglar = conn.execute("SELECT * FROM mal_kabul_log WHERE onay_durumu IN ('Onaylandı', 'Şartlı Kabul') AND tarih LIKE ? ORDER BY tarih DESC", (f"{secilen_ay}%",)).fetchall()
    red_loglar = conn.execute("SELECT * FROM mal_kabul_log WHERE onay_durumu IN ('Red (İade)', 'Reddedildi') AND tarih LIKE ? ORDER BY tarih DESC", (f"{secilen_ay}%",)).fetchall()

    conn.close()
    return render_template('mal_kabul.html', loglar=loglar, red_loglar=red_loglar, bekleyenler=bekleyenler, secilen_ay=secilen_ay)

@app.route('/depo', methods=['GET', 'POST'])
@login_required
def depo():
    conn = get_db_connection()
    bugun = date.today().strftime('%Y-%m-%d')

    if request.method == 'POST':
        islem = request.form.get('islem_tipi')

        if islem == 'manuel_sayim' and session.get('rol') == 'admin':
            conn.execute('UPDATE depo SET miktar = ? WHERE id = ?', (float(request.form['yeni_miktar']), request.form['urun_id']))
            flash("Sayım başarıyla güncellendi.", "success")

        elif islem == 'urun_sil' and session.get('rol') == 'admin':
            urun_id = request.form.get('urun_id')
            if urun_id:
                conn.execute("DELETE FROM depo WHERE id=?", (urun_id,))
                flash("Ürün ve stok bilgisi depodan tamamen silindi.", "success")

        elif islem == 'depo_cikis':
            urun_marka_val = request.form['urun_adi_marka']
            parcalar_val = urun_marka_val.split('|')
            urun_adi = parcalar_val[0]
            marka = parcalar_val[1] if len(parcalar_val) > 1 else ''

            miktar = float(request.form['miktar'])
            ek_aciklama = request.form.get('aciklama', '')

            bagli_uretim = request.form.get('bagli_uretim')
            if not bagli_uretim:
                flash("Lütfen bir hedef üretim veya kullanım yeri seçin!", "error")
                return redirect(url_for('depo'))

            parcalar = bagli_uretim.split('|')
            islem_tarihi = parcalar[0].strip()
            ogun_secimi = parcalar[1].strip()
            hedef_yemek = parcalar[2].strip()

            mevcut = conn.execute("SELECT id, miktar, birim FROM depo WHERE urun_adi COLLATE NOCASE = ?", (urun_adi,)).fetchone()

            if mevcut and mevcut['miktar'] >= miktar:
                if session.get('rol') == 'depocu':
                    conn.execute("INSERT INTO depo_cikis (tarih, ogun, yemek_adi, malzeme_adi, marka, miktar, birim, onay_durumu, aciklama) VALUES (?,?,?,?,?,?,?,'Bekliyor',?)",
                                 (islem_tarihi, ogun_secimi, hedef_yemek, urun_adi, marka, miktar, mevcut['birim'], f"Talep: {session['isim']} | {ek_aciklama}"))
                    flash(f"'{hedef_yemek}' için çıkış talebi oluşturuldu.", "success")
                else:
                    conn.execute("UPDATE depo SET miktar = miktar - ? WHERE id=?", (miktar, mevcut['id']))

                    if marka != 'Sistem/Eski Stok':
                        lots = conn.execute("SELECT id, kalan_miktar FROM stok_lotlari WHERE urun_adi COLLATE NOCASE=? AND (marka=? OR (marka IS NULL AND ?='')) AND kalan_miktar > 0 ORDER BY tarih ASC", (urun_adi, marka, marka)).fetchall()
                        kalan_dusulecek = miktar
                        for lot in lots:
                            if kalan_dusulecek <= 0: break
                            if lot['kalan_miktar'] <= kalan_dusulecek:
                                kalan_dusulecek -= lot['kalan_miktar']
                                conn.execute("UPDATE stok_lotlari SET kalan_miktar=0 WHERE id=?", (lot['id'],))
                            else:
                                conn.execute("UPDATE stok_lotlari SET kalan_miktar=kalan_miktar-? WHERE id=?", (kalan_dusulecek, lot['id']))
                                kalan_dusulecek = 0

                    conn.execute("INSERT INTO depo_cikis (tarih, ogun, yemek_adi, malzeme_adi, marka, miktar, birim, onay_durumu, aciklama) VALUES (?,?,?,?,?,?,?,'Onaylandı',?)",
                                 (islem_tarihi, ogun_secimi, hedef_yemek, urun_adi, marka, miktar, mevcut['birim'], f"Çıkış: {session['isim']} | {ek_aciklama}"))
                    flash(f"✅ Mutfağa '{hedef_yemek}' için {miktar} {mevcut['birim']} {urun_adi} ({marka if marka else 'Markasız'}) sevk edildi.", "success")
            else:
                flash(f"Hata: Depoda yeterli miktar bulunmuyor!", "error")

        elif islem == 'cikis_onayla' and session.get('rol') == 'admin':
            cikis = conn.execute("SELECT * FROM depo_cikis WHERE id=?", (request.form['cikis_id'],)).fetchone()
            if cikis and cikis['onay_durumu'] == 'Bekliyor':
                mevcut = conn.execute("SELECT id, miktar FROM depo WHERE urun_adi COLLATE NOCASE = ?", (cikis['malzeme_adi'],)).fetchone()
                if mevcut and mevcut['miktar'] >= cikis['miktar']:
                    yeni_detay = f"{cikis['yemek_adi']} | Onaylayan: {session['isim']}"
                    conn.execute("UPDATE depo SET miktar = miktar - ? WHERE id=?", (cikis['miktar'], mevcut['id']))

                    marka = cikis['marka'] if 'marka' in cikis.keys() else ''
                    if marka and marka != 'Sistem/Eski Stok':
                        lots = conn.execute("SELECT id, kalan_miktar FROM stok_lotlari WHERE urun_adi COLLATE NOCASE=? AND (marka=? OR (marka IS NULL AND ?='')) AND kalan_miktar > 0 ORDER BY tarih ASC", (cikis['malzeme_adi'], marka, marka)).fetchall()
                        kalan_dusulecek = cikis['miktar']
                        for lot in lots:
                            if kalan_dusulecek <= 0: break
                            if lot['kalan_miktar'] <= kalan_dusulecek:
                                kalan_dusulecek -= lot['kalan_miktar']
                                conn.execute("UPDATE stok_lotlari SET kalan_miktar=0 WHERE id=?", (lot['id'],))
                            else:
                                conn.execute("UPDATE stok_lotlari SET kalan_miktar=kalan_miktar-? WHERE id=?", (kalan_dusulecek, lot['id']))
                                kalan_dusulecek = 0

                    conn.execute("UPDATE depo_cikis SET onay_durumu='Onaylandı', yemek_adi=? WHERE id=?", (yeni_detay, request.form['cikis_id']))
                    flash("Çıkış talebi onaylandı ve stoktan düşüldü.", "success")
                else:
                    flash("Mevcut stok bu talebi karşılamıyor!", "error")

        elif islem == 'cikis_reddet' and session.get('rol') == 'admin':
            cikis = conn.execute("SELECT * FROM depo_cikis WHERE id=?", (request.form['cikis_id'],)).fetchone()
            yeni_detay = f"{cikis['yemek_adi']} | Reddeden: {session['isim']}"
            conn.execute("UPDATE depo_cikis SET onay_durumu='Reddedildi', yemek_adi=? WHERE id=?", (yeni_detay, request.form['cikis_id']))
            flash("Çıkış talebi reddedildi.", "success")

        conn.commit()
        return redirect(url_for('depo'))

    stoklar = conn.execute('SELECT * FROM depo ORDER BY kategori, urun_adi').fetchall()

    detayli_stoklar = []
    for s in stoklar:
        lots = conn.execute("SELECT marka, SUM(kalan_miktar) as miktar FROM stok_lotlari WHERE urun_adi COLLATE NOCASE=? AND kalan_miktar > 0 GROUP BY marka", (s['urun_adi'],)).fetchall()
        lot_total = 0
        for l in lots:
            detayli_stoklar.append({
                'urun_adi': s['urun_adi'], 'marka': l['marka'] if l['marka'] else 'Markasız',
                'miktar': l['miktar'], 'birim': s['birim'], 'gercek_marka': l['marka']
            })
            lot_total += l['miktar']

        if s['miktar'] > lot_total + 0.05:
            detayli_stoklar.append({
                'urun_adi': s['urun_adi'], 'marka': 'Sistem/Eski Stok',
                'miktar': s['miktar'] - lot_total, 'birim': s['birim'], 'gercek_marka': 'Sistem/Eski Stok'
            })

    bekleyen_cikislar = conn.execute("SELECT * FROM depo_cikis WHERE onay_durumu='Bekliyor' ORDER BY id DESC").fetchall()
    cikislar = conn.execute("SELECT * FROM depo_cikis WHERE onay_durumu='Onaylandı' ORDER BY id DESC LIMIT 30").fetchall()
    planlananlar = conn.execute("SELECT tarih, ogun, yemek_adi FROM uretim WHERE durum IN ('Planlandı', 'Üretimde') AND tarih >= ? ORDER BY tarih ASC", (bugun,)).fetchall()
    kategoriler = []
    for s in stoklar:
        kat = s['kategori']
        if kat and "Kırmızı Et" in kat: kat = "🥩 Kırmızı Et"
        if kat and kat not in kategoriler: kategoriler.append(kat)

    conn.close()
    return render_template('depo.html', stoklar=stoklar, detayli_stoklar=detayli_stoklar, cikislar=cikislar, bekleyen_cikislar=bekleyen_cikislar, bugun=bugun, kategoriler=kategoriler, planlananlar=planlananlar)

# ==========================================
# ÜRETİM VE HAFIZALI MENÜ PLANLAMA MOTORU
# ==========================================
@app.route('/uretim', methods=['GET', 'POST'])
@login_required
@admin_required
def uretim():
    conn = get_db_connection()
    bugun = date.today().strftime('%Y-%m-%d')
    if request.method == 'POST':
        islem = request.form.get('islem_tipi')
        m_tarih = request.form.get('mevcut_tarih', bugun)
        m_ogun = request.form.get('mevcut_ogun', 'Öğle Yemeği')

        if islem == 'yemek_adi_guncelle':
            conn.execute("UPDATE uretim SET yemek_adi=? WHERE id=?", (request.form['yeni_yemek_adi'], request.form['uretim_id'])); conn.commit(); flash("Yemek adı güncellendi.", "success")

        elif islem == 'hizli_icecek_dus':
            icecek_adi = "Ayran / Meyve Suyu"; miktar = float(request.form.get('icecek_miktar', 0))
            mevcut = conn.execute("SELECT id, miktar FROM depo WHERE urun_adi=?", (icecek_adi,)).fetchone()
            if mevcut and mevcut['miktar'] >= miktar:
                conn.execute("UPDATE depo SET miktar = miktar - ? WHERE id=?", (miktar, mevcut['id']))
                conn.execute("INSERT INTO depo_cikis (tarih, ogun, yemek_adi, malzeme_adi, miktar, birim, onay_durumu, aciklama) VALUES (?,?,?,?,?,'ADET','Onaylandı','Otomatik İçecek')", (m_tarih, m_ogun, "İçecek Dağıtımı", icecek_adi, miktar))
                conn.commit(); flash(f"✅ {int(miktar) if miktar.is_integer() else miktar} Adet İçecek düşüldü.", "success")
            else: flash("Depoda yeterli İçecek yok!", "error")

        elif islem == 'kriz_bildir':
            mevcut = conn.execute('SELECT id FROM gunluk_istatistik WHERE tarih=? AND ogun=?', (m_tarih, m_ogun)).fetchone()
            yetmedi = int(request.form.get('kisi_ac_kaldi', 0)); detay = request.form.get('alternatif_detay', '')
            if mevcut: conn.execute('UPDATE gunluk_istatistik SET yemek_yetmedi=?, alternatif_detay=? WHERE id=?', (yetmedi, detay, mevcut['id']))
            else: conn.execute('INSERT INTO gunluk_istatistik (tarih, ogun, yemek_yetmedi, alternatif_detay) VALUES (?,?,?,?)', (m_tarih, m_ogun, yetmedi, detay))
            conn.commit(); flash("Kriz raporlara işlendi.", "success")

        elif islem == 'manuel_yemek_ekle':
            y_adi = request.form['yemek_adi'].strip().title()
            mevcut_mi = conn.execute("SELECT id FROM uretim WHERE tarih=? AND ogun=? AND yemek_adi COLLATE NOCASE = ?", (m_tarih, request.form['ogun'], y_adi)).fetchone()
            if not mevcut_mi:
                conn.execute("INSERT INTO uretim (tarih, ogun, yemek_adi, durum, kategori) VALUES (?,?,?,'Planlandı',?)", (m_tarih, request.form['ogun'], y_adi, request.form.get('kategori', 'Normal')))
                conn.commit(); flash(f"✅ '{y_adi}' menüye eklendi.", "success")
            else: flash(f"⚠️ Bu yemek zaten bu öğünde ekli!", "error")

        elif islem == 'uretime_gonder':
            uretim_id = request.form['uretim_id']; planlanan = int(request.form['planlanan_kisi']); yemek_adi = request.form['yemek_adi']
            u = conn.execute("SELECT * FROM uretim WHERE id=?", (uretim_id,)).fetchone()
            sifirdan_kisi = max(0, planlanan - u['dondurucudan_alinan'])
            receteler = conn.execute("SELECT * FROM receteler WHERE yemek_adi=?", (yemek_adi,)).fetchall()

            if not receteler:
                flash(f"HATA: '{yemek_adi}' yemeğinin reçetesi boş! Lütfen önce malzeme ekleyin.", "error")
            else:
                yeterli = True; hata_mesaji = []
                if sifirdan_kisi > 0:
                    for r in receteler:
                        d_m = conn.execute("SELECT birim FROM depo WHERE urun_adi COLLATE NOCASE = ?", (r['malzeme_adi'],)).fetchone()
                        oran = 1000 if d_m and d_m['birim'].upper() in ['KG', 'LT'] else 1
                        gereken = (r['miktar'] * sifirdan_kisi) / oran
                        mutfak_stok = conn.execute("SELECT SUM(miktar) as c FROM depo_cikis WHERE tarih=? AND ogun=? AND yemek_adi=? AND malzeme_adi=? AND onay_durumu='Onaylandı'", (m_tarih, u['ogun'], yemek_adi, r['malzeme_adi'])).fetchone()
                        mutfak_miktar = mutfak_stok['c'] if mutfak_stok and mutfak_stok['c'] else 0

                        if mutfak_miktar < gereken - 0.05:
                            yeterli = False
                            eksik = round(gereken - mutfak_miktar, 2)
                            eksik_str = int(eksik) if eksik.is_integer() else eksik
                            hata_mesaji.append(f"{r['malzeme_adi']} ({eksik_str} {d_m['birim'] if d_m else 'KG'} eksik)")

                if not yeterli: flash(f"MUTFAK STOĞU YETERSİZ! Depodan eksik mal çıkılmış: {', '.join(hata_mesaji)}", "error")
                else:
                    conn.execute("UPDATE uretim SET planlanan_kisi=?, durum='Üretimde' WHERE id=?", (planlanan, uretim_id))
                    conn.commit(); flash(f"✅ '{yemek_adi}' üretime alındı. Mutfaktaki malzemeler kullanılıyor.", "success")

        elif islem == 'fazlayi_iade_et':
            u_id = request.form['uretim_id']
            u = conn.execute("SELECT * FROM uretim WHERE id=?", (u_id,)).fetchone()
            sifirdan_kisi = max(0, u['planlanan_kisi'] - u['dondurucudan_alinan'])
            receteler = conn.execute("SELECT * FROM receteler WHERE yemek_adi=?", (u['yemek_adi'],)).fetchall()

            iade_edilenler = []
            for r in receteler:
                d_m = conn.execute('SELECT id, birim FROM depo WHERE urun_adi COLLATE NOCASE = ?', (r['malzeme_adi'],)).fetchone()
                if not d_m: continue
                oran = 1000 if d_m['birim'].upper() in ['KG','LT'] else 1
                gereken = (r['miktar'] * sifirdan_kisi) / oran if u['durum'] != 'Planlandı' else 0
                mutfak_stok = conn.execute("SELECT SUM(miktar) as c FROM depo_cikis WHERE tarih=? AND ogun=? AND yemek_adi=? AND malzeme_adi=? AND onay_durumu='Onaylandı'", (u['tarih'], u['ogun'], u['yemek_adi'], r['malzeme_adi'])).fetchone()
                mutfak_miktar = mutfak_stok['c'] if mutfak_stok and mutfak_stok['c'] else 0

                fazla = round(mutfak_miktar - gereken, 2)
                if fazla > 0.05:
                    conn.execute('UPDATE depo SET miktar = miktar + ? WHERE id=?', (fazla, d_m['id']))

                    # YENİ EKLENEN: Geri dönen ürünü sistemde parçalamadan eski listeyle birleştir
                    mevcut_lot = conn.execute("SELECT id FROM stok_lotlari WHERE urun_adi COLLATE NOCASE=? ORDER BY id DESC LIMIT 1", (r['malzeme_adi'],)).fetchone()
                    if mevcut_lot:
                        conn.execute("UPDATE stok_lotlari SET kalan_miktar = kalan_miktar + ? WHERE id=?", (fazla, mevcut_lot['id']))
                    else:
                        conn.execute("INSERT INTO stok_lotlari (urun_adi, marka, baslangic_miktar, kalan_miktar, birim) VALUES (?, 'İade', ?, ?, ?)", (r['malzeme_adi'], fazla, fazla, d_m['birim']))

                    conn.execute("INSERT INTO depo_cikis (tarih, ogun, yemek_adi, malzeme_adi, miktar, birim, onay_durumu, aciklama) VALUES (?,?,?,?,?,?,'Onaylandı','Fazla Malzeme Depoya İade')", (u['tarih'], u['ogun'], u['yemek_adi'], r['malzeme_adi'], -fazla, d_m['birim']))
                    fazla_str = int(fazla) if fazla.is_integer() else fazla
                    iade_edilenler.append(f"{fazla_str} {d_m['birim']} {r['malzeme_adi']}")

            conn.commit()
            if iade_edilenler: flash(f"Fazla malzemeler depoya iade edildi: {', '.join(iade_edilenler)}", "success")
            else: flash("İade edilecek fazla malzeme bulunamadı.", "info")

        elif islem == 'uretim_iptal':
            conn.execute("UPDATE uretim SET durum='Planlandı', planlanan_kisi=0 WHERE id=?", (request.form['uretim_id'],))
            conn.commit(); flash("Üretim iptal edildi. Malzemeler mutfakta duruyor, isterseniz 'İade Et' butonuyla depoya yollayabilirsiniz.", "success")

        elif islem == 'uretim_sil': conn.execute('DELETE FROM uretim WHERE id=?', (request.form['uretim_id'],)); conn.commit()

        elif islem == 'mutfaktan_direkt_iade':
            malzeme_adi = request.form['malzeme_adi']
            iade_miktar = float(request.form['iade_miktar'])
            d_m = conn.execute('SELECT id, birim FROM depo WHERE urun_adi COLLATE NOCASE = ?', (malzeme_adi,)).fetchone()
            if d_m:
                conn.execute('UPDATE depo SET miktar = miktar + ? WHERE id=?', (iade_miktar, d_m['id']))

                # YENİ EKLENEN: Geri dönen ürünü sistemde parçalamadan eski listeyle birleştir
                mevcut_lot = conn.execute("SELECT id FROM stok_lotlari WHERE urun_adi COLLATE NOCASE=? ORDER BY id DESC LIMIT 1", (malzeme_adi,)).fetchone()
                if mevcut_lot:
                    conn.execute("UPDATE stok_lotlari SET kalan_miktar = kalan_miktar + ? WHERE id=?", (iade_miktar, mevcut_lot['id']))
                else:
                    conn.execute("INSERT INTO stok_lotlari (urun_adi, marka, baslangic_miktar, kalan_miktar, birim) VALUES (?, 'İade', ?, ?, ?)", (malzeme_adi, iade_miktar, iade_miktar, d_m['birim']))

                conn.execute("INSERT INTO depo_cikis (tarih, ogun, yemek_adi, malzeme_adi, miktar, birim, onay_durumu, aciklama) VALUES (?,?,?,?,?,?,'Onaylandı','Mutfaktan Depoya İade')", (m_tarih, m_ogun, 'Mutfak İadesi', malzeme_adi, -iade_miktar, d_m['birim']))
                conn.commit()
                flash(f"✅ {iade_miktar} {d_m['birim']} '{malzeme_adi}' başarıyla ana depoya geri alındı.", "success")

        elif islem == 'gunluk_kisi_kaydet':
            mevcut = conn.execute('SELECT id FROM gunluk_istatistik WHERE tarih=? AND ogun=?', (m_tarih, m_ogun)).fetchone()
            if mevcut: conn.execute('UPDATE gunluk_istatistik SET personel_sayisi=?, ogrenci_sayisi=? WHERE id=?', (int(request.form.get('personel_sayisi', 0)), int(request.form.get('ogrenci_sayisi', 0)), mevcut['id']))
            else: conn.execute('INSERT INTO gunluk_istatistik (tarih, ogun, personel_sayisi, ogrenci_sayisi) VALUES (?,?,?,?)', (m_tarih, m_ogun, int(request.form.get('personel_sayisi', 0)), int(request.form.get('ogrenci_sayisi', 0))))
            conn.commit()
        elif islem == 'gun_sonu': conn.execute("UPDATE uretim SET gerceklesen_kisi=?, durum='Dağıtıldı' WHERE id=?", (request.form['gerceklesen'], request.form['uretim_id'])); conn.commit(); flash("Yemek dağıtıldı olarak işaretlendi.", "success")
        elif islem == 'gun_sonu_iptal': conn.execute("UPDATE uretim SET gerceklesen_kisi=0, durum='Üretimde' WHERE id=?", (request.form['uretim_id'],)); conn.commit()
        elif islem == 'recete_ekle': 
            conn.execute('INSERT INTO receteler (yemek_adi, malzeme_adi, miktar, birim) VALUES (?,?,?,?)', (request.form['yemek_adi'], request.form['malzeme_adi'], float(request.form['miktar_gram']), request.form.get('birim', 'Gr')))
            conn.commit()
        elif islem == 'recete_sil': 
            conn.execute('DELETE FROM receteler WHERE id=?', (request.form['recete_id'],))
            conn.commit()
            
        elif islem == 'recete_arsiv_ekle':
            conn.execute('INSERT INTO receteler (yemek_adi, malzeme_adi, miktar, birim) VALUES (?,?,?,?)', (request.form['yemek_adi'].title(), request.form['malzeme_adi'], float(request.form['miktar_gram']), request.form.get('birim', 'Gr')))
            conn.commit()
            flash(f"Reçete arşivi güncellendi.", "success")
            
        elif islem == 'recete_arsiv_sil':
            conn.execute('DELETE FROM receteler WHERE id=?', (request.form['recete_id'],))
            conn.commit()
            flash("Malzeme reçeteden çıkarıldı.", "success")
            
        return redirect(url_for('uretim', tarih=m_tarih, ogun=m_ogun))

    tarih = request.args.get('tarih', bugun)
    ogun = request.args.get('ogun', 'Öğle Yemeği')

    istatistik = conn.execute('SELECT * FROM gunluk_istatistik WHERE tarih=? AND ogun=?', (tarih, ogun)).fetchone()
    toplam_gelen = (istatistik['personel_sayisi'] + istatistik['ogrenci_sayisi']) if istatistik else 0

    query = 'SELECT * FROM uretim WHERE tarih = ?'; params = [tarih]
    if ogun and ogun != 'Tümü': query += ' AND ogun = ?'; params.append(ogun)
    uretimler = []
    for u in conn.execute(query, params).fetchall():
        u_dict = dict(u); m_list = []
        sifirdan_kisi = max(0, u['planlanan_kisi'] - u['dondurucudan_alinan'])
        for r in conn.execute('SELECT * FROM receteler WHERE yemek_adi=?', (u['yemek_adi'],)).fetchall():
            d_m = conn.execute('SELECT birim FROM depo WHERE urun_adi COLLATE NOCASE = ?', (r['malzeme_adi'],)).fetchone()
            oran = 1000 if d_m and d_m['birim'].upper() in ['KG','LT'] else 1
            gereken = (r['miktar'] * sifirdan_kisi) / oran

            mutfak_stok = conn.execute("SELECT SUM(miktar) as c FROM depo_cikis WHERE tarih=? AND ogun=? AND yemek_adi=? AND malzeme_adi=? AND onay_durumu='Onaylandı'", (u['tarih'], u['ogun'], u['yemek_adi'], r['malzeme_adi'])).fetchone()
            mutfak_miktar = mutfak_stok['c'] if mutfak_stok and mutfak_stok['c'] else 0

            m_list.append({
                'id': r['id'], 'adi': r['malzeme_adi'],
                'gram': int(r['miktar']) if r['miktar'].is_integer() else r['miktar'],
                'gereken': gereken, 'mutfak_stok': mutfak_miktar, 'birim': d_m['birim'] if d_m else 'KG',
                'yeterli': mutfak_miktar >= (gereken - 0.05)
            })
        u_dict['malzemeler'] = m_list; uretimler.append(u_dict)

    yedek_stok_db = conn.execute('SELECT * FROM yedek_stok WHERE porsiyon > 0').fetchall()
    yedek_dict = {y['yemek_adi']: y['porsiyon'] for y in yedek_stok_db}
    depo_malz = conn.execute('SELECT urun_adi FROM depo ORDER BY urun_adi').fetchall()
    icecek_stok = conn.execute("SELECT miktar FROM depo WHERE urun_adi='Ayran / Meyve Suyu'").fetchone()
    icecek_miktar = int(icecek_stok['miktar']) if icecek_stok and float(icecek_stok['miktar']).is_integer() else (icecek_stok['miktar'] if icecek_stok else 0)
    mevcut_yemekler = conn.execute('SELECT DISTINCT yemek_adi FROM receteler ORDER BY yemek_adi').fetchall()

    # --- ANLIK MUTFAK DEPOSU HESAPLAMA MOTORU ---
    # Mutfak tezgahında görünmemesi gereken doğrudan tüketim/çıkış türleri
    istisnalar = ('Zayi/Fire', 'Personel İkram', 'Genel Tüketim', 'Kumanya')

    if ogun and ogun != 'Tümü':
        mutfak_stok_raw = conn.execute("SELECT malzeme_adi, birim, SUM(miktar) as toplam_cikan FROM depo_cikis WHERE tarih=? AND ogun=? AND onay_durumu='Onaylandı' GROUP BY malzeme_adi", (tarih, ogun)).fetchall()
        aktif_uretimler = conn.execute("SELECT yemek_adi, planlanan_kisi, dondurucudan_alinan FROM uretim WHERE tarih=? AND ogun=? AND durum IN ('Üretimde', 'Dağıtıldı')", (tarih, ogun)).fetchall()
    else:
        mutfak_stok_raw = conn.execute("SELECT malzeme_adi, birim, SUM(miktar) as toplam_cikan FROM depo_cikis WHERE tarih=? AND onay_durumu='Onaylandı' AND ogun NOT IN (?, ?, ?, ?) GROUP BY malzeme_adi", (tarih, *istisnalar)).fetchall()
        aktif_uretimler = conn.execute("SELECT yemek_adi, planlanan_kisi, dondurucudan_alinan FROM uretim WHERE tarih=? AND durum IN ('Üretimde', 'Dağıtıldı')", (tarih,)).fetchall()

    tuketim = {}
    for u in aktif_uretimler:
        kisi = max(0, u['planlanan_kisi'] - u['dondurucudan_alinan'])
        r_list = conn.execute("SELECT malzeme_adi, miktar FROM receteler WHERE yemek_adi=?", (u['yemek_adi'],)).fetchall()
        for r in r_list:
            malz = r['malzeme_adi']
            d_m = conn.execute('SELECT birim FROM depo WHERE urun_adi COLLATE NOCASE = ?', (malz,)).fetchone()
            oran = 1000 if d_m and d_m['birim'].upper() in ['KG','LT'] else 1
            tuketim[malz] = tuketim.get(malz, 0) + ((r['miktar'] * kisi) / oran)

    mutfak_deposu = []
    for m in mutfak_stok_raw:
        malz = m['malzeme_adi']
        kalan = m['toplam_cikan'] - tuketim.get(malz, 0)
        if kalan > 0.05:
            mutfak_deposu.append({'malzeme_adi': malz, 'mutfak_miktar': kalan, 'birim': m['birim']})
    # --------------------------------------------
    
    # REÇETE ARŞİVİNİ ÇEK VE GRUPLA
    receteler_ham = conn.execute("SELECT id, yemek_adi, malzeme_adi, miktar, birim FROM receteler ORDER BY yemek_adi ASC").fetchall()
    recete_arsivi = {}
    for r in receteler_ham:
        y_adi = r['yemek_adi']
        if y_adi not in recete_arsivi:
            recete_arsivi[y_adi] = []
        recete_arsivi[y_adi].append({'id': r['id'], 'malzeme': r['malzeme_adi'], 'miktar': r['miktar'], 'birim': r['birim']})

    conn.close()
    return render_template('uretim.html', uretimler=uretimler, secilen_tarih=tarih, secilen_ogun=ogun, istatistik=istatistik, depo_malzemeler=depo_malz, yedek_dict=yedek_dict, toplam_gelen=toplam_gelen, icecek_miktar=icecek_miktar, mevcut_yemekler=mevcut_yemekler, mutfak_deposu=mutfak_deposu, recete_arsivi=recete_arsivi)

@app.route('/denetim', methods=['GET', 'POST'])
@login_required
@admin_required
def denetim():
    conn = get_db_connection()
    secilen_tarih = request.args.get('tarih', date.today().strftime('%Y-%m-%d'))
    if request.method == 'POST':
        tarih = request.form.get('tarih'); denetmen = session.get('isim')
        mevcut = conn.execute("SELECT id FROM gunluk_denetim WHERE tarih=?", (tarih,)).fetchone()
        if mevcut:
            conn.execute('''UPDATE gunluk_denetim SET denetmen=?, depo_durum=?, depo_not=?, tadim_durum=?, tadim_not=?, benmari_durum=?, benmari_not=?, numune_durum=?, hijyen_durum=?, ufak_sorunlar=?, son_guncelleme=CURRENT_TIMESTAMP WHERE id=?''', (denetmen, request.form.get('depo_durum', 'Bekliyor'), request.form.get('depo_not', ''), request.form.get('tadim_durum', 'Bekliyor'), request.form.get('tadim_not', ''), request.form.get('benmari_durum', 'Bekliyor'), request.form.get('benmari_not', ''), request.form.get('numune_durum', 'Bekliyor'), request.form.get('hijyen_durum', 'Bekliyor'), request.form.get('ufak_sorunlar', ''), mevcut['id']))
        else:
            conn.execute('''INSERT INTO gunluk_denetim (tarih, denetmen, depo_durum, depo_not, tadim_durum, tadim_not, benmari_durum, benmari_not, numune_durum, hijyen_durum, ufak_sorunlar) VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (tarih, denetmen, request.form.get('depo_durum', 'Bekliyor'), request.form.get('depo_not', ''), request.form.get('tadim_durum', 'Bekliyor'), request.form.get('tadim_not', ''), request.form.get('benmari_durum', 'Bekliyor'), request.form.get('benmari_not', ''), request.form.get('numune_durum', 'Bekliyor'), request.form.get('hijyen_durum', 'Bekliyor'), request.form.get('ufak_sorunlar', '')))
        conn.commit(); flash("✅ Günlük Saha Denetimi başarıyla kaydedildi.", "success")
        return redirect(url_for('denetim', tarih=tarih))
    kayit = conn.execute("SELECT * FROM gunluk_denetim WHERE tarih=?", (secilen_tarih,)).fetchone(); conn.close()
    return render_template('denetim.html', kayit=kayit, secilen_tarih=secilen_tarih)

@app.route('/kumanya', methods=['GET', 'POST'])
@login_required
@admin_required
def kumanya():
    conn = get_db_connection()
    if request.method == 'POST':
        islem = request.form.get('islem_tipi')
        if islem == 'kumanya_planla':
            tarih = request.form['tarih']; kulup = request.form['kulup_adi']; kisi = int(request.form['kisi_sayisi']); tip = request.form['kumanya_tipi']
            conn.execute("INSERT INTO kumanya (tarih, kulup_adi, kisi_sayisi, kumanya_tipi, icerik_detay, durum) VALUES (?,?,?,?,?,?)", (tarih, kulup, kisi, tip, "Reçete Bekliyor...", "Planlandı"))
            conn.execute("INSERT INTO ajanda (tarih, not_icerik, renk_kodu, url) VALUES (?,?,?,?)", (tarih, f"🎒 PLAN: {kulup} ({kisi} Kişi - {tip})", '#eab308', '/kumanya'))
            conn.commit(); flash("Kumanya planlandı.", "success")
        elif islem == 'kumanya_recete_ekle':
            k_id = request.form['kumanya_id']
            kisi = conn.execute("SELECT kisi_sayisi FROM kumanya WHERE id=?", (k_id,)).fetchone()['kisi_sayisi']
            conn.execute("DELETE FROM kumanya_malzemeler WHERE kumanya_id=?", (k_id,))
            kutu_ozetleri = []
            for i in range(1, 6):
                kutu_adi = request.form.get(f'kutu_adi_{k_id}_{i}')
                if kutu_adi and kutu_adi.strip():
                    malzemeler = request.form.getlist(f'malzeme_{k_id}_{i}[]'); miktarlar = request.form.getlist(f'miktar_{k_id}_{i}[]'); kutu_malzeme_ozeti = []
                    for m_idx, malzeme in enumerate(malzemeler):
                        if malzeme and m_idx < len(miktarlar) and miktarlar[m_idx]:
                            try:
                                form_mik = float(miktarlar[m_idx])
                                mevcut = conn.execute("SELECT id, birim FROM depo WHERE urun_adi COLLATE NOCASE = ?", (malzeme,)).fetchone()
                                if mevcut:
                                    oran = 1000 if mevcut['birim'].upper() in ['KG', 'LT'] else 1
                                    toplam_dusulecek = round((form_mik * kisi) / oran, 2)
                                    conn.execute("INSERT INTO kumanya_malzemeler (kumanya_id, malzeme_adi, miktar, birim) VALUES (?,?,?,?)", (k_id, malzeme, toplam_dusulecek, mevcut['birim']))
                                    td_str = int(toplam_dusulecek) if toplam_dusulecek.is_integer() else toplam_dusulecek
                                    kutu_malzeme_ozeti.append(f"{malzeme} ({td_str} {mevcut['birim']})")
                            except ValueError: pass
                    if kutu_malzeme_ozeti: kutu_ozetleri.append(f"<strong class='text-gray-800'>{kutu_adi}</strong> <span class='text-gray-500 text-xs block mb-2'>↳ {', '.join(kutu_malzeme_ozeti)}</span>")
                    else: kutu_ozetleri.append(f"<strong class='text-gray-800'>{kutu_adi}</strong> (İçerik Girilmedi)<br>")
            conn.execute("UPDATE kumanya SET icerik_detay=?, durum='Hazırlanacak' WHERE id=?", ("".join(kutu_ozetleri) if kutu_ozetleri else "İçerik Belirtilmedi", k_id))
            conn.commit(); flash("Kumanya reçetesi oluşturuldu.", "success")
        elif islem == 'durum_guncelle':
            yeni_durum = request.form['yeni_durum']; k_id = request.form['kumanya_id']
            if yeni_durum in ['Hazırlandı', 'Teslim Edildi']:
                k = conn.execute("SELECT * FROM kumanya WHERE id=?", (k_id,)).fetchone()
                malzemeler = conn.execute("SELECT * FROM kumanya_malzemeler WHERE kumanya_id=?", (k_id,)).fetchall()
                yeterli = True; hata = []
                for m in malzemeler:
                    cikan = conn.execute('SELECT SUM(miktar) as c FROM depo_cikis WHERE tarih=? AND ogun="Kumanya" AND malzeme_adi COLLATE NOCASE = ?', (k['tarih'], m['malzeme_adi'])).fetchone()['c'] or 0
                    if cikan < m['miktar'] - 0.05: yeterli = False; hata.append(m['malzeme_adi'])
                if not yeterli: flash(f"HATA: {', '.join(hata)} eksik çıkılmış!", "error")
                else: conn.execute("UPDATE kumanya SET durum=? WHERE id=?", (yeni_durum, k_id)); conn.commit()
            else: conn.execute("UPDATE kumanya SET durum=? WHERE id=?", (yeni_durum, k_id)); conn.commit()
        elif islem == 'kumanya_sil':
            k_id = request.form['kumanya_id']
            k = conn.execute("SELECT * FROM kumanya WHERE id=?", (k_id,)).fetchone()
            if k: conn.execute("DELETE FROM ajanda WHERE not_icerik=?", (f"🎒 PLAN: {k['kulup_adi']} ({k['kisi_sayisi']} Kişi - {k['kumanya_tipi']})",))
            conn.execute("DELETE FROM kumanya WHERE id=?", (k_id,)); conn.execute("DELETE FROM kumanya_malzemeler WHERE kumanya_id=?", (k_id,)); conn.commit()
        return redirect(url_for('kumanya', tarih=request.form.get('tarih')))

    secilen_tarih = request.args.get('tarih', date.today().strftime('%Y-%m-%d'))
    gelecek_planlar = conn.execute("SELECT * FROM kumanya WHERE tarih >= ? ORDER BY tarih ASC", (date.today().strftime('%Y-%m-%d'),)).fetchall()
    gunun_kumanyalari = conn.execute("SELECT * FROM kumanya WHERE tarih=? ORDER BY id DESC", (secilen_tarih,)).fetchall()
    cikislar_db = conn.execute('SELECT malzeme_adi, SUM(miktar) as toplam_cikan FROM depo_cikis WHERE tarih=? AND ogun="Kumanya" GROUP BY malzeme_adi', (secilen_tarih,)).fetchall()
    gercek_cikislar = {c['malzeme_adi']: c['toplam_cikan'] for c in cikislar_db}
    gunluk_ihtiyac = {}
    for k in gunun_kumanyalari:
        for m in conn.execute("SELECT * FROM kumanya_malzemeler WHERE kumanya_id=?", (k['id'],)).fetchall():
            adi = m['malzeme_adi']
            if adi not in gunluk_ihtiyac: gunluk_ihtiyac[adi] = {'gereken': 0, 'cikan': gercek_cikislar.get(adi, 0), 'birim': m['birim']}
            gunluk_ihtiyac[adi]['gereken'] += m['miktar']
    depo_urunler = conn.execute("SELECT urun_adi FROM depo ORDER BY urun_adi").fetchall(); conn.close()
    return render_template('kumanya.html', planlar=gelecek_planlar, kumanyalar=gunun_kumanyalari, depo_urunler=depo_urunler, secilen_tarih=secilen_tarih, gunluk_ihtiyac=gunluk_ihtiyac)


@app.route('/satis', methods=['GET', 'POST'])
@login_required
@admin_required
def satis():
    conn = get_db_connection()
    secilen_tarih = request.args.get('tarih', date.today().strftime('%Y-%m-%d'))

    f_kampus = request.args.get('f_kampus', 'Tümü')
    f_fiyat = request.args.get('f_fiyat', 'Tümü')
    f_tahsilat = request.args.get('f_tahsilat', 'Tümü')
    mevcut_ay = secilen_tarih[:7]

    if request.method == 'POST':
        islem = request.form.get('islem_tipi')

        if islem == 'dis_paydas_ekle':
            ogünler = request.form.getlist('ogun[]')
            tarifeler = request.form.getlist('tarife[]')
            kişiler = request.form.getlist('kisi_sayisi[]')
            tarihler = request.form.getlist('tarih_satir[]')

            t_durumu = request.form.get('tahsilat_durumu', '✅ Ödendi (Nakit/Pos)')
            f_durumu = request.form.get('firma_odeme_durumu', '⏳ Hak Edişe Eklenecek (Bekliyor)')
            bagli_id = request.form.get('bagli_etkinlik_id')
            islem_tarihi = request.form.get('islem_tarihi')
            grup_adi = request.form.get('adi')
            kampus = request.form.get('kampus', 'Davutpaşa Merkez')
            turu = request.form.get('turu', 'Dış Paydaş')

            genel_toplam_tutar = 0
            genel_toplam_kisi = 0
            detay_log = []

            for i in range(len(kişiler)):
                if not kişiler[i] or int(kişiler[i]) <= 0: continue

                t_val = float(tarifeler[i])
                kisi_sayisi = int(kişiler[i])
                mevcut_ogun = ogünler[i]
                mevcut_tarih = tarihler[i] if tarihler and i < len(tarihler) else islem_tarihi

                genel_toplam_tutar += kisi_sayisi * t_val
                genel_toplam_kisi += kisi_sayisi
                detay_log.append(f"{mevcut_tarih[-5:]} {mevcut_ogun[:3]}: {kisi_sayisi}K ({int(t_val)}₺)")

            if genel_toplam_kisi > 0:
                yazilacak_ogun = ogünler[0] if len(detay_log) == 1 else "Karma Öğün"
                yazilacak_tarife = f"{float(tarifeler[0])} TL" if len(detay_log) == 1 else "Karma Paket"
                detay_str = " | ".join(detay_log)

                conn.execute('''INSERT INTO dis_paydas_satis (tarih, ogun, turu, adi, kisi_sayisi, tarife, odeme_yontemi, toplam_tutar, kampus, tahsilat_durumu, firma_odeme_durumu) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                             (islem_tarihi, yazilacak_ogun, turu, grup_adi, genel_toplam_kisi, yazilacak_tarife, detay_str, genel_toplam_tutar, kampus, t_durumu, f_durumu))

            if bagli_id:
                conn.execute("UPDATE etkinlikler SET durum='Satışa Aktarıldı' WHERE id=?", (bagli_id,))
                ajanda_kayit = conn.execute("SELECT id FROM ajanda WHERE not_icerik LIKE ?", (f"%{grup_adi}%",)).fetchone()
                if ajanda_kayit:
                    conn.execute("UPDATE ajanda SET not_icerik=? WHERE id=?", (f"✅ GERÇEKLEŞTİ: {grup_adi}", ajanda_kayit['id']))
                    conn.execute("INSERT INTO ajanda_tamamlananlar (ajanda_id, tarih) VALUES (?,?)", (ajanda_kayit['id'], islem_tarihi))

            conn.commit(); flash(f"✅ {grup_adi} (Top: {genel_toplam_kisi} Kişi, {genel_toplam_tutar} ₺) tek kalem olarak işlendi.", "success")

        elif islem == 'etkinlik_ekle':
            tarih = request.form['tarih']
            kisi = request.form['kisi_sayisi']
            adi = request.form['adi']
            conn.execute("INSERT INTO etkinlikler (tarih, bitis_tarihi, adi, kisi_sayisi, notlar, kampus, ogun) VALUES (?,?,?,?,?,?,?)",
                         (tarih, request.form.get('bitis_tarihi'), adi, int(kisi), request.form.get('notlar', ''), request.form.get('kampus', 'Davutpaşa Merkez'), request.form.get('ogun', 'Öğle Yemeği')))
            conn.execute("INSERT INTO ajanda (tarih, not_icerik, renk_kodu, url, bitis_tarihi, periyot) VALUES (?,?,?,?,?,?)",
                         (tarih, f"🎉 ETKİNLİK: {adi} ({kisi} Kişi)", '#10b981', '/satis', request.form.get('bitis_tarihi', ''), 'Günlük' if request.form.get('bitis_tarihi') else 'Tek Seferlik'))
            conn.commit(); flash("🎉 Etkinlik planlandı ve Ajandaya eklendi.", "success")

        elif islem == 'durum_guncelle':
            satis_id = request.form.get('satis_id')
            t_dur = request.form.get('tahsilat_durumu')
            f_dur = request.form.get('firma_odeme_durumu')

            if t_dur and f_dur:
                conn.execute("UPDATE dis_paydas_satis SET tahsilat_durumu=?, firma_odeme_durumu=? WHERE id=?", (t_dur, f_dur, satis_id))
            else:
                hedef = request.form.get('hedef')
                yeni_durum = request.form.get('yeni_durum')
                if hedef == 'tahsilat': conn.execute("UPDATE dis_paydas_satis SET tahsilat_durumu=? WHERE id=?", (yeni_durum, satis_id))
                elif hedef == 'firma': conn.execute("UPDATE dis_paydas_satis SET firma_odeme_durumu=? WHERE id=?", (yeni_durum, satis_id))
            conn.commit(); flash("✅ Ödeme durumları başarıyla güncellendi.", "success")

        elif islem == 'dis_paydas_sil':
            conn.execute('DELETE FROM dis_paydas_satis WHERE id=?', (request.form['kayit_id'],)); conn.commit()

            # Eğer istek arka plandan (AJAX) geldiyse sayfa yenileme, JSON dön
            if request.form.get('ajax') == 'true':
                from flask import jsonify
                return jsonify({"status": "success"})

            flash("Kayıt silindi.", "success")

        elif islem == 'etkinlik_sil':
            e_id = request.form['etkinlik_id']
            etk = conn.execute("SELECT * FROM etkinlikler WHERE id=?", (e_id,)).fetchone()
            if etk: conn.execute("DELETE FROM ajanda WHERE not_icerik LIKE ?", (f"%{etk['adi']}%",))
            conn.execute("DELETE FROM etkinlikler WHERE id=?", (e_id,)); conn.commit(); flash("Etkinlik iptal edildi ve Ajandadan silindi.", "success")

        return redirect(url_for('satis', tarih=request.form.get('islem_tarihi', secilen_tarih), f_kampus=f_kampus, f_fiyat=f_fiyat, f_tahsilat=f_tahsilat))

    # --- 1. SEKME MANTIĞI: Sadece Kapanmamış Açık Hesaplar (Tarih Bağımsız) ---
    dis_paydaslar = conn.execute("""
        SELECT * FROM dis_paydas_satis
        WHERE NOT (
            (tahsilat_durumu LIKE '✅%' OR tahsilat_durumu LIKE '🎁%')
            AND
            (firma_odeme_durumu LIKE '✅%' OR firma_odeme_durumu LIKE '❌%')
        )
        ORDER BY tarih DESC, id DESC
    """).fetchall()

    etkinlikler = conn.execute('SELECT * FROM etkinlikler ORDER BY tarih ASC').fetchall()

    # Bilanço Sorgusu: Sadece ödemesi alınmış VE firmaya yatırılmış/kendi stoğumuz olanları getir
    query = "SELECT * FROM dis_paydas_satis WHERE tarih LIKE ?"
    params = [f"{mevcut_ay}%"]

    if f_tahsilat == 'Tümü':
        # Varsayılan görünüm: Sadece tam kapanmış hesaplar (Kapatılmamışlar bilançoya düşmesin)
        query += " AND (tahsilat_durumu LIKE '✅%' OR tahsilat_durumu LIKE '🎁%') AND (firma_odeme_durumu LIKE '✅%' OR firma_odeme_durumu LIKE '❌%')"
    elif f_tahsilat == 'Vakıf':
        query += " AND tahsilat_durumu LIKE ?"; params.append("%Vakıf%")
    elif f_tahsilat == 'Ödendi':
        query += " AND tahsilat_durumu LIKE ?"; params.append("%Ödendi%")
    elif f_tahsilat == 'Bekliyor':
        query += " AND tahsilat_durumu LIKE ? AND tahsilat_durumu NOT LIKE ?"; params.append("%Bekliyor%"); params.append("%Vakıf%")

    if f_kampus != 'Tümü': query += " AND kampus = ?"; params.append(f_kampus)
    if f_fiyat != 'Tümü': query += " AND tarife = ?"; params.append(f"{f_fiyat} TL")

    gelenler_aylik = conn.execute(query, params).fetchall()
    conn.close()
    return render_template('satis.html', dis_paydaslar=dis_paydaslar, etkinlikler=etkinlikler, gelenler_aylik=gelenler_aylik, secilen_tarih=secilen_tarih, f_kampus=f_kampus, f_fiyat=f_fiyat, f_tahsilat=f_tahsilat)
@app.route('/et-isleme', methods=['GET', 'POST'])
@login_required
@admin_required
def et_isleme():
    conn = get_db_connection()

    try: conn.execute("ALTER TABLE et_isleme_log ADD COLUMN lot_id INTEGER")
    except: pass

    if request.method == 'POST':
        lot_id = request.form.get('kaynak_lot_id')

        def guvenli_float(deger):
            try: return float(deger) if deger else 0.0
            except: return 0.0

        harcanan = guvenli_float(request.form.get('harcanan_miktar'))
        kiymalik = guvenli_float(request.form.get('kiymalik'))
        kusbasi = guvenli_float(request.form.get('kusbasi'))
        sotelik = guvenli_float(request.form.get('sotelik'))
        kemik = guvenli_float(request.form.get('kemik'))

        lot = conn.execute("SELECT * FROM stok_lotlari WHERE id=?", (lot_id,)).fetchone()

        if not lot or lot['kalan_miktar'] < harcanan:
            flash("HATA: Seçilen karkasta belirttiğiniz kadar et bulunmuyor!", "error")
            return redirect(url_for('et_isleme'))

        kaynak_urun_adi = lot['urun_adi']
        damga_no = lot['lot_damga_no'] if lot['lot_damga_no'] else f"LOT-{lot_id}"

        conn.execute("UPDATE stok_lotlari SET kalan_miktar = kalan_miktar - ? WHERE id=?", (harcanan, lot_id))
        conn.execute('UPDATE depo SET miktar = miktar - ? WHERE urun_adi COLLATE NOCASE = ?', (harcanan, kaynak_urun_adi))

        kategori = "🥩 Kırmızı Et"

        for urun, mik in [('Dana Kıyma', kiymalik), ('Dana Kuşbaşı', kusbasi), ('Dana Sote', sotelik), ('Kemik / Fire', kemik)]:
            if mik > 0:
                mevcut = conn.execute('SELECT id FROM depo WHERE urun_adi COLLATE NOCASE = ?', (urun,)).fetchone()
                if mevcut: conn.execute('UPDATE depo SET miktar = miktar + ? WHERE id=?', (mik, mevcut['id']))
                else: conn.execute('INSERT INTO depo (kategori, urun_adi, miktar, birim) VALUES (?,?,?,?)', (kategori, urun, mik, 'KG'))

        kaynak_detay = f"{kaynak_urun_adi} [Damga: {damga_no}]"
        conn.execute('INSERT INTO et_isleme_log (kaynak, harcanan, detay, lot_id) VALUES (?,?,?,?)',
                     (kaynak_detay, harcanan, f"Kıyma: {kiymalik} | Kuşbaşı: {kusbasi} | Sote: {sotelik} | Kemik/Fire: {kemik}", lot_id))

        conn.commit(); flash(f"✅ {damga_no} damgalı karkas başarıyla parçalandı.", "success")
        return redirect(url_for('et_isleme'))

    et_stoklari = conn.execute("""
        SELECT sl.*, d.kategori
        FROM stok_lotlari sl
        JOIN depo d ON sl.urun_adi = d.urun_adi
        WHERE d.kategori IN ('Kırmızı Et', '🥩 Kırmızı Et') AND sl.kalan_miktar > 0
        ORDER BY sl.tarih ASC
    """).fetchall()

    gecmis_islemler = conn.execute("SELECT * FROM et_isleme_log ORDER BY id DESC LIMIT 5").fetchall()

    son_sevkiyatlar = conn.execute("""
        SELECT sl.* FROM stok_lotlari sl
        JOIN depo d ON sl.urun_adi = d.urun_adi
        WHERE d.kategori IN ('Kırmızı Et', '🥩 Kırmızı Et')
        ORDER BY sl.id DESC LIMIT 5
    """).fetchall()

    lot_karneleri = []
    for l in son_sevkiyatlar:
        loglar = conn.execute("SELECT harcanan, detay FROM et_isleme_log WHERE lot_id=?", (l['id'],)).fetchall()
        t_harcanan = 0; t_kiyma = 0; t_kusbasi = 0; t_sote = 0; t_kemik = 0
        for lg in loglar:
            t_harcanan += lg['harcanan']
            d = lg['detay']
            try: t_kiyma += float(re.search(r'Kıyma:\s*([\d\.]+)', d).group(1))
            except: pass
            try: t_kusbasi += float(re.search(r'Kuşbaşı:\s*([\d\.]+)', d).group(1))
            except: pass
            try: t_sote += float(re.search(r'Sote:\s*([\d\.]+)', d).group(1))
            except: pass
            try: t_kemik += float(re.search(r'Kemik/Fire:\s*([\d\.]+)', d).group(1))
            except: pass

        lot_karneleri.append({
            'id': l['id'], 'tarih': l['tarih'][:10], 'damga': l['lot_damga_no'] if l['lot_damga_no'] else 'DAMGASIZ',
            'urun': l['urun_adi'], 'baslangic': l['baslangic_miktar'], 'kalan': l['kalan_miktar'],
            'islenen': t_harcanan, 'kiyma': t_kiyma, 'kusbasi': t_kusbasi, 'sote': t_sote, 'kemik': t_kemik,
            'fire_oran': round((t_kemik / t_harcanan * 100), 1) if t_harcanan > 0 else 0
        })

    conn.close()
    return render_template('et_isleme.html', et_stoklari=et_stoklari, gecmis_islemler=gecmis_islemler, lot_karneleri=lot_karneleri)

@app.route('/yedek-uretim', methods=['GET', 'POST'])
@login_required
@admin_required
def yedek_uretim():
    conn = get_db_connection()
    bugun = date.today().strftime('%Y-%m-%d')
    if request.method == 'POST':
        islem = request.form.get('islem_tipi')
        if islem == 'yedek_ekle':
            y_adi = request.form['yemek_adi']; pors = int(request.form['porsiyon']); islem_tarihi = request.form.get('islem_tarihi', bugun)
            recete = conn.execute('SELECT * FROM receteler WHERE yemek_adi = ?', (y_adi,)).fetchall()
            if not recete: flash(f"HATA: '{y_adi}' reçetesi yok!", "error")
            else:
                yeterli_stok = True; dus_malz = []; hata = []
                for r in recete:
                    depo_malz = conn.execute("SELECT * FROM depo WHERE urun_adi COLLATE NOCASE = ?", (r['malzeme_adi'],)).fetchone()
                    birim = depo_malz['birim'] if depo_malz else 'KG'; oran = 1000 if birim.upper() in ['KG', 'LT'] else 1
                    gereken = (r['miktar'] * pors) / oran
                    if not depo_malz or depo_malz['miktar'] < gereken: hata.append(f"'{r['malzeme_adi']}'"); yeterli_stok = False
                    else: dus_malz.append({'adi': r['malzeme_adi'], 'dusulecek': gereken, 'birim': birim})
                if yeterli_stok:
                    for dm in dus_malz:
                        conn.execute('UPDATE depo SET miktar = miktar - ? WHERE urun_adi COLLATE NOCASE = ?', (dm['dusulecek'], dm['adi']))
                        conn.execute("INSERT INTO depo_cikis (tarih, ogun, yemek_adi, malzeme_adi, miktar, birim, onay_durumu) VALUES (?,?,?,?,?,?,'Onaylandı')", (islem_tarihi, 'Diğer', f"DONDURUCU İMALAT: {y_adi}", dm['adi'], dm['dusulecek'], dm['birim']))
                    mevcut = conn.execute('SELECT id FROM yedek_stok WHERE yemek_adi=?', (y_adi,)).fetchone()
                    if mevcut: conn.execute('UPDATE yedek_stok SET porsiyon = porsiyon + ? WHERE id=?', (pors, mevcut['id']))
                    else: conn.execute('INSERT INTO yedek_stok (yemek_adi, porsiyon) VALUES (?,?)', (y_adi, pors))
                    flash(f"Başarılı! Stoktan düşüldü ve dondurucuya aktarıldı.", "success")
                else: flash("STOK EKSİK: " + " | ".join(hata), "error")
            conn.commit()
        elif islem == 'yedek_dus':
            conn.execute('UPDATE yedek_stok SET porsiyon = porsiyon - ? WHERE yemek_adi=?', (int(request.form['dusulecek_porsiyon']), request.form['yemek_adi'])); conn.commit(); flash("Kullanım depodan başarıyla düşüldü.", "success")
        elif islem == 'yedek_sil':
            conn.execute('DELETE FROM yedek_stok WHERE id=?', (request.form['yedek_id'],)); conn.commit()
        return redirect(url_for('yedek_uretim'))
    yedekler = conn.execute('SELECT * FROM yedek_stok ORDER BY porsiyon DESC').fetchall()
    tum_yemekler = conn.execute('SELECT DISTINCT yemek_adi FROM receteler ORDER BY yemek_adi').fetchall(); conn.close()
    return render_template('yedek_uretim.html', yedekler=yedekler, tum_yemekler=tum_yemekler, bugun=bugun)

@app.route('/ihtar-tutanak', methods=['GET', 'POST'])
@login_required
@admin_required
def ihtar_tutanak():
    conn = get_db_connection()
    bugun = date.today().strftime('%Y-%m-%d')

    if request.method == 'POST':
        islem = request.form.get('islem_tipi')

        if islem == 'talep_ekle':
            firma = request.form['firma_adi']
            konu = request.form['konu']
            detay = request.form['detay']
            h_tarih = request.form.get('hatirlat_tarih', '')

            conn.execute("INSERT INTO firma_talepleri (tarih, firma_adi, konu, detay, hatirlat_tarih) VALUES (?,?,?,?,?)",
                         (bugun, firma, konu, detay, h_tarih))

            if h_tarih:
                conn.execute("INSERT INTO ajanda (tarih, not_icerik, renk_kodu, url, atanan_kisi) VALUES (?, ?, ?, ?, ?)",
                             (h_tarih, f"🔔 TALEP TAKİBİ: {firma} - {konu}", '#8b5cf6', '/ihtar-tutanak', session['kullanici_adi']))

            flash(f"{firma} firmasına iletilen talep kaydedildi.", "success")

        elif islem == 'talep_durum_guncelle':
            conn.execute("UPDATE firma_talepleri SET durum=? WHERE id=?", (request.form['yeni_durum'], request.form['talep_id']))
            flash("Talep durumu güncellendi.", "success")

        elif islem == 'talep_sil':
            conn.execute("DELETE FROM firma_talepleri WHERE id=?", (request.form['talep_id'],))
            flash("Talep kaydı silindi.", "success")

        elif islem == 'ihtar_ekle':
            firma = request.form['firma_adi']; konu = request.form['konu']; ihtar_tarihi = request.form['ihtar_tarihi']
            son_tarih = get_next_workday(request.form['son_tarih'])
            conn.execute("INSERT INTO ihtarlar (firma_adi, konu, ihtar_tarihi, son_tarih) VALUES (?,?,?,?)", (firma, konu, ihtar_tarihi, son_tarih))
            conn.execute("INSERT INTO ajanda (tarih, not_icerik, renk_kodu, url) VALUES (?,?,?,?)", (son_tarih, f"⏳ İHTAR BİTİŞİ: {firma} (Konu: {konu})", '#ef4444', '/ihtar-tutanak'))
            flash("İhtar kaydedildi. (Termin tarihi ajandaya eklendi)", "success")

        elif islem == 'ihtar_cozuldu':
            conn.execute("UPDATE ihtarlar SET durum='Çözüldü' WHERE id=?", (request.form['ihtar_id'],))
            flash("İhtar 'Çözüldü' olarak işaretlendi.", "success")

        elif islem == 'ihtar_sil':
            i_id = request.form['ihtar_id']
            ihtar = conn.execute("SELECT * FROM ihtarlar WHERE id=?", (i_id,)).fetchone()
            if ihtar: conn.execute("DELETE FROM ajanda WHERE not_icerik=?", (f"⏳ İHTAR BİTİŞİ: {ihtar['firma_adi']} (Konu: {ihtar['konu']})",))
            conn.execute("DELETE FROM ihtarlar WHERE id=?", (i_id,));
            flash("İhtar ve ajanda bildirimi silindi.", "success")

        elif islem == 'tutanak_ekle':
            bagli_id = request.form.get('bagli_ihtar_id')
            conn.execute("INSERT INTO tutanaklar (tarih, yer, firma_adi, konu, detay, bagli_ihtar_id) VALUES (?,?,?,?,?,?)",
                         (request.form['tarih'], request.form['yer'], request.form['firma_adi'], request.form['konu'], request.form['detay'], bagli_id))

            if bagli_id:
                conn.execute("UPDATE ihtarlar SET durum='Tutanak Tutuldu' WHERE id=?", (bagli_id,))

            flash("Tutanak başarıyla işlendi.", "success")

        elif islem == 'tutanak_sil':
            conn.execute("DELETE FROM tutanaklar WHERE id=?", (request.form['tutanak_id'],))
            flash("Tutanak kaydı silindi.", "success")

        conn.commit()
        return redirect(url_for('ihtar_tutanak'))

    talepler = conn.execute("SELECT * FROM firma_talepleri ORDER BY durum DESC, tarih DESC").fetchall()
    ihtarlar = conn.execute("SELECT * FROM ihtarlar ORDER BY son_tarih ASC").fetchall()
    tutanaklar = conn.execute("SELECT * FROM tutanaklar ORDER BY tarih DESC").fetchall()
    conn.close()

    return render_template('ihtar_tutanak.html', talepler=talepler, ihtarlar=ihtarlar, tutanaklar=tutanaklar, bugun=bugun)

@app.route('/dis-hizmetler', methods=['GET', 'POST'])
@login_required
@admin_required
def dis_hizmetler():
    conn = get_db_connection()
    bugun = date.today().strftime('%Y-%m-%d')
    if request.method == 'POST':
        islem = request.form.get('islem_tipi')
        if islem == 'hizmet_ekle':
            tarih = request.form['tarih']; firma = request.form['firma_adi']; turu = request.form['hizmet_turu']; notlar = request.form['notlar']
            conn.execute("INSERT INTO dis_hizmetler (tarih, firma_adi, hizmet_turu, notlar) VALUES (?,?,?,?)", (tarih, firma, turu, notlar))
            conn.execute("INSERT INTO ajanda (tarih, not_icerik, renk_kodu, url) VALUES (?,?,?,?)", (tarih, f"🎪 {firma} - {turu}", '#0f766e', '/dis-hizmetler'))
            conn.commit(); flash("Dış hizmet başarıyla planlandı ve Ajandaya işlendi.", "success")
        elif islem == 'belge_guncelle':
            h_id = request.form['hizmet_id']; hij = request.form['hijyen_belgesi']; tar = request.form['tarim_belgesi']; izin = request.form['ytu_izni']
            conn.execute("UPDATE dis_hizmetler SET hijyen_belgesi=?, tarim_belgesi=?, ytu_izni=? WHERE id=?", (hij, tar, izin, h_id)); conn.commit()
        elif islem == 'hizmet_sil':
            h_id = request.form['hizmet_id']
            h = conn.execute("SELECT * FROM dis_hizmetler WHERE id=?", (h_id,)).fetchone()
            if h: conn.execute("DELETE FROM ajanda WHERE not_icerik=?", (f"🎪 {h['firma_adi']} - {h['hizmet_turu']}",))
            conn.execute("DELETE FROM dis_hizmetler WHERE id=?", (h_id,)); conn.commit()
        return redirect(url_for('dis_hizmetler'))
    hizmetler = conn.execute("SELECT * FROM dis_hizmetler ORDER BY tarih DESC").fetchall(); conn.close()
    return render_template('dis_hizmetler.html', hizmetler=hizmetler, bugun=bugun)

@app.route('/periyodik-bakim', methods=['GET', 'POST'])
@login_required
@admin_required
def periyodik_bakim():
    conn = get_db_connection()
    bugun = date.today()
    secilen_yil = request.args.get('yil', str(bugun.year))
    secilen_ay = request.args.get('ay', str(bugun.month))

    if request.method == 'POST':
        islem = request.form.get('islem_tipi')
        if islem == 'bakim_ekle':
            ekipman = request.form['ekipman_adi']; aylar_str = ",".join(request.form.getlist('aylar[]'))
            conn.execute("INSERT INTO periyodik_bakim (ekipman_adi, bakim_aylari) VALUES (?,?)", (ekipman, aylar_str)); conn.commit()
            flash(f"'{ekipman}' çizelgeye eklendi.", "success")

        elif islem == 'bakim_sil':
            b_id = request.form['bakim_id']
            conn.execute("DELETE FROM periyodik_bakim WHERE id=?", (b_id,))
            conn.execute("DELETE FROM periyodik_bakim_log WHERE bakim_id=?", (b_id,)); conn.commit()
            flash("Bakım kaydı tamamen silindi.", "success")

        elif islem == 'bakim_yapildi':
            conn.execute("INSERT INTO periyodik_bakim_log (bakim_id, yil, ay) VALUES (?,?,?)", (request.form['bakim_id'], request.form['yil'], request.form['ay'])); conn.commit()
            flash("Bakım evrakı alındı ve sisteme işlendi.", "success")

        elif islem == 'bakim_geri_al':
            conn.execute("DELETE FROM periyodik_bakim_log WHERE bakim_id=? AND yil=? AND ay=?", (request.form['bakim_id'], request.form['yil'], request.form['ay'])); conn.commit()
            flash("İşlem geri alındı.", "success")

        return redirect(url_for('periyodik_bakim', ay=request.form.get('ay', secilen_ay), yil=request.form.get('yil', secilen_yil)))

    bakimlar = conn.execute("SELECT * FROM periyodik_bakim ORDER BY id ASC").fetchall()
    logs = conn.execute("SELECT * FROM periyodik_bakim_log WHERE yil=?", (secilen_yil,)).fetchall()
    log_dict = {(str(l['bakim_id']), str(l['ay'])): True for l in logs}

    aylik_bakimlar = []
    for b in bakimlar:
        aylar = b['bakim_aylari'].split(',') if b['bakim_aylari'] else []
        if secilen_ay in aylar:
            durum = 'Yapıldı' if (str(b['id']), secilen_ay) in log_dict else 'Bekliyor'
            aylik_bakimlar.append({'id': b['id'], 'ekipman_adi': b['ekipman_adi'], 'durum': durum})

    matris = {}
    for b in bakimlar:
        matris[b['id']] = {'adi': b['ekipman_adi'], 'aylar': {}}
        gerekli_aylar = b['bakim_aylari'].split(',') if b['bakim_aylari'] else []
        for a_no in range(1, 13):
            a_str = str(a_no)
            if a_str in gerekli_aylar:
                if (str(b['id']), a_str) in log_dict: matris[b['id']]['aylar'][a_str] = 'Yapıldı'
                else: matris[b['id']]['aylar'][a_str] = 'Bekliyor'
            else: matris[b['id']]['aylar'][a_str] = 'Yok'

    conn.close()
    ay_isimleri = { '1': 'Ocak', '2': 'Şubat', '3': 'Mart', '4': 'Nisan', '5': 'Mayıs', '6': 'Haziran', '7': 'Temmuz', '8': 'Ağustos', '9': 'Eylül', '10': 'Ekim', '11': 'Kasım', '12': 'Aralık' }

    return render_template('periyodik_bakim.html', bakimlar=bakimlar, aylik_bakimlar=aylik_bakimlar, matris=matris, ay_isimleri=ay_isimleri, secilen_ay=secilen_ay, secilen_yil=secilen_yil)

@app.route('/raporlar')
@login_required
@admin_required
def raporlar():
    conn = get_db_connection()
    istatistikler_raw = conn.execute('SELECT * FROM gunluk_istatistik ORDER BY tarih DESC').fetchall()

    rapor_listesi = []
    for ist in istatistikler_raw:
        ist_dict = dict(ist)
        ist_dict['uretimler'] = conn.execute("SELECT * FROM uretim WHERE tarih=? AND ogun=?", (ist['tarih'], ist['ogun'])).fetchall()
        ist_dict['toplam_gelen'] = ist['personel_sayisi'] + ist['ogrenci_sayisi']
        rapor_listesi.append(ist_dict)

    # FİRE VERİLERİ (Grafik ve Tablo İçin)
    fire_kategori_verisi = conn.execute("SELECT kategori, SUM(miktar) as toplam_miktar, birim FROM fire_kayitlari GROUP BY kategori, birim").fetchall()
    fire_detay_verisi = conn.execute("SELECT urun_adi, kategori, miktar, birim, tarih, kullanici, aciklama FROM fire_kayitlari ORDER BY tarih DESC LIMIT 100").fetchall()

    conn.close()
    return render_template('raporlar.html',
                           raporlar=rapor_listesi,
                           fire_kategori_verisi=[dict(row) for row in fire_kategori_verisi],
                           fire_detay_verisi=[dict(row) for row in fire_detay_verisi])

@app.route('/uretim-fire-ekle', methods=['POST'])
@login_required
def uretim_fire_ekle():
    urun_adi = request.form.get('urun_adi')
    miktar = request.form.get('miktar')
    aciklama = request.form.get('aciklama', '')

    try: miktar = float(miktar)
    except:
        flash('Geçersiz miktar.', 'error')
        return redirect(url_for('uretim'))

    conn = get_db_connection()
    try:
        # 1. Ana depodan ürünün kategorisi ve birimini bul
        d_m = conn.execute('SELECT kategori, birim FROM depo WHERE urun_adi COLLATE NOCASE = ?', (urun_adi,)).fetchone()
        birim = d_m['birim'] if d_m else 'KG'
        kategori = d_m['kategori'] if d_m else 'Genel'

        # 2. Raporlar sayfasındaki istatistikler için Fire tablomuza kaydet
        conn.execute('INSERT INTO fire_kayitlari (kategori, urun_adi, miktar, birim, kullanici, aciklama) VALUES (?, ?, ?, ?, ?, ?)',
                  (kategori, urun_adi, miktar, birim, session.get('isim', 'Sistem'), aciklama))

        # 3. SİHİRLİ DOKUNUŞ: Mutfak tezgahındaki stoktan düşmesi için EKSİ değerle çıkış kaydı gir (Ana depoyu etkilemez, sadece mutfağı düşürür)
        bugun = date.today().strftime('%Y-%m-%d')
        conn.execute("INSERT INTO depo_cikis (tarih, ogun, yemek_adi, malzeme_adi, miktar, birim, onay_durumu, aciklama) VALUES (?,?,?,?,?,?,'Onaylandı',?)",
                     (bugun, 'Diğer', 'Zayi/Fire', urun_adi, -miktar, birim, f"Fire: {aciklama}"))

        conn.commit()
        flash(f'🔥 {miktar} {birim} {urun_adi} mutfaktan fire olarak düşüldü.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Hata: {str(e)}', 'error')
    finally:
        conn.close()

    return redirect(url_for('uretim'))
@app.route('/personel', methods=['GET', 'POST'])
@login_required
@admin_required
def personel():
    conn = get_db_connection()
    bugun = date.today().strftime('%Y-%m-%d')

    if request.method == 'POST':
        islem = request.form.get('islem_tipi')

        if islem == 'personel_ekle':
            conn.execute("INSERT INTO personeller (ad_soyad, sicil_no, gorev, mesai_baslangic, mesai_bitis) VALUES (?,?,?,?,?)",
                         (request.form['ad_soyad'], request.form['sicil_no'], request.form['gorev'], request.form['mesai_baslangic'], request.form['mesai_bitis']))
            conn.commit(); flash("Personel başarıyla eklendi.", "success")

        elif islem == 'personel_sil':
            p_id = request.form['personel_id']
            conn.execute("UPDATE personeller SET durum='Pasif' WHERE id=?", (p_id,)); conn.commit()
            flash("Personel pasife alındı (Arşivlendi).", "success")

        elif islem == 'izin_ekle':
            p_id = request.form['personel_id']; bas = request.form['baslangic']; bit = request.form['bitis']; tur = request.form['izin_turu']; notlar = request.form['aciklama']
            
            # 🔥 SİSTEM KALKANI: İzin Çakışma Kontrolü
            cakisma = conn.execute("SELECT id FROM personel_izinler WHERE personel_id=? AND baslangic_tarihi <= ? AND bitis_tarihi >= ?", (p_id, bit, bas)).fetchone()
            
            if cakisma:
                flash("HATA: Bu personel seçilen tarihlerde zaten izinde! Lütfen mükerrer işlem yapmayın.", "error")
            else:
                conn.execute("INSERT INTO personel_izinler (personel_id, baslangic_tarihi, bitis_tarihi, izin_turu, aciklama) VALUES (?,?,?,?,?)", (p_id, bas, bit, tur, notlar))
                p = conn.execute("SELECT ad_soyad FROM personeller WHERE id=?", (p_id,)).fetchone()
                conn.execute("INSERT INTO ajanda (tarih, not_icerik, renk_kodu, url) VALUES (?,?,?,?)", (bas, f"🏖️ İZİNLİ: {p['ad_soyad']} ({tur} Başlangıcı)", '#db2777', '/personel'))
                conn.commit(); flash("İzin başarıyla sisteme işlendi.", "success")

        elif islem == 'izin_erken_bitir':
            izin_id = request.form['izin_id']
            # İzni "dün" bitmiş gibi gösteriyoruz ki "bugün" iş başı yapmış sayılsın!
            dun = (date.today() - timedelta(days=1)).strftime('%Y-%m-%d')
            conn.execute("UPDATE personel_izinler SET bitis_tarihi=? WHERE id=?", (dun, izin_id))
            conn.commit()
            flash("Personelin izni bitirildi ve BUGÜN itibarıyla işbaşı yaptı.", "success")

        elif islem == 'izin_iptal':
            # Yanlışlıkla girilen veya tamamen silinmek istenen izinler için
            izin_id = request.form['izin_id']
            conn.execute("DELETE FROM personel_izinler WHERE id=?", (izin_id,))
            conn.commit()
            flash("Hatalı izin kaydı sistemden tamamen silindi.", "success")
            
        elif islem == 'izin_hakki_guncelle':
            p_id = request.form['personel_id']
            yeni_hak = request.form['izin_hakki']
            conn.execute("UPDATE personeller SET izin_hakki=? WHERE id=?", (yeni_hak, p_id))
            conn.commit(); flash("Personel izin bakiyesi (hakkı) güncellendi.", "success")

        elif islem == 'pdks_senkronize':
            flash("Resmi API entegrasyonu bekleniyor. Lütfen BİDB'den API yetkisi talep ediniz.", "error")

        return redirect(url_for('personel'))

    # İzin haklarını ve kullanılan günleri hesapla
    personeller_raw = conn.execute("SELECT * FROM personeller WHERE durum='Aktif' ORDER BY ad_soyad").fetchall()
    personeller = []
    from datetime import datetime
    
    for p in personeller_raw:
        p_dict = dict(p)
        izinler_p = conn.execute("SELECT baslangic_tarihi, bitis_tarihi FROM personel_izinler WHERE personel_id=? AND izin_turu='Yıllık İzin'", (p['id'],)).fetchall()
        kullanilan = 0
        for iz in izinler_p:
            try:
                b = datetime.strptime(iz['baslangic_tarihi'], '%Y-%m-%d')
                e = datetime.strptime(iz['bitis_tarihi'], '%Y-%m-%d')
                if e >= b: kullanilan += (e - b).days + 1
            except: pass
        
        p_dict['kullanilan_izin'] = kullanilan
        p_dict['izin_hakki'] = p['izin_hakki'] if 'izin_hakki' in p.keys() else 14
        p_dict['kalan_izin'] = p_dict['izin_hakki'] - kullanilan
        personeller.append(p_dict)

    bugun_izinliler_db = conn.execute("SELECT personel_id FROM personel_izinler WHERE baslangic_tarihi <= ? AND bitis_tarihi >= ?", (bugun, bugun)).fetchall()
    izinli_idler = [i['personel_id'] for i in bugun_izinliler_db]
    izinler = conn.execute('''SELECT i.*, p.ad_soyad FROM personel_izinler i JOIN personeller p ON i.personel_id = p.id ORDER BY i.baslangic_tarihi DESC LIMIT 50''').fetchall()
    pdks_kayitlari = conn.execute('''SELECT l.*, p.ad_soyad, p.gorev FROM pdks_log l JOIN personeller p ON l.personel_id = p.id WHERE l.tarih=? ORDER BY l.giris_saati DESC''', (bugun,)).fetchall()

    conn.close()
    return render_template('personel.html', personeller=personeller, izinler=izinler, pdks_kayitlari=pdks_kayitlari, izinli_idler=izinli_idler, bugun=bugun)
@app.route('/api/urun-gecmisi/<int:urun_id>')
@login_required
def urun_gecmisi(urun_id):
    from flask import jsonify
    import re
    try:
        conn = get_db_connection()
        urun = conn.execute("SELECT urun_adi FROM depo WHERE id=?", (urun_id,)).fetchone()

        if not urun:
            conn.close()
            return jsonify({"error": "Ürün bulunamadı"}), 404

        urun_adi = urun['urun_adi']
        tum_hareketler = []

        markalar_db = conn.execute("SELECT marka, SUM(kalan_miktar) as c FROM stok_lotlari WHERE urun_adi COLLATE NOCASE=? AND kalan_miktar > 0 GROUP BY marka", (urun_adi,)).fetchall()
        markalar = [{"marka": m['marka'] if m['marka'] else "Markasız", "miktar": m['c']} for m in markalar_db]

        try:
            girisler = conn.execute("SELECT tarih, kabul_eden, miktar, birim, onay_durumu, notlar, marka FROM mal_kabul_log WHERE urun_adi = ?", (urun_adi,)).fetchall()
            for g in girisler:
                ek_bilgi = f" | {g['notlar']}" if g['notlar'] else ""
                marka_bilgi = f" [{g['marka']}]" if 'marka' in g.keys() and g['marka'] else ""
                tum_hareketler.append({
                    "tarih": g['tarih'], "kisi": g['kabul_eden'], "miktar": g['miktar'],
                    "birim": g['birim'], "tip": "GİRİŞ", "detay": f"{g['onay_durumu']}{marka_bilgi}{ek_bilgi}"
                })
        except Exception as e: print("Mal Kabul Tablosu Hatası:", e)

        try:
            cikislar = conn.execute("SELECT tarih, miktar, birim, yemek_adi, onay_durumu, marka FROM depo_cikis WHERE malzeme_adi = ?", (urun_adi,)).fetchall()
            for c in cikislar:
                marka_bilgi = f" [{c['marka']}]" if 'marka' in c.keys() and c['marka'] else ""
                tum_hareketler.append({
                    "tarih": c['tarih'], "kisi": "Mutfak/Sistem", "miktar": c['miktar'],
                    "birim": c['birim'], "tip": "ÇIKIŞ", "detay": f"{c['onay_durumu']} | {c['yemek_adi']}{marka_bilgi}"
                })
        except Exception as e: print("Depo Çıkış Tablosu Hatası:", e)

        try:
            et_kaynak = conn.execute("SELECT tarih, harcanan, detay, kaynak FROM et_isleme_log WHERE kaynak = ?", (urun_adi,)).fetchall()
            for k in et_kaynak:
                tum_hareketler.append({
                    "tarih": k['tarih'], "kisi": "Et Şefliği", "miktar": k['harcanan'],
                    "birim": "KG", "tip": "ÜRETİM ÇIKIŞI", "detay": f"Üretilenler: {k['detay']}"
                })

            arama = f"%{urun_adi}%"
            et_urun = conn.execute("SELECT tarih, detay, kaynak FROM et_isleme_log WHERE detay LIKE ?", (arama,)).fetchall()
            for u in et_urun:
                tam_detay = str(u['detay'])
                eslesme = re.search(f"{urun_adi}.*?([\d\.]+)", tam_detay, re.IGNORECASE)
                bulunan_miktar = eslesme.group(1) if eslesme else "0.00"
                tum_hareketler.append({
                    "tarih": u['tarih'], "kisi": "Et Şefliği", "miktar": bulunan_miktar,
                    "birim": "KG", "tip": "ÜRETİM (Giriş)", "detay": f"Ana Karkas: {u['kaynak']}"
                })
        except Exception as e: print("Et İşleme Tablosu Hatası:", e)

        tum_hareketler.sort(key=lambda x: str(x['tarih']), reverse=True)
        conn.close()
        return jsonify({"urun_adi": urun_adi, "markalar": markalar, "gecmis": tum_hareketler})
    except Exception as e:
        print("API Hatası:", e)
        return jsonify({"error": str(e)}), 500
@app.route('/personel-import', methods=['POST'])
@login_required
@admin_required
def personel_import():
    file = request.files.get('file')
    if not file or file.filename == '':
        flash("Lütfen bir dosya seçin.", "error")
        return redirect(url_for('personel'))

    try:
        df = pd.read_excel(file) if file.filename.endswith('.xlsx') else pd.read_csv(file)
        # Gerekli sütun kontrolü (Excel başlıkları)
        required = ['ad_soyad', 'sicil_no', 'gorev']
        if not all(col in df.columns for col in required):
            flash("Dosya formatı hatalı! Sütunlar: ad_soyad, sicil_no, gorev olmalı.", "error")
            return redirect(url_for('personel'))

        conn = get_db_connection()
        count = 0
        for _, row in df.iterrows():
            try:
                conn.execute("INSERT INTO personeller (ad_soyad, sicil_no, gorev) VALUES (?, ?, ?)",
                             (str(row['ad_soyad']), str(row['sicil_no']), str(row['gorev'])))
                count += 1
            except sqlite3.IntegrityError:
                continue # Sicil no zaten varsa atla
        conn.commit()
        conn.close()
        flash(f"✅ {count} adet personel başarıyla sisteme aktarıldı.", "success")
    except Exception as e:
        flash(f"Hata: {str(e)}", "error")

    return redirect(url_for('personel'))
@app.route('/api/et-arsivi/<int:offset>')
@login_required
def et_arsivi_api(offset):
    from flask import jsonify
    import re
    conn = get_db_connection()

    lotlar = conn.execute("""
        SELECT sl.* FROM stok_lotlari sl
        JOIN depo d ON sl.urun_adi = d.urun_adi
        WHERE d.kategori IN ('Kırmızı Et', '🥩 Kırmızı Et')
        ORDER BY sl.id DESC LIMIT 5 OFFSET ?
    """, (offset,)).fetchall()

    lot_karneleri = []
    for l in lotlar:
        loglar = conn.execute("SELECT harcanan, detay FROM et_isleme_log WHERE lot_id=?", (l['id'],)).fetchall()
        t_harcanan = 0; t_kiyma = 0; t_kusbasi = 0; t_sote = 0; t_kemik = 0
        for lg in loglar:
            t_harcanan += lg['harcanan']
            d = lg['detay']
            try: t_kiyma += float(re.search(r'Kıyma:\s*([\d\.]+)', d).group(1))
            except: pass
            try: t_kusbasi += float(re.search(r'Kuşbaşı:\s*([\d\.]+)', d).group(1))
            except: pass
            try: t_sote += float(re.search(r'Sote:\s*([\d\.]+)', d).group(1))
            except: pass
            try: t_kemik += float(re.search(r'Kemik/Fire:\s*([\d\.]+)', d).group(1))
            except: pass

        lot_karneleri.append({
            'id': l['id'], 'tarih': l['tarih'][:10], 'damga': l['lot_damga_no'] if l['lot_damga_no'] else 'DAMGASIZ',
            'urun': l['urun_adi'], 'baslangic': l['baslangic_miktar'], 'kalan': l['kalan_miktar'],
            'islenen': t_harcanan, 'kiyma': t_kiyma, 'kusbasi': t_kusbasi, 'sote': t_sote, 'kemik': t_kemik,
            'fire_oran': round((t_kemik / t_harcanan * 100), 1) if t_harcanan > 0 else 0
        })
    conn.close()
    return jsonify(lot_karneleri)

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5001, debug=True)