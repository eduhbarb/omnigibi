"""Testes de normalizacao de titulos, leitura de precos, volumes e similaridade."""

from __future__ import annotations

from app.services.text import (
    base_title,
    expand_query_variants,
    extract_variant,
    extract_volume,
    guess_issue,
    guess_publisher,
    normalize,
    parse_price,
    parse_price_range,
    relevance,
    same_product,
    similarity,
    variants_compatible,
    volumes_compatible,
)


def test_parse_price_formatos_brasileiros():
    assert parse_price("R$ 1.234,56") == 1234.56
    assert parse_price("R$ 89,90") == 89.90
    assert parse_price("49.90") == 49.90
    assert parse_price(120) == 120.0
    assert parse_price("De R$ 89,90 por R$ 49,90") == 89.90  # primeiro preco do texto
    assert parse_price("indisponivel") is None
    assert parse_price(None) is None


def test_parse_price_range_detecta_preco_cheio_e_atual():
    full, current = parse_price_range("De R$ 89,90 por R$ 49,90")
    assert full == 89.90
    assert current == 49.90

    full, current = parse_price_range("R$ 39,90")
    assert full is None
    assert current == 39.90


def test_normalize_remove_acentos_stopwords_e_ruido():
    assert normalize("Watchmen - Edição Definitiva") == "watchmen definitiva"
    assert normalize("HQ  V de Vingança") == "vinganca"
    assert normalize("") == ""
    assert normalize("Turma da Mônica") == "turma monica"


def test_similarity_e_relevance():
    assert similarity("Watchmen Edicao Definitiva", "Watchmen - Edicao Definitiva") == 1.0
    assert similarity("Watchmen", "Sandman") < 0.3
    assert relevance("Sandman", "Sandman - Edicao Definitiva Vol. 1") > 0.7
    assert relevance("Sandman", "Bone - Edicao Completa") < 0.34


def test_guess_issue():
    assert guess_issue("Turma da Monica #12") == "12"
    assert guess_issue("X-Men nº 7") == "7"
    assert guess_issue("Watchmen") is None


def test_normalize_preserva_o_numero_do_volume():
    """Regressao: o volume era descartado como ruido e Vol. 1 virava Vol. 2."""
    assert normalize("Batman & Robin: Ano Um Vol. 01") == "batman robin ano 1"
    assert normalize("Batman & Robin: Ano Um Vol. 02") == "batman robin ano 2"
    assert normalize("Batman & Robin: Ano Um Vol. 1") == "batman robin ano 1"
    assert normalize("Batman & Robin: Ano Um Vol. II") == "batman robin ano 2"
    # o numero do volume nao aparece duplicado quando o titulo cita "Ano Um"
    assert "1 1" not in normalize("Batman & Robin: Ano Um Vol. 01")


def test_extract_volume_identifica_edicao():
    assert extract_volume("Batman & Robin: Ano Um Vol. 02") == "2"
    assert extract_volume("Batman & Robin: Ano Um") == "1"  # "Ano Um" = primeiro
    assert extract_volume("Sandman - Edicao Definitiva Vol. III") == "3"
    assert extract_volume("Turma da Monica #12") == "12"
    assert extract_volume("Watchmen - Edicao Definitiva") is None
    # "V de Vinganca" nao e um volume
    assert extract_volume("V de Vinganca") is None
    assert volumes_compatible("1", "1") is True
    assert volumes_compatible("1", None) is True
    assert volumes_compatible("1", "2") is False


def test_base_title_agrupa_volumes_da_mesma_obra():
    assert base_title("Batman & Robin: Ano Um Vol. 01") == base_title(
        "Batman & Robin: Ano Um Vol. 02"
    )


def test_same_product_separa_volumes_e_livros_diferentes():
    # mesmo produto em lojas diferentes (titulos com variacao)
    assert same_product("Batman & Robin: Ano Um Vol. 01", "Batman & robin: ano um vol. 01")
    assert same_product(
        "Batman & Robin: Ano Um Vol. 01", "Batman & Robin: Ano Um - Editora Independente"
    )
    assert same_product("Watchmen - Edicao Definitiva", "Watchmen Edicao Definitiva Capa Dura")

    # volumes diferentes NUNCA sao o mesmo produto
    assert not same_product("Batman & Robin: Ano Um Vol. 01", "Batman & Robin: Ano Um Vol. 02")
    assert not same_product("Sandman - Edicao Definitiva Vol. 1", "Sandman Vol. II")

    # livros diferentes que compartilham palavras tambem nao
    assert not same_product("Batman & Robin: Ano Um Vol. 01", "Robin: Ano Um")
    assert not same_product("Batman & Robin: Ano Um Vol. 01", "Tim Drake: Robin Vol. 01")


def test_relevance_penaliza_serie_ausente_e_baixa_cobertura():
    alta = relevance("Batman & Robin: Ano Um", "Batman & Robin: Ano Um Vol. 01")
    assert alta == 1.0

    # outro livro do Robin nao pode pontuar como o proprio procurado
    outro = relevance("Batman & Robin: Ano Um", "Tim Drake: Robin Vol. 01")
    assert outro < 0.34
    # o volume 02 continua aparecendo, porem como relacionado
    assert 0.2 <= relevance("Batman & Robin: Ano Um", "Batman & Robin: Ano Um Vol. 02") < 0.34
    # titulo que so compartilha "Batman" e apenas parecido
    assert relevance("Batman & Robin: Ano Um", "Batman Renascido") < 0.34


def test_same_product_separa_capa_normal_da_variante():
    # a capa variante e um produto diferente da capa normal do mesmo numero
    assert not same_product("Absolute Batman 01", "Absolute Batman 01 - Capa Variante")
    assert not same_product("Absolute Batman 01", "Absolute Batman 01 Variant Cover")

    # duas variantes (grafias diferentes) continuam sendo o mesmo produto
    assert same_product(
        "Absolute Batman 01 - Capa Variante", "Absolute Batman 01 Variant Cover"
    )

    assert extract_variant("Absolute Batman 01 - Capa Variante") == "variante"
    assert extract_variant("Absolute Batman 01 Variant Cover") == "variante"
    assert extract_variant("Absolute Batman 01") is None
    assert variants_compatible(None, None) is True
    assert variants_compatible("variante", "variante") is True
    assert variants_compatible("variante", None) is False


def test_expand_query_variants_aumenta_a_recall():
    variantes = expand_query_variants("Batman & Robin: Ano Um")
    assert variantes[0] == "Batman & Robin: Ano Um"
    assert any("&" not in v for v in variantes[1:])
    assert len(variantes) <= 3
    assert expand_query_variants("   ") == []


def test_guess_publisher_por_titulo_vendedor_ou_nada():
    assert guess_publisher("Absolute Batman 01 - Panini") == "Panini"
    assert guess_publisher(None, "Panini Store", "Panini Store") == "Panini"
    assert guess_publisher("Sandman Edição Definitiva Darkside") == "Darkside"
    # sem editora reconhecivel: a interface omite o campo
    assert guess_publisher("Watchmen Edição Definitiva") is None
    assert guess_publisher(None, None) is None

