import io
import json
import os
import re
import shutil
import zipfile
from datetime import date, datetime, timedelta
import fitz  # PyMuPDF
from flask import Flask, request, jsonify, render_template, send_from_directory, send_file
from pypdf import PdfReader, PdfWriter
from pathlib import Path

app = Flask(__name__)

BASE_DIR    = Path(__file__).parent
UPLOAD_DIR  = BASE_DIR / "uploads"
OUTPUT_DIR  = BASE_DIR / "outputs"
ARCHIVE_DIR = BASE_DIR / "archive"
MAPPINGS_FILE = BASE_DIR / "mappings.json"
BRANDS_FILE       = BASE_DIR / "brands.json"
HISTORY_FILE      = BASE_DIR / "history.json"
TRACKING_LOG_FILE   = BASE_DIR / "tracking_log.json"
TRACKING_INDEX_FILE = BASE_DIR / "tracking_index.json"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
ARCHIVE_DIR.mkdir(exist_ok=True)

BRAND_DISPLAY_DEFAULT = {
    "pura":  "Creatina Black Skull Pura",
    "turbo": "Creatina Black Skull Turbo",
    "dux":   "Creatina DUX",
    "roupa": "Roupas",
}
STANDARD_ORDER = ["pura", "turbo", "dux", "roupa"]

BR_STATES = {"AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG",
             "PA","PB","PR","PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO"}

# Full state names → abbreviation (longer names listed first to avoid partial matches)
BR_STATE_NAMES = [
    ("MATO GROSSO DO SUL", "MS"), ("MATO GROSSO", "MT"),
    ("ESPIRITO SANTO", "ES"),     ("ESPÍRITO SANTO", "ES"),
    ("RIO GRANDE DO NORTE", "RN"),("RIO GRANDE DO SUL", "RS"),
    ("RIO DE JANEIRO", "RJ"),     ("SANTA CATARINA", "SC"),
    ("SAO PAULO", "SP"),          ("SÃO PAULO", "SP"),
    ("MINAS GERAIS", "MG"),       ("DISTRITO FEDERAL", "DF"),
    ("ACRE", "AC"),               ("ALAGOAS", "AL"),
    ("AMAPA", "AP"),              ("AMAPÁ", "AP"),
    ("AMAZONAS", "AM"),           ("BAHIA", "BA"),
    ("CEARA", "CE"),              ("CEARÁ", "CE"),
    ("GOIAS", "GO"),              ("GOIÁS", "GO"),
    ("MARANHAO", "MA"),           ("MARANHÃO", "MA"),
    ("PARA", "PA"),               ("PARÁ", "PA"),
    ("PARAIBA", "PB"),            ("PARAÍBA", "PB"),
    ("PARANA", "PR"),             ("PARANÁ", "PR"),
    ("PERNAMBUCO", "PE"),
    ("PIAUI", "PI"),              ("PIAUÍ", "PI"),
    ("RONDONIA", "RO"),           ("RONDÔNIA", "RO"),
    ("RORAIMA", "RR"),            ("SERGIPE", "SE"),
    ("TOCANTINS", "TO"),
]

BR_HOLIDAYS_FIXED = {
    (1,  1): "Confraternização Universal",
    (4, 21): "Tiradentes",
    (5,  1): "Dia do Trabalho",
    (9,  7): "Independência do Brasil",
    (10,12): "Nossa Senhora Aparecida",
    (11, 2): "Finados",
    (11,15): "Proclamação da República",
    (12,25): "Natal",
}

# Easter-based variable holidays pre-computed for 2025-2028
# Easter: 2025=Apr 20, 2026=Apr 5, 2027=Mar 28, 2028=Apr 16
BR_HOLIDAYS_VARIABLE = {
    2025: {(3,3):"Segunda de Carnaval",(3,4):"Terça de Carnaval",(3,5):"Quarta de Cinzas",
           (4,18):"Paixão de Cristo",(4,20):"Páscoa",(6,19):"Corpus Christi"},
    2026: {(2,16):"Segunda de Carnaval",(2,17):"Terça de Carnaval",(2,18):"Quarta de Cinzas",
           (4,3):"Paixão de Cristo",(4,5):"Páscoa",(6,4):"Corpus Christi"},
    2027: {(3,1):"Segunda de Carnaval",(3,2):"Terça de Carnaval",(3,3):"Quarta de Cinzas",
           (3,26):"Paixão de Cristo",(3,28):"Páscoa",(5,27):"Corpus Christi"},
    2028: {(2,28):"Segunda de Carnaval",(2,29):"Terça de Carnaval",(3,1):"Quarta de Cinzas",
           (4,14):"Paixão de Cristo",(4,16):"Páscoa",(6,15):"Corpus Christi"},
}

# ── Mappings ─────────────────────────────────────────────────────────────────

def load_mappings():
    if MAPPINGS_FILE.exists():
        with open(MAPPINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_mappings(mappings):
    with open(MAPPINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(mappings, f, ensure_ascii=False, indent=2)


def load_brands():
    if BRANDS_FILE.exists():
        with open(BRANDS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_brands(brands):
    with open(BRANDS_FILE, "w", encoding="utf-8") as f:
        json.dump(brands, f, ensure_ascii=False, indent=2)


def get_brand_display():
    result = dict(BRAND_DISPLAY_DEFAULT)
    result.update(load_brands())
    return result


def load_history():
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def load_tracking_log():
    if TRACKING_LOG_FILE.exists():
        with open(TRACKING_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_tracking_log(log):
    with open(TRACKING_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def load_tracking_index():
    if TRACKING_INDEX_FILE.exists():
        with open(TRACKING_INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_tracking_index(index):
    with open(TRACKING_INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)



def mapping_key(produto, sku):
    return f"{sku.strip()}|{_normalize_titulo(produto)}"


_TRACKING_RE = re.compile(r'\bBR[A-Z0-9]{8,25}\b')


def extract_tracking_numbers(page):
    """Return unique BR... tracking numbers found on a label page."""
    return list(set(_TRACKING_RE.findall(page.get_text("text"))))


def _normalize_titulo(t):
    """Normalise product title for loose comparison."""
    t = t.lower().strip()
    t = re.sub(r'[^\w\s]', '', t)
    return re.sub(r'\s+', ' ', t)


def detect_sku_title_conflicts(filenames, mappings):
    """Return SKUs that appear in the PDFs under a title not present in mappings,
    but whose base SKU IS mapped under a different title."""
    mapped_skus = {}  # base_sku -> list of (mk, info)
    for mk, info in mappings.items():
        if "|" in mk:
            base = mk.split("|", 1)[0]
            mapped_skus.setdefault(base, []).append((mk, info))

    conflicts = {}
    for filename in filenames:
        pdf_path = UPLOAD_DIR / filename
        doc = fitz.open(str(pdf_path))
        for page_num in range(len(doc)):
            for item in extract_checklist(doc[page_num]):
                sku   = item["sku"].strip()
                mk    = mapping_key(item["produto"], item["sku"])
                titulo = re.sub(r"\s+", " ", item["produto"]).strip()
                if mk in mappings:
                    continue  # known product, no conflict
                if sku not in mapped_skus or sku in conflicts:
                    continue
                # Same base SKU is mapped but under a different title
                existing = mapped_skus[sku]
                conflicts[sku] = {
                    "sku": sku,
                    "titulo_encontrado": titulo,
                    "titulo_salvo": existing[0][1].get("titulo", existing[0][0].split("|", 1)[-1]),
                    "categoria_atual": existing[0][1].get("categoria", "?"),
                    "kit_size": existing[0][1].get("kit_size", 1),
                }
        doc.close()
    return list(conflicts.values())


# ── PDF extraction ────────────────────────────────────────────────────────────

# Checklist column x-boundaries (consistent across all Shopee label PDFs)
COL_NUM_MAX     = 50
COL_PRODUTO_MAX = 120
COL_SKU_MAX     = 170
COL_VAR_MAX     = 215

def word_column(x):
    if x < COL_NUM_MAX:       return "num"
    elif x < COL_PRODUTO_MAX: return "produto"
    elif x < COL_SKU_MAX:     return "sku"
    elif x < COL_VAR_MAX:     return "variacao"
    else:                     return "quantidade"

def extract_checklist(page):
    """Extract checklist items from one label page using word positions."""
    words = page.get_text("words")
    checklist_y = None
    for w in words:
        x0, y0, x1, y1, word, *_ = w
        if "Checklist" in word:
            checklist_y = y0
            break
    if checklist_y is None:
        return []
    header_y = None
    for w in words:
        x0, y0, x1, y1, word, *_ = w
        if y0 > checklist_y and word.strip() == "Quantidade":
            header_y = y0
            break
    if header_y is None:
        return []
    data = []
    for w in words:
        x0, y0, x1, y1, word, *_ = w
        if y0 > header_y + 2:
            data.append({"x": x0, "y": y0, "word": word, "col": word_column(x0)})
    if not data:
        return []
    y_groups = {}
    for d in data:
        y_key = round(d["y"] / 3) * 3
        y_groups.setdefault(y_key, []).append(d)
    items = []
    current = None
    for y_key in sorted(y_groups):
        row = y_groups[y_key]
        num_words  = [d["word"] for d in row if d["col"] == "num"      and d["word"].isdigit()]
        prod_words = [d["word"] for d in row if d["col"] == "produto"]
        sku_words  = [d["word"] for d in row if d["col"] == "sku"]
        var_words  = [d["word"] for d in row if d["col"] == "variacao"]
        qty_words  = [d["word"] for d in row if d["col"] == "quantidade"]
        if num_words:
            if current is not None:
                items.append(current)
            qty_raw = qty_words[0] if qty_words else "0"
            qty = int(qty_raw) if qty_raw.isdigit() else 0
            current = {
                "produto":    " ".join(prod_words),
                "sku":        sku_words[0] if sku_words else "",
                "variacao":   " ".join(var_words),
                "quantidade": qty,
            }
        elif current is not None:
            if prod_words:
                current["produto"] += " " + " ".join(prod_words)
            if var_words and not current["variacao"]:
                current["variacao"] = " ".join(var_words)
    if current is not None:
        items.append(current)
    for item in items:
        item["produto"] = re.sub(r"\s+", " ", item["produto"]).strip()
    return items


def extract_checklist_combined(page):
    """Extrai checklist de páginas onde etiqueta e checklist estão lado a lado
    (formato RETIRADA PELO COMPRADOR da Shopee).
    Detecta as posições reais das colunas pelo cabeçalho para não depender de
    offsets fixos (as colunas ficam comprimidas na metade direita da página)."""
    words = page.get_text("words")
    # Encontra "Checklist" para ancorar a busca
    checklist_y = None
    for w in words:
        x0, y0, x1, y1, word, *_ = w
        if "Checklist" in word:
            checklist_y = y0
            break
    if checklist_y is None:
        return []
    # Encontra "Quantidade" (cabeçalho da última coluna) abaixo do título
    header_y = None
    qty_header_x = None
    for w in words:
        x0, y0, x1, y1, word, *_ = w
        if y0 > checklist_y and word.strip() == "Quantidade":
            header_y = y0
            qty_header_x = x0
            break
    if header_y is None:
        return []
    # Mapeia posição X real de cada coluna a partir do cabeçalho
    col_xs = {}
    for w in words:
        x0, y0, x1, y1, word, *_ = w
        if abs(y0 - header_y) > 4:
            continue
        wc = word.strip()
        if wc == "#":
            col_xs["num"] = x0
        elif wc == "Produto":
            col_xs["produto"] = x0
        elif wc == "SKU":
            col_xs["sku"] = x0
        elif "Varia" in wc:
            col_xs["variacao"] = x0
        elif wc == "Quantidade":
            col_xs["quantidade"] = x0
    if "num" not in col_xs or "quantidade" not in col_xs:
        return []
    x_min = col_xs["num"] - 5
    # Classificador dinâmico: atribui cada x à coluna mais próxima à esquerda
    col_order = sorted((v, k) for k, v in col_xs.items())
    def col_of(x):
        result = col_order[0][1]
        for col_x, col_name in col_order:
            if x >= col_x - 5:
                result = col_name
        return result
    data = []
    for w in words:
        x0, y0, x1, y1, word, *_ = w
        if y0 > header_y + 2 and x0 >= x_min:
            data.append({"x": x0, "y": y0, "word": word, "col": col_of(x0)})
    if not data:
        return []
    y_groups = {}
    for d in data:
        y_key = round(d["y"] / 3) * 3
        y_groups.setdefault(y_key, []).append(d)
    items = []
    current = None
    for y_key in sorted(y_groups):
        row = y_groups[y_key]
        num_words  = [d["word"] for d in row if d["col"] == "num"      and d["word"].isdigit()]
        prod_words = [d["word"] for d in row if d["col"] == "produto"]
        sku_words  = [d["word"] for d in row if d["col"] == "sku"]
        var_words  = [d["word"] for d in row if d["col"] == "variacao"]
        qty_words  = [d["word"] for d in row if d["col"] == "quantidade"]
        if num_words:
            if current is not None:
                items.append(current)
            qty_raw = qty_words[0] if qty_words else "0"
            current = {
                "produto":    " ".join(prod_words),
                "sku":        sku_words[0] if sku_words else "",
                "variacao":   " ".join(var_words),
                "quantidade": int(qty_raw) if qty_raw.isdigit() else 0,
            }
        elif current is not None:
            if prod_words:
                current["produto"] += " " + " ".join(prod_words)
            if var_words and not current["variacao"]:
                current["variacao"] = " ".join(var_words)
    if current is not None:
        items.append(current)
    for item in items:
        item["produto"] = re.sub(r"\s+", " ", item["produto"]).strip()
    return items


def get_unknown_items_combined(pdf_path, mappings):
    doc = fitz.open(str(pdf_path))
    seen = {}
    for page_num in range(len(doc)):
        for item in extract_checklist_combined(doc[page_num]):
            key = mapping_key(item["produto"], item["sku"])
            if key not in mappings and key not in seen:
                seen[key] = {
                    "produto": item["produto"], "sku": item["sku"], "key": key,
                    "file": pdf_path.name, "page": page_num + 1,
                }
    doc.close()
    return list(seen.values())


_CEP_RE = re.compile(r'^\d{5}-\d{3}$')

def extract_destination_state(page):
    """Extract destination state UF from a Shopee label page.

    Shopee labels write the full state name (e.g. 'São Paulo', 'Bahia') in
    the address. The name may wrap across two lines, so we concatenate all
    words near the CEP into one text block and search for known state names.
    """
    words = page.get_text("words")
    page_h = page.rect.height
    dest_limit = page_h * 0.55  # destination section is the top ~55%

    # Word list restricted to destination section
    wlist = sorted(
        [(round(y0), w.strip(), round(x0))
         for x0, y0, x1, y1, w, *_ in words
         if w.strip() and y0 < dest_limit],
        key=lambda t: (t[0], t[2])
    )

    def _search_text(text):
        """Return UF if text contains a known state name or abbreviation."""
        tu = text.upper()
        for name, uf in BR_STATE_NAMES:
            if name in tu:
                return uf
        for token in tu.split():
            if len(token) == 2 and token in BR_STATES:
                return token
            for sep in ('/', '-'):
                if sep in token:
                    tail = token.rsplit(sep, 1)[-1]
                    if len(tail) == 2 and tail in BR_STATES:
                        return tail
        return None

    # Strategy 1: find destination CEP, combine ALL words in ±50pt window
    cep_ys = [y for y, w, _ in wlist if _CEP_RE.match(w)]
    for cy in cep_ys:
        nearby = [w for y, w, _ in wlist if cy - 50 <= y <= cy + 15]
        uf = _search_text(" ".join(nearby))
        if uf:
            return uf

    # Fallback: combine all destination words and search
    uf = _search_text(" ".join(w for _, w, _ in wlist))
    return uf


def extract_store_name(pdf_path):
    """Extract store name from REMETENTE field on first label page."""
    doc = fitz.open(str(pdf_path))
    store = "Desconhecida"
    for page_num in range(min(3, len(doc))):
        words = doc[page_num].get_text("words")
        remetente_y = None
        for w in words:
            x0, y0, x1, y1, word, *_ = w
            if word.strip() == "REMETENTE" and x0 < 100:
                remetente_y = y0
                break
        if remetente_y is None:
            continue
        name_words = sorted(
            [(x0, word) for x0, y0, x1, y1, word, *_ in words
             if remetente_y + 2 < y0 < remetente_y + 14 and x0 < 200],
            key=lambda w: w[0]
        )
        if name_words:
            store = " ".join(w[1] for w in name_words).strip()
            break
    doc.close()
    return store


# ── Classification ────────────────────────────────────────────────────────────

def classify_page_items(items, mappings):
    unknown, category_units, category_set = [], {}, set()
    for item in items:
        key = mapping_key(item["produto"], item["sku"])
        if key not in mappings:
            unknown.append({"produto": item["produto"], "sku": item["sku"], "key": key})
        else:
            info = mappings[key]
            cat  = info["categoria"]
            kit  = info.get("kit_size", 1)
            category_set.add(cat)
            category_units[cat] = category_units.get(cat, 0) + kit * item["quantidade"]
    return {"unknown": unknown, "category_units": category_units, "category_set": list(category_set)}


def determine_output_pdf(items, mappings):
    for item in items:
        if mapping_key(item["produto"], item["sku"]) not in mappings:
            return None
    res      = classify_page_items(items, mappings)
    cat_set  = set(res["category_set"])
    cat_units = res["category_units"]
    if "roupa" in cat_set:
        return "roupas" if cat_set == {"roupa"} else "variacoes"
    if len(cat_set) > 1:
        return "variacoes"
    if len(cat_set) == 1:
        cat = list(cat_set)[0]
        return f"{cat}_{cat_units[cat]}"
    return "variacoes"


def check_label_format(pdf_path):
    """Sample up to 15 pages to detect if the Shopee label layout changed.
    Returns a list of issue strings, empty if everything looks normal.
    """
    doc = fitz.open(str(pdf_path))
    checklist_text_pages = 0
    items_found_pages    = 0
    sample = min(15, len(doc))
    for page_num in range(sample):
        page = doc[page_num]
        if "Checklist" in page.get_text("text"):
            checklist_text_pages += 1
            if extract_checklist(page):
                items_found_pages += 1
    doc.close()
    issues = []
    if checklist_text_pages > 0 and items_found_pages == 0:
        issues.append(
            f"{checklist_text_pages} página(s) com 'Checklist' encontradas, "
            "mas nenhum item extraído — as colunas parecem estar em posições diferentes."
        )
    elif checklist_text_pages > 0 and items_found_pages < checklist_text_pages * 0.4:
        issues.append(
            f"Só {items_found_pages} de {checklist_text_pages} página(s) com checklist "
            "foram lidas corretamente — parte do layout pode ter mudado."
        )
    return issues


def get_unknown_items(pdf_path, mappings):
    doc = fitz.open(str(pdf_path))
    seen = {}
    for page_num in range(len(doc)):
        for item in extract_checklist(doc[page_num]):
            key = mapping_key(item["produto"], item["sku"])
            if key not in mappings and key not in seen:
                seen[key] = {
                    "produto": item["produto"], "sku": item["sku"], "key": key,
                    "file": pdf_path.name, "page": page_num + 1,
                }
    doc.close()
    return list(seen.values())


# ── Holiday helpers ──────────────────────────────────────────────────────────

def get_br_holidays(year):
    result = {}
    for (month, day), name in BR_HOLIDAYS_FIXED.items():
        try:
            result[date(year, month, day).isoformat()] = name
        except ValueError:
            pass
    for (month, day), name in BR_HOLIDAYS_VARIABLE.get(year, {}).items():
        result[date(year, month, day).isoformat()] = name
    return result


def _make_period_filter(period, from_date=None, to_date=None):
    today     = date.today()
    yesterday = today - timedelta(days=1)
    def in_period(entry_date_str):
        try:
            ed = date.fromisoformat(entry_date_str)
        except Exception:
            return False
        if period == "today":     return ed == today
        if period == "yesterday": return ed == yesterday
        if period == "7d":        return (today - ed).days < 7
        if period == "15d":       return (today - ed).days < 15
        if period == "30d":       return (today - ed).days < 30
        if period == "custom" and from_date and to_date:
            return date.fromisoformat(from_date) <= ed <= date.fromisoformat(to_date)
        return True
    return in_period


def _date_range(period, filtered, from_date=None, to_date=None):
    today     = date.today()
    yesterday = today - timedelta(days=1)
    if period == "today":     return today, today
    if period == "yesterday": return yesterday, yesterday
    if period == "7d":        return today - timedelta(days=6), today
    if period == "15d":       return today - timedelta(days=14), today
    if period == "30d":       return today - timedelta(days=29), today
    if period == "custom" and from_date and to_date:
        return date.fromisoformat(from_date), date.fromisoformat(to_date)
    dates = [date.fromisoformat(e["date"]) for e in filtered if e.get("date")]
    if not dates:
        return today, today
    return min(dates), max(dates)


# ── Cover page generator ─────────────────────────────────────────────────────

_SKIP_COVER = {"sem_produto", "sem_checklist", "pagina_em_branco",
               "ml_sem_produto", "ml_sem_checklist"}

def _cover_title(key):
    k = re.sub(r'^ml_', '', key)
    m = re.match(r'^([a-z]+)_(\d+)$', k)
    if m:
        return f"{m.group(1).upper()} {m.group(2)}"
    labels = {"roupas": "ROUPAS", "variacoes": "VARIAÇÕES"}
    return labels.get(k, k.upper())

def create_cover_page_bytes(title, pedidos, unidades, source="", label_date=""):
    """Portrait 4×6 in cover page for thermal/Zebra label printer (black only)."""
    W, H  = 288, 432  # 4×6 inches @ 72 dpi — portrait
    BLACK = (0, 0, 0)
    GRAY  = (0.55, 0.55, 0.55)
    doc   = fitz.open()
    page  = doc.new_page(width=W, height=H)

    font  = fitz.Font("hebo")   # Helvetica Bold (built-in)
    tw    = fitz.TextWriter(page.rect)

    def add_centered(text, baseline_y, fontsize):
        fs = fontsize
        while fs >= 8:
            if font.text_length(text, fs) <= W - 24:
                break
            fs -= 2
        x = (W - font.text_length(text, fs)) / 2
        tw.append((x, baseline_y), text, font=font, fontsize=fs)

    title_y = 160
    if source:
        add_centered(source, 72, 22)
        # draw thin separator line
        page.draw_line((40, 88), (W - 40, 88), color=GRAY, width=0.8)

    add_centered(title,                 title_y, 64)
    add_centered(f"{pedidos} PEDIDOS",  252,     48)
    add_centered(f"{unidades} UNIDADES",340,     48)

    if label_date:
        try:
            d = datetime.strptime(label_date, "%Y-%m-%d")
            display_date = d.strftime("%d/%m/%Y")
        except ValueError:
            display_date = label_date
        add_centered(display_date, 403, 22)

    tw.write_text(page, color=BLACK)

    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    buf.seek(0)
    return buf.read()


def _prepend_cover(writer, key, label_date=""):
    """Insert a cover page as the first page of writer, in-place."""
    if key in _SKIP_COVER:
        return
    pedidos = len(writer.pages)
    if pedidos == 0:
        return
    m      = re.match(r'^(?:ml_)?([a-z]+)_(\d+)$', key)
    upo    = int(m.group(2)) if m else 1
    source = "MERCADO LIVRE" if key.startswith("ml_") else ""
    cover_bytes = create_cover_page_bytes(_cover_title(key), pedidos, pedidos * upo,
                                          source=source, label_date=label_date)
    cover_reader = PdfReader(io.BytesIO(cover_bytes))
    writer.insert_page(cover_reader.pages[0], index=0)


# ── Core split/merge ──────────────────────────────────────────────────────────

def compute_brand_totals(breakdown):
    brand_orders, brand_units = {}, {}
    for key, count in breakdown.items():
        if key in ("roupas", "roupas.pdf"):
            brand_orders["roupa"] = brand_orders.get("roupa", 0) + count
            brand_units["roupa"]  = brand_units.get("roupa", 0) + count
            continue
        if key.startswith("variacoes"):
            brand_orders["variacoes"] = brand_orders.get("variacoes", 0) + count
            brand_units["variacoes"]  = brand_units.get("variacoes", 0) + count
            continue
        m = re.match(r"^(.+)_(\d+)$", key)
        if not m:
            continue
        brand, upo = m.group(1), int(m.group(2))
        brand_orders[brand] = brand_orders.get(brand, 0) + count
        brand_units[brand]  = brand_units.get(brand, 0) + count * upo
    return brand_orders, brand_units


def split_pdfs_merged(filenames, mappings, label_date="", retirada_filenames=None):
    """
    Process multiple PDFs and merge pages with the same output key into one PDF each.
    Returns stats + output file list.
    """
    retirada_set = set(retirada_filenames or [])
    writers            = {}  # output_key -> PdfWriter
    page_results       = []
    store_names        = []
    store_items        = {}  # store -> {sku_key -> {sku, produto, categoria, kit_size, units}}
    store_label_counts = {}  # store -> number of label pages (1 page = 1 customer order)
    state_counts       = {}  # UF -> number of orders shipped there
    tracking_numbers   = {}  # tracking_number -> {filename, page}
    total_pages_all    = 0
    blank_pages_all    = 0
    sem_check_all      = 0
    breakdown_all      = {}

    for filename in filenames:
        pdf_path = UPLOAD_DIR / filename
        doc      = fitz.open(str(pdf_path))
        reader   = PdfReader(str(pdf_path))
        n_pages  = len(doc)
        total_pages_all += n_pages

        store = extract_store_name(pdf_path)
        if store not in store_names:
            store_names.append(store)

        _cl_fn = extract_checklist_combined if filename in retirada_set else extract_checklist
        for page_num in range(n_pages):
            fitz_page = doc[page_num]
            items     = _cl_fn(fitz_page)

            if not items:
                text = fitz_page.get_text("text").strip()
                if len(text) < 20:
                    output_key = "pagina_em_branco"
                    blank_pages_all += 1
                else:
                    output_key = "sem_checklist"
                    sem_check_all  += 1
                page_results.append({
                    "file": filename, "page": page_num + 1,
                    "items": [], "output": output_key,
                })
            else:
                output_key = determine_output_pdf(items, mappings) or "__unknown__"
                page_results.append({
                    "file": filename, "page": page_num + 1,
                    "output": output_key,
                    "items": [{"produto": i["produto"][:50], "sku": i["sku"],
                               "quantidade": i["quantidade"]} for i in items],
                })

            if output_key in ("pagina_em_branco", "__unknown__"):
                continue

            writers.setdefault(output_key, PdfWriter()).add_page(reader.pages[page_num])
            if output_key not in ("sem_checklist",):
                breakdown_all[output_key] = breakdown_all.get(output_key, 0) + 1

            # Collect per-store item stats (one pass per page)
            if items:
                store_label_counts[store] = store_label_counts.get(store, 0) + 1
                uf = extract_destination_state(fitz_page)
                if uf:
                    state_counts[uf] = state_counts.get(uf, 0) + 1
                page_tns = extract_tracking_numbers(fitz_page)
                page_items_info = []
                for item in items:
                    mk2 = mapping_key(item["produto"], item["sku"])
                    if mk2 in mappings:
                        info2 = mappings[mk2]
                        page_items_info.append({
                            "produto":   item["produto"],
                            "sku":       item["sku"],
                            "quantidade": item["quantidade"],
                            "categoria": info2.get("categoria", "?"),
                            "kit_size":  info2.get("kit_size", 1),
                        })
                for tn in page_tns:
                    if tn not in tracking_numbers:
                        tracking_numbers[tn] = {
                            "filename": filename,
                            "page":     page_num + 1,
                            "store":    store,
                            "items":    page_items_info,
                        }
                for item in items:
                    mk      = mapping_key(item["produto"], item["sku"])
                    sku_key = item["sku"].strip()
                    if mk not in mappings:
                        continue
                    info = mappings[mk]
                    kit  = info.get("kit_size", 1)
                    qty  = item["quantidade"]
                    store_items.setdefault(store, {})
                    if sku_key not in store_items[store]:
                        store_items[store][sku_key] = {
                            "sku":       item["sku"],
                            "produto":   item["produto"],
                            "categoria": info["categoria"],
                            "kit_size":  kit,
                            "units":     0,
                        }
                    store_items[store][sku_key]["units"] += kit * qty

        doc.close()

    # Write output PDFs (with cover page prepended)
    output_files       = []
    output_page_counts = {}
    for key, writer in writers.items():
        _prepend_cover(writer, key, label_date=label_date)
        out_path = OUTPUT_DIR / f"{key}.pdf"
        with open(out_path, "wb") as f:
            writer.write(f)
        output_files.append(f"{key}.pdf")
        # subtract cover page from counts used for verification
        cover_offset = 0 if key in _SKIP_COVER else 1
        output_page_counts[f"{key}.pdf"] = len(writer.pages) - cover_offset

    label_pages  = total_pages_all - blank_pages_all - sem_check_all
    output_total = sum(c for k, c in output_page_counts.items()
                       if not k.startswith("sem_checklist"))
    verified = output_total == label_pages

    brand_orders, brand_units = compute_brand_totals(breakdown_all)
    brand_display = get_brand_display()
    brand_summary = sorted(
        [{"brand": b, "display": brand_display.get(b, b),
          "orders": brand_orders[b], "units": brand_units[b]}
         for b in brand_orders],
        key=lambda x: (
            STANDARD_ORDER.index(x["brand"]) if x["brand"] in STANDARD_ORDER else len(STANDARD_ORDER),
            x["brand"]
        )
    )

    # Security: check tracking duplicates vs history log
    tracking_log      = load_tracking_log()
    tracking_conflicts = []
    for tn, info in tracking_numbers.items():
        if tn in tracking_log:
            tracking_conflicts.append({
                "tracking": tn,
                "previous_date": tracking_log[tn].get("date", "?"),
                "filename": info["filename"],
                "page": info["page"],
            })

    # Security: detect SKU+title mismatches
    sku_conflicts = detect_sku_title_conflicts(filenames, mappings)

    stats = {
        "total_pages":        total_pages_all,
        "label_pages":        label_pages,
        "blank_pages":        blank_pages_all,
        "sem_checklist":      sem_check_all,
        "store_names":        store_names,
        "store_items":        store_items,
        "store_label_counts": store_label_counts,
        "state_counts":       state_counts,
        "breakdown":          breakdown_all,
        "output_page_counts": output_page_counts,
        "brand_summary":      brand_summary,
        "tracking_numbers":   list(tracking_numbers.keys()),
        "tracking_data":      tracking_numbers,
        "verification": {
            "ok":           verified,
            "label_pages":  label_pages,
            "output_total": output_total,
        },
    }

    security_alerts = {
        "tracking_conflicts": tracking_conflicts,
        "sku_conflicts":      sku_conflicts,
    }

    return {"output_files": output_files, "page_results": page_results,
            "stats": stats, "errors": [], "security_alerts": security_alerts}


# ── Mercado Livre ─────────────────────────────────────────────────────────────

ML_STORE_NAME = "REPZILLA ML"


def is_ml_label_page(page):
    return "Cidade de destino" in page.get_text("text")


def extract_ml_label_ids(page):
    """Return (pack_id, venda_id) from an ML label page. Either may be None."""
    text     = page.get_text("text")
    pack_id  = None
    venda_id = None
    m = re.search(r'Pack\s+ID:\s*(\d+)', text, re.IGNORECASE)
    if m:
        pack_id = m.group(1)
    m = re.search(r'Venda:\s*(\d+)', text, re.IGNORECASE)
    if m:
        venda_id = m.group(1)
    return pack_id, venda_id


def extract_ml_label_state(page):
    """Extract destination state from 'Cidade de destino' line."""
    m = re.search(r'Cidade\s+de\s+destino\s*[:\-]?\s*([^\n]+)',
                  page.get_text("text"), re.IGNORECASE)
    if not m:
        return None
    dest = m.group(1).strip()
    for sep in ('/', ',', ' - '):
        if sep in dest:
            tail = dest.rsplit(sep, 1)[-1].strip()
            uf = tail[:2].upper()
            if uf in BR_STATES:
                return uf
    dest_u = dest.upper()
    for name, uf in BR_STATE_NAMES:
        if name in dest_u:
            return uf
    for tok in dest_u.split():
        if len(tok) == 2 and tok in BR_STATES:
            return tok
    return None


_ML_PACKID_RE = re.compile(r'^Pack\s+ID:\s*(\d+)', re.IGNORECASE)
_ML_VENDA_RE  = re.compile(r'^Venda:\s*(\d+)', re.IGNORECASE)
_ML_SKU_RE    = re.compile(r'^SKU:\s*(\S+)', re.IGNORECASE)
_ML_QTY_RE    = re.compile(r'^Quantidade:\s*(\d+)', re.IGNORECASE)


def parse_ml_product_pages(doc):
    """Parse product identification pages in an ML PDF.

    Each page has:
      - Blocks: Pack ID / Venda / customer name / SKU / Quantidade (in order)
      - Footer: "Despachem as suas vendas..." followed by one product title per entry

    Titles at the bottom are paired positionally with entries in block order.
    Keyed by both Pack ID and Venda ID.
    """
    product_map = {}
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text")
        if "Checklist" in text or "REMETENTE" in text:
            continue
        if is_ml_label_page(page):
            continue
        if "SKU:" not in text:
            continue
        lines = [l.strip() for l in text.split('\n') if l.strip()]

        # Split at "Despachem" footer: titles live after it
        despachem_idx = next(
            (i for i, l in enumerate(lines) if l.lower().startswith("despachem")), None
        )
        block_lines  = lines[:despachem_idx] if despachem_idx is not None else lines
        page_titles  = lines[despachem_idx + 1:] if despachem_idx is not None else []

        # Parse blocks in order, collecting (pack_id, venda_id, sku, quantidade)
        entries = []
        i = 0
        while i < len(block_lines):
            m_pack  = _ML_PACKID_RE.match(block_lines[i])
            m_venda = _ML_VENDA_RE.match(block_lines[i])
            if not (m_pack or m_venda):
                i += 1; continue

            pack_id  = m_pack.group(1)  if m_pack  else None
            venda_id = m_venda.group(1) if m_venda else None
            sku = None; quantidade = 1
            j = i + 1
            while j < min(i + 15, len(block_lines)):
                if _ML_PACKID_RE.match(block_lines[j]):
                    break
                mv = _ML_VENDA_RE.match(block_lines[j])
                if mv:
                    if venda_id:
                        break
                    venda_id = mv.group(1)
                    j += 1; continue
                ms = _ML_SKU_RE.match(block_lines[j])
                mq = _ML_QTY_RE.match(block_lines[j])
                if ms:
                    sku = ms.group(1).strip()
                elif mq:
                    try: quantidade = int(mq.group(1))
                    except ValueError: pass
                j += 1

            if sku:
                entries.append({"pack_id": pack_id, "venda_id": venda_id,
                                 "sku": sku, "quantidade": quantidade})
            i = j

        # Pair each entry with its positional title from the footer section
        for idx, e in enumerate(entries):
            titulo = page_titles[idx] if idx < len(page_titles) else e["sku"]
            item = {"sku": e["sku"], "produto": titulo, "quantidade": e["quantidade"], "page": page_num + 1}
            if e["pack_id"]:
                product_map.setdefault(e["pack_id"], []).append(item)
            if e["venda_id"]:
                product_map.setdefault(e["venda_id"], []).append(item)

    return product_map


def get_unknown_items_ml(pdf_path, mappings):
    doc = fitz.open(str(pdf_path))
    product_map = parse_ml_product_pages(doc)
    doc.close()
    seen: dict = {}
    for items in product_map.values():
        for item in items:
            k = mapping_key(item["produto"], item["sku"])
            if k not in mappings and k not in seen:
                seen[k] = {
                    "produto": item["produto"], "sku": item["sku"], "key": k,
                    "file": pdf_path.name, "page": item.get("page", 1),
                }
    return list(seen.values())


def split_ml_pdfs(filenames, mappings, label_date=""):
    """Process ML PDFs — same return structure as split_pdfs_merged."""
    writers            = {}
    page_results       = []
    store_items        = {}
    store_label_counts = {}
    state_counts       = {}
    tracking_numbers   = {}
    total_pages_all    = 0
    unmatched_all      = 0
    breakdown_all      = {}
    store              = ML_STORE_NAME

    for filename in filenames:
        pdf_path = UPLOAD_DIR / filename
        doc      = fitz.open(str(pdf_path))
        reader   = PdfReader(str(pdf_path))
        total_pages_all += len(doc)
        product_map = parse_ml_product_pages(doc)

        for page_num in range(len(doc)):
            fp    = doc[page_num]
            if not is_ml_label_page(fp):
                page_results.append({
                    "file": filename, "page": page_num + 1,
                    "items": [], "output": "product_info_page",
                })
                continue
            pack_id, venda_id = extract_ml_label_ids(fp)
            items = product_map.get(pack_id, []) if pack_id else []
            if not items and venda_id:
                items = product_map.get(venda_id, [])
            if not items:
                unmatched_all += 1
                writers.setdefault("ml_sem_produto", PdfWriter()).add_page(reader.pages[page_num])
                page_results.append({
                    "file": filename, "page": page_num + 1,
                    "items": [], "output": "ml_sem_produto",
                })
                continue
            checklist_items = [
                {"produto": it["produto"], "sku": it["sku"], "quantidade": it["quantidade"]}
                for it in items
            ]
            raw_key = determine_output_pdf(checklist_items, mappings)
            if raw_key is None:
                page_results.append({
                    "file": filename, "page": page_num + 1,
                    "output": "__unknown__", "items": checklist_items,
                })
                continue
            ml_key = "ml_" + raw_key
            writers.setdefault(ml_key, PdfWriter()).add_page(reader.pages[page_num])
            breakdown_all[ml_key] = breakdown_all.get(ml_key, 0) + 1
            store_label_counts[store] = store_label_counts.get(store, 0) + 1
            uf = extract_ml_label_state(fp)
            if uf:
                state_counts[uf] = state_counts.get(uf, 0) + 1
            for tn in extract_tracking_numbers(fp):
                if tn not in tracking_numbers:
                    tracking_numbers[tn] = {"filename": filename, "page": page_num + 1}
            page_results.append({
                "file": filename, "page": page_num + 1, "output": ml_key,
                "items": [{"produto": it["produto"][:50], "sku": it["sku"],
                            "quantidade": it["quantidade"]} for it in items],
            })
            for item in checklist_items:
                mk      = mapping_key(item["produto"], item["sku"])
                sku_key = item["sku"].strip()
                if mk not in mappings:
                    continue
                info = mappings[mk]
                kit  = info.get("kit_size", 1)
                store_items.setdefault(store, {})
                if sku_key not in store_items[store]:
                    store_items[store][sku_key] = {
                        "sku":       item["sku"],
                        "produto":   item["produto"],
                        "categoria": info["categoria"],
                        "kit_size":  kit,
                        "units":     0,
                    }
                store_items[store][sku_key]["units"] += kit * item["quantidade"]
        doc.close()

    output_files       = []
    output_page_counts = {}
    for key, writer in writers.items():
        _prepend_cover(writer, key, label_date=label_date)
        out_path = OUTPUT_DIR / f"{key}.pdf"
        with open(out_path, "wb") as f:
            writer.write(f)
        output_files.append(f"{key}.pdf")
        cover_offset = 0 if key in _SKIP_COVER else 1
        output_page_counts[f"{key}.pdf"] = len(writer.pages) - cover_offset

    label_pages  = store_label_counts.get(store, 0)
    output_total = sum(c for k, c in output_page_counts.items()
                       if k != "ml_sem_produto.pdf")
    verified     = (output_total == label_pages)

    stripped = {k.replace("ml_", "", 1): v for k, v in breakdown_all.items()}
    brand_orders, brand_units = compute_brand_totals(stripped)
    brand_display = get_brand_display()
    brand_summary = sorted(
        [{"brand": b, "display": brand_display.get(b, b),
          "orders": brand_orders[b], "units": brand_units[b]}
         for b in brand_orders],
        key=lambda x: (
            STANDARD_ORDER.index(x["brand"]) if x["brand"] in STANDARD_ORDER else len(STANDARD_ORDER),
            x["brand"]
        )
    )

    tracking_log       = load_tracking_log()
    tracking_conflicts = []
    for tn, info in tracking_numbers.items():
        if tn in tracking_log:
            tracking_conflicts.append({
                "tracking": tn,
                "previous_date": tracking_log[tn].get("date", "?"),
                "filename": info["filename"],
                "page": info["page"],
            })

    stats = {
        "total_pages":        total_pages_all,
        "label_pages":        label_pages,
        "blank_pages":        0,
        "sem_checklist":      unmatched_all,
        "store_names":        [store],
        "store_items":        store_items,
        "store_label_counts": store_label_counts,
        "state_counts":       state_counts,
        "breakdown":          breakdown_all,
        "output_page_counts": output_page_counts,
        "brand_summary":      brand_summary,
        "tracking_numbers":   list(tracking_numbers.keys()),
        "tracking_data":      tracking_numbers,
        "verification":       {
            "ok":           verified,
            "label_pages":  label_pages,
            "output_total": output_total,
        },
    }

    security_alerts = {
        "tracking_conflicts": tracking_conflicts,
        "sku_conflicts":      [],
    }

    return {"output_files": output_files, "page_results": page_results,
            "stats": stats, "errors": [], "security_alerts": security_alerts}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    files    = request.files.getlist("pdfs")
    retirada = request.args.get("retirada") == "1"
    if not files or all(f.filename == "" for f in files):
        return jsonify({"error": "Nenhum arquivo enviado"}), 400

    mappings    = load_mappings()
    saved       = []
    all_unknown = {}
    store_names = []
    batch_skus  = {}
    sku_stores  = {}
    _extract_fn = extract_checklist_combined if retirada else extract_checklist
    _unknown_fn = get_unknown_items_combined if retirada else get_unknown_items

    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            continue
        pdf_path = UPLOAD_DIR / file.filename
        file.save(str(pdf_path))
        saved.append(file.filename)
        s = extract_store_name(pdf_path)
        if s not in store_names:
            store_names.append(s)
        for u in _unknown_fn(pdf_path, mappings):
            all_unknown.setdefault(u["key"], u)
        doc = fitz.open(str(pdf_path))
        for page_num in range(len(doc)):
            for item in _extract_fn(doc[page_num]):
                base = item["sku"].strip()
                norm = _normalize_titulo(item["produto"])
                batch_skus.setdefault(base, set()).add(norm)
                sku_stores.setdefault(base, set()).add(s)
        doc.close()

    if not saved:
        return jsonify({"error": "Nenhum PDF válido encontrado"}), 400

    # Base SKUs with 2+ titles in batch where at least one title is NOT yet mapped
    dup_skus = set()
    for base, titles in batch_skus.items():
        if len(titles) > 1:
            if any(f"{base}|{t}" not in mappings for t in titles):
                dup_skus.add(base)
    has_batch_sku_dups = bool(dup_skus)
    # Only return stores for duplicate SKUs with unmapped entries
    sku_stores_out = {base: sorted(sku_stores[base]) for base in dup_skus}

    format_warnings = {}
    for fname in saved:
        issues = check_label_format(UPLOAD_DIR / fname)
        if issues:
            format_warnings[fname] = issues

    return jsonify({
        "filenames":           saved,
        "unknown":             list(all_unknown.values()),
        "format_warnings":     format_warnings,
        "store_names":         store_names,
        "has_batch_sku_dups":  has_batch_sku_dups,
        "sku_stores":          sku_stores_out,
    })


@app.route("/save-mappings", methods=["POST"])
def save_mappings_route():
    data = request.json
    mappings = load_mappings()
    mappings.update(data.get("mappings", {}))
    save_mappings(mappings)
    return jsonify({"ok": True})


@app.route("/process", methods=["POST"])
def process():
    data               = request.json
    filenames          = data.get("filenames", [])
    retirada_filenames = data.get("retirada_filenames", [])
    label_date         = data.get("label_date", "")
    all_fnames         = filenames + retirada_filenames
    if not all_fnames:
        return jsonify({"error": "Nenhum arquivo especificado"}), 400

    mappings    = load_mappings()
    all_unknown = {}
    retirada_set = set(retirada_filenames)
    for filename in all_fnames:
        pdf_path = UPLOAD_DIR / filename
        if not pdf_path.exists():
            return jsonify({"error": f"Arquivo não encontrado: {filename}"}), 404
        _fn = get_unknown_items_combined if filename in retirada_set else get_unknown_items
        for u in _fn(pdf_path, mappings):
            all_unknown.setdefault(u["key"], u)

    if all_unknown:
        return jsonify({"error": "Ainda há produtos não classificados",
                        "unknown": list(all_unknown.values())}), 400

    return jsonify(split_pdfs_merged(all_fnames, mappings, label_date=label_date,
                                     retirada_filenames=retirada_filenames))


@app.route("/download/<filename>")
def download(filename):
    today = date.today().strftime("%d.%m.%y")
    stem  = filename.replace(".pdf", "").replace("_", " ")
    return send_from_directory(str(OUTPUT_DIR), filename, as_attachment=True,
                               download_name=f"{stem} {today}.pdf")


def _pdf_with_cover(pdf_path, key, label_date, saved_opc, breakdown):
    """Return BytesIO of the PDF with cover page regenerated for the given date/counts."""
    if key in _SKIP_COVER:
        with open(pdf_path, "rb") as f:
            return io.BytesIO(f.read())
    pedidos = saved_opc.get(key + ".pdf", breakdown.get(key, 0))
    m       = re.match(r'^(?:ml_)?([a-z]+)_(\d+)$', key)
    upo     = int(m.group(2)) if m else 1
    source  = "MERCADO LIVRE" if key.startswith("ml_") else ""
    cover_bytes  = create_cover_page_bytes(_cover_title(key), pedidos, pedidos * upo,
                                           source=source, label_date=label_date)
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    for i, pg in enumerate(reader.pages):
        if i > 0:
            writer.add_page(pg)
    writer.insert_page(PdfReader(io.BytesIO(cover_bytes)).pages[0], index=0)
    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)
    return buf


def _archive_pdf_path(entry, filename):
    """Retorna o caminho do PDF no arquivo da expedição, ou None se não existir."""
    archive_id = entry.get("archive_dir")
    if archive_id:
        p = ARCHIVE_DIR / archive_id / filename
        if p.exists():
            return p
    return None


@app.route("/history/download/<int:idx>/<path:filename>")
def history_download(idx, filename):
    history = load_history()
    if not (0 <= idx < len(history)):
        return jsonify({"error": "Expedição não encontrada"}), 404
    if not filename.endswith(".pdf") or "/" in filename or "\\" in filename:
        return jsonify({"error": "Arquivo inválido"}), 400
    entry      = history[idx]
    label_date = entry.get("date", "")
    breakdown  = entry.get("totals", {}).get("breakdown", {})
    saved_opc  = entry.get("output_page_counts", {})
    key        = filename.replace(".pdf", "")

    # Usa arquivo da expedição se disponível, senão cai no disco atual
    pdf_path = _archive_pdf_path(entry, filename) or (OUTPUT_DIR / filename)
    if not pdf_path.exists():
        return jsonify({"error": "Arquivo não encontrado no disco"}), 404

    buf = _pdf_with_cover(pdf_path, key, label_date, saved_opc, breakdown)
    try:
        d = datetime.strptime(label_date, "%Y-%m-%d")
        date_str = d.strftime("%d.%m.%y")
    except Exception:
        date_str = date.today().strftime("%d.%m.%y")
    stem = filename.replace(".pdf", "").replace("_", " ")
    return send_file(buf, as_attachment=True,
                     download_name=f"{stem} {date_str}.pdf",
                     mimetype="application/pdf")


@app.route("/history/download-all/<int:idx>", methods=["POST"])
def history_download_all(idx):
    history = load_history()
    if not (0 <= idx < len(history)):
        return jsonify({"error": "Expedição não encontrada"}), 404
    entry      = history[idx]
    label_date = entry.get("date", "")
    breakdown  = entry.get("totals", {}).get("breakdown", {})
    saved_opc  = entry.get("output_page_counts", {})
    filenames  = request.json.get("filenames", [])
    try:
        d = datetime.strptime(label_date, "%Y-%m-%d")
        date_str = d.strftime("%d.%m.%y")
    except Exception:
        date_str = date.today().strftime("%d.%m.%y")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in filenames:
            path = _archive_pdf_path(entry, fname) or (OUTPUT_DIR / fname)
            if not path.exists():
                continue
            key  = fname.replace(".pdf", "")
            stem = fname.replace(".pdf", "").replace("_", " ")
            pdf_buf = _pdf_with_cover(path, key, label_date, saved_opc, breakdown)
            zf.writestr(f"{stem} {date_str}.pdf", pdf_buf.read())
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f"etiquetas {date_str}.zip",
                     mimetype="application/zip")


@app.route("/download-all", methods=["POST"])
def download_all():
    data      = request.json
    filenames = data.get("filenames", [])
    today     = date.today().strftime("%d.%m.%y")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in filenames:
            path = OUTPUT_DIR / fname
            if path.exists():
                stem = fname.replace(".pdf", "").replace("_", " ")
                zf.write(str(path), f"{stem} {today}.pdf")
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f"etiquetas {today}.zip",
                     mimetype="application/zip")


@app.route("/mappings", methods=["GET"])
def get_mappings():
    return jsonify(load_mappings())


@app.route("/mappings/sku-duplicates", methods=["GET"])
def mappings_sku_duplicates():
    """Return groups of mapping entries that share the same base SKU."""
    mappings = load_mappings()
    by_sku = {}
    for mk, info in mappings.items():
        base = mk.split("|", 1)[0] if "|" in mk else mk
        by_sku.setdefault(base, []).append({
            "key":      mk,
            "titulo":   info.get("titulo", mk.split("|", 1)[-1] if "|" in mk else mk),
            "categoria": info.get("categoria", "?"),
            "kit_size": info.get("kit_size", 1),
        })
    duplicates = {sku: entries for sku, entries in by_sku.items() if len(entries) > 1}
    return jsonify(duplicates)


@app.route("/pdf-page/<path:filename>/<int:page_num>")
def pdf_page_image(filename, page_num):
    """Return a single PDF page rendered as PNG for the classify preview."""
    import re
    from flask import Response
    if not re.match(r'^[\w\-. ]+\.pdf$', filename, re.IGNORECASE):
        return jsonify({"error": "invalid filename"}), 400
    pdf_path = UPLOAD_DIR / filename
    if not pdf_path.exists():
        return jsonify({"error": "not found"}), 404
    try:
        doc = fitz.open(str(pdf_path))
        if page_num < 1 or page_num > len(doc):
            doc.close()
            return jsonify({"error": "page out of range"}), 400
        page = doc[page_num - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
        img_bytes = pix.tobytes("png")
        doc.close()
        return Response(img_bytes, mimetype="image/png")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/mappings/delete", methods=["POST"])
def delete_mapping():
    data     = request.json
    mappings = load_mappings()
    mappings.pop(data.get("key", ""), None)
    save_mappings(mappings)
    return jsonify({"ok": True})


@app.route("/brands", methods=["GET"])
def get_brands_route():
    return jsonify(get_brand_display())


@app.route("/brands/save", methods=["POST"])
def save_brand_route():
    data    = request.json
    slug    = data.get("slug", "").strip()
    display = data.get("display", "").strip()
    if not slug or not display:
        return jsonify({"error": "slug e display são obrigatórios"}), 400
    brands = load_brands()
    brands[slug] = display
    save_brands(brands)
    return jsonify({"ok": True})


@app.route("/history/add", methods=["POST"])
def history_add():
    data        = request.json
    history     = load_history()
    entry_date  = data.get("date") or date.today().isoformat()
    expedition_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    entry       = {
        "id":                 expedition_id,
        "timestamp":          datetime.now().isoformat(),
        "date":               entry_date,
        "store_items":         data.get("store_items", {}),
        "store_label_counts":  data.get("store_label_counts", {}),
        "state_counts":        data.get("state_counts", {}),
        "output_page_counts":  data.get("output_page_counts", {}),
        "totals": {
            "label_pages": data.get("label_pages", 0),
            "breakdown":   data.get("breakdown", {}),
        },
    }

    # Arquivar PDFs desta expedição para downloads históricos corretos
    breakdown = data.get("breakdown", {})
    opc       = data.get("output_page_counts", {})
    archive_exp_dir = ARCHIVE_DIR / expedition_id
    archive_exp_dir.mkdir(exist_ok=True)
    archived = []
    for fname in list(opc.keys()):
        src = OUTPUT_DIR / fname
        if src.exists():
            shutil.copy2(str(src), str(archive_exp_dir / fname))
            archived.append(fname)
    if archived:
        entry["archive_dir"] = expedition_id

    history.append(entry)
    save_history(history)

    # Save new tracking numbers to log (skip ones already handled by /security-resolve)
    new_tracking = data.get("tracking_numbers", [])
    if new_tracking:
        tracking_log = load_tracking_log()
        for tn in new_tracking:
            if tn not in tracking_log:
                tracking_log[tn] = {"date": entry_date}
        save_tracking_log(tracking_log)

    # Save enriched tracking index (tracking_number -> product info)
    tracking_data = data.get("tracking_data", {})
    if tracking_data:
        index = load_tracking_index()
        for tn, info in tracking_data.items():
            index[tn] = {
                "date":         entry_date,
                "expedition_id": entry["id"],
                "store":        info.get("store", ""),
                "filename":     info.get("filename", ""),
                "page":         info.get("page", 0),
                "items":        info.get("items", []),
            }
        save_tracking_index(index)

    return jsonify({"ok": True, "id": entry["id"]})


@app.route("/security-resolve", methods=["POST"])
def security_resolve():
    """Resolve security alerts: tracking duplicates and SKU+title conflicts."""
    data    = request.json
    today   = date.today().isoformat()

    # Tracking resolutions: {tracking_number: "delete_previous" | "keep_both" | "ignore_today"}
    tracking_actions = data.get("tracking_actions", {})
    tracking_log     = load_tracking_log()
    for tn, action in tracking_actions.items():
        if action == "delete_previous":
            # Replace old entry with today's date
            tracking_log[tn] = {"date": today}
        elif action == "keep_both":
            # Save today alongside the existing entry
            existing = tracking_log.get(tn, {})
            tracking_log[tn] = {
                "date": today,
                "also_seen": existing.get("date"),
            }
        # "ignore_today": don't touch the log for this number

    save_tracking_log(tracking_log)

    # SKU conflict resolutions: [{sku, titulo_encontrado, categoria, kit_size}]
    sku_actions = data.get("sku_actions", [])
    if sku_actions:
        mappings = load_mappings()
        for action in sku_actions:
            sku = action.get("sku", "")
            if sku in mappings:
                mappings[sku]["categoria"] = action.get("categoria", mappings[sku]["categoria"])
                mappings[sku]["kit_size"]  = action.get("kit_size", mappings[sku].get("kit_size", 1))
                mappings[sku]["titulo"]    = action.get("titulo_encontrado", "")
        save_mappings(mappings)

    return jsonify({"ok": True})


@app.route("/history/metrics", methods=["POST"])
def history_metrics():
    data      = request.json
    stores    = data.get("stores", [])
    period    = data.get("period", "all")
    from_date = data.get("from_date")
    to_date   = data.get("to_date")
    history   = load_history()
    today     = date.today()
    yesterday = today - timedelta(days=1)

    def in_period(entry_date_str):
        try:
            ed = date.fromisoformat(entry_date_str)
        except Exception:
            return False
        if period == "today":     return ed == today
        if period == "yesterday": return ed == yesterday
        if period == "7d":        return (today - ed).days < 7
        if period == "15d":       return (today - ed).days < 15
        if period == "30d":       return (today - ed).days < 30
        if period == "custom" and from_date and to_date:
            return date.fromisoformat(from_date) <= ed <= date.fromisoformat(to_date)
        return True

    filtered = [e for e in history if in_period(e.get("date", ""))]

    store_totals   = {}
    product_totals = {}

    for entry in filtered:
        slc = entry.get("store_label_counts", {})
        # Fallback for old entries: approximate label count from product units (best effort)
        if not slc:
            slc = {s: entry.get("totals", {}).get("label_pages", 0)
                   for s in entry.get("store_items", {})}

        for store_name, items in entry.get("store_items", {}).items():
            if stores and store_name not in stores:
                continue
            label_count = slc.get(store_name, 0)
            if store_name not in store_totals:
                store_totals[store_name] = {"orders": 0, "units": 0, "produtos": {}}
            store_totals[store_name]["orders"] += label_count
            for sku_key, item in items.items():
                units = item.get("units", 0)
                store_totals[store_name]["units"] += units
                st_prod = store_totals[store_name]["produtos"]
                if sku_key not in st_prod:
                    st_prod[sku_key] = {**item, "units": 0}
                st_prod[sku_key]["units"] += units
                if sku_key not in product_totals:
                    product_totals[sku_key] = {**item, "units": 0}
                product_totals[sku_key]["units"] += units

    # Top products grouped by categoria
    brand_display  = get_brand_display()
    cat_totals     = {}
    for item in product_totals.values():
        cat = item.get("categoria", "other")
        if cat not in cat_totals:
            display = "Roupas" if cat == "roupa" else brand_display.get(cat, cat.capitalize())
            cat_totals[cat] = {"categoria": cat, "display": display, "units": 0}
        cat_totals[cat]["units"] += item.get("units", 0)
    top_products = sorted(cat_totals.values(), key=lambda x: x["units"], reverse=True)
    # Kit vs avulso: label counts where those skus appeared
    kit_units  = sum(v["units"] for v in product_totals.values() if v.get("kit_size", 1) > 1)
    solo_units = sum(v["units"] for v in product_totals.values() if v.get("kit_size", 1) == 1)
    all_stores = sorted({s for e in history for s in e.get("store_items", {})})

    total_orders = sum(s["orders"] for s in store_totals.values())
    total_units  = sum(s["units"]  for s in store_totals.values())

    return jsonify({
        "store_totals":  store_totals,
        "top_products":  top_products,
        "kit_vs_solo":   {"kit": kit_units, "solo": solo_units},
        "all_stores":    all_stores,
        "total_entries": len(filtered),
        "summary": {
            "total_orders": total_orders,
            "total_units":  total_units,
        },
    })


@app.route("/history/states", methods=["POST"])
def history_states():
    data      = request.json
    period    = data.get("period", "all")
    from_date = data.get("from_date")
    to_date   = data.get("to_date")
    history   = load_history()
    in_period = _make_period_filter(period, from_date, to_date)
    filtered  = [e for e in history if in_period(e.get("date", ""))]
    state_totals = {}
    for entry in filtered:
        for uf, count in entry.get("state_counts", {}).items():
            state_totals[uf] = state_totals.get(uf, 0) + count
    return jsonify({"state_totals": state_totals})


@app.route("/history/daily", methods=["POST"])
def history_daily():
    data      = request.json
    stores    = data.get("stores", [])
    period    = data.get("period", "all")
    from_date = data.get("from_date")
    to_date   = data.get("to_date")
    history   = load_history()

    in_period = _make_period_filter(period, from_date, to_date)
    filtered  = [e for e in history if in_period(e.get("date", ""))]
    if not filtered:
        return jsonify({"days": [], "holidays": {}})

    start_d, end_d = _date_range(period, filtered, from_date, to_date)
    years = set()
    cur = start_d
    while cur <= end_d:
        years.add(cur.year); cur += timedelta(days=1)
    holidays = {}
    for y in years:
        holidays.update(get_br_holidays(y))

    WEEKDAYS = ["Segunda","Terça","Quarta","Quinta","Sexta","Sábado","Domingo"]
    daily = {}
    cur = start_d
    while cur <= end_d:
        iso = cur.isoformat()
        daily[iso] = {
            "date": iso, "weekday": WEEKDAYS[cur.weekday()],
            "holiday": holidays.get(iso),
            "orders": 0, "units": 0,
            "by_categoria": {}, "by_store": {},
        }
        cur += timedelta(days=1)

    for entry in filtered:
        d_str = entry.get("date", "")
        if d_str not in daily:
            continue
        slc = entry.get("store_label_counts", {})
        day = daily[d_str]
        for store_name, items in entry.get("store_items", {}).items():
            if stores and store_name not in stores:
                continue
            label_count = slc.get(store_name, 0)
            day["orders"] += label_count
            day["by_store"][store_name] = day["by_store"].get(store_name, 0) + label_count
            for sku_key, item in items.items():
                units = item.get("units", 0)
                cat   = item.get("categoria", "other")
                day["units"] += units
                day["by_categoria"][cat] = day["by_categoria"].get(cat, 0) + units

    return jsonify({"days": list(daily.values()), "holidays": holidays})


@app.route("/history/products-list", methods=["GET"])
def history_products_list():
    history  = load_history()
    products = {}
    for entry in history:
        for store_name, items in entry.get("store_items", {}).items():
            for sku_key, item in items.items():
                if sku_key not in products:
                    products[sku_key] = {
                        "sku_key":   sku_key,
                        "sku":       item.get("sku", sku_key),
                        "produto":   item.get("produto", sku_key),
                        "categoria": item.get("categoria", ""),
                        "kit_size":  item.get("kit_size", 1),
                    }
    return jsonify(sorted(products.values(), key=lambda x: x["produto"]))


@app.route("/history/product-metrics", methods=["POST"])
def history_product_metrics():
    data      = request.json
    categoria = data.get("categoria", "")
    stores    = data.get("stores", [])
    period    = data.get("period", "all")
    from_date = data.get("from_date")
    to_date   = data.get("to_date")
    history   = load_history()

    in_period = _make_period_filter(period, from_date, to_date)
    filtered  = [e for e in history if in_period(e.get("date", ""))]
    if not filtered or not categoria:
        return jsonify({"days":[],"store_totals":{},"holidays":{},"category_info":{}})

    brand_display = get_brand_display()
    cat_display   = "Roupas" if categoria == "roupa" else brand_display.get(categoria, categoria.capitalize())
    category_info = {"categoria": categoria, "display": cat_display}

    start_d, end_d = _date_range(period, filtered, from_date, to_date)
    years = set()
    cur = start_d
    while cur <= end_d:
        years.add(cur.year); cur += timedelta(days=1)
    holidays = {}
    for y in years:
        holidays.update(get_br_holidays(y))

    WEEKDAYS = ["Segunda","Terça","Quarta","Quinta","Sexta","Sábado","Domingo"]
    daily = {}
    cur = start_d
    while cur <= end_d:
        iso = cur.isoformat()
        daily[iso] = {
            "date": iso, "weekday": WEEKDAYS[cur.weekday()],
            "holiday": holidays.get(iso),
            "total_units": 0, "by_store": {},
        }
        cur += timedelta(days=1)

    store_totals = {}
    for entry in filtered:
        d_str = entry.get("date", "")
        if d_str not in daily:
            continue
        for store_name, items in entry.get("store_items", {}).items():
            if stores and store_name not in stores:
                continue
            for sku_key, item in items.items():
                if item.get("categoria") != categoria:
                    continue
                units = item.get("units", 0)
                daily[d_str]["total_units"] += units
                daily[d_str]["by_store"][store_name] = \
                    daily[d_str]["by_store"].get(store_name, 0) + units
                store_totals[store_name] = store_totals.get(store_name, 0) + units

    return jsonify({
        "days":          list(daily.values()),
        "store_totals":  store_totals,
        "holidays":      holidays,
        "category_info": category_info,
    })


@app.route("/history/entries", methods=["GET"])
def history_entries_list():
    history = load_history()
    entries = []
    for i, entry in enumerate(history):
        slc = entry.get("store_label_counts", {})
        total_orders = sum(slc.values()) if slc else entry.get("totals", {}).get("label_pages", 0)
        stores = list(slc.keys()) or list(entry.get("store_items", {}).keys())
        entries.append({
            "index":        i,
            "date":         entry.get("date", "?"),
            "id":           entry.get("id", str(i)),
            "stores":       stores,
            "store_orders": {s: slc.get(s, 0) for s in stores},
            "total_orders": total_orders,
        })
    # Sort by date descending so newest is first regardless of insertion order
    entries.sort(key=lambda e: e["date"], reverse=True)
    return jsonify({"entries": entries})


@app.route("/history/delete", methods=["POST"])
def history_delete():
    data    = request.json
    idx     = data.get("index")
    history = load_history()
    if idx is None or not (0 <= idx < len(history)):
        return jsonify({"ok": False, "error": "index inválido"}), 400
    history.pop(idx)
    save_history(history)
    return jsonify({"ok": True})


@app.route("/history/delete-store", methods=["POST"])
def history_delete_store():
    data    = request.json
    idx     = data.get("index")
    store   = data.get("store", "")
    history = load_history()
    if idx is None or not (0 <= idx < len(history)):
        return jsonify({"ok": False, "error": "index inválido"}), 400
    entry = history[idx]
    entry.get("store_items", {}).pop(store, None)
    entry.get("store_label_counts", {}).pop(store, None)
    # If no stores remain, remove the whole entry
    remaining = (list(entry.get("store_items", {}).keys())
                 or list(entry.get("store_label_counts", {}).keys()))
    if not remaining:
        history.pop(idx)
    else:
        new_lp = sum(entry.get("store_label_counts", {}).values())
        entry.setdefault("totals", {})["label_pages"] = new_lp
        history[idx] = entry
    save_history(history)
    return jsonify({"ok": True})


@app.route("/history/reopen/<int:idx>", methods=["GET"])
def history_reopen(idx):
    history = load_history()
    if not (0 <= idx < len(history)):
        return jsonify({"error": "Expedição não encontrada"}), 404
    entry    = history[idx]
    breakdown = entry.get("totals", {}).get("breakdown", {})
    label_pages = entry.get("totals", {}).get("label_pages", 0)
    store_items         = entry.get("store_items", {})
    store_label_counts  = entry.get("store_label_counts", {})

    # Use saved output_page_counts from history (set at lacrar time, covers excluded)
    saved_opc = entry.get("output_page_counts", {})

    # Check which output files still exist on disk (for download links only)
    output_files       = []
    output_page_counts = {}
    for key in breakdown:
        fname = key + ".pdf"
        fpath = OUTPUT_DIR / fname
        if fpath.exists():
            output_files.append(fname)
        if fname in saved_opc:
            output_page_counts[fname] = saved_opc[fname]
        elif fpath.exists():
            # Fallback for old history entries that don't have saved counts
            try:
                doc = fitz.open(str(fpath))
                output_page_counts[fname] = max(0, len(doc) - 1)
                doc.close()
            except Exception:
                output_page_counts[fname] = 0

    brand_orders, brand_units = compute_brand_totals(breakdown)
    brand_display = get_brand_display()
    brand_summary = sorted(
        [{"brand": b, "display": brand_display.get(b, b),
          "orders": brand_orders[b], "units": brand_units[b]}
         for b in brand_orders],
        key=lambda x: (
            STANDARD_ORDER.index(x["brand"]) if x["brand"] in STANDARD_ORDER else len(STANDARD_ORDER),
            x["brand"]
        )
    )

    store_names  = list(store_label_counts.keys()) or list(store_items.keys())
    saved_total  = sum(output_page_counts.values()) if output_page_counts else label_pages

    stats = {
        "label_pages":        label_pages,
        "total_pages":        saved_total,
        "blank_pages":        0,
        "sem_checklist":      0,
        "store_names":        store_names,
        "breakdown":          breakdown,
        "output_page_counts": output_page_counts,
        "brand_summary":      brand_summary,
        "is_history_reopen":  True,
        "verification": {
            "ok":           True,
            "label_pages":  label_pages,
            "output_total": label_pages,
        },
    }
    return jsonify({
        "stats":         stats,
        "output_files":  output_files,
        "files_on_disk": len(output_files),
        "date":          entry.get("date", ""),
    })


@app.route("/metrics/export", methods=["POST"])
def metrics_export():
    d              = request.json
    period_label   = d.get("period_label", "Período selecionado")
    summary        = d.get("summary", {})
    store_totals   = d.get("store_totals", {})
    all_prods      = d.get("all_products_data", [])
    state_totals   = d.get("state_totals", {})

    total_orders = summary.get("total_orders", 0)
    store_rows = sorted(store_totals.items(), key=lambda x: x[1].get("orders", 0), reverse=True)
    state_rows = sorted(state_totals.items(), key=lambda x: x[1], reverse=True)
    total_states = sum(v for _, v in state_rows)

    def pct(n, t): return f"{round(n/t*100)}%" if t else "—"

    rows_store = "".join(
        f"<tr><td>{s}</td><td class='n'>{v.get('orders',0)}</td>"
        f"<td class='n'>{pct(v.get('orders',0),total_orders)}</td>"
        f"<td class='n'>{v.get('units',0)}</td></tr>"
        for s, v in store_rows
    )
    prod_blocks = ""
    for p in all_prods:
        cat  = p.get("cat", "")
        disp = p.get("display", cat)
        st   = p.get("store_totals", {})
        if not st: continue
        tot  = p.get("total_units", 0)
        ul   = "peças" if cat == "roupa" else "potes"
        rows = "".join(
            f"<tr><td>{s}</td><td class='n'>{v}</td><td class='n'>{pct(v,tot)}</td></tr>"
            for s, v in sorted(st.items(), key=lambda x: x[1], reverse=True)
        )
        prod_blocks += f"""
        <div class="pb">
          <div class="pn">{disp} <span class="badge">{ul}</span></div>
          <table><tr><th>Loja</th><th class='n'>{ul.capitalize()}</th><th class='n'>%</th></tr>{rows}</table>
        </div>"""

    state_block = ""
    if state_rows:
        state_rows_html = "".join(
            f"<tr><td>{uf}</td><td class='n'>{cnt}</td><td class='n'>{pct(cnt,total_states)}</td></tr>"
            for uf, cnt in state_rows
        )
        state_block = f"""
        <div class="sec">Estados</div>
        <table><tr><th>UF</th><th class='n'>Pedidos</th><th class='n'>%</th></tr>{state_rows_html}</table>"""

    html = f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<title>REPZILLA — Métricas</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:Arial,sans-serif;color:#1a1a2e;padding:32px;font-size:13px;max-width:860px;margin:auto}}
h1{{font-size:24px;font-weight:900;letter-spacing:-1px;margin-bottom:3px}}
.sub{{font-size:11px;color:#888;margin-bottom:24px}}
.sec{{font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:1px;color:#8888a8;margin:22px 0 8px;border-bottom:2px solid #e2e2ec;padding-bottom:4px}}
.stat-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:0}}
.sb{{border:1px solid #e2e2ec;border-radius:8px;padding:14px;text-align:center}}
.sv{{font-size:28px;font-weight:900;line-height:1;margin-bottom:3px}}
.sl{{font-size:10px;color:#8888a8}}
table{{width:100%;border-collapse:collapse;margin-bottom:0}}
th{{font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.5px;color:#8888a8;text-align:left;padding:6px 8px;border-bottom:2px solid #e2e2ec}}
td{{padding:7px 8px;border-bottom:1px solid #f0f0f8;font-size:13px}}
.n{{text-align:right;font-weight:700;font-variant-numeric:tabular-nums}}
.pb{{margin-top:14px}}
.pn{{font-size:14px;font-weight:700;margin-bottom:6px}}
.badge{{font-size:9px;font-weight:800;text-transform:uppercase;padding:2px 7px;border-radius:4px;background:#f0f0f8;color:#8888a8;margin-left:6px;vertical-align:middle}}
.print-btn{{margin-bottom:20px}}
.print-btn button{{padding:9px 22px;background:#6c5fff;color:#fff;border:none;border-radius:7px;font-size:13px;font-weight:700;cursor:pointer}}
@media print{{.print-btn{{display:none}}.pb{{page-break-inside:avoid}}}}
</style></head><body>
<div class="print-btn"><button onclick="window.print()">🖨️ Salvar como PDF</button></div>
<h1>REPZILLA</h1>
<p class="sub">Relatório de Métricas &nbsp;·&nbsp; {period_label}</p>
<div class="sec">Resumo</div>
<div class="stat-grid">
  <div class="sb"><div class="sv">{summary.get('total_orders',0)}</div><div class="sl">pedidos totais</div></div>
  <div class="sb"><div class="sv">{summary.get('total_units',0)}</div><div class="sl">unidades totais</div></div>
  <div class="sb"><div class="sv">{len(store_totals)}</div><div class="sl">lojas ativas</div></div>
  <div class="sb"><div class="sv">{summary.get('total_entries',0)}</div><div class="sl">expedições</div></div>
</div>
<div class="sec">Pedidos por Loja</div>
<table><tr><th>Loja</th><th class='n'>Pedidos</th><th class='n'>%</th><th class='n'>Unidades</th></tr>{rows_store}</table>
<div class="sec">Análise por Produto</div>
{prod_blocks}
{state_block}
</body></html>"""
    from flask import Response
    return Response(html, mimetype="text/html")


@app.route("/ml/upload", methods=["POST"])
def ml_upload():
    files = request.files.getlist("pdfs")
    if not files or all(f.filename == "" for f in files):
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    mappings    = load_mappings()
    saved       = []
    all_unknown = {}
    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            continue
        pdf_path = UPLOAD_DIR / file.filename
        file.save(str(pdf_path))
        saved.append(file.filename)
        for u in get_unknown_items_ml(pdf_path, mappings):
            all_unknown.setdefault(u["key"], u)
    if not saved:
        return jsonify({"error": "Nenhum PDF válido encontrado"}), 400
    return jsonify({"filenames": saved, "unknown": list(all_unknown.values())})


@app.route("/ml/process", methods=["POST"])
def ml_process():
    data       = request.json
    filenames  = data.get("filenames", [])
    label_date = data.get("label_date", "")
    if not filenames:
        return jsonify({"error": "Nenhum arquivo especificado"}), 400
    mappings    = load_mappings()
    all_unknown = {}
    for filename in filenames:
        pdf_path = UPLOAD_DIR / filename
        if not pdf_path.exists():
            return jsonify({"error": f"Arquivo não encontrado: {filename}"}), 404
        for u in get_unknown_items_ml(pdf_path, mappings):
            all_unknown.setdefault(u["key"], u)
    if all_unknown:
        return jsonify({"error": "Ainda há produtos não classificados",
                        "unknown": list(all_unknown.values())}), 400
    return jsonify(split_ml_pdfs(filenames, mappings, label_date=label_date))


@app.route("/ml/download-all", methods=["POST"])
def ml_download_all():
    data      = request.json
    filenames = data.get("filenames", [])
    today     = date.today().strftime("%d.%m.%y")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in filenames:
            path = OUTPUT_DIR / fname
            if path.exists():
                stem = fname.replace(".pdf", "").replace("ml_", "").replace("_", " ")
                zf.write(str(path), f"ML {stem} {today}.pdf")
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f"ml_etiquetas_{today}.zip",
                     mimetype="application/zip")


@app.route("/order-locator", methods=["POST"])
def order_locator():
    """Search tracking numbers in the index. Returns found/not-found per number."""
    data     = request.json
    numbers  = [n.strip().upper() for n in data.get("numbers", []) if n.strip()]
    index    = load_tracking_index()
    found    = {}
    not_found = []
    for tn in numbers:
        if tn in index:
            entry = index[tn]
            # Check if source PDF still exists on disk
            src_available = (UPLOAD_DIR / entry.get("filename", "")).exists() if entry.get("filename") else False
            found[tn] = {**entry, "src_available": src_available}
        else:
            not_found.append(tn)
    return jsonify({"found": found, "not_found": not_found})


@app.route("/order-locator/download", methods=["POST"])
def order_locator_download():
    """Extract label pages for given tracking numbers and return as merged PDF."""
    data    = request.json
    numbers = [n.strip().upper() for n in data.get("numbers", []) if n.strip()]
    index   = load_tracking_index()

    writer  = PdfWriter()
    missing = []
    for tn in numbers:
        if tn not in index:
            missing.append(tn)
            continue
        entry    = index[tn]
        pdf_path = UPLOAD_DIR / entry.get("filename", "")
        page_num = entry.get("page", 1) - 1  # 0-indexed
        if not pdf_path.exists():
            missing.append(tn)
            continue
        try:
            reader = PdfReader(str(pdf_path))
            if 0 <= page_num < len(reader.pages):
                writer.add_page(reader.pages[page_num])
        except Exception:
            missing.append(tn)

    if not writer.pages:
        return jsonify({"error": "Nenhuma etiqueta encontrada no disco"}), 404

    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)
    today = date.today().strftime("%d.%m.%y")
    return send_file(buf, as_attachment=True,
                     download_name=f"pedidos_localizados_{today}.pdf",
                     mimetype="application/pdf")


def _split_pdf_columns(src_bytes, num_cols=None, with_selo=False):
    """Corta PDF com etiquetas lado a lado e retorna PDF com uma etiqueta por página.
    Usa insert_pdf + manipulação do content stream para preservar texto extraível,
    idêntico ao comportamento do sistema corta-etiqueta original (pdf-lib).
    Constantes calibradas no sistema original: TOP_CROP=20 (PN), CONTENT_H=355 (TN)."""
    TARGET_W  = 283.46  # 100 mm
    TARGET_H  = 425.20  # 150 mm
    TOP_CROP  = 20
    CONTENT_H = 355
    AJUSTE_X  = -5      # AJUSTE_X_GLOBAL do sistema original

    # Carimbo "Conferido" — medidas do selo-config.ts (coords PDF: y=0 embaixo)
    SELO_X      = 165
    SELO_Y_PDF  = 205   # distância da borda inferior
    SELO_W      = 80
    SELO_H      = 80

    selo_bytes = None
    if with_selo:
        selo_path = Path(__file__).parent / "conferido.png"
        if selo_path.exists():
            selo_bytes = selo_path.read_bytes()

    src_doc = fitz.open(stream=src_bytes, filetype="pdf")
    out_doc = fitz.open()

    for page_num in range(len(src_doc)):
        page = src_doc[page_num]
        w, h = page.rect.width, page.rect.height

        if num_cols is None:
            ratio = w / h if h else 1
            if   ratio > 2.5: cols = 4
            elif ratio > 1.2: cols = 4
            elif ratio > 0.6: cols = 2
            else:              cols = 1
        else:
            cols = int(num_cols)

        col_w = w / cols

        for col in range(cols):
            col_x0 = col * col_w
            col_x1 = (col + 1) * col_w

            if not page.get_text("blocks", clip=fitz.Rect(col_x0, 0, col_x1, h)):
                continue

            # Copia página completa preservando fontes e recursos (texto extraível)
            out_doc.insert_pdf(src_doc, from_page=page_num, to_page=page_num)
            out_page = out_doc[-1]

            # Matriz de transformação: recorta coluna e escala para TARGET_W × TARGET_H
            # Em coordenadas PDF nativas (y=0 embaixo):
            # clip inferior: h - TOP_CROP - CONTENT_H  (ex.: 612-20-355=237)
            scale_x = TARGET_W / col_w       # ~1.431 para 198→283.46
            scale_y = TARGET_H / CONTENT_H   # ~1.197 para 355→425.2
            pdf_y_bot = h - TOP_CROP - CONTENT_H
            start_x = col_x0 + AJUSTE_X      # AJUSTE_X_GLOBAL do sistema original
            tx = -start_x * scale_x
            ty = -pdf_y_bot * scale_y

            # Envolve o content stream original com a transformação
            original = out_page.read_contents()
            new_content = (
                f"q {scale_x:.6f} 0 0 {scale_y:.6f} {tx:.6f} {ty:.6f} cm\n"
                .encode()
                + original
                + b"\nQ\n"
            )

            # Grava no primeiro xref de content e aponta /Contents para ele
            content_xrefs = out_page.get_contents()
            out_doc.update_stream(content_xrefs[0], new_content)
            out_page.set_contents(content_xrefs[0])

            # Define tamanho da página de saída e remove CropBox residual
            out_page.set_mediabox(fitz.Rect(0, 0, TARGET_W, TARGET_H))
            out_doc.xref_set_key(out_page.xref, "CropBox", "null")

            # Overlay do carimbo "Conferido" (coords PDF y-up → fitz y-down)
            if selo_bytes:
                selo_y0 = TARGET_H - SELO_Y_PDF - SELO_H  # topo em fitz
                out_page.insert_image(
                    fitz.Rect(SELO_X, selo_y0, SELO_X + SELO_W, selo_y0 + SELO_H),
                    stream=selo_bytes,
                    overlay=True,
                )

    try:
        cat_xref = out_doc.pdf_catalog()
        out_doc.xref_set_key(cat_xref, "ViewerPreferences", "<</PrintScaling /None>>")
    except Exception:
        pass

    result = out_doc.tobytes(garbage=4, deflate=True)
    out_doc.close()
    src_doc.close()
    return result


def _split_ml_full(src_bytes, labels_per_page=3, num_cols=4):
    """Corta PDF de etiquetas Full do Mercado Livre (grade cols × linhas)
    e agrupa labels_per_page etiquetas por página A4, empilhadas verticalmente."""
    OUT_W = 595.0   # A4 retrato
    OUT_H = 842.0

    src_doc = fitz.open(stream=src_bytes, filetype="pdf")
    cells = []  # (page_num, clip_rect)

    for page_num in range(len(src_doc)):
        page    = src_doc[page_num]
        w, h    = page.rect.width, page.rect.height
        col_w   = w / num_cols

        # Detecta linhas horizontais separadoras via desenhos do PDF
        y_lines = set()
        for d in page.get_drawings():
            r = d.get("rect")
            if r and r.width >= w * 0.7:
                y_lines.add(round(r.y0))
                y_lines.add(round(r.y1))

        y_bounds = sorted(y_lines)

        # Fallback: clusteriza por posição Y dos blocos de texto
        if len(y_bounds) < 3:
            blocks = page.get_text("blocks")
            raw_ys = sorted(set(b[1] for b in blocks if b[4].strip()))
            y_bounds = [0.0]
            prev = -999.0
            for y in raw_ys:
                if y - prev > 15:
                    if y_bounds:
                        y_bounds.append((prev + y) / 2 if prev > 0 else y)
                    prev = y
            y_bounds.append(h)
            y_bounds = sorted(set(round(v) for v in y_bounds))

        for ri in range(len(y_bounds) - 1):
            y0, y1 = y_bounds[ri], y_bounds[ri + 1]
            if y1 - y0 < 10:
                continue
            for ci in range(num_cols):
                clip = fitz.Rect(ci * col_w, y0, (ci + 1) * col_w, y1)
                if page.get_text("text", clip=clip).strip():
                    cells.append((page_num, clip))

    out_doc  = fitz.open()
    slot_h   = OUT_H / labels_per_page

    for i in range(0, len(cells), labels_per_page):
        batch    = cells[i:i + labels_per_page]
        out_page = out_doc.new_page(width=OUT_W, height=OUT_H)
        for slot, (pn, clip) in enumerate(batch):
            dest = fitz.Rect(0, slot * slot_h, OUT_W, (slot + 1) * slot_h)
            out_page.show_pdf_page(dest, src_doc, pn, clip=clip, keep_proportion=False)

    try:
        cat = out_doc.pdf_catalog()
        out_doc.xref_set_key(cat, "ViewerPreferences", "<</PrintScaling /None>>")
    except Exception:
        pass

    result = out_doc.tobytes(garbage=4, deflate=True)
    out_doc.close()
    src_doc.close()
    return result


@app.route("/cortar-ml", methods=["POST"])
def cortar_ml_full():
    f = request.files.get("pdf")
    if not f or not f.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Envie um arquivo PDF válido"}), 400
    try:
        result = _split_ml_full(f.read(), labels_per_page=3, num_cols=4)
    except Exception as e:
        return jsonify({"error": f"Erro ao processar PDF: {e}"}), 500
    buf = io.BytesIO(result)
    buf.seek(0)
    safe_name = re.sub(r"[^\w.\-]", "_", f.filename.rsplit(".", 1)[0])
    return send_file(buf, mimetype="application/pdf",
                     as_attachment=True,
                     download_name=f"ml_cortado_{safe_name}.pdf")


@app.route("/cortar", methods=["POST"])
def cortar_etiquetas():
    f = request.files.get("pdf")
    if not f or not f.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Envie um arquivo PDF válido"}), 400
    cols_param = request.form.get("cols", "auto")
    num_cols   = None if cols_param == "auto" else int(cols_param)
    with_selo  = request.form.get("selo") == "1"
    try:
        result = _split_pdf_columns(f.read(), num_cols, with_selo=with_selo)
    except Exception as e:
        return jsonify({"error": f"Erro ao processar PDF: {e}"}), 500
    buf = io.BytesIO(result)
    buf.seek(0)
    safe_name = re.sub(r"[^\w.\-]", "_", f.filename.rsplit(".", 1)[0])
    return send_file(buf, mimetype="application/pdf",
                     as_attachment=True,
                     download_name=f"cortado_{safe_name}.pdf")


@app.route("/caixas/gerar", methods=["POST"])
def caixas_gerar():
    data     = request.get_json()
    date_iso = data.get("date", "")
    address  = data.get("address", "").strip()
    boxes    = data.get("boxes", [])
    total    = len(boxes)
    if not boxes:
        return jsonify({"error": "Nenhuma caixa informada"}), 400

    # Formata data de YYYY-MM-DD para DD/MM/YYYY
    try:
        from datetime import datetime as _dt
        date_display = _dt.strptime(date_iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        date_display = date_iso

    doc = fitz.open()
    W, H = 595, 842  # A4 retrato
    M    = 45        # margem horizontal

    for i, box in enumerate(boxes):
        num_pedidos = int(box.get("pedidos", 0))
        box_num     = i + 1
        page        = doc.new_page(width=W, height=H)

        # "N PEDIDOS" — negrito, muito grande
        r = fitz.Rect(M, 70, W - M, 230)
        page.insert_textbox(r, f"{num_pedidos} PEDIDOS",
                            fontsize=80, fontname="hebo",
                            align=fitz.TEXT_ALIGN_CENTER, color=(0, 0, 0))

        # "CAIXA X/Y" — negrito, muito grande
        r = fitz.Rect(M, 250, W - M, 430)
        page.insert_textbox(r, f"CAIXA {box_num}/{total}",
                            fontsize=80, fontname="hebo",
                            align=fitz.TEXT_ALIGN_CENTER, color=(0, 0, 0))

        # Data — itálico, grande
        r = fitz.Rect(M, 445, W - M, 570)
        page.insert_textbox(r, date_display,
                            fontsize=54, fontname="heit",
                            align=fitz.TEXT_ALIGN_CENTER, color=(0, 0, 0))

        # Endereço — itálico, menor, sublinhado manual
        if address:
            addr_size = 17
            r_addr = fitz.Rect(M, 640, W - M, 780)
            page.insert_textbox(r_addr, address,
                                fontsize=addr_size, fontname="heit",
                                align=fitz.TEXT_ALIGN_CENTER, color=(0, 0, 0))
            # Sublinhado: calcula largura do texto e desenha linha(s)
            lines = []
            words = address.split()
            current = ""
            line_w = W - 2 * M
            for word in words:
                test = (current + " " + word).strip()
                tw = fitz.get_text_length(test, fontname="heit", fontsize=addr_size)
                if tw <= line_w:
                    current = test
                else:
                    if current:
                        lines.append(current)
                    current = word
            if current:
                lines.append(current)
            line_h = addr_size * 1.35
            for li, line in enumerate(lines):
                tw  = fitz.get_text_length(line, fontname="heit", fontsize=addr_size)
                x0  = (W - tw) / 2
                x1  = x0 + tw
                y_u = 640 + (li + 1) * line_h + 1
                page.draw_line(fitz.Point(x0, y_u), fitz.Point(x1, y_u),
                               color=(0, 0, 0), width=0.7)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    doc.close()
    return send_file(buf, mimetype="application/pdf",
                     as_attachment=True, download_name="caixas.pdf")


if __name__ == "__main__":
    import webbrowser, threading, time
    threading.Thread(target=lambda: (time.sleep(1), webbrowser.open("http://localhost:5000")),
                     daemon=True).start()
    app.run(debug=False, port=5000, use_reloader=False)
