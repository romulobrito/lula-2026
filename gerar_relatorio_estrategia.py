# -*- coding: utf-8 -*-
"""Build XLSX and HTML strategy reports from municipal result CSVs.

Pipeline:
1. Read one or more CSVs that share the resumo_municipios header.
2. Recompute percentages from absolute votes.
3. Attach a macro-region (built-in for RJ, or a user map via --regioes).
4. Classify each municipality into an operational role.
5. Write one XLSX and one HTML per state (UF).
6. Draw an IBGE municipality map colored by campaign role.

Comments, docstrings, and log lines stay ASCII. Accented Portuguese appears
only in report labels shown to the reader.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import html
import json
import logging
import sys
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

logger = logging.getLogger("estrategia")

# IBGE state codes used to download the municipality mesh.
CODIGOS_UF: dict[str, str] = {
    "AC": "12",
    "AL": "27",
    "AM": "13",
    "AP": "16",
    "BA": "29",
    "CE": "23",
    "DF": "53",
    "ES": "32",
    "GO": "52",
    "MA": "21",
    "MG": "31",
    "MS": "50",
    "MT": "51",
    "PA": "15",
    "PB": "25",
    "PE": "26",
    "PI": "22",
    "PR": "41",
    "RJ": "33",
    "RN": "24",
    "RO": "11",
    "RR": "14",
    "RS": "43",
    "SC": "42",
    "SE": "28",
    "SP": "35",
    "TO": "17",
}

COR_PAPEL: dict[str, str] = {
    "Intensificar": "#c47b2b",
    "Reduzir o buraco": "#a33b3b",
    "Consolidar": "#2e7d4f",
    "Apoio local": "#8d99a6",
    "Presenca contida": "#5c6b7a",
}
COR_LULA = "#2f6f9f"
COR_FLAVIO = "#5c6b7a"
COR_DEFICIT = "#a33b3b"
COR_SEM_DADO = "#e6e8ee"

URL_MALHA = (
    "https://servicodados.ibge.gov.br/api/v3/malhas/estados/{codigo}"
    "?formato=application/vnd.geo+json&qualidade=minima&intrarregiao=municipio"
)
URL_LOCALIDADES = (
    "https://servicodados.ibge.gov.br/api/v1/localidades/estados/{codigo}/municipios"
)
DIRETORIO_CACHE = Path(__file__).resolve().parent / "cache"

# Count fields required on every input file. Matching ignores case and accents.
COLUNAS_INTEIRAS: tuple[str, ...] = (
    "UF",
    "MUNICIPIO",
    "LOCAIS",
    "APTOS",
    "COMPARECIMENTO",
    "ABSTENCOES",
    "VOTOS VALIDOS",
    "LULA",
    "FLAVIO BOLSONARO",
    "DIFERENCA (votos)",
    "LOCAIS LULA A FRENTE",
    "LOCAIS FLAVIO A FRENTE",
    "LOCAIS EMPATE",
)

COLUNAS_PERCENTUAIS: tuple[str, ...] = (
    "% ABSTENCOES",
    "% LULA (validos)",
    "% FLAVIO (validos)",
    "DIFERENCA (p.p.)",
)

# City role: close and large electorates first, structural gaps last.
PP_CURTO_CIDADE_GRANDE = 8.0
APTOS_CURTO = 50_000
PP_MEDIO_CIDADE_MUITO_GRANDE = 13.0
APTOS_MUITO_GRANDE = 100_000
PP_DISPUTAVEL = 16.0
APTOS_DISPUTAVEL = 60_000
DEFICIT_VOLUME = 15_000
PP_ESTRUTURAL = 28.0
APTOS_PEQUENO = 40_000
PP_PEQUENO_LARGO = 15.0

# Region role uses the same idea at aggregate scale.
PP_REGIAO_ESTRUTURAL = 28.0
PP_REGIAO_CURTA = 12.0
VOTOS_PP_RELEVANTE = 8_000.0
PP_REGIAO_DISPUTAVEL = 13.0
DEFICIT_REGIAO_DISPUTAVEL = 30_000
DEFICIT_REGIAO_VOLUME = 80_000
PP_REGIAO_APERTADA = 10.0

# How much of an absolute vote deficit is treated as recoverable.
FATOR_ATE_8 = 1.0
FATOR_ATE_15 = 0.65
FATOR_ATE_25 = 0.35
FATOR_ACIMA_25 = 0.15
# An absentee counts as half a deficit vote, and only inside the two
# bands where turnout can still change the state result.
PESO_AUSENTE = 0.5

TOLERANCIA_PP = 0.2
ROTULO_SEM_REGIAO = "Sem macrorregiao"

ORDEM_PAPEL: dict[str, int] = {
    "Intensificar": 0,
    "Reduzir o buraco": 1,
    "Consolidar": 2,
    "Apoio local": 3,
    "Presenca contida": 4,
}

ROTULO_PAPEL: dict[str, str] = {
    "Intensificar": "Intensificar",
    "Reduzir o buraco": "Reduzir o buraco",
    "Consolidar": "Consolidar",
    "Apoio local": "Apoio local",
    "Presenca contida": "Presença contida",
}

ROTULO_PAPEL_REGIAO: dict[str, str] = {
    "Intensificar": "Intensificar",
    "Reduzir o buraco": "Reduzir o buraco",
    "Consolidar": "Consolidar",
    "Presenca contida": "Presença contida",
    "Seletivo": "Seletivo",
    "Sem mapa": "Sem mapa",
}

TEXTO_PAPEL: dict[str, str] = {
    "Intensificar": (
        "Prioridade da campanha. Lula está atrás por pouco e a cidade tem muitos eleitores, "
        "então um ponto percentual aqui vale mais votos do que o mesmo ponto numa cidade pequena. "
        "É para onde vão comício, corpo a corpo e agenda."
    ),
    "Reduzir o buraco": (
        "Lula está atrás por muitos votos, mas a diferença ainda não é irreversível. "
        "Virar a cidade neste ciclo é improvável. O objetivo é diminuir o buraco, "
        "porque cada voto recuperado entra na soma do estado."
    ),
    "Consolidar": (
        "Lula já tem mais votos que Flávio neste município. "
        "A tarefa é segurar essa dianteira e ampliar os locais de votação em que ele fica à frente, "
        "para o resultado não virar no fim."
    ),
    "Apoio local": (
        "A cidade é pequena, ou a margem fica no meio: nem curta o bastante para decidir o estado, "
        "nem larga o bastante para dispensar acompanhamento. "
        "Entra na soma, mas não puxa a estrutura principal."
    ),
    "Presenca contida": (
        "A diferença já é larga demais para concentrar estrutura: 28 pontos ou mais, "
        "ou uma cidade pequena em que Lula perde por mais de 15 pontos. "
        "A orientação é presença leve, para a margem não crescer, "
        "e deixar comício e agenda nas faixas em que o voto ainda muda o estado."
    ),
    "Sem dado no CSV": (
        "O município está no mapa do IBGE, mas não há linha de votos no arquivo. "
        "Sem resultado, ele não recebe papel nem entra na soma do estado."
    ),
}


class ErroCsv(Exception):
    """Raised when an input CSV cannot produce a report."""


@dataclass(frozen=True)
class Municipio:
    """One municipality after validation and recomputation."""

    uf: str
    nome: str
    regiao: str
    papel: str
    indice: float
    locais: int
    aptos: int
    comparecimento: int
    abstencoes: int
    abstencao_pct: float
    validos: int
    lula: int
    lula_pct: float
    flavio: int
    flavio_pct: float
    dif_pp: float
    dif_votos: int
    locais_lula: int
    locais_flavio: int
    locais_empate: int
    origem: str

    @property
    def votos_por_pp(self) -> float:
        """Valid votes represented by one percentage point."""
        if self.validos <= 0:
            return 0.0
        return self.validos / 100.0

    @property
    def trocas_para_empatar(self) -> int:
        """Switches from Flavio to Lula that would tie a city Lula is losing."""
        if self.dif_votos >= 0:
            return 0
        return (abs(self.dif_votos) + 1) // 2

    @property
    def votos_novos_para_empatar(self) -> int:
        """New Lula votes that tie the city if Flavio's total stays put."""
        if self.dif_votos >= 0:
            return 0
        return abs(self.dif_votos)


@dataclass(frozen=True)
class Agregado:
    """Sum of municipalities that share a state or a macro-region."""

    nome: str
    municipios: int
    locais: int
    aptos: int
    comparecimento: int
    abstencoes: int
    validos: int
    lula: int
    flavio: int
    locais_lula: int
    locais_flavio: int
    locais_empate: int

    @property
    def dif_votos(self) -> int:
        """Lula votes minus Flavio votes. Negative means Lula is behind."""
        return self.lula - self.flavio

    @property
    def lula_pct(self) -> float:
        """Lula share of valid votes."""
        if self.validos <= 0:
            return 0.0
        return 100.0 * self.lula / self.validos

    @property
    def flavio_pct(self) -> float:
        """Flavio share of valid votes."""
        if self.validos <= 0:
            return 0.0
        return 100.0 * self.flavio / self.validos

    @property
    def dif_pp(self) -> float:
        """Percentage-point gap, Lula minus Flavio."""
        return self.lula_pct - self.flavio_pct

    @property
    def abstencao_pct(self) -> float:
        """Abstention as a percent of registered voters."""
        if self.aptos <= 0:
            return 0.0
        return 100.0 * self.abstencoes / self.aptos

    @property
    def votos_por_pp(self) -> float:
        """Valid votes represented by one percentage point."""
        if self.validos <= 0:
            return 0.0
        return self.validos / 100.0

    @property
    def deficit(self) -> int:
        """Votes Lula is behind. Zero when Lula is ahead or tied."""
        if self.dif_votos >= 0:
            return 0
        return -self.dif_votos


@dataclass(frozen=True)
class Relatorio:
    """Everything written for a single state."""

    uf: str
    origem: str
    gerado_em: str
    municipios: tuple[Municipio, ...]
    estado: Agregado
    regioes: tuple[Agregado, ...]
    avisos: tuple[str, ...]
    fonte_regiao: str


def normalizar(texto: str) -> str:
    """Uppercase a label and drop accents so names match across files.

    Args:
        texto: Raw header or municipality name.

    Returns:
        ASCII-folded uppercase text with collapsed spaces.
    """
    decomposicao = unicodedata.normalize("NFKD", texto.strip())
    sem_acento = "".join(
        caractere
        for caractere in decomposicao
        if not unicodedata.combining(caractere)
    )
    for aspas in ("'", "\u2019", "\u2018", "\u00b4"):
        sem_acento = sem_acento.replace(aspas, "")
    sem_acento = sem_acento.replace("-", " ")
    return " ".join(sem_acento.upper().split())


def variantes_nome(nome: str) -> tuple[str, ...]:
    """Return lookup keys for a municipality, including common spelling swaps.

    Args:
        nome: Municipality name from the CSV or from IBGE.

    Returns:
        Normalized keys. The second key exists only when a token is LUIS or LUIZ.
    """
    chave = normalizar(nome)
    trocas = {
        "LUIS": "LUIZ",
        "LUIZ": "LUIS",
        "THOME": "TOME",
        "TOME": "THOME",
        "EUSEBIA": "EUZEBIA",
        "EUZEBIA": "EUSEBIA",
        "MELO": "MELLO",
        "MELLO": "MELO",
        "DE": "DO",
        "DO": "DE",
    }
    partes = chave.split(" ")
    indices = [indice for indice, parte in enumerate(partes) if parte in trocas]
    chaves = {chave}
    if indices:
        todas = list(partes)
        for indice in indices:
            todas[indice] = trocas[todas[indice]]
        chaves.add(" ".join(todas))
        for indice in indices:
            uma = list(partes)
            uma[indice] = trocas[uma[indice]]
            chaves.add(" ".join(uma))
    return tuple(chaves)


def _mapa_rj() -> dict[str, str]:
    """Return the built-in RJ macro-region map keyed by normalized name.

    Returns:
        Municipality key to region display name.
    """
    grupos: dict[str, tuple[str, ...]] = {
        "Capital": ("RIO DE JANEIRO",),
        "Baixada Fluminense": (
            "BELFORD ROXO",
            "DUQUE DE CAXIAS",
            "GUAPIMIRIM",
            "ITAGUAI",
            "JAPERI",
            "MAGE",
            "MESQUITA",
            "NILOPOLIS",
            "NOVA IGUACU",
            "PARACAMBI",
            "QUEIMADOS",
            "SAO JOAO DE MERITI",
            "SEROPEDICA",
        ),
        "Leste Metropolitano": (
            "NITEROI",
            "SAO GONCALO",
            "ITABORAI",
            "MARICA",
            "TANGUA",
            "RIO BONITO",
            "CACHOEIRAS DE MACACU",
            "SILVA JARDIM",
        ),
        "Região dos Lagos": (
            "ARARUAMA",
            "ARMACAO DOS BUZIOS",
            "ARRAIAL DO CABO",
            "CABO FRIO",
            "IGUABA GRANDE",
            "SAO PEDRO DA ALDEIA",
            "SAQUAREMA",
        ),
        "Norte Fluminense": (
            "CAMPOS DOS GOYTACAZES",
            "MACAE",
            "RIO DAS OSTRAS",
            "CASIMIRO DE ABREU",
            "SAO JOAO DA BARRA",
            "SAO FRANCISCO DE ITABAPOANA",
            "SAO FIDELIS",
            "CARDOSO MOREIRA",
            "QUISSAMA",
            "CARAPEBUS",
            "CONCEICAO DE MACABU",
        ),
        "Noroeste Fluminense": (
            "APERIBE",
            "BOM JESUS DO ITABAPOANA",
            "CAMBUCI",
            "ITALVA",
            "ITAOCARA",
            "ITAPERUNA",
            "LAJE DO MURIAE",
            "MIRACEMA",
            "NATIVIDADE",
            "PORCIUNCULA",
            "SANTO ANTONIO DE PADUA",
            "SAO JOSE DE UBA",
            "VARRE-SAI",
        ),
        "Região Serrana": (
            "PETROPOLIS",
            "TERESOPOLIS",
            "NOVA FRIBURGO",
            "BOM JARDIM",
            "CARMO",
            "CANTAGALO",
            "CORDEIRO",
            "DUAS BARRAS",
            "MACUCO",
            "SANTA MARIA MADALENA",
            "SAO JOSE DO VALE DO RIO PRETO",
            "SAO SEBASTIAO DO ALTO",
            "SUMIDOURO",
            "TRAJANO DE MORAES",
        ),
        "Médio Paraíba": (
            "VOLTA REDONDA",
            "BARRA MANSA",
            "RESENDE",
            "BARRA DO PIRAI",
            "PINHEIRAL",
            "PIRAI",
            "PORTO REAL",
            "QUATIS",
            "ITATIAIA",
        ),
        "Vale do Café e Centro-Sul": (
            "VALENCA",
            "RIO CLARO",
            "RIO DAS FLORES",
            "VASSOURAS",
            "MENDES",
            "MIGUEL PEREIRA",
            "PATY DO ALFERES",
            "ENGENHEIRO PAULO DE FRONTIN",
            "PARAIBA DO SUL",
            "TRES RIOS",
            "AREAL",
            "SAPUCAIA",
            "COMENDADOR LEVY GASPARIAN",
        ),
        "Costa Verde": (
            "ANGRA DOS REIS",
            "PARATY",
            "MANGARATIBA",
        ),
    }
    mapa: dict[str, str] = {}
    for regiao, nomes in grupos.items():
        for nome in nomes:
            chave = normalizar(nome)
            if chave in mapa:
                raise RuntimeError("Duplicated RJ municipality in the built-in map.")
            mapa[chave] = regiao
    return mapa


MAPAS_EMBUTIDOS: dict[str, dict[str, str]] = {
    "RJ": _mapa_rj(),
}


def fator_proximidade(margem_pp: float) -> float:
    """Weight a vote deficit by how close the municipal margin is.

    Args:
        margem_pp: Absolute gap in percentage points.

    Returns:
        Factor from 0.15 (wide gap) to 1.0 (gap of at most 8 points).
    """
    if margem_pp <= 8.0:
        return FATOR_ATE_8
    if margem_pp <= 15.0:
        return FATOR_ATE_15
    if margem_pp <= 25.0:
        return FATOR_ATE_25
    return FATOR_ACIMA_25


def indice_atencao(dif_pp: float, dif_votos: int, abstencoes: int, papel: str) -> float:
    """Score cities Lula is losing. Higher means more useful attention.

    Args:
        dif_pp: Lula percent minus Flavio percent.
        dif_votos: Lula votes minus Flavio votes.
        abstencoes: Registered voters who did not turn out.
        papel: Operational role already assigned to the municipality.

    Returns:
        Deficit times the closeness factor. In Intensificar and Reduzir o
        buraco, half of the abstentions are added before that discount.
        Zero when Lula is ahead or tied.
    """
    if dif_votos >= 0:
        return 0.0
    fator = fator_proximidade(abs(dif_pp))
    base = float(-dif_votos) * fator
    if papel not in ("Intensificar", "Reduzir o buraco"):
        return base
    return base + (PESO_AUSENTE * float(max(abstencoes, 0)) * fator)


def classificar_municipio(dif_pp: float, dif_votos: int, aptos: int) -> str:
    """Assign an operational role from margin, vote gap, and electorate size.

    Args:
        dif_pp: Lula percent minus Flavio percent.
        dif_votos: Lula votes minus Flavio votes.
        aptos: Registered voters.

    Returns:
        Internal role key. See ROTULO_PAPEL for the report label.
    """
    margem = abs(dif_pp)
    if dif_votos > 0:
        return "Consolidar"
    if margem <= PP_CURTO_CIDADE_GRANDE and aptos >= APTOS_CURTO:
        return "Intensificar"
    if margem <= PP_MEDIO_CIDADE_MUITO_GRANDE and aptos >= APTOS_MUITO_GRANDE:
        return "Intensificar"
    if margem <= PP_DISPUTAVEL and aptos >= APTOS_DISPUTAVEL:
        return "Intensificar"
    if margem >= PP_ESTRUTURAL:
        return "Presenca contida"
    if dif_votos <= -DEFICIT_VOLUME:
        return "Reduzir o buraco"
    if aptos < APTOS_PEQUENO and margem > PP_PEQUENO_LARGO:
        return "Presenca contida"
    return "Apoio local"


def classificar_regiao(agregado: Agregado) -> str:
    """Assign a region role from its gap, deficit, and votes per point.

    Args:
        agregado: Summed macro-region.

    Returns:
        Internal region-role key.
    """
    if agregado.nome == ROTULO_SEM_REGIAO:
        return "Sem mapa"
    margem = abs(agregado.dif_pp)
    if agregado.dif_pp > 0:
        return "Consolidar"
    if margem >= PP_REGIAO_ESTRUTURAL:
        return "Presenca contida"
    if margem <= PP_REGIAO_CURTA and agregado.votos_por_pp >= VOTOS_PP_RELEVANTE:
        return "Intensificar"
    if margem <= PP_REGIAO_DISPUTAVEL and agregado.deficit >= DEFICIT_REGIAO_DISPUTAVEL:
        return "Intensificar"
    if agregado.deficit >= DEFICIT_REGIAO_VOLUME and margem > 15.0:
        return "Reduzir o buraco"
    if margem <= PP_REGIAO_APERTADA:
        return "Consolidar"
    return "Seletivo"


def agregar(nome: str, municipios: list[Municipio]) -> Agregado:
    """Sum absolute counts for a named group of municipalities.

    Args:
        nome: State code or macro-region label.
        municipios: Rows that belong to the group.

    Returns:
        Aggregated counts. Percentages are derived later from those counts.
    """
    return Agregado(
        nome=nome,
        municipios=len(municipios),
        locais=sum(item.locais for item in municipios),
        aptos=sum(item.aptos for item in municipios),
        comparecimento=sum(item.comparecimento for item in municipios),
        abstencoes=sum(item.abstencoes for item in municipios),
        validos=sum(item.validos for item in municipios),
        lula=sum(item.lula for item in municipios),
        flavio=sum(item.flavio for item in municipios),
        locais_lula=sum(item.locais_lula for item in municipios),
        locais_flavio=sum(item.locais_flavio for item in municipios),
        locais_empate=sum(item.locais_empate for item in municipios),
    )


def detectar_delimitador(amostra: str) -> str:
    """Pick semicolon or comma from the header line.

    Args:
        amostra: Start of the file, including the header.

    Returns:
        The delimiter character.

    Raises:
        ErroCsv: When neither delimiter appears in the header.
    """
    primeira = amostra.splitlines()[0] if amostra else ""
    pontos = primeira.count(";")
    virgulas = primeira.count(",")
    if pontos == 0 and virgulas == 0:
        raise ErroCsv("Header has neither ';' nor ',' as a delimiter.")
    if pontos >= virgulas:
        return ";"
    return ","


def parse_numero(texto: str, campo: str, linha: int) -> float:
    """Parse a Brazilian or plain decimal number.

    Args:
        texto: Cell text.
        campo: Column name, used in the error.
        linha: 1-based data row number (header is row 1).

    Returns:
        Parsed float.

    Raises:
        ErroCsv: When the cell is empty or not numeric.
    """
    bruto = texto.strip()
    if bruto == "":
        raise ErroCsv(
            "Empty value at row {linha}, column {campo}.".format(
                linha=linha,
                campo=campo,
            )
        )
    negativo = bruto.startswith("-")
    corpo = bruto[1:] if negativo else bruto
    if "," in corpo:
        corpo = corpo.replace(".", "").replace(",", ".")
    elif corpo.count(".") > 1:
        corpo = corpo.replace(".", "")
    try:
        valor = float(corpo)
    except ValueError as erro:
        raise ErroCsv(
            "Invalid number at row {linha}, column {campo}: {texto!r}.".format(
                linha=linha,
                campo=campo,
                texto=texto,
            )
        ) from erro
    if negativo:
        return -valor
    return valor


def parse_inteiro(texto: str, campo: str, linha: int) -> int:
    """Parse a whole number stored as text.

    Args:
        texto: Cell text.
        campo: Column name, used in the error.
        linha: 1-based data row number.

    Returns:
        Rounded integer.

    Raises:
        ErroCsv: When the value is not a whole number.
    """
    valor = parse_numero(texto, campo, linha)
    if abs(valor - round(valor)) > 0.001:
        raise ErroCsv(
            "Expected an integer at row {linha}, column {campo}.".format(
                linha=linha,
                campo=campo,
            )
        )
    return int(round(valor))


def indice_colunas(cabecalho: list[str]) -> dict[str, str]:
    """Map each required column name to the original header text.

    Args:
        cabecalho: Header cells from the CSV.

    Returns:
        Canonical column name to the header text DictReader will use.

    Raises:
        ErroCsv: When a required column is missing.
    """
    encontrados: dict[str, str] = {}
    for original in cabecalho:
        encontrados[normalizar(original)] = original
    canonicas = COLUNAS_INTEIRAS + COLUNAS_PERCENTUAIS
    faltando = [nome for nome in canonicas if normalizar(nome) not in encontrados]
    if faltando:
        raise ErroCsv(
            "Missing columns: {lista}.".format(lista=", ".join(faltando))
        )
    return {nome: encontrados[normalizar(nome)] for nome in canonicas}


def carregar_mapa_externo(caminho: Path) -> dict[tuple[str, str], str]:
    """Load an optional municipality-to-region CSV.

    The file needs MUNICIPIO and REGIAO. UF is optional. A blank UF applies
    the row to every state. An external row overrides the built-in RJ map.

    Args:
        caminho: Path to the region map.

    Returns:
        Pairs of (UF or "", normalized municipality) to region label.

    Raises:
        ErroCsv: When the file or the required columns are missing.
    """
    if not caminho.is_file():
        raise ErroCsv(
            "Region map not found: {caminho}.".format(caminho=caminho)
        )
    texto = caminho.read_text(encoding="utf-8-sig")
    if texto.strip() == "":
        raise ErroCsv(
            "Region map is empty: {caminho}.".format(caminho=caminho)
        )
    delimitador = detectar_delimitador(texto)
    leitor = csv.DictReader(texto.splitlines(), delimiter=delimitador)
    if leitor.fieldnames is None:
        raise ErroCsv("Region map has no header.")
    por_nome = {normalizar(nome): nome for nome in leitor.fieldnames}
    if "MUNICIPIO" not in por_nome or "REGIAO" not in por_nome:
        raise ErroCsv("Region map must include MUNICIPIO and REGIAO columns.")
    mapa: dict[tuple[str, str], str] = {}
    for indice, linha in enumerate(leitor, start=2):
        nome = (linha.get(por_nome["MUNICIPIO"]) or "").strip()
        regiao = (linha.get(por_nome["REGIAO"]) or "").strip()
        if nome == "" or regiao == "":
            raise ErroCsv(
                "Region map row {indice} is missing MUNICIPIO or REGIAO.".format(
                    indice=indice
                )
            )
        uf = ""
        if "UF" in por_nome:
            uf = normalizar(linha.get(por_nome["UF"]) or "")
        mapa[(uf, normalizar(nome))] = regiao
    if not mapa:
        raise ErroCsv("Region map has a header but no data rows.")
    return mapa


def resolver_regiao(
    uf: str,
    nome: str,
    mapa_externo: dict[tuple[str, str], str],
) -> str:
    """Find the macro-region for one municipality.

    Args:
        uf: State code.
        nome: Municipality name as stored in the result CSV.
        mapa_externo: Optional user map. Matching UF wins over a blank UF.

    Returns:
        Region label, or ROTULO_SEM_REGIAO when no map applies.
    """
    chave = normalizar(nome)
    if (uf, chave) in mapa_externo:
        return mapa_externo[(uf, chave)]
    if ("", chave) in mapa_externo:
        return mapa_externo[("", chave)]
    embutido = MAPAS_EMBUTIDOS.get(uf, {})
    if chave in embutido:
        return embutido[chave]
    return ROTULO_SEM_REGIAO


def ler_municipios(
    caminho: Path,
    mapa_externo: dict[tuple[str, str], str],
) -> tuple[list[Municipio], list[str]]:
    """Read and validate every data row of a summary CSV.

    Args:
        caminho: Result CSV in the resumo_municipios pattern.
        mapa_externo: Optional region overrides.

    Returns:
        Municipalities and (UF, warning) pairs for the report.

    Raises:
        ErroCsv: When the file is missing, empty, or structurally invalid.
    """
    if not caminho.is_file():
        raise ErroCsv("CSV not found: {caminho}.".format(caminho=caminho))
    texto = caminho.read_text(encoding="utf-8-sig")
    if texto.strip() == "":
        raise ErroCsv("CSV is empty: {caminho}.".format(caminho=caminho))
    delimitador = detectar_delimitador(texto)
    leitor = csv.DictReader(texto.splitlines(), delimiter=delimitador)
    if leitor.fieldnames is None:
        raise ErroCsv("CSV has no header: {caminho}.".format(caminho=caminho))
    colunas = indice_colunas(list(leitor.fieldnames))
    avisos: list[tuple[str, str]] = []
    vistos: set[tuple[str, str]] = set()
    municipios: list[Municipio] = []

    for numero, linha in enumerate(leitor, start=2):
        uf = normalizar(linha[colunas["UF"]])
        nome = (linha[colunas["MUNICIPIO"]] or "").strip()
        if uf == "" or nome == "":
            raise ErroCsv(
                "Row {numero} is missing UF or MUNICIPIO.".format(numero=numero)
            )
        locais = parse_inteiro(linha[colunas["LOCAIS"]], "LOCAIS", numero)
        aptos = parse_inteiro(linha[colunas["APTOS"]], "APTOS", numero)
        comparecimento = parse_inteiro(
            linha[colunas["COMPARECIMENTO"]],
            "COMPARECIMENTO",
            numero,
        )
        abstencoes = parse_inteiro(
            linha[colunas["ABSTENCOES"]],
            "ABSTENCOES",
            numero,
        )
        validos = parse_inteiro(
            linha[colunas["VOTOS VALIDOS"]],
            "VOTOS VALIDOS",
            numero,
        )
        lula = parse_inteiro(linha[colunas["LULA"]], "LULA", numero)
        flavio = parse_inteiro(
            linha[colunas["FLAVIO BOLSONARO"]],
            "FLAVIO BOLSONARO",
            numero,
        )
        locais_lula = parse_inteiro(
            linha[colunas["LOCAIS LULA A FRENTE"]],
            "LOCAIS LULA A FRENTE",
            numero,
        )
        locais_flavio = parse_inteiro(
            linha[colunas["LOCAIS FLAVIO A FRENTE"]],
            "LOCAIS FLAVIO A FRENTE",
            numero,
        )
        locais_empate = parse_inteiro(
            linha[colunas["LOCAIS EMPATE"]],
            "LOCAIS EMPATE",
            numero,
        )
        csv_lula_pct = parse_numero(
            linha[colunas["% LULA (validos)"]],
            "% LULA (validos)",
            numero,
        )
        csv_flavio_pct = parse_numero(
            linha[colunas["% FLAVIO (validos)"]],
            "% FLAVIO (validos)",
            numero,
        )
        csv_dif_pp = parse_numero(
            linha[colunas["DIFERENCA (p.p.)"]],
            "DIFERENCA (p.p.)",
            numero,
        )
        csv_dif_votos = parse_inteiro(
            linha[colunas["DIFERENCA (votos)"]],
            "DIFERENCA (votos)",
            numero,
        )
        csv_abst_pct = parse_numero(
            linha[colunas["% ABSTENCOES"]],
            "% ABSTENCOES",
            numero,
        )

        if aptos < 0 or validos < 0 or lula < 0 or flavio < 0:
            raise ErroCsv(
                "Negative count at row {numero} ({nome}).".format(
                    numero=numero,
                    nome=nome,
                )
            )
        if validos == 0:
            raise ErroCsv(
                "Row {numero} ({nome}) has zero valid votes.".format(
                    numero=numero,
                    nome=nome,
                )
            )

        lula_pct = 100.0 * lula / validos
        flavio_pct = 100.0 * flavio / validos
        dif_pp = lula_pct - flavio_pct
        dif_votos = lula - flavio
        abstencao_pct = (100.0 * abstencoes / aptos) if aptos else 0.0

        if abs(dif_votos - csv_dif_votos) > 1:
            avisos.append(
                (
                    uf,
                    "Linha {numero}, {nome}: a diferença de votos no arquivo é {arquivo}; o recálculo dá {calculado}.".format(
                        numero=numero,
                        nome=nome,
                        arquivo=csv_dif_votos,
                        calculado=dif_votos,
                    ),
                )
            )
        if abs(dif_pp - csv_dif_pp) > TOLERANCIA_PP:
            avisos.append(
                (
                    uf,
                    "Linha {numero}, {nome}: a diferença em pontos no arquivo se afasta mais de {tol} p.p. do recálculo.".format(
                        numero=numero,
                        nome=nome,
                        tol=TOLERANCIA_PP,
                    ),
                )
            )
        if abs(lula_pct - csv_lula_pct) > TOLERANCIA_PP or abs(flavio_pct - csv_flavio_pct) > TOLERANCIA_PP:
            avisos.append(
                (
                    uf,
                    "Linha {numero}, {nome}: o percentual dos candidatos no arquivo não fecha com votos / votos válidos.".format(
                        numero=numero,
                        nome=nome,
                    ),
                )
            )
        if aptos and abs(abstencao_pct - csv_abst_pct) > TOLERANCIA_PP:
            avisos.append(
                (
                    uf,
                    "Linha {numero}, {nome}: o percentual de abstenção no arquivo não fecha com abstenções / aptos.".format(
                        numero=numero,
                        nome=nome,
                    ),
                )
            )
        soma_locais = locais_lula + locais_flavio + locais_empate
        if locais != soma_locais:
            avisos.append(
                (
                    uf,
                    "Linha {numero}, {nome}: LOCAIS é {locais}, mas as três colunas de locais somam {soma}.".format(
                        numero=numero,
                        nome=nome,
                        locais=locais,
                        soma=soma_locais,
                    ),
                )
            )

        chave = (uf, normalizar(nome))
        if chave in vistos:
            raise ErroCsv(
                "Duplicate municipality at row {numero}: {uf} {nome}.".format(
                    numero=numero,
                    uf=uf,
                    nome=nome,
                )
            )
        vistos.add(chave)
        papel = classificar_municipio(dif_pp, dif_votos, aptos)
        municipios.append(
            Municipio(
                uf=uf,
                nome=nome,
                regiao=resolver_regiao(uf, nome, mapa_externo),
                papel=papel,
                indice=indice_atencao(dif_pp, dif_votos, abstencoes, papel),
                locais=locais,
                aptos=aptos,
                comparecimento=comparecimento,
                abstencoes=abstencoes,
                abstencao_pct=abstencao_pct,
                validos=validos,
                lula=lula,
                lula_pct=lula_pct,
                flavio=flavio,
                flavio_pct=flavio_pct,
                dif_pp=dif_pp,
                dif_votos=dif_votos,
                locais_lula=locais_lula,
                locais_flavio=locais_flavio,
                locais_empate=locais_empate,
                origem=str(caminho),
            )
        )

    if not municipios:
        raise ErroCsv("CSV has a header but no data rows: {caminho}.".format(caminho=caminho))
    return municipios, avisos


def carregar_mesorregioes(uf: str) -> dict[str, str]:
    """Load IBGE mesoregion names keyed by municipality name variants.

    Args:
        uf: Two-letter state code.

    Returns:
        Normalized name variants to the mesoregion label. Empty when unavailable.
    """
    codigo = CODIGOS_UF.get(uf)
    if codigo is None:
        return {}
    cache = DIRETORIO_CACHE / "mesorregioes_{codigo}.json".format(codigo=codigo)
    if cache.is_file():
        carregado = json.loads(cache.read_text(encoding="utf-8"))
        if isinstance(carregado, dict):
            return {str(chave): str(valor) for chave, valor in carregado.items()}
        return {}
    try:
        locais = _baixar_json(URL_LOCALIDADES.format(codigo=codigo))
    except (URLError, TimeoutError, json.JSONDecodeError, UnicodeError, OSError) as erro:
        logger.warning("mesoregion download failed for %s: %s", uf, erro)
        return {}
    if not isinstance(locais, list):
        return {}
    mapa: dict[str, str] = {}
    for item in locais:
        if not isinstance(item, dict):
            continue
        nome = str(item.get("nome", ""))
        micro = item.get("microrregiao")
        meso_nome = ""
        if isinstance(micro, dict):
            meso = micro.get("mesorregiao")
            if isinstance(meso, dict):
                meso_nome = str(meso.get("nome", "")).strip()
        if nome == "" or meso_nome == "":
            continue
        for chave in variantes_nome(nome):
            mapa[chave] = meso_nome
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(mapa, ensure_ascii=False), encoding="utf-8")
        logger.info("downloaded IBGE mesoregions for %s", uf)
    except OSError as erro:
        logger.warning("could not cache mesoregions for %s: %s", uf, erro)
    return mapa


def completar_regioes(municipios: list[Municipio]) -> tuple[list[Municipio], str]:
    """Fill municipalities that have no campaign region with the IBGE mesoregion.

    Args:
        municipios: Rows of a single state.

    Returns:
        Updated rows and a source tag: campanha, ibge, mista or nenhuma.
    """
    if not municipios:
        return municipios, "nenhuma"
    faltam = [item for item in municipios if item.regiao == ROTULO_SEM_REGIAO]
    if not faltam:
        return municipios, "campanha"
    mapa = carregar_mesorregioes(municipios[0].uf)
    if not mapa:
        if len(faltam) == len(municipios):
            return municipios, "nenhuma"
        return municipios, "mista"
    preenchidos = 0
    atualizados: list[Municipio] = []
    for item in municipios:
        if item.regiao != ROTULO_SEM_REGIAO:
            atualizados.append(item)
            continue
        regiao = ""
        for chave in variantes_nome(item.nome):
            if chave in mapa:
                regiao = mapa[chave]
                break
        if regiao:
            atualizados.append(replace(item, regiao=regiao))
            preenchidos += 1
        else:
            atualizados.append(item)
    if preenchidos == 0:
        return atualizados, "nenhuma" if len(faltam) == len(municipios) else "mista"
    if len(faltam) == len(municipios):
        return atualizados, "ibge"
    return atualizados, "mista"


def montar_relatorio(
    uf: str,
    municipios: list[Municipio],
    origem: str,
    avisos: list[str],
    fonte_regiao: str,
) -> Relatorio:
    """Aggregate one state and attach region roles.

    Args:
        uf: State code.
        municipios: Rows already filtered to that state.
        origem: Source path recorded in the report.
        avisos: Warnings for this state, already filtered by UF.

    Returns:
        Report ready for XLSX and HTML.
    """
    por_regiao: dict[str, list[Municipio]] = {}
    for item in municipios:
        por_regiao.setdefault(item.regiao, []).append(item)
    sem_mapa = por_regiao.get(ROTULO_SEM_REGIAO, [])
    if sem_mapa:
        amostra = ", ".join(normalizar(item.nome) for item in sem_mapa[:12])
        avisos.append(
            "{uf}: {quantidade} municípios sem macrorregião ({amostra}). Use --regioes para informar o agrupamento.".format(
                uf=uf,
                quantidade=len(sem_mapa),
                amostra=amostra,
            )
        )
    regioes = [
        agregar(nome, grupo)
        for nome, grupo in por_regiao.items()
    ]
    regioes.sort(key=lambda item: (-item.deficit, item.nome))
    estado = agregar(uf, municipios)
    return Relatorio(
        uf=uf,
        origem=origem,
        gerado_em=datetime.now().isoformat(timespec="seconds"),
        municipios=tuple(sorted(municipios, key=lambda item: normalizar(item.nome))),
        estado=estado,
        regioes=tuple(regioes),
        avisos=tuple(avisos),
        fonte_regiao=fonte_regiao,
    )


def fila(relatorio: Relatorio) -> list[Municipio]:
    """Order municipalities the way a campaign would work the list.

    Args:
        relatorio: One state report.

    Returns:
        Role order, then attention score (or lead, for cities Lula already won).
    """

    def chave(item: Municipio) -> tuple[int, float, str]:
        if item.papel == "Consolidar":
            peso = -float(item.dif_votos)
        else:
            peso = -item.indice
        return (ORDEM_PAPEL[item.papel], peso, normalizar(item.nome))

    return sorted(relatorio.municipios, key=chave)


def fmt_int(valor: int) -> str:
    """Format an integer with a Brazilian thousands separator.

    Args:
        valor: Whole number.

    Returns:
        Text such as -1.273.823.
    """
    sinal = "-" if valor < 0 else ""
    corpo = "{valor:,}".format(valor=abs(valor)).replace(",", ".")
    return "{sinal}{corpo}".format(sinal=sinal, corpo=corpo)


def fmt_decimal(valor: float, casas: int = 2) -> str:
    """Format a float with Brazilian separators.

    Args:
        valor: Number to format.
        casas: Digits after the decimal comma.

    Returns:
        Text such as 13,68.
    """
    sinal = "-" if valor < 0 else ""
    corpo = "{valor:,.{casas}f}".format(valor=abs(valor), casas=casas)
    corpo = corpo.replace(",", "X").replace(".", ",").replace("X", ".")
    return "{sinal}{corpo}".format(sinal=sinal, corpo=corpo)


def fmt_pp(valor: float) -> str:
    """Format a signed percentage-point gap.

    Args:
        valor: Gap, Lula minus Flavio.

    Returns:
        Text with an explicit plus when Lula is ahead.
    """
    texto = fmt_decimal(valor, 2)
    if valor > 0:
        return "+{texto}".format(texto=texto)
    return texto


def fmt_pct(valor: float) -> str:
    """Format a percent for the HTML report.

    Args:
        valor: Percent already scaled by 100.

    Returns:
        Text with a percent sign.
    """
    return "{numero}%".format(numero=fmt_decimal(valor, 2))


def _nomes(itens: list[Municipio], limite: int) -> str:
    """Join municipality names for a narrative sentence.

    Args:
        itens: Already sorted rows.
        limite: Maximum names to include.

    Returns:
        Comma-separated names, empty when there are no rows.
    """
    return ", ".join(item.nome for item in itens[:limite])


def paragrafos(relatorio: Relatorio) -> list[str]:
    """Build the short reading that opens the report.

    Args:
        relatorio: One state report.

    Returns:
        Paragraphs in Portuguese for the reader.
    """
    estado = relatorio.estado
    texto: list[str] = []
    texto.append(
        "No estado {uf}, Lula tem {lula} dos votos válidos e Flávio Bolsonaro {flavio}. A diferença é de {pp} p.p. e de {votos} votos. Cada ponto percentual no estado vale {ponto} votos.".format(
            uf=relatorio.uf,
            lula=fmt_pct(estado.lula_pct),
            flavio=fmt_pct(estado.flavio_pct),
            pp=fmt_pp(estado.dif_pp),
            votos=fmt_int(estado.dif_votos),
            ponto=fmt_int(int(round(estado.votos_por_pp))),
        )
    )
    maior = max(relatorio.municipios, key=lambda item: item.aptos)
    texto.append(
        "O maior colégio é {nome}, com {aptos} aptos, margem de {pp} p.p. e {votos} votos de diferença. A abstenção desse município é {abst}. Abstenção alta mostra onde há eleitor para mobilizar; a preferência de quem faltou não está neste arquivo.".format(
            nome=maior.nome,
            aptos=fmt_int(maior.aptos),
            pp=fmt_pp(maior.dif_pp),
            votos=fmt_int(maior.dif_votos),
            abst=fmt_pct(maior.abstencao_pct),
        )
    )
    intensificar = [
        item
        for item in fila(relatorio)
        if item.papel == "Intensificar"
    ]
    reduzir = sorted(
        (item for item in relatorio.municipios if item.papel == "Reduzir o buraco"),
        key=lambda item: item.dif_votos,
    )
    consolidar = sorted(
        (item for item in relatorio.municipios if item.papel == "Consolidar"),
        key=lambda item: -item.dif_votos,
    )
    presenca = sorted(
        (item for item in relatorio.municipios if item.papel == "Presenca contida"),
        key=lambda item: item.dif_votos,
    )
    if intensificar:
        texto.append(
            "Intensificar, nesta ordem: {nomes}. São municípios de margem curta e eleitorado grande. O índice soma o déficit e metade das abstenções, e esse total cai quando a margem passa de 8, de 15 e de 25 pontos.".format(
                nomes=_nomes(intensificar, 8),
            )
        )
    if reduzir:
        texto.append(
            "Reduzir o buraco, sem tratar virada como meta: {nomes}. O déficit absoluto passa de {limite} votos e a margem fica abaixo de {teto} pontos.".format(
                nomes=_nomes(reduzir, 8),
                limite=fmt_int(DEFICIT_VOLUME),
                teto=fmt_decimal(PP_ESTRUTURAL, 0),
            )
        )
    if consolidar:
        texto.append(
            "Consolidar onde Lula já lidera: {nomes}.".format(
                nomes=_nomes(consolidar, 8),
            )
        )
    if presenca:
        texto.append(
            "Presença contida: a diferença já é larga demais para concentrar estrutura. Os maiores déficits nessa faixa são {nomes}. A orientação é presença leve, para a margem não crescer, e deslocar comício e agenda para Intensificar e Reduzir o buraco. O corte usado é margem de {teto} pontos ou mais.".format(
                teto=fmt_decimal(PP_ESTRUTURAL, 0),
                nomes=_nomes(presenca, 6),
            )
        )
    mapeadas = [item for item in relatorio.regioes if item.nome != ROTULO_SEM_REGIAO]
    if mapeadas:
        ordenadas = sorted(mapeadas, key=lambda item: -item.votos_por_pp)
        descricao = "; ".join(
            "{nome} ({papel}, {ponto} votos por ponto, diferença {votos})".format(
                nome=item.nome,
                papel=ROTULO_PAPEL_REGIAO[classificar_regiao(item)],
                ponto=fmt_int(int(round(item.votos_por_pp))),
                votos=fmt_int(item.dif_votos),
            )
            for item in ordenadas[:4]
        )
        texto.append(
            "Macrorregiões em que um ponto percentual pesa mais: {descricao}.".format(
                descricao=descricao,
            )
        )
    else:
        texto.append(
            "Este estado não tem mapa de macrorregião embutido. A fila municipal continua válida. Para agrupar regiões, rode de novo com --regioes apontando para um CSV com as colunas MUNICIPIO e REGIAO (UF é opcional)."
        )
    texto.append(
        "Os percentuais do relatório são recalculados a partir dos votos absolutos. O próximo corte útil é o resultado por local de votação nos municípios de Intensificar e nos de maior déficit."
    )
    return texto


def metodologia() -> list[str]:
    """Return the rule list printed in both outputs.

    Returns:
        Short Portuguese sentences that define each role.
    """
    return [
        "Diferença = votos de Lula menos votos de Flávio. Número negativo significa Lula atrás.",
        "Intensificar é a prioridade. Lula está atrás, a margem é curta e o eleitorado é grande: até 8 p.p. com 50 mil aptos ou mais; ou até 13 p.p. com 100 mil aptos ou mais; ou até 16 p.p. com 60 mil aptos ou mais. Um ponto percentual nessas cidades muda mais votos.",
        "Reduzir o buraco: Lula está atrás por 15 mil votos ou mais, com margem abaixo de 28 p.p., e a cidade não entrou em Intensificar. A meta é diminuir a diferença, não virar o município.",
        "Presença contida: a diferença já é larga demais para concentrar comício e agenda. Entram municípios com margem de 28 p.p. ou mais e também os de menos de 40 mil aptos com margem acima de 15 p.p. A orientação é presença leve, para a margem não crescer.",
        "Consolidar: Lula já tem mais votos que Flávio. O trabalho é segurar a dianteira e ampliar os locais em que ele fica à frente.",
        "Apoio local: o que sobra depois das faixas acima. A cidade entra na soma do estado, mas não justifica deslocar a estrutura principal.",
        "Índice de atenção = (déficit de votos + metade das abstenções) x fator de proximidade (1,00 até 8 p.p.; 0,65 até 15; 0,35 até 25; 0,15 acima disso). A metade das abstenções só entra em Intensificar e em Reduzir o buraco. Em Presença contida o ausente não sobe o município.",
        "Trocas para empatar = metade do déficit, arredondada para cima. Votos novos para empatar = o déficit inteiro, se o total de Flávio ficar parado.",
        "O mapa embutido cobre as macrorregiões de campanha do RJ. Nos demais estados o gráfico usa a mesorregião do IBGE. Um CSV em --regioes substitui os dois quando o nome do município coincide.",
        "Abstenção não revela preferência. Ela só mede o tamanho do eleitorado que não compareceu.",
    ]


def _estilo_cabecalho() -> Font:
    """Font used on the header row of every sheet.

    Returns:
        Bold white font.
    """
    return Font(bold=True, color="FFFFFF", name="Calibri", size=11)


def _preencher_cabecalho() -> PatternFill:
    """Fill used on the header row of every sheet.

    Returns:
        Solid dark fill.
    """
    return PatternFill("solid", fgColor="1F4E79")


def _escrever_tabela(
    aba: Worksheet,
    cabecalhos: list[str],
    linhas: list[list[object]],
    larguras: dict[str, int],
) -> None:
    """Write a header, rows, autofilter, and basic number formats.

    Args:
        aba: Target worksheet.
        cabecalhos: Column titles.
        linhas: Body rows aligned with the titles.
        larguras: Preferred width by header title.
    """
    aba.append(cabecalhos)
    for celula in aba[1]:
        celula.font = _estilo_cabecalho()
        celula.fill = _preencher_cabecalho()
        celula.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for linha in linhas:
        aba.append(linha)
    aba.auto_filter.ref = aba.dimensions
    aba.freeze_panes = "A2"
    aba.row_dimensions[1].height = 32
    aba.auto_filter.ref = "A1:{coluna}{ultima}".format(
        coluna=get_column_letter(len(cabecalhos)),
        ultima=max(1, len(linhas) + 1),
    )
    aba.freeze_panes = "A2"
    for indice, titulo in enumerate(cabecalhos, start=1):
        aba.column_dimensions[get_column_letter(indice)].width = larguras.get(titulo, 18)
        for linha_idx in range(2, len(linhas) + 2):
            celula = aba.cell(linha_idx, indice)
            if isinstance(celula.value, float):
                celula.number_format = "0.00"
            elif isinstance(celula.value, int) and titulo not in {"Municípios", "Locais Lula", "Locais Flávio", "Locais empate", "Locais"}:
                celula.number_format = "#,##0"
            celula.alignment = Alignment(vertical="center")


def _linha_municipio(item: Municipio) -> list[object]:
    """Convert one municipality into a spreadsheet row.

    Args:
        item: Classified municipality.

    Returns:
        Values in the municipal column order.
    """
    return [
        item.nome,
        item.uf,
        item.regiao if item.regiao != ROTULO_SEM_REGIAO else "Sem macrorregião",
        ROTULO_PAPEL[item.papel],
        round(item.indice, 1),
        item.locais,
        item.aptos,
        item.comparecimento,
        item.abstencoes,
        round(item.abstencao_pct, 2),
        item.validos,
        item.lula,
        round(item.lula_pct, 2),
        item.flavio,
        round(item.flavio_pct, 2),
        round(item.dif_pp, 2),
        item.dif_votos,
        round(item.votos_por_pp, 1),
        item.trocas_para_empatar,
        item.votos_novos_para_empatar,
        item.locais_lula,
        item.locais_flavio,
        item.locais_empate,
    ]


CABECALHO_MUNICIPIO: list[str] = [
    "Município",
    "UF",
    "Macrorregião",
    "Papel",
    "Índice de atenção",
    "Locais",
    "Aptos",
    "Comparecimento",
    "Abstenções",
    "Abstenção %",
    "Votos válidos",
    "Lula",
    "Lula %",
    "Flávio",
    "Flávio %",
    "Diferença (p.p.)",
    "Diferença (votos)",
    "Votos em 1 p.p.",
    "Trocas para empatar",
    "Votos novos para empatar",
    "Locais Lula",
    "Locais Flávio",
    "Locais empate",
]

LARGURA_MUNICIPIO: dict[str, int] = {
    "Município": 34,
    "UF": 8,
    "Macrorregião": 30,
    "Papel": 22,
    "Índice de atenção": 18,
    "Locais": 12,
    "Aptos": 14,
    "Comparecimento": 16,
    "Abstenções": 14,
    "Abstenção %": 14,
    "Votos válidos": 16,
    "Lula": 14,
    "Lula %": 12,
    "Flávio": 14,
    "Flávio %": 12,
    "Diferença (p.p.)": 18,
    "Diferença (votos)": 18,
    "Votos em 1 p.p.": 18,
    "Trocas para empatar": 20,
    "Votos novos para empatar": 24,
    "Locais Lula": 14,
    "Locais Flávio": 14,
    "Locais empate": 14,
}


def escrever_xlsx(relatorio: Relatorio, destino: Path) -> None:
    """Write the workbook for one state.

    Args:
        relatorio: Aggregated state report.
        destino: XLSX path. Parent directory must already exist.
    """
    livro = Workbook()
    leitura = livro.active
    leitura.title = "Leitura"
    leitura.sheet_properties.tabColor = "1F4E79"
    leitura["A1"] = "Estratégia municipal — {uf}".format(uf=relatorio.uf)
    leitura["A1"].font = Font(bold=True, size=16, color="1F4E79", name="Calibri")
    leitura["A2"] = "Fonte: {origem}".format(origem=relatorio.origem)
    leitura["A3"] = "Gerado em: {quando}".format(quando=relatorio.gerado_em)
    leitura.column_dimensions["A"].width = 120
    linha_cursor = 5
    for paragrafo in paragrafos(relatorio):
        leitura.cell(linha_cursor, 1, paragrafo)
        leitura.cell(linha_cursor, 1).alignment = Alignment(wrap_text=True, vertical="top")
        leitura.row_dimensions[linha_cursor].height = 48
        linha_cursor += 1
    linha_cursor += 1
    leitura.cell(linha_cursor, 1, "Regras")
    leitura.cell(linha_cursor, 1).font = Font(bold=True, size=14, name="Calibri")
    linha_cursor += 1
    for regra in metodologia():
        leitura.cell(linha_cursor, 1, regra)
        leitura.cell(linha_cursor, 1).alignment = Alignment(wrap_text=True, vertical="top")
        leitura.row_dimensions[linha_cursor].height = 32
        linha_cursor += 1
    if relatorio.avisos:
        linha_cursor += 1
        leitura.cell(linha_cursor, 1, "Avisos de conferência")
        leitura.cell(linha_cursor, 1).font = Font(bold=True, size=14, name="Calibri")
        linha_cursor += 1
        for aviso in relatorio.avisos:
            leitura.cell(linha_cursor, 1, aviso)
            leitura.cell(linha_cursor, 1).alignment = Alignment(wrap_text=True)
            linha_cursor += 1

    estado = relatorio.estado
    aba_estado = livro.create_sheet("Estado")
    _escrever_tabela(
        aba_estado,
        ["Indicador", "Valor"],
        [
            ["UF", relatorio.uf],
            ["Municípios", estado.municipios],
            ["Aptos", estado.aptos],
            ["Comparecimento", estado.comparecimento],
            ["Abstenções", estado.abstencoes],
            ["Abstenção %", round(estado.abstencao_pct, 2)],
            ["Votos válidos", estado.validos],
            ["Lula", estado.lula],
            ["Lula %", round(estado.lula_pct, 2)],
            ["Flávio", estado.flavio],
            ["Flávio %", round(estado.flavio_pct, 2)],
            ["Diferença (p.p.)", round(estado.dif_pp, 2)],
            ["Diferença (votos)", estado.dif_votos],
            ["Votos em 1 p.p.", round(estado.votos_por_pp, 1)],
            ["Locais", estado.locais],
            ["Locais Lula à frente", estado.locais_lula],
            ["Locais Flávio à frente", estado.locais_flavio],
            ["Locais empate", estado.locais_empate],
        ],
        {"Indicador": 28, "Valor": 22},
    )

    deficit_estado = estado.deficit
    linhas_regiao: list[list[object]] = []
    for regiao in relatorio.regioes:
        parcela = 0.0
        if deficit_estado > 0:
            parcela = 100.0 * regiao.deficit / deficit_estado
        rotulo = regiao.nome if regiao.nome != ROTULO_SEM_REGIAO else "Sem macrorregião"
        linhas_regiao.append(
            [
                rotulo,
                ROTULO_PAPEL_REGIAO[classificar_regiao(regiao)],
                regiao.municipios,
                regiao.locais,
                regiao.aptos,
                round(regiao.abstencao_pct, 2),
                regiao.validos,
                regiao.lula,
                round(regiao.lula_pct, 2),
                regiao.flavio,
                round(regiao.flavio_pct, 2),
                round(regiao.dif_pp, 2),
                regiao.dif_votos,
                round(regiao.votos_por_pp, 1),
                round(parcela, 1),
                regiao.locais_lula,
                regiao.locais_flavio,
                regiao.locais_empate,
            ]
        )
    aba_regiao = livro.create_sheet("Macrorregioes")
    _escrever_tabela(
        aba_regiao,
        [
            "Macrorregião",
            "Papel",
            "Municípios",
            "Locais",
            "Aptos",
            "Abstenção %",
            "Votos válidos",
            "Lula",
            "Lula %",
            "Flávio",
            "Flávio %",
            "Diferença (p.p.)",
            "Diferença (votos)",
            "Votos em 1 p.p.",
            "Parcela do déficit %",
            "Locais Lula",
            "Locais Flávio",
            "Locais empate",
        ],
        linhas_regiao,
        {
            "Macrorregião": 32,
            "Papel": 22,
            "Municípios": 14,
            "Locais": 12,
            "Aptos": 14,
            "Abstenção %": 14,
            "Votos válidos": 16,
            "Lula": 14,
            "Lula %": 12,
            "Flávio": 14,
            "Flávio %": 12,
            "Diferença (p.p.)": 18,
            "Diferença (votos)": 18,
            "Votos em 1 p.p.": 18,
            "Parcela do déficit %": 20,
            "Locais Lula": 14,
            "Locais Flávio": 16,
            "Locais empate": 14,
        },
    )

    aba_mun = livro.create_sheet("Municipios")
    _escrever_tabela(
        aba_mun,
        CABECALHO_MUNICIPIO,
        [_linha_municipio(item) for item in relatorio.municipios],
        LARGURA_MUNICIPIO,
    )
    aba_fila = livro.create_sheet("Fila")
    _escrever_tabela(
        aba_fila,
        CABECALHO_MUNICIPIO,
        [_linha_municipio(item) for item in fila(relatorio)],
        LARGURA_MUNICIPIO,
    )
    livro.save(destino)


def _regioes_no_grafico(relatorio: Relatorio) -> list[Agregado]:
    """Return mapped macro-regions ordered by the size of Lula's vote deficit.

    Args:
        relatorio: One state report.

    Returns:
        Regions that have a real name, largest deficit first.
    """
    mapeadas = [
        item for item in relatorio.regioes if item.nome != ROTULO_SEM_REGIAO
    ]
    return sorted(mapeadas, key=lambda item: (-item.deficit, item.nome))


def _baixar_json(url: str) -> object:
    """Download one JSON document from the IBGE API.

    Args:
        url: Absolute URL.

    Returns:
        Parsed JSON value.

    Raises:
        URLError: When the request fails.
        TimeoutError: When the server does not answer in time.
        json.JSONDecodeError: When the body is not JSON.
    """
    pedido = Request(
        url,
        headers={
            "User-Agent": "estrategia-municipal/1.0",
            "Accept-Encoding": "identity",
        },
    )
    with urlopen(pedido, timeout=30) as resposta:
        corpo = resposta.read()
    if corpo[:2] == b"\x1f\x8b":
        corpo = gzip.decompress(corpo)
    return json.loads(corpo.decode("utf-8"))


def carregar_malha(uf: str) -> dict[str, object] | None:
    """Load the IBGE municipality mesh for one state, using a local cache.

    Args:
        uf: Two-letter state code.

    Returns:
        GeoJSON object with a nome property on each feature, or None.
    """
    codigo = CODIGOS_UF.get(uf)
    if codigo is None:
        logger.warning("no IBGE code for UF %s", uf)
        return None
    cache = DIRETORIO_CACHE / "malha_{codigo}.json".format(codigo=codigo)
    if cache.is_file():
        logger.info("map cache hit %s", cache)
        carregado = json.loads(cache.read_text(encoding="utf-8"))
        if isinstance(carregado, dict):
            return carregado
        return None
    try:
        malha = _baixar_json(URL_MALHA.format(codigo=codigo))
        locais = _baixar_json(URL_LOCALIDADES.format(codigo=codigo))
    except (URLError, TimeoutError, json.JSONDecodeError, UnicodeError, OSError) as erro:
        logger.warning("map download failed for %s: %s", uf, erro)
        return None
    if not isinstance(malha, dict) or not isinstance(locais, list):
        logger.warning("map payload for %s has an unexpected shape", uf)
        return None
    nomes: dict[str, str] = {}
    for item in locais:
        if isinstance(item, dict) and "id" in item and "nome" in item:
            nomes[str(item["id"])] = str(item["nome"])
    feicoes = malha.get("features")
    if not isinstance(feicoes, list):
        return None
    for feicao in feicoes:
        if not isinstance(feicao, dict):
            continue
        propriedades = feicao.get("properties")
        if not isinstance(propriedades, dict):
            continue
        codigo_mun = str(propriedades.get("codarea", ""))
        propriedades["nome"] = nomes.get(codigo_mun, "")
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps(malha, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("downloaded IBGE mesh for %s", uf)
    except OSError as erro:
        logger.warning("could not cache mesh for %s: %s", uf, erro)
    return malha


def _aneis(geometria: dict[str, object]) -> list[list[list[float]]]:
    """Flatten polygon rings from a GeoJSON geometry.

    Args:
        geometria: GeoJSON geometry object.

    Returns:
        Rings as lists of [longitude, latitude] pairs.
    """
    tipo = geometria.get("type")
    coordenadas = geometria.get("coordinates")
    aneis: list[list[list[float]]] = []
    if tipo == "Polygon" and isinstance(coordenadas, list):
        for anel in coordenadas:
            if isinstance(anel, list):
                aneis.append(anel)
    elif tipo == "MultiPolygon" and isinstance(coordenadas, list):
        for poligono in coordenadas:
            if not isinstance(poligono, list):
                continue
            for anel in poligono:
                if isinstance(anel, list):
                    aneis.append(anel)
    return aneis


def montar_svg_mapa(relatorio: Relatorio) -> str:
    """Build an SVG choropleth of municipalities colored by campaign role.

    Args:
        relatorio: One state report.

    Returns:
        An SVG element, or an empty string when the mesh is unavailable.
    """
    malha = carregar_malha(relatorio.uf)
    if malha is None:
        return ""
    feicoes = malha.get("features")
    if not isinstance(feicoes, list) or not feicoes:
        return ""
    por_nome: dict[str, Municipio] = {}
    for item in relatorio.municipios:
        for chave in variantes_nome(item.nome):
            por_nome.setdefault(chave, item)
    pontos: list[tuple[float, float]] = []
    desenhos: list[tuple[list[list[list[float]]], Municipio | None, str]] = []
    for feicao in feicoes:
        if not isinstance(feicao, dict):
            continue
        geometria = feicao.get("geometry")
        propriedades = feicao.get("properties")
        if not isinstance(geometria, dict) or not isinstance(propriedades, dict):
            continue
        aneis = _aneis(geometria)
        if not aneis:
            continue
        for anel in aneis:
            for par in anel:
                if isinstance(par, list) and len(par) >= 2:
                    pontos.append((float(par[0]), float(par[1])))
        nome_ibge = str(propriedades.get("nome", ""))
        municipio = None
        for chave in variantes_nome(nome_ibge):
            if chave in por_nome:
                municipio = por_nome[chave]
                break
        desenhos.append((aneis, municipio, nome_ibge))
    if not pontos or not desenhos:
        return ""
    longitudes = [par[0] for par in pontos]
    latitudes = [par[1] for par in pontos]
    min_lon = min(longitudes)
    max_lon = max(longitudes)
    min_lat = min(latitudes)
    max_lat = max(latitudes)
    span_x = max_lon - min_lon
    span_y = max_lat - min_lat
    if span_x <= 0 or span_y <= 0:
        return ""
    largura = 920.0
    margem = 8.0
    escala = (largura - 2 * margem) / span_x
    altura = span_y * escala + 2 * margem

    def projetar(lon: float, lat: float) -> tuple[float, float]:
        x = margem + (lon - min_lon) * escala
        y = margem + (max_lat - lat) * escala
        return x, y

    caminhos: list[str] = []
    rotulos: list[str] = []
    casados = 0
    for indice_desenho, (aneis, municipio, nome_ibge) in enumerate(desenhos):
        partes: list[str] = []
        for anel in aneis:
            comandos: list[str] = []
            for indice, par in enumerate(anel):
                if not isinstance(par, list) or len(par) < 2:
                    continue
                x, y = projetar(float(par[0]), float(par[1]))
                letra = "M" if not comandos else "L"
                comandos.append(
                    "{letra}{x:.1f},{y:.1f}".format(letra=letra, x=x, y=y)
                )
            if len(comandos) >= 3:
                comandos.append("Z")
                partes.append(" ".join(comandos))
        if not partes:
            continue
        centro_x = 0.0
        centro_y = 0.0
        maior_anel = 0
        for anel in aneis:
            xs: list[float] = []
            ys: list[float] = []
            for par in anel:
                if not isinstance(par, list) or len(par) < 2:
                    continue
                x, y = projetar(float(par[0]), float(par[1]))
                xs.append(x)
                ys.append(y)
            if len(xs) > maior_anel:
                maior_anel = len(xs)
                centro_x = sum(xs) / len(xs)
                centro_y = sum(ys) / len(ys)
        if municipio is None:
            cor = COR_SEM_DADO
            papel = "Sem dado"
            info = "{nome}. Sem linha correspondente no CSV.".format(nome=nome_ibge)
        else:
            casados += 1
            cor = COR_PAPEL.get(municipio.papel, COR_SEM_DADO)
            papel = municipio.papel
            info = "{nome}. {papel}. {pp} p.p. {votos} votos.".format(
                nome=municipio.nome,
                papel=ROTULO_PAPEL[municipio.papel],
                pp=fmt_pp(municipio.dif_pp),
                votos=fmt_int(municipio.dif_votos),
            )
        nome_visivel = municipio.nome if municipio is not None else nome_ibge
        caminhos.append(
            "<path data-id=\"{indice}\" data-papel=\"{papel}\" data-info=\"{info}\" d=\"{d}\" fill=\"{cor}\" stroke=\"#ffffff\" stroke-width=\"0.7\" vector-effect=\"non-scaling-stroke\" fill-rule=\"evenodd\" onmouseenter=\"detalheMapa(this)\" onclick=\"detalheMapa(this)\" ondblclick=\"zoomNoMunicipio(this)\"><title>{titulo}</title></path>".format(
                indice=indice_desenho,
                papel=html.escape(papel, quote=True),
                info=html.escape(info, quote=True),
                d=" ".join(partes),
                cor=cor,
                titulo=html.escape(info),
            )
        )
        if maior_anel > 0:
            rotulos.append(
                "<text class=\"mapa-nome\" data-id=\"{indice}\" data-papel=\"{papel}\" x=\"{x:.1f}\" y=\"{y:.1f}\" text-anchor=\"middle\" dominant-baseline=\"middle\">{nome}</text>".format(
                    indice=indice_desenho,
                    papel=html.escape(papel, quote=True),
                    x=centro_x,
                    y=centro_y,
                    nome=html.escape(nome_visivel),
                )
            )
    logger.info(
        "map matched %s of %s municipalities in %s",
        casados,
        len(relatorio.municipios),
        relatorio.uf,
    )
    titulo = "Mapa dos municípios de {uf} por papel da campanha".format(uf=relatorio.uf)
    return (
        "<svg id=\"mapa-svg\" class=\"mapa-svg\" xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 {largura:.0f} {altura:.0f}\" role=\"img\" aria-label=\"{rotulo}\">"
        "<title>{rotulo}</title><g id=\"mapa-camada\">{caminhos}{rotulos}</g></svg>"
    ).format(
        largura=largura,
        altura=altura,
        rotulo=html.escape(titulo),
        caminhos="".join(caminhos),
        rotulos="".join(rotulos),
    )


def svg_deficit(regioes: list[Agregado]) -> str:
    """Draw a horizontal bar chart of the vote deficit by macro-region.

    Args:
        regioes: Regions already ordered for display.

    Returns:
        SVG markup.
    """
    if not regioes:
        return ""
    largura = 860.0
    margem_esq = 270.0
    margem_dir = 78.0
    topo = 8.0
    altura_linha = 32.0
    altura = topo + altura_linha * len(regioes) + 8.0
    maximo = max(item.deficit for item in regioes)
    if maximo <= 0:
        maximo = 1
    util = largura - margem_esq - margem_dir
    linhas: list[str] = []
    for indice, regiao in enumerate(regioes):
        y = topo + indice * altura_linha
        largura_barra = util * (regiao.deficit / maximo)
        valor = "{numero} mil".format(numero=fmt_decimal(regiao.deficit / 1000.0, 1))
        linhas.append(
            "<text x=\"{x}\" y=\"{y}\" font-size=\"12\" fill=\"#1a1a1a\">{nome}</text>".format(
                x=8,
                y=y + 18,
                nome=html.escape(regiao.nome),
            )
        )
        linhas.append(
            "<rect x=\"{x}\" y=\"{y}\" width=\"{w}\" height=\"16\" fill=\"{cor}\"></rect>".format(
                x=margem_esq,
                y=y + 6,
                w=max(largura_barra, 1.0),
                cor=COR_DEFICIT,
            )
        )
        linhas.append(
            "<text x=\"{x}\" y=\"{y}\" font-size=\"12\" fill=\"#1a1a1a\">{valor}</text>".format(
                x=margem_esq + largura_barra + 6,
                y=y + 18,
                valor=html.escape(valor),
            )
        )
    return (
        "<svg viewBox=\"0 0 {largura:.0f} {altura:.0f}\" width=\"{largura:.0f}\" height=\"{altura:.0f}\" role=\"img\" aria-label=\"Déficit de votos por macrorregião\">"
        "{linhas}</svg>"
    ).format(largura=largura, altura=altura, linhas="".join(linhas))


def svg_percentuais(regioes: list[Agregado]) -> str:
    """Draw grouped columns of Lula and Flavio shares by macro-region.

    Args:
        regioes: Regions already ordered for display.

    Returns:
        SVG markup.
    """
    if not regioes:
        return ""
    topo = 16.0
    base = 250.0
    margem_esq = 36.0
    grupo = 78.0
    largura = margem_esq + grupo * len(regioes) + 16.0
    altura = 420.0
    teto = 80.0
    maior = max(max(item.lula_pct, item.flavio_pct) for item in regioes)
    if maior > teto:
        teto = 100.0
    linhas: list[str] = [
        "<line x1=\"{x}\" y1=\"{topo}\" x2=\"{x}\" y2=\"{base}\" stroke=\"#d7dbe2\"></line>".format(
            x=margem_esq,
            topo=topo,
            base=base,
        )
    ]
    for marca in (0, 20, 40, 60, 80):
        if marca > teto:
            continue
        y = base - (base - topo) * (marca / teto)
        linhas.append(
            "<line x1=\"{x1}\" y1=\"{y}\" x2=\"{x2}\" y2=\"{y}\" stroke=\"#e6e8ee\"></line>".format(
                x1=margem_esq,
                x2=largura - 8,
                y=y,
            )
        )
        linhas.append(
            "<text x=\"4\" y=\"{y}\" font-size=\"11\" fill=\"#5c6b7a\">{marca}</text>".format(
                y=y + 4,
                marca=marca,
            )
        )
    for indice, regiao in enumerate(regioes):
        x0 = margem_esq + indice * grupo + 18
        for deslocamento, percentual, cor in (
            (0.0, regiao.lula_pct, COR_LULA),
            (18.0, regiao.flavio_pct, COR_FLAVIO),
        ):
            barra = (base - topo) * (percentual / teto)
            linhas.append(
                "<rect x=\"{x}\" y=\"{y}\" width=\"14\" height=\"{h}\" fill=\"{cor}\"></rect>".format(
                    x=x0 + deslocamento,
                    y=base - barra,
                    h=max(barra, 0.0),
                    cor=cor,
                )
            )
        rotulo = regiao.nome if len(regiao.nome) <= 16 else "{nome}.".format(nome=regiao.nome[:15])
        linhas.append(
            "<text x=\"{x}\" y=\"{y}\" font-size=\"11\" fill=\"#1a1a1a\" transform=\"rotate(40 {x} {y})\">{nome}</text>".format(
                x=x0,
                y=base + 18,
                nome=html.escape(rotulo),
            )
        )
    return (
        "<svg viewBox=\"0 0 {largura:.0f} {altura:.0f}\" width=\"{largura:.0f}\" height=\"{altura:.0f}\" role=\"img\" aria-label=\"Lula e Flávio por macrorregião\">"
        "{linhas}</svg>"
    ).format(largura=largura, altura=altura, linhas="".join(linhas))


def html_ordem(relatorio: Relatorio) -> str:
    """Build the priority callout that opens the canvas-style report.

    Args:
        relatorio: One state report.

    Returns:
        A callout element.
    """
    intensificar = [item for item in fila(relatorio) if item.papel == "Intensificar"]
    if intensificar:
        nomes = _nomes(intensificar, 6)
        frase = "Ordem de atenção: {nomes}.".format(nomes=nomes)
    else:
        frase = "Nenhum município grande entrou na faixa de intensificar."
    mapeadas = _regioes_no_grafico(relatorio)
    if mapeadas and relatorio.estado.deficit > 0:
        maior = max(mapeadas, key=lambda item: item.deficit)
        parcela = 100.0 * maior.deficit / relatorio.estado.deficit
        frase = "{frase} {regiao} concentra {parcela} do déficit estadual ({votos} votos, {pp} p.p.).".format(
            frase=frase,
            regiao=maior.nome,
            parcela=fmt_pct(parcela),
            votos=fmt_int(maior.dif_votos),
            pp=fmt_pp(maior.dif_pp),
        )
    return "<aside class=\"callout\"><strong>Ordem de atenção</strong><p>{texto}</p></aside>".format(
        texto=html.escape(frase),
    )


def html_frentes(relatorio: Relatorio) -> str:
    """Build the three-column allocation block used by the canvas layout.

    Args:
        relatorio: One state report.

    Returns:
        A section with three columns.
    """
    estado = relatorio.estado
    maior = max(relatorio.municipios, key=lambda item: item.aptos)
    parcela_aptos = (100.0 * maior.aptos / estado.aptos) if estado.aptos else 0.0
    if maior.dif_votos > 0:
        fechamento = "Lula já lidera por {votos} votos. O trabalho aqui é consolidar a dianteira, não empatar.".format(
            votos=fmt_int(maior.dif_votos),
        )
    else:
        fechamento = "Empatar pede cerca de {trocas} trocas de voto, ou {novos} votos novos se o total de Flávio permanecer o mesmo.".format(
            trocas=fmt_int(maior.trocas_para_empatar),
            novos=fmt_int(maior.votos_novos_para_empatar),
        )
    coluna_1 = [
        "{nome} tem {parcela} dos aptos do estado e margem de {pp} p.p. ({votos} votos). Cada ponto percentual nesse município vale {ponto} votos. {fechamento}".format(
            nome=maior.nome,
            parcela=fmt_pct(parcela_aptos),
            pp=fmt_pp(maior.dif_pp),
            votos=fmt_int(maior.dif_votos),
            ponto=fmt_int(int(round(maior.votos_por_pp))),
            fechamento=fechamento,
        ),
        "Lula fica à frente em {lula} dos {locais} locais. O esforço adicional cabe nos {flavio} restantes. A abstenção é {abst}. A preferência de quem faltou não está no arquivo.".format(
            lula=fmt_int(maior.locais_lula),
            locais=fmt_int(maior.locais),
            flavio=fmt_int(maior.locais_flavio),
            abst=fmt_pct(maior.abstencao_pct),
        ),
    ]
    outros = [
        item
        for item in fila(relatorio)
        if item.papel == "Intensificar" and item.nome != maior.nome
    ]
    if outros:
        detalhes = ", ".join(
            "{nome} ({pp} p.p.)".format(nome=item.nome, pp=fmt_pp(item.dif_pp))
            for item in outros[:6]
        )
        coluna_2 = [
            "Depois do maior colégio, a fila curta é {detalhes}.".format(detalhes=detalhes)
        ]
    else:
        coluna_2 = [
            "Fora do maior colégio, nenhum outro município cruzou a faixa de intensificar."
        ]
    regioes_curtas = [
        item
        for item in _regioes_no_grafico(relatorio)
        if classificar_regiao(item) == "Intensificar" and item.nome != maior.regiao
    ]
    if regioes_curtas:
        coluna_2.append(
            "Macrorregiões nessa faixa: {nomes}.".format(
                nomes=", ".join(
                    "{nome} ({pp} p.p., {votos} votos)".format(
                        nome=item.nome,
                        pp=fmt_pp(item.dif_pp),
                        votos=fmt_int(item.dif_votos),
                    )
                    for item in regioes_curtas[:3]
                )
            )
        )
    buracos = [
        item
        for item in _regioes_no_grafico(relatorio)
        if classificar_regiao(item) == "Reduzir o buraco"
    ]
    if buracos and estado.deficit > 0:
        principal = max(buracos, key=lambda item: item.deficit)
        parcela = 100.0 * principal.deficit / estado.deficit
        cidades_regiao = [
            item
            for item in relatorio.municipios
            if item.regiao == principal.nome and item.dif_votos < 0
        ]
        cidades_grandes = [item for item in cidades_regiao if item.aptos >= APTOS_DISPUTAVEL]
        cidades = sorted(
            cidades_grandes or cidades_regiao,
            key=lambda item: abs(item.dif_pp),
        )
        coluna_3 = [
            "{nome} tem {parcela} dos aptos do estado e {fatia} do déficit estadual. A margem regional é de {pp} p.p. ({votos} votos). Cada ponto ali vale {ponto} votos. A meta operacional é encurtar a margem.".format(
                nome=principal.nome,
                parcela=fmt_pct((100.0 * principal.aptos / estado.aptos) if estado.aptos else 0.0),
                fatia=fmt_pct(parcela),
                pp=fmt_pp(principal.dif_pp),
                votos=fmt_int(principal.dif_votos),
                ponto=fmt_int(int(round(principal.votos_por_pp))),
            )
        ]
        if cidades:
            coluna_3.append(
                "Dentro da região, os municípios grandes mais próximos são {nomes}.".format(
                    nomes=", ".join(
                        "{nome} ({pp} p.p.)".format(nome=item.nome, pp=fmt_pp(item.dif_pp))
                        for item in cidades[:4]
                    )
                )
            )
    else:
        reduzir = [
            item for item in relatorio.municipios if item.papel == "Reduzir o buraco"
        ]
        if reduzir:
            coluna_3 = [
                "Os maiores déficits municipais fora da faixa de virada são {nomes}.".format(
                    nomes=_nomes(sorted(reduzir, key=lambda item: item.dif_votos), 5)
                )
            ]
        else:
            coluna_3 = [
                "Nenhuma região acumulou um déficit largo o bastante para entrar como redução de buraco."
            ]
    blocos = [
        ("1. Fechar o maior colégio", coluna_1),
        ("2. Corredor ainda disputável", coluna_2),
        ("3. Encurtar o maior buraco", coluna_3),
    ]
    colunas_html: list[str] = []
    for titulo, paragrafos_coluna in blocos:
        miolos = "".join(
            "<p>{texto}</p>".format(texto=html.escape(texto))
            for texto in paragrafos_coluna
        )
        colunas_html.append(
            "<section><h3>{titulo}</h3>{miolos}</section>".format(
                titulo=html.escape(titulo),
                miolos=miolos,
            )
        )
    return "<div class=\"frentes\">{colunas}</div>".format(colunas="".join(colunas_html))


def html_presenca(relatorio: Relatorio) -> str:
    """Describe regions where a heavy push has low return.

    Args:
        relatorio: One state report.

    Returns:
        HTML section, or an empty string when no such region exists.
    """
    contidas = [
        item
        for item in _regioes_no_grafico(relatorio)
        if classificar_regiao(item) == "Presenca contida"
    ]
    extremos = sorted(
        (item for item in relatorio.municipios if item.dif_votos < 0),
        key=lambda item: item.dif_pp,
    )
    if not contidas and not extremos:
        return ""
    frases: list[str] = []
    if contidas:
        frases.append(
            "Presença contida em {nomes}. Nessas regiões a diferença já é larga: manter uma presença leve evita que a margem cresça, e o esforço maior fica em Intensificar e em Reduzir o buraco.".format(
                nomes=", ".join(
                    "{nome} ({pp} p.p.)".format(nome=item.nome, pp=fmt_pp(item.dif_pp))
                    for item in contidas
                )
            )
        )
    if extremos:
        pior = extremos[0]
        frases.append(
            "O extremo municipal do arquivo é {nome}, com {pp} p.p. e {votos} votos de diferença.".format(
                nome=pior.nome,
                pp=fmt_pp(pior.dif_pp),
                votos=fmt_int(pior.dif_votos),
            )
        )
    miolos = "".join("<p>{texto}</p>".format(texto=html.escape(texto)) for texto in frases)
    return "<h2>Onde não concentrar a estrutura</h2>{miolos}".format(miolos=miolos)


def html_seletor_mapa(papel: str, rotulo: str, cor: str, ativo: bool) -> str:
    """Build one map selector that shows only municipalities of that role.

    Args:
        papel: Role key used by the filter script. "Todas" shows every municipality.
        rotulo: Visible label.
        cor: Swatch color. Empty when the selector has no color.
        ativo: Whether this selector starts selected.

    Returns:
        A button placed on the map legend.
    """
    classe = "seletor-mapa ativo" if ativo else "seletor-mapa"
    if papel == "Todas":
        dica = "Mostra de novo todos os municípios do estado."
    else:
        dica = TEXTO_PAPEL.get(papel, TEXTO_PAPEL["Sem dado no CSV"])
    swatch = "<i style=\"background:{cor}\"></i>".format(cor=cor) if cor else ""
    return (
        "<button type=\"button\" class=\"{classe}\" data-filtro=\"{papel}\" "
        "data-rotulo=\"{rotulo_attr}\" aria-pressed=\"{pressed}\" "
        "onclick=\"filtrar('{papel}')\" title=\"{dica}\">"
        "{swatch}{rotulo}<span class=\"dica\" role=\"tooltip\">{dica_visivel}</span></button>"
    ).format(
        classe=classe,
        papel=html.escape(papel, quote=True),
        rotulo_attr=html.escape(rotulo, quote=True),
        pressed="true" if ativo else "false",
        dica=html.escape(dica, quote=True),
        swatch=swatch,
        rotulo=html.escape(rotulo),
        dica_visivel=html.escape(dica),
    )


def html_legenda_mapa() -> str:
    """Build clickable map selectors, one for each legend color.

    Returns:
        HTML for the selectors above the municipality map.
    """
    itens: list[str] = [
        html_seletor_mapa("Todas", "Todos", "", True),
    ]
    for chave, rotulo in ROTULO_PAPEL.items():
        itens.append(html_seletor_mapa(chave, rotulo, COR_PAPEL[chave], False))
    itens.append(html_seletor_mapa("Sem dado", "Sem dado no CSV", COR_SEM_DADO, False))
    return (
        "<div class=\"legenda seletores-mapa\" role=\"group\" "
        "aria-label=\"Filtrar municípios no mapa\">{itens}</div>"
    ).format(itens="".join(itens))


def html_botao_filtro(papel: str, rotulo: str, ativo: bool) -> str:
    """Build one municipality filter button with its role explanation.

    Args:
        papel: Internal role key passed to the filter script. Empty means all rows.
        rotulo: Visible label.
        ativo: Whether the button starts selected.

    Returns:
        A button element.
    """
    classe = " ativo" if ativo else ""
    dica = TEXTO_PAPEL.get(papel, "")
    titulo = " title=\"{dica}\"".format(dica=html.escape(dica, quote=True)) if dica else ""
    dica_html = (
        "<span class=\"dica\" role=\"tooltip\">{texto}</span>".format(texto=html.escape(dica))
        if dica
        else ""
    )
    return (
        "<button type=\"button\" class=\"{classe}\" data-filtro=\"{papel}\"{titulo} "
        "aria-pressed=\"{pressed}\" onclick=\"filtrar('{papel}')\">"
        "{rotulo}{dica}</button>"
    ).format(
        classe=classe.strip(),
        titulo=titulo,
        pressed="true" if ativo else "false",
        papel=html.escape(papel, quote=True),
        rotulo=html.escape(rotulo),
        dica=dica_html,
    )


def _celula(texto: str, alinhado: str = "left") -> str:
    """Escape one HTML table cell.

    Args:
        texto: Visible text.
        alinhado: left or right.

    Returns:
        A td element.
    """
    classe = "num" if alinhado == "right" else "txt"
    return "<td class=\"{classe}\">{texto}</td>".format(
        classe=classe,
        texto=html.escape(texto),
    )


def escrever_html(relatorio: Relatorio, destino: Path, svg_mapa: str) -> None:
    """Write a self-contained HTML report laid out like the strategy canvas.

    Args:
        relatorio: Aggregated state report.
        destino: HTML path. Parent directory must already exist.
        svg_mapa: Municipality choropleth, or an empty string.
    """
    estado = relatorio.estado
    regras = [
        "<li>{texto}</li>".format(texto=html.escape(regra))
        for regra in metodologia()
    ]
    kpis = [
        ("", "Lula no estado (votos válidos)", fmt_pct(estado.lula_pct)),
        ("perigo", "Flávio no estado (votos válidos)", fmt_pct(estado.flavio_pct)),
        ("atencao", "Diferença estadual", "{pp} p.p.".format(pp=fmt_pp(estado.dif_pp))),
        ("", "Abstenção sobre aptos", fmt_pct(estado.abstencao_pct)),
    ]
    cartoes: list[str] = []
    for classe, rotulo, valor in kpis:
        cartoes.append(
            "<article class=\"kpi {classe}\"><p class=\"valor\">{valor}</p><p class=\"rotulo\">{rotulo}</p></article>".format(
                classe=classe,
                valor=html.escape(valor),
                rotulo=html.escape(rotulo),
            )
        )
    abertura = (
        "Dá para priorizar. No conjunto do estado Lula tem {lula} dos votos válidos "
        "e Flávio Bolsonaro {flavio}: diferença de {pp} p.p. e de {votos} votos. "
        "Cada ponto percentual no estado vale {ponto} votos. Esse déficit se concentra em poucos blocos."
    ).format(
        lula=fmt_pct(estado.lula_pct),
        flavio=fmt_pct(estado.flavio_pct),
        pp=fmt_pp(estado.dif_pp),
        votos=fmt_int(estado.dif_votos),
        ponto=fmt_int(int(round(estado.votos_por_pp))),
    )
    regioes = _regioes_no_grafico(relatorio)
    if relatorio.fonte_regiao == "ibge":
        nome_grupo = "mesorregião do IBGE"
        nota_grupo = "Agrupamento pelas mesorregiões do IBGE. Um CSV passado em --regioes substitui esse recorte."
    else:
        nome_grupo = "macrorregião"
        nota_grupo = "Macrorregiões agrupadas para operação de campanha."
    if len(regioes) >= 2:
        maior_deficit = max(regioes, key=lambda item: item.deficit)
        graficos = "".join(
            [
                "<h2>Déficit de votos de Lula por {grupo}</h2>".format(grupo=html.escape(nome_grupo)),
                "<p>Cada barra é a soma de votos de Flávio menos votos de Lula. {nome} concentra {votos} votos de diferença.</p>".format(
                    nome=html.escape(maior_deficit.nome),
                    votos=html.escape(fmt_int(maior_deficit.dif_votos)),
                ),
                "<div class=\"grafico\">",
                svg_deficit(regioes),
                "</div>",
                "<p class=\"fonte\">Fonte: {origem}. {nota} Valores em milhares de votos. Diferença = Flávio menos Lula.</p>".format(
                    origem=html.escape(relatorio.origem),
                    nota=html.escape(nota_grupo),
                ),
                "<h2>Lula e Flávio sobre os votos válidos</h2>",
                "<div class=\"legenda\">",
                "<span><i style=\"background:{lula}\"></i>Lula (% dos válidos)</span>".format(lula=COR_LULA),
                "<span><i style=\"background:{flavio}\"></i>Flávio (% dos válidos)</span>".format(flavio=COR_FLAVIO),
                "</div>",
                "<div class=\"grafico\">",
                svg_percentuais(regioes),
                "</div>",
                "<p class=\"fonte\">Percentual sobre votos válidos. As duas séries somam menos de 100% porque há outros votos válidos.</p>",
            ]
        )
    else:
        graficos = ""
    if svg_mapa:
        bloco_mapa = "".join(
            [
                "<h2>Mapa dos municípios</h2>",
                "<p>A cor é o papel da campanha. Os seletores mostram só os municípios daquele papel. A roda do mouse aproxima, o arrasto move o mapa e dois cliques num município centralizam o nome.</p>",
                "<div class=\"mapa-wrap\">",
                html_legenda_mapa(),
                "<div class=\"zoom-mapa\">",
                "<button type=\"button\" onclick=\"zoomMapa(1.3)\">Aproximar</button>",
                "<button type=\"button\" onclick=\"zoomMapa(0.77)\">Afastar</button>",
                "<button type=\"button\" onclick=\"zoomMapa(0)\">Ver o estado</button>",
                "</div>",
                svg_mapa,
                "<p id=\"mapa-info\">Passe o cursor sobre um município.</p>",
                "</div>",
                "<p class=\"fonte\">Malha municipal do IBGE, qualidade mínima. Município sem cor correspondente não veio no CSV.</p>",
            ]
        )
    else:
        bloco_mapa = (
            "<aside class=\"callout aviso\"><strong>Mapa indisponível</strong>"
            "<p>A malha do IBGE não pôde ser baixada nesta execução. "
            "O restante do relatório foi gerado. Rode de novo com rede para criar o mapa.</p></aside>"
        )

    linhas_regiao: list[str] = []
    for regiao in relatorio.regioes:
        parcela = 0.0
        if estado.deficit > 0:
            parcela = 100.0 * regiao.deficit / estado.deficit
        rotulo = regiao.nome if regiao.nome != ROTULO_SEM_REGIAO else "Sem macrorregião"
        papel_interno = classificar_regiao(regiao)
        papel = ROTULO_PAPEL_REGIAO[papel_interno]
        colunas = [
            _celula(rotulo),
            _celula(papel),
            _celula(fmt_int(regiao.municipios), "right"),
            _celula(fmt_int(regiao.aptos), "right"),
            _celula(fmt_pct(regiao.lula_pct), "right"),
            _celula(fmt_pct(regiao.flavio_pct), "right"),
            _celula(fmt_pp(regiao.dif_pp), "right"),
            _celula(fmt_int(regiao.dif_votos), "right"),
            _celula(fmt_int(int(round(regiao.votos_por_pp))), "right"),
            _celula(fmt_pct(parcela), "right"),
            _celula(
                "{lula} / {total}".format(
                    lula=fmt_int(regiao.locais_lula),
                    total=fmt_int(regiao.locais),
                ),
                "right",
            ),
        ]
        marca = papel_interno if papel_interno in COR_PAPEL else ""
        linhas_regiao.append(
            "<tr data-papel=\"{marca}\">{cols}</tr>".format(
                marca=html.escape(marca, quote=True),
                cols="".join(colunas),
            )
        )

    linhas_muni: list[str] = []
    for item in fila(relatorio):
        regiao = item.regiao if item.regiao != ROTULO_SEM_REGIAO else "Sem macrorregião"
        colunas = [
            _celula(item.nome),
            _celula(regiao),
            _celula(ROTULO_PAPEL[item.papel]),
            _celula(fmt_pp(item.dif_pp), "right"),
            _celula(fmt_int(item.dif_votos), "right"),
            _celula(fmt_int(int(round(item.indice))), "right"),
            _celula(fmt_int(item.aptos), "right"),
            _celula(fmt_pct(item.abstencao_pct), "right"),
            _celula(fmt_pct(item.lula_pct), "right"),
            _celula(fmt_pct(item.flavio_pct), "right"),
            _celula(
                "{lula} / {flavio}".format(
                    lula=fmt_int(item.locais_lula),
                    flavio=fmt_int(item.locais_flavio),
                ),
                "right",
            ),
        ]
        linhas_muni.append(
            "<tr data-papel=\"{papel}\">{cols}</tr>".format(
                papel=html.escape(item.papel, quote=True),
                cols="".join(colunas),
            )
        )

    avisos_html = ""
    if relatorio.avisos:
        itens = "".join(
            "<li>{texto}</li>".format(texto=html.escape(aviso))
            for aviso in relatorio.avisos
        )
        avisos_html = "<h2>Avisos de conferência</h2><ul>{itens}</ul>".format(itens=itens)

    estilo = """
body { margin: 0; font-family: Arial, Helvetica, sans-serif; color: #1a1a1a; background: #f7f7f8; }
main { max-width: 1100px; margin: 0 auto; padding: 28px 20px 56px; }
h1 { font-size: 24px; font-weight: 650; margin: 0 0 8px; }
h2 { font-size: 18px; margin: 28px 0 8px; }
h3 { font-size: 16px; margin: 0 0 8px; }
p, li { line-height: 1.45; }
.fonte { color: #5c6b7a; font-size: 13px; }
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin: 18px 0; }
.kpi .valor { margin: 0; font-size: 22px; font-weight: 700; }
.kpi .rotulo { margin: 4px 0 0; color: #5c6b7a; font-size: 13px; }
.kpi.perigo .valor { color: #a33b3b; }
.kpi.atencao .valor { color: #c47b2b; }
.callout { border-left: 3px solid #2f6f9f; background: #f3f7fb; padding: 12px 14px; margin: 16px 0; }
.callout.aviso { border-left-color: #c47b2b; background: #fbf6ef; }
.callout strong { display: block; margin-bottom: 4px; }
.callout p { margin: 0; }
.frentes { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; }
.frentes p { margin: 0 0 10px; }
.grafico { overflow-x: auto; }
.grafico svg { height: auto; display: block; }
.mapa-wrap svg { width: 100%; height: auto; display: block; }
.mapa-svg { touch-action: none; cursor: grab; background: #f7f8fa; }
.mapa-svg.arrastando { cursor: grabbing; }
.zoom-mapa { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 8px; }
.zoom-mapa button { border: 1px solid #c5cad3; background: #fff; color: #1a1a1a; border-radius: 999px; padding: 6px 12px; cursor: pointer; font: inherit; }
.mapa-nome { display: none; font-weight: 650; fill: #1a1a1a; stroke: #ffffff; paint-order: stroke; pointer-events: none; }
.mapa-wrap { background: #fff; border: 1px solid #d7dbe2; padding: 12px; overflow: visible; }
.legenda { display: flex; flex-wrap: wrap; gap: 14px; margin: 8px 0 0; font-size: 13px; }
.legenda i { display: inline-block; width: 12px; height: 12px; margin-right: 6px; vertical-align: -1px; }
.seletores-mapa { gap: 8px; margin: 0 0 12px; }
.seletor-mapa { position: relative; display: inline-flex; align-items: center; border: 1px solid #c5cad3; background: #fff; color: #1a1a1a; border-radius: 999px; padding: 6px 12px; cursor: pointer; font: inherit; }
.seletor-mapa.ativo { background: #1a1a1a; color: #fff; border-color: #1a1a1a; }
.seletor-mapa.ativo i { box-shadow: 0 0 0 1px #fff; }
.com-dica { position: relative; cursor: help; }
.dica { display: none; position: absolute; left: 0; bottom: calc(100% + 8px); width: 340px; background: #1a1a1a; color: #fff; padding: 8px 10px; font-size: 12px; line-height: 1.45; z-index: 3; text-align: left; font-weight: 400; }
.com-dica:hover .dica, .com-dica:focus .dica, button:hover .dica, button:focus .dica { display: block; }
#mapa-info { min-height: 1.3em; margin: 8px 0 0; }
.mapa-wrap svg.com-nomes .mapa-nome { display: block; }
.mapa-wrap path { cursor: pointer; }
.mapa-wrap path:hover { stroke: #1a1a1a; stroke-width: 1.4; }
hr.divisoria { border: 0; border-top: 1px solid #d7dbe2; margin: 28px 0 8px; }
.filtros { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0 12px; }
.filtros button { position: relative; border: 1px solid #c5cad3; background: #fff; color: #1a1a1a; border-radius: 999px; padding: 6px 12px; cursor: pointer; font: inherit; }
.filtros button.ativo { background: #1a1a1a; color: #fff; border-color: #1a1a1a; }
.tabela-wrap { overflow-x: auto; border: 1px solid #d7dbe2; background: #fff; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #e6e8ee; text-align: left; white-space: nowrap; }
th { background: #f3f4f6; color: #1a1a1a; position: sticky; top: 0; font-weight: 650; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr:nth-child(even) { background: #fafafa; }
tr[data-papel="Intensificar"] td:first-child { border-left: 3px solid #c47b2b; }
tr[data-papel="Reduzir o buraco"] td:first-child { border-left: 3px solid #a33b3b; }
tr[data-papel="Consolidar"] td:first-child { border-left: 3px solid #2e7d4f; }
tr[data-papel="Presenca contida"] td:first-child { border-left: 3px solid #5c6b7a; }
tr[data-papel="Apoio local"] td:first-child { border-left: 3px solid #8d99a6; }
caption { caption-side: bottom; text-align: left; color: #5c6b7a; padding: 8px 10px; white-space: normal; }
details { margin-top: 28px; }
@media (max-width: 900px) {
  .kpis, .frentes { grid-template-columns: 1fr; }
}
"""
    documento = "".join(
        [
            "<!DOCTYPE html><html lang=\"pt-BR\"><head><meta charset=\"utf-8\">",
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
            "<title>Estratégia ",
            html.escape(relatorio.uf),
            "</title><style>",
            estilo,
            "</style></head><body><main>",
            "<h1>Onde intensificar a campanha de Lula — ",
            html.escape(relatorio.uf),
            "</h1><p>",
            html.escape(abertura),
            "</p><p class=\"fonte\">Fonte: ",
            html.escape(relatorio.origem),
            ". Gerado em ",
            html.escape(relatorio.gerado_em),
            ". Diferença = Lula menos Flávio Bolsonaro, recalculada sobre os votos válidos. ",
            html.escape(nota_grupo),
            "</p>",
            "<section class=\"kpis\">",
            "".join(cartoes),
            "</section>",
            html_ordem(relatorio),
            graficos,
            bloco_mapa,
            "<h2>Como distribuir a estrutura</h2>",
            html_frentes(relatorio),
            "<hr class=\"divisoria\">",
            "<h2>",
            html.escape(nome_grupo[0].upper() + nome_grupo[1:]),
            " e o papel de cada uma</h2>",
            "<p class=\"fonte\">Votos em 1 p.p. = votos válidos da região divididos por 100. Parcela do déficit = fatia dos votos em que Lula está atrás.</p>",
            "<div class=\"tabela-wrap\"><table><thead><tr>",
            "<th>Macrorregião</th><th>Papel</th><th>Municípios</th><th>Aptos</th><th>Lula</th><th>Flávio</th>",
            "<th>Diferença (p.p.)</th><th>Diferença (votos)</th><th>Votos em 1 p.p.</th><th>Parcela do déficit</th><th>Locais de Lula</th>",
            "</tr></thead><tbody>",
            "\n".join(linhas_regiao),
            "</tbody><caption>Parcela do déficit usa só o que Lula está atrás.</caption></table></div>",
            "<h2>Municípios que mudam a fila</h2>",
            "<p>A tabela reúne todos os municípios, na ordem operacional. Estes seletores são os mesmos do mapa.</p>",
            "<div class=\"filtros\">",
            html_botao_filtro("Todas", "Todas", True),
            html_botao_filtro("Intensificar", "Intensificar", False),
            html_botao_filtro("Reduzir o buraco", "Reduzir o buraco", False),
            html_botao_filtro("Consolidar", "Consolidar", False),
            html_botao_filtro("Apoio local", "Apoio local", False),
            html_botao_filtro("Presenca contida", "Presença contida", False),
            "</div>",
            "<div class=\"tabela-wrap\"><table id=\"tabela-municipios\"><thead><tr>",
            "<th>Município</th><th>Macrorregião</th><th>Papel</th><th>Diferença (p.p.)</th><th>Diferença (votos)</th>",
            "<th>Índice</th><th>Aptos</th><th>Abstenção</th><th>Lula</th><th>Flávio</th><th>Locais Lula / Flávio</th>",
            "</tr></thead><tbody>",
            "\n".join(linhas_muni),
            "</tbody><caption>Diferença em pontos e em votos: Lula menos Flávio. Abstenção sobre aptos. Em Intensificar e Reduzir o buraco, o índice soma metade das abstenções e aplica o desconto da margem.</caption></table></div>",
            html_presenca(relatorio),
            "<aside class=\"callout aviso\"><strong>Limite deste arquivo</strong>",
            "<p>O recorte é municipal. Faltam zona, seção, perfil do eleitor e série histórica. ",
            "A abstenção mostra onde há gente para mobilizar; a preferência de quem faltou continua desconhecida. ",
            "O próximo corte útil é o resultado por local de votação nos municípios de intensificar e nos de maior déficit.</p></aside>",
            "<details><summary>Regras usadas na classificação</summary><ul>",
            "\n".join(regras),
            "</ul></details>",
            avisos_html,
            "</main><script>",
            "function filtrar(papel) {",
            "  var linhas = document.querySelectorAll('#tabela-municipios tbody tr');",
            "  for (var i = 0; i < linhas.length; i += 1) {",
            "    var valor = linhas[i].getAttribute('data-papel');",
            "    linhas[i].hidden = !(papel === 'Todas' || valor === papel);",
            "  }",
            "  var formas = document.querySelectorAll('.mapa-wrap path');",
            "  for (var k = 0; k < formas.length; k += 1) {",
            "    var papelForma = formas[k].getAttribute('data-papel');",
            "    var ativa = papel === 'Todas' || papelForma === papel;",
            "    formas[k].style.opacity = ativa ? '1' : '0.08';",
            "    formas[k].style.pointerEvents = ativa ? 'auto' : 'none';",
            "  }",
            "  var nomes = document.querySelectorAll('.mapa-nome');",
            "  for (var n = 0; n < nomes.length; n += 1) {",
            "    var papelNome = nomes[n].getAttribute('data-papel');",
            "    var nomeAtivo = papel === 'Todas' || papelNome === papel;",
            "    nomes[n].style.display = nomeAtivo ? '' : 'none';",
            "  }",
            "  var botoes = document.querySelectorAll('[data-filtro]');",
            "  for (var j = 0; j < botoes.length; j += 1) {",
            "    var marcado = botoes[j].getAttribute('data-filtro') === papel;",
            "    botoes[j].classList.toggle('ativo', marcado);",
            "    botoes[j].setAttribute('aria-pressed', marcado ? 'true' : 'false');",
            "  }",
            "  var info = document.getElementById('mapa-info');",
            "  if (info && papel === 'Todas') {",
            "    info.textContent = 'Passe o cursor sobre um município.';",
            "  } else if (info) {",
            "    var escolhido = document.querySelector('.seletores-mapa [data-filtro=\"' + papel + '\"]');",
            "    var nome = escolhido ? escolhido.getAttribute('data-rotulo') : papel;",
            "    info.textContent = 'Mostrando só: ' + nome + '.';",
            "  }",
            "}",
            "function detalheMapa(path) {",
            "  var alvo = document.getElementById('mapa-info');",
            "  if (alvo) { alvo.textContent = path.getAttribute('data-info'); }",
            "}",
            "var mapaZoom = { escala: 1, x: 0, y: 0 };",
            "function aplicarZoom() {",
            "  var camada = document.getElementById('mapa-camada');",
            "  var svg = document.getElementById('mapa-svg');",
            "  if (!camada || !svg) { return; }",
            "  camada.setAttribute('transform', 'translate(' + mapaZoom.x + ' ' + mapaZoom.y + ') scale(' + mapaZoom.escala + ')');",
            "  var nomesZoom = document.querySelectorAll('.mapa-nome');",
            "  var tamanho = (13 / mapaZoom.escala).toFixed(2);",
            "  var halo = (2.6 / mapaZoom.escala).toFixed(2);",
            "  for (var i = 0; i < nomesZoom.length; i += 1) {",
            "    nomesZoom[i].style.fontSize = tamanho + 'px';",
            "    nomesZoom[i].style.strokeWidth = halo + 'px';",
            "  }",
            "  if (mapaZoom.escala >= 2.4) { svg.classList.add('com-nomes'); }",
            "  else { svg.classList.remove('com-nomes'); }",
            "}",
            "function pontoNoMapa(svg, clienteX, clienteY) {",
            "  var ponto = svg.createSVGPoint();",
            "  ponto.x = clienteX;",
            "  ponto.y = clienteY;",
            "  var matriz = svg.getScreenCTM();",
            "  if (!matriz) { return ponto; }",
            "  return ponto.matrixTransform(matriz.inverse());",
            "}",
            "function zoomMapa(fator) {",
            "  var svg = document.getElementById('mapa-svg');",
            "  if (!svg) { return; }",
            "  if (fator === 0) {",
            "    mapaZoom.escala = 1; mapaZoom.x = 0; mapaZoom.y = 0; aplicarZoom(); return;",
            "  }",
            "  var caixa = svg.viewBox.baseVal;",
            "  var centroX = caixa.width / 2;",
            "  var centroY = caixa.height / 2;",
            "  var nova = Math.min(16, Math.max(1, mapaZoom.escala * fator));",
            "  var localX = (centroX - mapaZoom.x) / mapaZoom.escala;",
            "  var localY = (centroY - mapaZoom.y) / mapaZoom.escala;",
            "  mapaZoom.escala = nova;",
            "  mapaZoom.x = centroX - localX * nova;",
            "  mapaZoom.y = centroY - localY * nova;",
            "  if (nova <= 1) { mapaZoom.escala = 1; mapaZoom.x = 0; mapaZoom.y = 0; }",
            "  aplicarZoom();",
            "}",
            "function zoomNoMunicipio(path) {",
            "  var svg = document.getElementById('mapa-svg');",
            "  if (!svg) { return; }",
            "  var caixa = path.getBBox();",
            "  var vista = svg.viewBox.baseVal;",
            "  var folga = 36;",
            "  var escalaX = vista.width / Math.max(caixa.width + folga, 1);",
            "  var escalaY = vista.height / Math.max(caixa.height + folga, 1);",
            "  var escala = Math.min(16, Math.max(2.6, Math.min(escalaX, escalaY)));",
            "  var meioX = caixa.x + caixa.width / 2;",
            "  var meioY = caixa.y + caixa.height / 2;",
            "  mapaZoom.escala = escala;",
            "  mapaZoom.x = vista.width / 2 - meioX * escala;",
            "  mapaZoom.y = vista.height / 2 - meioY * escala;",
            "  aplicarZoom();",
            "  detalheMapa(path);",
            "}",
            "function iniciarZoomMapa() {",
            "  var svg = document.getElementById('mapa-svg');",
            "  if (!svg) { return; }",
            "  var arrastando = false;",
            "  var ultimoX = 0;",
            "  var ultimoY = 0;",
            "  svg.addEventListener('wheel', function(evento) {",
            "    evento.preventDefault();",
            "    var ponto = pontoNoMapa(svg, evento.clientX, evento.clientY);",
            "    var fator = evento.deltaY < 0 ? 1.15 : (1 / 1.15);",
            "    var nova = Math.min(16, Math.max(1, mapaZoom.escala * fator));",
            "    var localX = (ponto.x - mapaZoom.x) / mapaZoom.escala;",
            "    var localY = (ponto.y - mapaZoom.y) / mapaZoom.escala;",
            "    mapaZoom.escala = nova;",
            "    mapaZoom.x = ponto.x - localX * nova;",
            "    mapaZoom.y = ponto.y - localY * nova;",
            "    if (nova <= 1) { mapaZoom.escala = 1; mapaZoom.x = 0; mapaZoom.y = 0; }",
            "    aplicarZoom();",
            "  }, { passive: false });",
            "  svg.addEventListener('pointerdown', function(evento) {",
            "    if (evento.button !== 0) { return; }",
            "    arrastando = true;",
            "    ultimoX = evento.clientX;",
            "    ultimoY = evento.clientY;",
            "    svg.classList.add('arrastando');",
            "    svg.setPointerCapture(evento.pointerId);",
            "  });",
            "  svg.addEventListener('pointermove', function(evento) {",
            "    if (!arrastando) { return; }",
            "    var antes = pontoNoMapa(svg, ultimoX, ultimoY);",
            "    var agora = pontoNoMapa(svg, evento.clientX, evento.clientY);",
            "    mapaZoom.x += agora.x - antes.x;",
            "    mapaZoom.y += agora.y - antes.y;",
            "    ultimoX = evento.clientX;",
            "    ultimoY = evento.clientY;",
            "    aplicarZoom();",
            "  });",
            "  function soltar(evento) {",
            "    arrastando = false;",
            "    svg.classList.remove('arrastando');",
            "    if (svg.hasPointerCapture(evento.pointerId)) { svg.releasePointerCapture(evento.pointerId); }",
            "  }",
            "  svg.addEventListener('pointerup', soltar);",
            "  svg.addEventListener('pointercancel', soltar);",
            "}",
            "iniciarZoomMapa();",
            "</script></body></html>",
        ]
    )
    destino.write_text(documento, encoding="utf-8")


def gerar_arquivos(
    relatorio: Relatorio,
    diretorio: Path,
    sufixo: str,
) -> tuple[Path, Path]:
    """Write the XLSX and HTML pair for one state.

    Args:
        relatorio: Aggregated state report.
        diretorio: Output directory. Created when missing.
        sufixo: Extra stem fragment when two inputs share a UF.

    Returns:
        Paths of the XLSX and the HTML file.
    """
    diretorio.mkdir(parents=True, exist_ok=True)
    base = "estrategia_{uf}{sufixo}".format(uf=relatorio.uf, sufixo=sufixo)
    xlsx = diretorio / "{base}.xlsx".format(base=base)
    pagina = diretorio / "{base}.html".format(base=base)
    svg_mapa = montar_svg_mapa(relatorio)
    if svg_mapa:
        mapa = diretorio / "{base}_mapa.svg".format(base=base)
        mapa.write_text(
            "".join(
                [
                    "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n",
                    svg_mapa,
                    "\n",
                ]
            ),
            encoding="utf-8",
        )
        logger.info("wrote %s", mapa)
    escrever_xlsx(relatorio, xlsx)
    escrever_html(relatorio, pagina, svg_mapa)
    logger.info("wrote %s", xlsx)
    logger.info("wrote %s", pagina)
    return xlsx, pagina


def processar_arquivo(
    caminho: Path,
    diretorio: Path,
    mapa_externo: dict[tuple[str, str], str],
    ocupados: set[str],
) -> list[tuple[Path, Path]]:
    """Turn one CSV into one report per UF found inside it.

    Args:
        caminho: Result CSV.
        diretorio: Output directory.
        mapa_externo: Optional region map.
        ocupados: Stems already written in this run, to avoid clobbering.

    Returns:
        Written file pairs.

    Raises:
        ErroCsv: When the CSV cannot be read.
    """
    municipios, avisos = ler_municipios(caminho, mapa_externo)
    por_uf: dict[str, list[Municipio]] = {}
    for item in municipios:
        por_uf.setdefault(item.uf, []).append(item)
    escritos: list[tuple[Path, Path]] = []
    for uf in sorted(por_uf):
        avisos_uf = [mensagem for uf_aviso, mensagem in avisos if uf_aviso == uf]
        municipios_uf, fonte_regiao = completar_regioes(por_uf[uf])
        relatorio = montar_relatorio(
            uf,
            municipios_uf,
            str(caminho),
            avisos_uf,
            fonte_regiao,
        )
        base = "estrategia_{uf}".format(uf=uf)
        sufixo = ""
        if base in ocupados:
            sufixo = "_{nome}".format(nome=caminho.stem)
        ocupados.add("{base}{sufixo}".format(base=base, sufixo=sufixo))
        escritos.append(gerar_arquivos(relatorio, diretorio, sufixo))
    return escritos


def construir_argumentos(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Arguments excluding the program name.

    Returns:
        Parsed namespace with csvs, saida, and regioes.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Gera relatorio XLSX e HTML a partir de CSV no padrao "
            "resumo_municipios_RJ.csv. Aceita varios arquivos e outros estados."
        )
    )
    parser.add_argument(
        "csvs",
        nargs="+",
        type=Path,
        help="Um ou mais CSV com o mesmo cabecalho do resumo municipal.",
    )
    parser.add_argument(
        "--saida",
        type=Path,
        default=Path("saida"),
        help="Diretorio dos arquivos gerados. Padrao: ./saida",
    )
    parser.add_argument(
        "--regioes",
        type=Path,
        default=None,
        help=(
            "CSV opcional com MUNICIPIO e REGIAO. "
            "UF e opcional e restringe a linha a um estado."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the report generator.

    Args:
        argv: Arguments excluding the program name. None uses sys.argv.

    Returns:
        0 when every input was written, 1 when at least one input failed.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    argumentos = construir_argumentos(sys.argv[1:] if argv is None else argv)
    try:
        mapa = (
            carregar_mapa_externo(argumentos.regioes)
            if argumentos.regioes is not None
            else {}
        )
    except ErroCsv as erro:
        logger.error("%s", erro)
        return 1

    falhas = 0
    ocupados: set[str] = set()
    for caminho in argumentos.csvs:
        try:
            processar_arquivo(caminho, argumentos.saida, mapa, ocupados)
        except ErroCsv as erro:
            falhas += 1
            logger.error("%s: %s", caminho, erro)
        except OSError as erro:
            falhas += 1
            logger.error("%s: %s", caminho, erro)
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
