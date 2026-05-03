import io
import json
import re
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
MAPPINGS_FILE = BASE_DIR / "mappings.json"
BRANDS_FILE   = BASE_DIR / "brands.json"
HISTORY_FILE  = BASE_DIR / "history.json"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

BRAND_DISPLAY_DEFAULT = {
    "pura":  "Creatina Black Skull Pura",
    "turbo": "Creatina Black Skull Turbo",
    "dux":   "Creatina DUX",
}
STANDARD_ORDER = ["pura", "turbo", "dux"]

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


def mapping_key(produto, sku):
    # SKU is the stable unique identifier
    return sku.strip()


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
            qty = int(qty_words[0]) if qty_words else 0
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
                seen[key] = {"produto": item["produto"], "sku": item["sku"], "key": key}
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
    today = date.today()
    def in_period(entry_date_str):
        try:
            ed = date.fromisoformat(entry_date_str)
        except Exception:
            return False
        if period == "today":  return ed == today
        if period == "7d":     return (today - ed).days < 7
        if period == "15d":    return (today - ed).days < 15
        if period == "30d":    return (today - ed).days < 30
        if period == "custom" and from_date and to_date:
            return date.fromisoformat(from_date) <= ed <= date.fromisoformat(to_date)
        return True
    return in_period


def _date_range(period, filtered, from_date=None, to_date=None):
    today = date.today()
    if period == "today":   return today, today
    if period == "7d":      return today - timedelta(days=6), today
    if period == "15d":     return today - timedelta(days=14), today
    if period == "30d":     return today - timedelta(days=29), today
    if period == "custom" and from_date and to_date:
        return date.fromisoformat(from_date), date.fromisoformat(to_date)
    dates = [date.fromisoformat(e["date"]) for e in filtered if e.get("date")]
    if not dates:
        return today, today
    return min(dates), max(dates)


# ── Core split/merge ──────────────────────────────────────────────────────────

def compute_brand_totals(breakdown):
    brand_orders, brand_units = {}, {}
    for key, count in breakdown.items():
        m = re.match(r"^(.+)_(\d+)$", key)
        if not m:
            continue
        brand, upo = m.group(1), int(m.group(2))
        brand_orders[brand] = brand_orders.get(brand, 0) + count
        brand_units[brand]  = brand_units.get(brand, 0) + count * upo
    return brand_orders, brand_units


def split_pdfs_merged(filenames, mappings):
    """
    Process multiple PDFs and merge pages with the same output key into one PDF each.
    Returns stats + output file list.
    """
    writers            = {}  # output_key -> PdfWriter
    page_results       = []
    store_names        = []
    store_items        = {}  # store -> {sku_key -> {sku, produto, categoria, kit_size, units}}
    store_label_counts = {}  # store -> number of label pages (1 page = 1 customer order)
    state_counts       = {}  # UF -> number of orders shipped there
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

        for page_num in range(n_pages):
            fitz_page = doc[page_num]
            items     = extract_checklist(fitz_page)

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
                for item in items:
                    key = mapping_key(item["produto"], item["sku"])
                    if key not in mappings:
                        continue
                    info = mappings[key]
                    kit  = info.get("kit_size", 1)
                    qty  = item["quantidade"]
                    store_items.setdefault(store, {})
                    if key not in store_items[store]:
                        store_items[store][key] = {
                            "sku":       item["sku"],
                            "produto":   item["produto"],
                            "categoria": info["categoria"],
                            "kit_size":  kit,
                            "units":     0,
                        }
                    store_items[store][key]["units"] += kit * qty

        doc.close()

    # Write output PDFs
    output_files       = []
    output_page_counts = {}
    for key, writer in writers.items():
        out_path = OUTPUT_DIR / f"{key}.pdf"
        with open(out_path, "wb") as f:
            writer.write(f)
        output_files.append(f"{key}.pdf")
        output_page_counts[f"{key}.pdf"] = len(writer.pages)

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
        "verification": {
            "ok":           verified,
            "label_pages":  label_pages,
            "output_total": output_total,
        },
    }

    return {"output_files": output_files, "page_results": page_results,
            "stats": stats, "errors": []}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
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
        for u in get_unknown_items(pdf_path, mappings):
            all_unknown.setdefault(u["key"], u)

    if not saved:
        return jsonify({"error": "Nenhum PDF válido encontrado"}), 400

    format_warnings = {}
    for fname in saved:
        issues = check_label_format(UPLOAD_DIR / fname)
        if issues:
            format_warnings[fname] = issues

    return jsonify({
        "filenames":       saved,
        "unknown":         list(all_unknown.values()),
        "format_warnings": format_warnings,
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
    data      = request.json
    filenames = data.get("filenames", [])
    if not filenames:
        return jsonify({"error": "Nenhum arquivo especificado"}), 400

    mappings    = load_mappings()
    all_unknown = {}
    for filename in filenames:
        pdf_path = UPLOAD_DIR / filename
        if not pdf_path.exists():
            return jsonify({"error": f"Arquivo não encontrado: {filename}"}), 404
        for u in get_unknown_items(pdf_path, mappings):
            all_unknown.setdefault(u["key"], u)

    if all_unknown:
        return jsonify({"error": "Ainda há produtos não classificados",
                        "unknown": list(all_unknown.values())}), 400

    return jsonify(split_pdfs_merged(filenames, mappings))


@app.route("/download/<filename>")
def download(filename):
    today = date.today().strftime("%d.%m.%y")
    stem  = filename.replace(".pdf", "").replace("_", " ")
    return send_from_directory(str(OUTPUT_DIR), filename, as_attachment=True,
                               download_name=f"{stem} {today}.pdf")


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
    data    = request.json
    history = load_history()
    entry   = {
        "id":                datetime.now().strftime("%Y%m%d-%H%M%S"),
        "timestamp":         datetime.now().isoformat(),
        "date":              data.get("date") or date.today().isoformat(),
        "store_items":        data.get("store_items", {}),
        "store_label_counts": data.get("store_label_counts", {}),
        "state_counts":       data.get("state_counts", {}),
        "totals": {
            "label_pages": data.get("label_pages", 0),
            "breakdown":   data.get("breakdown", {}),
        },
    }
    history.append(entry)
    save_history(history)
    return jsonify({"ok": True, "id": entry["id"]})


@app.route("/history/metrics", methods=["POST"])
def history_metrics():
    data      = request.json
    stores    = data.get("stores", [])
    period    = data.get("period", "all")
    from_date = data.get("from_date")
    to_date   = data.get("to_date")
    history   = load_history()
    today     = date.today()

    def in_period(entry_date_str):
        try:
            ed = date.fromisoformat(entry_date_str)
        except Exception:
            return False
        if period == "today":  return ed == today
        if period == "7d":     return (today - ed).days < 7
        if period == "15d":    return (today - ed).days < 15
        if period == "30d":    return (today - ed).days < 30
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


if __name__ == "__main__":
    import webbrowser, threading, time
    threading.Thread(target=lambda: (time.sleep(1), webbrowser.open("http://localhost:5000")),
                     daemon=True).start()
    app.run(debug=False, port=5000, use_reloader=False)
