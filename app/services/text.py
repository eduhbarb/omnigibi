"""Normalizacao de texto, extracao de precos, volumes/edicoes e similaridade.

Um ponto central deste modulo e a **identidade do quadrinho**: volume, numero
de edicao e "ano" fazem parte da identidade da obra. Dois titulos iguais com
volumes diferentes sao quadrinhos diferentes e jamais devem ser tratados como
o mesmo produto (o que contaminaria a comparacao de precos e o historico).
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Termos que nao ajudam a identificar a obra em uma busca de quadrinhos.
STOPWORDS = {
    "a", "o", "as", "os", "um", "uma", "de", "da", "do", "das", "dos", "e", "em",
    "no", "na", "nos", "nas", "para", "por", "com", "sem", "que", "the", "of",
    "and", "edicao", "edição", "ed", "volume", "vol", "numero", "número", "n",
    "capa", "dura", "brochura", "novo", "nova", "lacrado", "original", "kit",
    "colecao", "coleção", "livro", "comic", "comics", "quadrinhos", "quadrinho",
    # "parte/part/tomo/book" entram junto com vol/volume como ruido de edicao.
    "hq", "hqs", "graphic", "novel", "box", "leia", "promocao", "promoção",
    "parte", "part", "tomo", "book", "serie", "série",
    # ruido de marketplace/importados
    "importado", "reimpressao", "relançamento", "frete", "gratis", "grátis",
    "envio", "imediato", "pronta", "entrega", "usado", "seminovo",
}

# Ruido tipico de anuncios de marketplace.
NOISE_PATTERNS = (
    r"frete\s+gr[aá]tis",
    r"envio\s+imediato",
    r"pronta\s+entrega",
    r"lacrado",
    r"original",
    r"importado",
)

# Canonicalizacao de tokens: grafias diferentes do mesmo marcador.
# "variant cover" (ingles) e "capa variante" (pt) sao a mesma edicao.
TOKEN_CANON = {"variant": "variante"}

# Numeros escritos por extenso usados como volume ("Ano Um", "Volume Dois").
NUMBER_WORDS = {
    "um": "1", "uma": "1", "dois": "2", "duas": "2", "tres": "3", "quatro": "4",
    "cinco": "5", "seis": "6", "sete": "7", "oito": "8", "nove": "9", "dez": "10",
    "onze": "11", "doze": "12", "treze": "13", "quatorze": "14", "catorze": "14",
    "quinze": "15", "dezesseis": "16", "dezessete": "17", "dezoito": "18",
    "dezenove": "19", "vinte": "20",
}

# Algarismos romanos (so convertidos quando seguem um marcador de volume).
ROMAN_NUMERALS = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7",
    "viii": "8", "ix": "9", "x": "10", "xi": "11", "xii": "12", "xiii": "13",
}

_WORDS_ALT = "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
_ROMANS_ALT = "|".join(sorted(ROMAN_NUMERALS, key=len, reverse=True))
_MARKERS_ALT = "vol|volume|book|livro|parte|tomo|part"

# Titulo terminando em numero solto ("... 3") tambem indica volume.
_TRAILING_NUMBER_RE = re.compile(r"[^\d\s]\s+(\d{1,3})\s*$")

# Regras (em ordem de prioridade) para descobrir o volume/edicao de um titulo.
_VOLUME_RULES: tuple[re.Pattern[str], ...] = (
    re.compile(r"#\s*(\d{1,4})"),
    re.compile(r"\bn[º°o]\.?\s*(\d{1,4})", re.IGNORECASE),
    re.compile(rf"\b(?:{_MARKERS_ALT})\b\.?\s*(\d{{1,4}})", re.IGNORECASE),
    re.compile(rf"\b(?:{_MARKERS_ALT})\b\.?\s*({_WORDS_ALT}|{_ROMANS_ALT})\b", re.IGNORECASE),
    re.compile(r"\bano\s+(\d{1,4})", re.IGNORECASE),
    re.compile(rf"\bano\s+({_WORDS_ALT}|{_ROMANS_ALT})\b", re.IGNORECASE),
)

_PRICE_RE = re.compile(
    r"R\$\s*(?P<value>\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d+(?:[.,]\d{2})?)",
    re.IGNORECASE,
)


def strip_accents(text: str) -> str:
    """Remove acentos preservando as letras."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


# --------------------------------------------------------------------- volumes
def number_to_digit(value: str) -> str | None:
    """Converte '01', 'um', 'II' no digito canonico ('1', '1', '2')."""
    if not value:
        return None
    token = value.strip().strip(".").lower()
    if not token:
        return None
    if token.isdigit():
        return str(int(token))
    if token in NUMBER_WORDS:
        return NUMBER_WORDS[token]
    if token in ROMAN_NUMERALS:
        return ROMAN_NUMERALS[token]
    return None


def _find_volume_match(text: str) -> re.Match[str] | None:
    """Primeira expressao de volume valida no texto (por ordem de prioridade)."""
    for rule in _VOLUME_RULES:
        for match in rule.finditer(text):
            if number_to_digit(match.group(1)) is not None:
                return match
    return _TRAILING_NUMBER_RE.search(text)


def extract_volume(text: str) -> str | None:
    """Descobre o volume/edicao de um titulo.

    >>> extract_volume("Batman & Robin: Ano Um Vol. 02")
    '2'
    >>> extract_volume("Batman & Robin: Ano Um")
    '1'
    >>> extract_volume("Watchmen - Edicao Definitiva") is None
    True
    """
    if not text:
        return None
    match = _find_volume_match(strip_accents(str(text)))
    if match is None:
        return None
    return number_to_digit(match.group(1))


def volumes_compatible(volume_a: str | None, volume_b: str | None) -> bool:
    """Dois itens podem ser o mesmo produto?

    Volumes diferentes => produtos diferentes. Volume desconhecido de um dos
    lados nao bloqueia (a loja pode ter omitido o numero do titulo).
    """
    if volume_a is None or volume_b is None:
        return True
    return volume_a == volume_b


# ------------------------------------------------------------------- variantes
# "capa variante" NAO e o mesmo produto que a capa normal, mesmo com o titulo
# base igual. Sem isso, "... capa variante" era tratado como sufixo de loja e as
# duas edicoes eram fundidas num unico produto.
_VARIANT_TOKENS = {"variante", "variant"}


def extract_variant(text: str) -> str | None:
    """Marcador de edicao variante (\'variante\'/\'variant\'), se houver.

    >>> extract_variant("Absolute Batman 01 - Capa Variante")
    'variante'
    >>> extract_variant("Absolute Batman 01") is None
    True
    """
    if not text:
        return None
    tokens = set(re.findall(r"[a-z]+", strip_accents(str(text)).lower()))
    if tokens & _VARIANT_TOKENS:
        return "variante"
    return None


def variants_compatible(variant_a: str | None, variant_b: str | None) -> bool:
    """Uma capa variante so e o mesmo produto que outra capa variante."""
    return variant_a == variant_b


# ------------------------------------------------------------------ normalize
def _canonicalize_volume_number(text: str) -> str:
    """Canonicaliza SO a expressao de volume principal do titulo.

    Em "Batman & Robin: Ano Um Vol. 02" o volume e o "02"; o "Ano Um" faz parte
    do nome da obra e nao pode ser confundido com o volume. Ja em
    "Batman & Robin: Ano Um" e o proprio "Ano Um" que identifica a edicao.
    """
    match = _find_volume_match(text)
    if match is None:
        return text
    digit = number_to_digit(match.group(1))
    if digit is None:
        return text
    # troca apenas o numero (preserva marcador: "ano um" -> "ano 1", "vol. 02" -> "vol. 2")
    return f"{text[: match.start(1)]}{digit}{text[match.end(1):]}"


def normalize(text: str) -> str:
    """Normaliza um titulo: minusculo, sem acento, sem ruido, com volume canonico.

    >>> normalize("Batman & Robin: Ano Um Vol. 01")
    'batman robin ano 1'
    """
    if not text:
        return ""
    clean = strip_accents(str(text)).lower()
    clean = re.sub(r"[\u2013\u2014\-_/|&+]+", " ", clean)
    for pattern in NOISE_PATTERNS:
        clean = re.sub(pattern, " ", clean)
    clean = _canonicalize_volume_number(clean)
    clean = re.sub(r"[^a-z0-9\s#.:]+", " ", clean)

    tokens: list[str] = []
    seen_numbers: set[str] = set()
    for raw_token in clean.split():
        token = raw_token.strip(".:#-")
        if not token:
            continue
        token = TOKEN_CANON.get(token, token)
        if token.isdigit():
            # Numeros NUNCA sao descartados: "#1" x "#2" distingue os volumes.
            # Repeticoes do mesmo numero vem do titulo citar o volume duas vezes
            # ("Ano Um Vol. 01" -> "ano 1 1"), entao colapsamos a duplicata.
            digit = str(int(token))
            if digit in seen_numbers:
                continue
            seen_numbers.add(digit)
            tokens.append(digit)
            continue
        if token in STOPWORDS or len(token) < 2:
            continue
        tokens.append(token)

    return " ".join(tokens).strip()


def token_set(text: str) -> set[str]:
    return set(normalize(text).split())


def base_title(text: str) -> str:
    """Titulo normalizado sem o volume (agrupa os volumes de uma mesma obra)."""
    volume = extract_volume(text)
    normalized = normalize(text)
    if volume is None:
        return normalized
    tokens = [tok for tok in normalized.split() if tok != volume]
    return " ".join(tokens)


# --------------------------------------------------------------------- precos
def parse_price(raw: str | float | int | None) -> float | None:
    """Converte 'R$ 1.234,56', '1234.56' ou 1234.56 em float."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return round(float(raw), 2)

    text = str(raw).strip()
    if not text:
        return None

    match = _PRICE_RE.search(text)
    candidate = match.group("value") if match else text
    candidate = re.sub(r"[^\d.,]", "", candidate)
    if not candidate:
        return None

    if "," in candidate and "." in candidate:
        # Formato pt-BR: 1.234,56
        candidate = candidate.replace(".", "").replace(",", ".")
    elif "," in candidate:
        candidate = candidate.replace(",", ".")
    elif candidate.count(".") > 1:
        candidate = candidate.replace(".", "")

    try:
        value = float(candidate)
    except ValueError:
        return None
    return round(value, 2) if value > 0 else None


def parse_price_range(raw: str) -> tuple[float | None, float | None]:
    """Extrai (preco_cheio, preco_atual) de textos como 'De R$ 89,90 por R$ 49,90'."""
    prices = [parse_price(m.group(0)) for m in _PRICE_RE.finditer(raw or "")]
    prices = [p for p in prices if p]
    if not prices:
        return None, None
    if len(prices) == 1:
        return None, prices[0]
    return max(prices), min(prices)


# ---------------------------------------------------------------- similaridade
# Quando os volumes sao explicitamente diferentes, a similaridade e derrubada
# para que nenhum agrupamento (ou monitoramento) misture volumes distintos.
INCOMPATIBLE_VOLUME_FACTOR = 0.35

# Penalidade quando o termo mais distintivo da busca nao aparece no titulo.
LEADING_TOKEN_MISSING_FACTOR = 0.45

# Quando o titulo cobre pouquissimos termos da busca ele e apenas parecido.
LOW_COVERAGE_MIN = 0.6
LOW_COVERAGE_FACTOR = 0.5

# Jaccard minimo para considerar dois titulos como o mesmo produto.
PRODUCT_JACCARD_THRESHOLD = 0.85


def similarity(a: str, b: str) -> float:
    """Similaridade 0..1 combinando Jaccard de tokens e SequenceMatcher."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0

    incompatible = not volumes_compatible(extract_volume(a), extract_volume(b))
    if na == nb:
        return 0.25 if incompatible else 1.0

    ta, tb = set(na.split()), set(nb.split())
    jaccard = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    containment = 0.0
    if ta and tb:
        shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        containment = len(shorter & longer) / len(shorter)

    score = (jaccard * 0.3) + (ratio * 0.4) + (containment * 0.3)
    if incompatible:
        score *= INCOMPATIBLE_VOLUME_FACTOR
    return round(min(score, 1.0), 4)


def relevance(query: str, title: str) -> float:
    """Pontuacao de relevancia da busca, com bonus quando todos os tokens batem."""
    base = similarity(query, title)
    q_tokens, t_tokens = token_set(query), token_set(title)
    # O bonus de cobertura so vale quando a identidade (volume) e compativel.
    if q_tokens and q_tokens.issubset(t_tokens):
        if volumes_compatible(extract_volume(query), extract_volume(title)):
            base = min(1.0, base + 0.2)
    if normalize(query) == normalize(title):
        return 1.0

    # O primeiro termo da busca costuma ser a serie/personagem — a parte mais
    # distintiva. Se ele nao aparece no titulo, o item e apenas "relacionado"
    # (ex.: buscar "Batman & Robin: Ano Um" e casar com "Tim Drake: Robin").
    query_tokens = normalize(query).split()
    if query_tokens and query_tokens[0] not in t_tokens:
        base *= LEADING_TOKEN_MISSING_FACTOR

    # Titulos que compartilham poucos termos da busca sao apenas parecidos
    # (ex.: "Batman Renascido" para a busca "Batman & Robin: Ano Um").
    if q_tokens:
        coverage = len(q_tokens & t_tokens) / len(q_tokens)
        if coverage < LOW_COVERAGE_MIN:
            base *= LOW_COVERAGE_FACTOR

    return round(min(base, 1.0), 4)


def same_product(title_a: str, title_b: str) -> bool:
    """Decide se duas ofertas sao o MESMO produto (para agrupar precos).

    Regras (todas exigem volume compativel):
    1. titulos normalizados identicos -> mesmo produto;
    2. um titulo e o outro acrescido de sufixo (editora, acabamento, vendedor)
       -> mesmo produto, desde que o nucleo tenha ao menos 2 termos;
    3. caso contrario, exige conjuntos de tokens quase identicos (Jaccard alto),
       o que evita fundir livros diferentes ("Robin: Ano Um" nao e
       "Batman & Robin: Ano Um").

    Alem do volume, a **capa variante** tambem e identidade: a capa normal e a
    capa variante do mesmo numero sao produtos distintos.
    """
    if not volumes_compatible(extract_volume(title_a), extract_volume(title_b)):
        return False
    if not variants_compatible(extract_variant(title_a), extract_variant(title_b)):
        return False

    na, nb = normalize(title_a), normalize(title_b)
    if not na or not nb:
        return False
    if na == nb:
        return True

    # 2. um titulo e o outro acrescido de sufixo (editora, acabamento, vendedor):
    #    compara nos dois sentidos, exigindo fronteira de palavra no prefixo.
    #    Um nucleo de 2+ termos ja e especifico o bastante ("watchmen definitiva"
    #    ≈ "watchmen definitiva - panini"); com 1 termo nao ("watchmen" x
    #    "watchmen definitiva", que sao edicoes diferentes).
    for candidate, extended in ((na, nb), (nb, na)):
        if len(candidate.split()) >= 2 and extended.startswith(candidate + " "):
            return True

    ta, tb = set(na.split()), set(nb.split())
    jaccard = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    return jaccard >= PRODUCT_JACCARD_THRESHOLD


def expand_query_variants(query: str, max_variants: int = 3) -> list[str]:
    """Variacoes de busca que aumentam a recall do lado da loja.

    Muitos buscadores sao sensiveis a pontuacao ("Batman & Robin: Ano Um" pode
    nao achar nada, enquanto "Batman Robin Ano Um" acha). A variacao tambem
    neutraliza separadores que os sites tratam como filtros.
    """
    query = (query or "").strip()
    if not query:
        return []

    variants: list[str] = []

    def _add(value: str) -> None:
        value = re.sub(r"\s+", " ", value).strip()
        if value and value.lower() not in {v.lower() for v in variants}:
            variants.append(value)

    _add(query)

    # 1. pontuacao removida e "&" virando "e"
    simplified = re.sub(r"[^\w\s]", " ", query)
    _add(simplified)
    _add(re.sub(r"\s*&\s*", " e ", query))

    # 2. sem conectivos ruidosos ("de", "do", "da") ajuda alguns catalogos
    without_stopwords = " ".join(
        tok for tok in re.sub(r"[^\w\s]", " ", query).split() if tok.lower() not in {"de", "do", "da", "dos", "das"}
    )
    _add(without_stopwords)

    return variants[:max_variants]


def guess_issue(title: str) -> str | None:
    """Numero da edicao/volume ('#12', 'nº 12', 'vol. 3', 'Ano Um')."""
    return extract_volume(title)


# Editoras/selos comuns em anuncios de quadrinhos no Brasil.
# O nome canonico e devolvido quando qualquer variacao aparece no texto.
PUBLISHERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Panini", ("panini",)),
    ("Darkside", ("darkside", "dark side")),
    ("Pipoca & Nanquim", ("pipoca e nanquim", "pipoca & nanquim", "pipoca nanquim")),
    ("Veneta", ("veneta",)),
    ("Devir", ("devir",)),
    ("NewPOP", ("newpop", "new pop")),
    ("JBC", ("jbc", "editora jbc")),
    ("Conrad", ("conrad",)),
    ("Mythos", ("mythos",)),
    ("Zarabatana", ("zarabatana",)),
    ("Mino", ("editora mino", "mino editora")),
    ("Nemo", ("nemo",)),
    ("Planeta", ("planeta deagostini", "planeta")),
    ("Salvat", ("salvat",)),
    ("Eaglemoss", ("eaglemoss",)),
    ("Marvel", ("marvel",)),
    ("DC Comics", ("dc comics",)),
    ("Dark Horse", ("dark horse",)),
    ("Image", ("image comics",)),
    ("Titan", ("titan books", "titan comics")),
)


def guess_publisher(*texts: str | None) -> str | None:
    """Descobre a editora mencionada em titulos/vendedor/rotulo da loja.

    Busca best-effort por nomes conhecidos de editoras. Devolve ``None`` quando
    nenhuma aparece — nesse caso a interface simplesmente omite a editora.
    """
    haystack = " " + strip_accents(" ".join(t for t in texts if t)).lower() + " "
    if not haystack.strip():
        return None
    for name, variants in PUBLISHERS:
        for variant in variants:
            if f" {strip_accents(variant).lower()} " in haystack:
                return name
    return None
