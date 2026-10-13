# -*- coding: utf-8 -*-
"""Sensitivity of the published roles and of the legacy queue.

One parameter moves at a time. The official CSVs and the HTML reports are
only read. Competitiveness is not treated as an operational priority.
"""

from __future__ import annotations

import csv
import hashlib
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from gerar_relatorio_estrategia import (  # noqa: E402
    ORDEM_PAPEL,
    PESO_AUSENTE,
    PRECEDENCIAS_MUNICIPIO,
    ROTULO_SEM_REGIAO,
    Agregado,
    Municipio,
    ParametrosFator,
    ParametrosMunicipio,
    ParametrosRegiao,
    classificar_municipio,
    classificar_regiao,
    competitividade_eleitoral,
    completar_regioes,
    fator_proximidade,
    indice_atencao,
    ler_municipios,
)

DELTAS: tuple[float, ...] = (0.95, 1.05, 0.90, 1.10, 0.80, 1.20)
FRACOES_TOPO: tuple[float, ...] = (0.05, 0.10, 0.20)
PAPEIS: tuple[str, ...] = (
    "Intensificar",
    "Reduzir o buraco",
    "Consolidar",
    "Apoio local",
    "Presenca contida",
)
ORDEM_REGIAO: dict[str, int] = {
    "Intensificar": 0,
    "Reduzir o buraco": 1,
    "Consolidar": 2,
    "Seletivo": 3,
    "Presenca contida": 4,
    "Sem mapa": 5,
}
CAMPOS_MUNICIPIO: tuple[tuple[str, bool], ...] = (
    ("pp_curto", False),
    ("aptos_curto", True),
    ("pp_medio", False),
    ("aptos_muito_grande", True),
    ("pp_disputavel", False),
    ("aptos_disputavel", True),
    ("deficit_volume", True),
    ("pp_estrutural", False),
    ("aptos_pequeno", True),
    ("pp_pequeno_largo", False),
)
CAMPOS_FATOR: tuple[tuple[str, bool], ...] = (
    ("ate_8", False),
    ("ate_15", False),
    ("ate_25", False),
    ("acima_25", False),
    ("limite_8", False),
    ("limite_15", False),
    ("limite_25", False),
)
CAMPOS_REGIAO: tuple[tuple[str, bool], ...] = (
    ("pp_estrutural", False),
    ("pp_curta", False),
    ("votos_pp", True),
    ("pp_disputavel", False),
    ("deficit_disputavel", True),
    ("deficit_volume", True),
    ("pp_apertada", False),
    ("margem_volume", False),
)


@dataclass(frozen=True)
class Linha:
    """One municipality kept in memory for the sweep."""

    uf: str
    nome: str
    regiao: str
    locais: int
    aptos: int
    comparecimento: int
    abstencoes: int
    validos: int
    lula: int
    flavio: int
    dif_pp: float
    dif_votos: int
    locais_lula: int
    locais_flavio: int
    locais_empate: int
    papel: str
    indice: float

    @property
    def chave(self) -> str:
        """Stable id used in rankings and output tables."""
        return "{uf}|{nome}".format(uf=self.uf, nome=self.nome)

    @property
    def competitividade(self) -> float | None:
        """Closeness of Lula and Flavio. None when the sum is zero."""
        return competitividade_eleitoral(self.lula, self.flavio)


@dataclass(frozen=True)
class RegiaoBase:
    """One macro-region summed from the municipalities that belong to it."""

    uf: str
    nome: str
    agregado: Agregado
    papel: str

    @property
    def chave(self) -> str:
        """Stable id of the region inside its state."""
        return "{uf}|{nome}".format(uf=self.uf, nome=self.nome)


@dataclass(frozen=True)
class Cenario:
    """One perturbation, or a note that the perturbation was rejected."""

    familia: str
    parametro: str
    fator_relativo: float
    valor: str
    aceito: bool
    motivo: str
    municipio: ParametrosMunicipio
    fatores: ParametrosFator
    peso: float
    regiao: ParametrosRegiao


class ArvoreSoma:
    """Fenwick tree used to count concordant pairs in Kendall's tau-b."""

    def __init__(self, tamanho: int) -> None:
        self.nos = [0] * (tamanho + 1)

    def soma(self, indice: int) -> int:
        """Return the prefix sum up to indice, inclusive."""
        total = 0
        while indice > 0:
            total += self.nos[indice]
            indice -= indice & -indice
        return total

    def atualiza(self, indice: int, delta: int) -> None:
        """Add delta at indice."""
        while indice < len(self.nos):
            self.nos[indice] += delta
            indice += indice & -indice


def pares_iguais(valores: list[tuple[float, ...]]) -> int:
    """Count unordered pairs that share the same key.

    Args:
        valores: Ranking keys.

    Returns:
        Number of tied pairs.
    """
    ordenados = sorted(valores)
    total = 0
    inicio = 0
    tamanho = len(ordenados)
    while inicio < tamanho:
        fim = inicio + 1
        while fim < tamanho and ordenados[fim] == ordenados[inicio]:
            fim += 1
        grupo = fim - inicio
        total += grupo * (grupo - 1) // 2
        inicio = fim
    return total


def kendall_tau_b(
    origem: list[tuple[float, ...]],
    comparado: list[tuple[float, ...]],
) -> float | None:
    """Kendall tau-b. Ties in either ranking stay in the denominator.

    Args:
        origem: Baseline keys. Smaller means a better rank.
        comparado: Keys under the scenario, aligned with origem.

    Returns:
        Tau-b, or None when a ranking has no pair that can move.
    """
    tamanho = len(origem)
    if tamanho < 2 or len(comparado) != tamanho:
        return None
    postos = {valor: indice + 1 for indice, valor in enumerate(sorted(set(comparado)))}
    pontos = sorted(
        ((origem[indice], postos[comparado[indice]]) for indice in range(tamanho)),
        key=lambda item: (item[0], item[1]),
    )
    arvore = ArvoreSoma(len(postos))
    concordantes = 0
    discordantes = 0
    cursor = 0
    while cursor < tamanho:
        proximo = cursor + 1
        while proximo < tamanho and pontos[proximo][0] == pontos[cursor][0]:
            proximo += 1
        for posicao in range(cursor, proximo):
            posto = pontos[posicao][1]
            menores = arvore.soma(posto - 1)
            maiores = arvore.soma(len(postos)) - arvore.soma(posto)
            concordantes += menores
            discordantes += maiores
        for posicao in range(cursor, proximo):
            arvore.atualiza(pontos[posicao][1], 1)
        cursor = proximo
    pares = tamanho * (tamanho - 1) // 2
    livres_origem = pares - pares_iguais(origem)
    livres_comparado = pares - pares_iguais(comparado)
    if livres_origem == 0 or livres_comparado == 0:
        return None
    return (concordantes - discordantes) / ((livres_origem * livres_comparado) ** 0.5)


def postos_medios(chaves: list[tuple[float, ...]]) -> list[float]:
    """Average rank. Rank 1 is the smallest key. Ties share the average.

    Args:
        chaves: Ranking keys.

    Returns:
        One average rank per input, in the original order.
    """
    ordem = sorted(range(len(chaves)), key=lambda indice: chaves[indice])
    postos = [0.0] * len(chaves)
    inicio = 0
    tamanho = len(chaves)
    while inicio < tamanho:
        fim = inicio
        while fim + 1 < tamanho and chaves[ordem[fim + 1]] == chaves[ordem[inicio]]:
            fim += 1
        medio = (inicio + 1 + fim + 1) / 2.0
        for posicao in range(inicio, fim + 1):
            postos[ordem[posicao]] = medio
        inicio = fim + 1
    return postos


def pearson(esquerda: list[float], direita: list[float]) -> float | None:
    """Pearson correlation. None when either side has no variance.

    Args:
        esquerda: First series.
        direita: Second series, same length.

    Returns:
        Correlation in [-1, 1], or None.
    """
    tamanho = len(esquerda)
    if tamanho < 2 or len(direita) != tamanho:
        return None
    media_esquerda = sum(esquerda) / float(tamanho)
    media_direita = sum(direita) / float(tamanho)
    numerador = 0.0
    soma_esquerda = 0.0
    soma_direita = 0.0
    for indice in range(tamanho):
        delta_esquerda = esquerda[indice] - media_esquerda
        delta_direita = direita[indice] - media_direita
        numerador += delta_esquerda * delta_direita
        soma_esquerda += delta_esquerda * delta_esquerda
        soma_direita += delta_direita * delta_direita
    if soma_esquerda == 0.0 or soma_direita == 0.0:
        return None
    return numerador / ((soma_esquerda * soma_direita) ** 0.5)


def spearman(
    origem: list[tuple[float, ...]],
    comparado: list[tuple[float, ...]],
) -> float | None:
    """Spearman correlation on average ranks.

    Args:
        origem: Baseline keys.
        comparado: Scenario keys, aligned with origem.

    Returns:
        Spearman correlation, or None when every item is tied.
    """
    return pearson(postos_medios(origem), postos_medios(comparado))


def jaccard(esquerda: set[str], direita: set[str]) -> float:
    """Jaccard index. Two empty sets count as complete overlap.

    Args:
        esquerda: First set of municipality keys.
        direita: Second set.

    Returns:
        Intersection over union.
    """
    if not esquerda and not direita:
        return 1.0
    uniao = esquerda | direita
    if not uniao:
        return 1.0
    return len(esquerda & direita) / float(len(uniao))


def percentil(valores: list[float], probabilidade: float) -> float:
    """Linear percentile of a sample.

    Args:
        valores: Numbers.
        probabilidade: Point in [0, 1].

    Returns:
        Interpolated percentile. Zero when the sample is empty.
    """
    if not valores:
        return 0.0
    ordem = sorted(valores)
    if len(ordem) == 1:
        return ordem[0]
    posicao = (len(ordem) - 1) * probabilidade
    baixo = int(posicao)
    alto = min(baixo + 1, len(ordem) - 1)
    peso = posicao - baixo
    return ordem[baixo] * (1.0 - peso) + ordem[alto] * peso


def chave_fila(papel: str, indice: float, dif_votos: int) -> tuple[float, ...]:
    """Operational sort key. Smaller is earlier in the campaign queue.

    Args:
        papel: Municipal role.
        indice: Legacy score.
        dif_votos: Lula votes minus Flavio votes.

    Returns:
        Category order, then the legacy weight. The name is not part of the key,
        so equal scores stay tied.
    """
    if papel == "Consolidar":
        peso = -float(dif_votos)
    else:
        peso = -indice
    return (float(ORDEM_PAPEL[papel]), peso)


def chave_regiao(papel: str, deficit: int) -> tuple[float, ...]:
    """Analysis order for regions. This is not a published campaign queue.

    Args:
        papel: Regional role.
        deficit: Votes Lula is behind in the region.

    Returns:
        Role order, then the deficit, larger deficits first inside the role.
    """
    return (float(ORDEM_REGIAO[papel]), -float(deficit))


def topo(
    chaves: list[tuple[float, ...]],
    nomes: list[str],
    fracao: float,
    tamanho_fixo: bool,
) -> set[str]:
    """Municipalities at the top of a ranking.

    Args:
        chaves: Smaller means a better rank.
        nomes: Ids aligned with chaves.
        fracao: Share of the list, such as 0.10.
        tamanho_fixo: When True, break residual ties by name and take exactly
            that many rows. When False, keep every row tied with the cutoff.

    Returns:
        Set of ids.
    """
    tamanho = len(chaves)
    quantidade = max(1, int(round(tamanho * fracao)))
    ordem = sorted(range(tamanho), key=lambda indice: (chaves[indice], nomes[indice]))
    if tamanho_fixo:
        return {nomes[indice] for indice in ordem[:quantidade]}
    corte = chaves[ordem[quantidade - 1]]
    return {nomes[indice] for indice in range(tamanho) if chaves[indice] <= corte}


def motivo_fator(fatores: ParametrosFator) -> str:
    """Reject closeness steps that leave (0, 1] or stop decreasing.

    Args:
        fatores: Candidate steps.

    Returns:
        Empty when the steps stay ordered. Otherwise a short reason.
    """
    valores = (fatores.ate_8, fatores.ate_15, fatores.ate_25, fatores.acima_25)
    for valor in valores:
        if valor <= 0.0 or valor > 1.0:
            return "fator fora de (0, 1]"
    if not (fatores.ate_8 >= fatores.ate_15 >= fatores.ate_25 >= fatores.acima_25):
        return "fatores deixaram de ser decrescentes"
    if fatores.limite_8 <= 0.0:
        return "limite de margem nao positivo"
    if not (fatores.limite_8 < fatores.limite_15 < fatores.limite_25):
        return "limites de margem deixaram de ser crescentes"
    return ""


def motivo_municipio(params: ParametrosMunicipio) -> str:
    """Reject municipal thresholds that invert a band or are not positive.

    Args:
        params: Candidate thresholds.

    Returns:
        Empty when the bands stay ordered.
    """
    if params.precedencia not in PRECEDENCIAS_MUNICIPIO:
        return "precedencia desconhecida"
    margens = (
        params.pp_curto,
        params.pp_medio,
        params.pp_disputavel,
        params.pp_estrutural,
        params.pp_pequeno_largo,
    )
    if any(valor <= 0.0 for valor in margens):
        return "limiar de margem nao positivo"
    if not (params.pp_curto <= params.pp_medio <= params.pp_disputavel < params.pp_estrutural):
        return "faixas de margem municipal fora de ordem"
    if params.pp_pequeno_largo >= params.pp_estrutural:
        return "margem da cidade pequena cruza a margem estrutural"
    aptos = (
        params.aptos_pequeno,
        params.aptos_curto,
        params.aptos_disputavel,
        params.aptos_muito_grande,
    )
    if any(valor <= 0 for valor in aptos):
        return "limiar de aptos nao positivo"
    if not (
        params.aptos_pequeno
        < params.aptos_curto
        <= params.aptos_disputavel
        <= params.aptos_muito_grande
    ):
        return "faixas de aptos fora de ordem"
    if params.deficit_volume <= 0:
        return "deficit nao positivo"
    return ""


def motivo_regiao(params: ParametrosRegiao) -> str:
    """Reject regional thresholds that invert a band or are not positive.

    Args:
        params: Candidate thresholds.

    Returns:
        Empty when the bands stay ordered.
    """
    margens = (
        params.pp_estrutural,
        params.pp_curta,
        params.pp_disputavel,
        params.pp_apertada,
        params.margem_volume,
    )
    if any(valor <= 0.0 for valor in margens):
        return "limiar regional de margem nao positivo"
    if not (params.pp_curta <= params.pp_disputavel < params.pp_estrutural):
        return "faixas regionais de margem fora de ordem"
    if params.votos_pp <= 0 or params.deficit_disputavel <= 0 or params.deficit_volume <= 0:
        return "limiar regional de votos nao positivo"
    if params.deficit_disputavel > params.deficit_volume:
        return "deficits regionais fora de ordem"
    return ""


def motivo_peso(peso: float) -> str:
    """Reject an absentee weight outside (0, 1].

    Args:
        peso: Candidate weight.

    Returns:
        Empty when the weight stays in range.
    """
    if peso <= 0.0 or peso > 1.0:
        return "peso da abstencao fora de (0, 1]"
    return ""


def perturbar(valor: float, fator: float, inteiro: bool) -> float | int:
    """Scale a threshold. Counts are rounded to the nearest integer.

    Args:
        valor: Published threshold.
        fator: Multiplier such as 1.10.
        inteiro: When True, round to an integer count.

    Returns:
        Perturbed threshold.
    """
    novo = float(valor) * fator
    if inteiro:
        return int(round(novo))
    return novo


def cenario_base() -> Cenario:
    """The published parameter set, used as the reference row."""
    return Cenario(
        familia="referencia",
        parametro="nenhum",
        fator_relativo=1.0,
        valor="publicado",
        aceito=True,
        motivo="",
        municipio=ParametrosMunicipio(),
        fatores=ParametrosFator(),
        peso=PESO_AUSENTE,
        regiao=ParametrosRegiao(),
    )


def montar_cenarios() -> list[Cenario]:
    """Build the one-at-a-time grid and the two named precedence scenarios.

    Returns:
        Accepted and rejected scenarios. Rejected rows keep the reason.
    """
    base_municipio = ParametrosMunicipio()
    base_fator = ParametrosFator()
    base_regiao = ParametrosRegiao()
    cenarios = [cenario_base()]
    for nome, inteiro in CAMPOS_MUNICIPIO:
        origem = getattr(base_municipio, nome)
        for delta in DELTAS:
            novo = perturbar(float(origem), delta, inteiro)
            params = replace(base_municipio, **{nome: novo})
            motivo = motivo_municipio(params)
            cenarios.append(
                Cenario(
                    familia="limiar_municipal",
                    parametro=nome,
                    fator_relativo=delta,
                    valor=str(novo),
                    aceito=motivo == "",
                    motivo=motivo,
                    municipio=params,
                    fatores=base_fator,
                    peso=PESO_AUSENTE,
                    regiao=base_regiao,
                )
            )
    for nome, inteiro in CAMPOS_FATOR:
        origem = getattr(base_fator, nome)
        for delta in DELTAS:
            novo = perturbar(float(origem), delta, inteiro)
            fatores = replace(base_fator, **{nome: novo})
            motivo = motivo_fator(fatores)
            cenarios.append(
                Cenario(
                    familia="fator_legado",
                    parametro=nome,
                    fator_relativo=delta,
                    valor=str(novo),
                    aceito=motivo == "",
                    motivo=motivo,
                    municipio=base_municipio,
                    fatores=fatores,
                    peso=PESO_AUSENTE,
                    regiao=base_regiao,
                )
            )
    for delta in DELTAS:
        peso = float(perturbar(PESO_AUSENTE, delta, False))
        motivo = motivo_peso(peso)
        cenarios.append(
            Cenario(
                familia="peso_abstencao",
                parametro="peso_ausente",
                fator_relativo=delta,
                valor=str(peso),
                aceito=motivo == "",
                motivo=motivo,
                municipio=base_municipio,
                fatores=base_fator,
                peso=peso,
                regiao=base_regiao,
            )
        )
    for nome, inteiro in CAMPOS_REGIAO:
        origem = getattr(base_regiao, nome)
        for delta in DELTAS:
            novo = perturbar(float(origem), delta, inteiro)
            params = replace(base_regiao, **{nome: novo})
            motivo = motivo_regiao(params)
            cenarios.append(
                Cenario(
                    familia="limiar_regional",
                    parametro=nome,
                    fator_relativo=delta,
                    valor=str(novo),
                    aceito=motivo == "",
                    motivo=motivo,
                    municipio=base_municipio,
                    fatores=base_fator,
                    peso=PESO_AUSENTE,
                    regiao=params,
                )
            )
    for precedencia in ("presenca_antes", "deficit_antes"):
        params = replace(base_municipio, precedencia=precedencia)
        cenarios.append(
            Cenario(
                familia="precedencia",
                parametro=precedencia,
                fator_relativo=1.0,
                valor=precedencia,
                aceito=True,
                motivo="",
                municipio=params,
                fatores=base_fator,
                peso=PESO_AUSENTE,
                regiao=base_regiao,
            )
        )
    return cenarios


def avaliar_municipios(
    linhas: list[Linha],
    cenario: Cenario,
) -> tuple[list[str], list[float]]:
    """Recompute role and legacy index. Votes are not touched.

    Args:
        linhas: Baseline municipalities.
        cenario: Thresholds to apply.

    Returns:
        Roles and legacy scores aligned with linhas.
    """
    papeis: list[str] = []
    indices: list[float] = []
    for linha in linhas:
        papel = classificar_municipio(
            linha.dif_pp,
            linha.dif_votos,
            linha.aptos,
            cenario.municipio,
        )
        indice = indice_atencao(
            linha.dif_pp,
            linha.dif_votos,
            linha.abstencoes,
            papel,
            cenario.peso,
            cenario.fatores,
        )
        papeis.append(papel)
        indices.append(indice)
    return papeis, indices


def avaliar_regioes(regioes: list[RegiaoBase], cenario: Cenario) -> list[str]:
    """Recompute regional roles from the same summed votes.

    Args:
        regioes: Baseline regions.
        cenario: Regional thresholds to apply.

    Returns:
        Roles aligned with regioes.
    """
    return [
        classificar_regiao(regiao.agregado, cenario.regiao)
        for regiao in regioes
    ]


def metricas_ranking(
    chaves_base: list[tuple[float, ...]],
    chaves_novas: list[tuple[float, ...]],
    nomes: list[str],
) -> dict[str, float]:
    """Spearman, Kendall and top-slice overlap for one pair of rankings.

    Args:
        chaves_base: Baseline keys.
        chaves_novas: Scenario keys.
        nomes: Ids aligned with both lists.

    Returns:
        Flat metric dictionary. Missing correlations stay out of the mapping
        only when the caller checks for them; this always writes a number or
        uses a sentinel through the CSV layer.
    """
    saida: dict[str, float] = {}
    correlacao = spearman(chaves_base, chaves_novas)
    tau = kendall_tau_b(chaves_base, chaves_novas)
    saida["spearman"] = correlacao if correlacao is not None else float("nan")
    saida["kendall"] = tau if tau is not None else float("nan")
    postos_base = postos_medios(chaves_base)
    postos_novos = postos_medios(chaves_novas)
    deltas = [abs(postos_novos[i] - postos_base[i]) for i in range(len(nomes))]
    saida["delta_mediana"] = percentil(deltas, 0.50)
    saida["delta_p90"] = percentil(deltas, 0.90)
    for fracao in FRACOES_TOPO:
        marca = str(int(round(fracao * 100)))
        base_empate = topo(chaves_base, nomes, fracao, False)
        novo_empate = topo(chaves_novas, nomes, fracao, False)
        base_fixo = topo(chaves_base, nomes, fracao, True)
        novo_fixo = topo(chaves_novas, nomes, fracao, True)
        saida["jaccard_empate_{marca}".format(marca=marca)] = jaccard(base_empate, novo_empate)
        saida["jaccard_fixo_{marca}".format(marca=marca)] = jaccard(base_fixo, novo_fixo)
        saida["sobreposicao_{marca}".format(marca=marca)] = (
            len(base_empate & novo_empate) / float(len(base_empate))
            if base_empate
            else 1.0
        )
        saida["n_topo_base_{marca}".format(marca=marca)] = float(len(base_empate))
        saida["n_topo_cenario_{marca}".format(marca=marca)] = float(len(novo_empate))
    return saida


def spearman_no_papel(
    linhas: list[Linha],
    indices_base: list[float],
    indices_novos: list[float],
    papel: str,
) -> float | None:
    """Correlate the legacy score inside one baseline role.

    Args:
        linhas: Baseline rows.
        indices_base: Published legacy scores.
        indices_novos: Scores under the scenario.
        papel: Baseline role that defines the group.

    Returns:
        Spearman of the score inside that group, or None when it cannot move.
    """
    origem: list[tuple[float, ...]] = []
    comparado: list[tuple[float, ...]] = []
    for indice, linha in enumerate(linhas):
        if linha.papel != papel:
            continue
        origem.append((-indices_base[indice],))
        comparado.append((-indices_novos[indice],))
    if len(origem) < 2:
        return None
    return spearman(origem, comparado)


def numero(valor: float) -> str:
    """Format a metric. NaN becomes an empty cell.

    Args:
        valor: Metric value.

    Returns:
        Short decimal text, or empty when the metric is undefined.
    """
    if valor != valor:
        return ""
    return "{valor:.6f}".format(valor=valor)


def escrever_csv(caminho: Path, cabecalho: list[str], linhas: list[list[object]]) -> None:
    """Write one UTF-8 table.

    Args:
        caminho: Destination.
        cabecalho: Column names.
        linhas: Data rows.
    """
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.writer(arquivo, lineterminator="\n")
        escritor.writerow(cabecalho)
        for linha in linhas:
            escritor.writerow(linha)


def escapar(texto: str) -> str:
    """Escape text placed inside an SVG file.

    Args:
        texto: Raw label.

    Returns:
        XML-safe text.
    """
    return (
        texto.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def svg_barras(caminho: Path, titulo: str, rotulos: list[str], valores: list[float]) -> None:
    """Horizontal bar chart in a self-contained SVG.

    Args:
        caminho: Destination.
        titulo: ASCII title.
        rotulos: One label per bar.
        valores: Bar lengths. Negative values are drawn as zero.
    """
    if not rotulos:
        return
    largura = 920
    altura_linha = 22
    margem_esquerda = 280
    margem_topo = 36
    altura = margem_topo + altura_linha * len(rotulos) + 24
    maior = max(valores) if valores else 1.0
    if maior <= 0:
        maior = 1.0
    largura_util = largura - margem_esquerda - 80
    partes = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'.format(
            w=largura,
            h=altura,
        ),
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="16" y="22" font-family="sans-serif" font-size="14">{titulo}</text>'.format(
            titulo=escapar(titulo),
        ),
    ]
    for indice, rotulo in enumerate(rotulos):
        y = margem_topo + indice * altura_linha
        comprimento = largura_util * max(valores[indice], 0.0) / maior
        partes.append(
            '<text x="12" y="{y}" font-family="sans-serif" font-size="12">{rotulo}</text>'.format(
                y=y + 14,
                rotulo=escapar(rotulo),
            )
        )
        partes.append(
            '<rect x="{x}" y="{y}" width="{w}" height="14" fill="#2f6f9f"/>'.format(
                x=margem_esquerda,
                y=y,
                w=max(comprimento, 0.0),
            )
        )
        partes.append(
            '<text x="{x}" y="{y}" font-family="sans-serif" font-size="11">{valor}</text>'.format(
                x=margem_esquerda + comprimento + 6,
                y=y + 12,
                valor="{valor:.3f}".format(valor=valores[indice]),
            )
        )
    partes.append("</svg>")
    caminho.write_text("\n".join(partes), encoding="utf-8")


def svg_heatmap(
    caminho: Path,
    titulo: str,
    matriz: dict[tuple[str, str], int],
    ordem: list[str] | None = None,
) -> None:
    """Category transition counts.

    Args:
        caminho: Destination.
        titulo: ASCII title.
        matriz: Counts keyed by (from, to).
        ordem: Category order. None uses the municipal roles.
    """
    ordem = list(PAPEIS) if ordem is None else ordem
    celula = 78
    margem = 150
    lado = margem + celula * len(ordem) + 20
    maior = max(matriz.values()) if matriz else 1
    if maior <= 0:
        maior = 1
    partes = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'.format(
            w=lado + 40,
            h=lado,
        ),
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="16" y="22" font-family="sans-serif" font-size="14">{titulo}</text>'.format(
            titulo=escapar(titulo),
        ),
    ]
    for coluna, papel in enumerate(ordem):
        partes.append(
            '<text x="{x}" y="{y}" font-family="sans-serif" font-size="10" transform="rotate(-40 {x} {y})">{papel}</text>'.format(
                x=margem + coluna * celula + 8,
                y=margem - 8,
                papel=escapar(papel),
            )
        )
    for linha, origem in enumerate(ordem):
        partes.append(
            '<text x="8" y="{y}" font-family="sans-serif" font-size="11">{papel}</text>'.format(
                y=margem + linha * celula + 28,
                papel=escapar(origem),
            )
        )
        for coluna, destino in enumerate(ordem):
            contagem = matriz.get((origem, destino), 0)
            tinta = int(round(255 - 180 * (contagem / float(maior))))
            cor = "#{r:02x}{g:02x}{b:02x}".format(r=tinta, g=tinta, b=255)
            partes.append(
                '<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{cor}" stroke="#ffffff"/>'.format(
                    x=margem + coluna * celula,
                    y=margem + linha * celula,
                    w=celula - 4,
                    h=celula - 4,
                    cor=cor,
                )
            )
            partes.append(
                '<text x="{x}" y="{y}" font-family="sans-serif" font-size="11">{n}</text>'.format(
                    x=margem + coluna * celula + 8,
                    y=margem + linha * celula + 28,
                    n=contagem,
                )
            )
    partes.append("</svg>")
    caminho.write_text("\n".join(partes), encoding="utf-8")


def hash_arquivo(caminho: Path) -> str:
    """SHA-256 of a file, read in full and not rewritten.

    Args:
        caminho: Existing file.

    Returns:
        Hex digest.
    """
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def commit_atual(raiz: Path) -> str:
    """Current git commit, or a marker when git is unavailable.

    Args:
        raiz: Repository root.

    Returns:
        Commit hash.
    """
    try:
        resultado = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=raiz,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "desconhecido"
    return resultado.stdout.strip()


def listar_csvs(raiz: Path) -> list[Path]:
    """Official municipal files in the repository root only.

    Args:
        raiz: Repository root.

    Returns:
        Sorted CSV paths. Nested extracts are ignored.
    """
    return sorted(raiz.glob("resumo_municipios_*.csv"))


def carregar_linhas(raiz: Path) -> list[Linha]:
    """Read every official municipality and attach the published role.

    Args:
        raiz: Repository root.

    Returns:
        One row per municipality. Region names follow the report rules.
    """
    linhas: list[Linha] = []
    for caminho in listar_csvs(raiz):
        municipios, _avisos = ler_municipios(caminho, {})
        por_uf: dict[str, list[Municipio]] = {}
        for item in municipios:
            por_uf.setdefault(item.uf, []).append(item)
        for grupo in por_uf.values():
            preenchidos, _fonte = completar_regioes(grupo)
            for item in preenchidos:
                linhas.append(
                    Linha(
                        uf=item.uf,
                        nome=item.nome,
                        regiao=item.regiao,
                        locais=item.locais,
                        aptos=item.aptos,
                        comparecimento=item.comparecimento,
                        abstencoes=item.abstencoes,
                        validos=item.validos,
                        lula=item.lula,
                        flavio=item.flavio,
                        dif_pp=item.dif_pp,
                        dif_votos=item.dif_votos,
                        locais_lula=item.locais_lula,
                        locais_flavio=item.locais_flavio,
                        locais_empate=item.locais_empate,
                        papel=item.papel,
                        indice=item.indice,
                    )
                )
    return linhas


def agregar_regioes(linhas: list[Linha]) -> list[RegiaoBase]:
    """Sum municipalities that share a state and a region name.

    Args:
        linhas: Loaded municipalities.

    Returns:
        One classified region per group.
    """
    grupos: dict[tuple[str, str], list[Linha]] = {}
    for linha in linhas:
        grupos.setdefault((linha.uf, linha.regiao), []).append(linha)
    regioes: list[RegiaoBase] = []
    for (uf, nome), grupo in sorted(grupos.items()):
        agregado = Agregado(
            nome=nome,
            municipios=len(grupo),
            locais=sum(item.locais for item in grupo),
            aptos=sum(item.aptos for item in grupo),
            comparecimento=sum(item.comparecimento for item in grupo),
            abstencoes=sum(item.abstencoes for item in grupo),
            validos=sum(item.validos for item in grupo),
            lula=sum(item.lula for item in grupo),
            flavio=sum(item.flavio for item in grupo),
            locais_lula=sum(item.locais_lula for item in grupo),
            locais_flavio=sum(item.locais_flavio for item in grupo),
            locais_empate=sum(item.locais_empate for item in grupo),
        )
        regioes.append(
            RegiaoBase(
                uf=uf,
                nome=nome,
                agregado=agregado,
                papel=classificar_regiao(agregado),
            )
        )
    return regioes


def casos_fronteira() -> list[list[object]]:
    """Synthetic points just below, on, and just above each published cut.

    Returns:
        Rows for the discontinuity table. A category change here is the rule
        itself, not an arithmetic error.
    """
    linhas: list[list[object]] = []
    for ponto in (8.0, 15.0, 25.0):
        abaixo = fator_proximidade(ponto - 0.01)
        no_limite = fator_proximidade(ponto)
        acima = fator_proximidade(ponto + 0.01)
        linhas.append(
            [
                "fator_proximidade",
                ponto,
                abaixo,
                no_limite,
                acima,
                "descontinuidade esperada do indice legado",
            ]
        )
    sondas = (
        ("margem_8_pp", -7.99, -100, 50_000, "Intensificar"),
        ("margem_8_pp", -8.00, -100, 50_000, "Intensificar"),
        ("margem_8_pp", -8.01, -100, 50_000, "Apoio local"),
        ("aptos_50000", -8.00, -100, 49_999, "Apoio local"),
        ("aptos_50000", -8.00, -100, 50_000, "Intensificar"),
        ("margem_28_pp", -27.99, -100, 80_000, "Apoio local"),
        ("margem_28_pp", -28.00, -100, 80_000, "Presenca contida"),
        ("margem_28_pp", -28.01, -100, 80_000, "Presenca contida"),
        ("deficit_15000", -20.00, -14_999, 80_000, "Apoio local"),
        ("deficit_15000", -20.00, -15_000, 80_000, "Reduzir o buraco"),
        ("cidade_pequena_15_pp", -15.00, -100, 39_999, "Apoio local"),
        ("cidade_pequena_15_pp", -15.01, -100, 39_999, "Presenca contida"),
        ("cidade_pequena_15_pp", -15.01, -100, 40_000, "Apoio local"),
    )
    for nome, dif_pp, dif_votos, aptos, _esperado in sondas:
        obtido = classificar_municipio(dif_pp, dif_votos, aptos)
        linhas.append(
            [
                nome,
                "{pp}|{votos}|{aptos}".format(pp=dif_pp, votos=dif_votos, aptos=aptos),
                "",
                obtido,
                "",
                "descontinuidade esperada da regra",
            ]
        )
    return linhas


def municipios_perto_de_limiar(linhas: list[Linha]) -> list[list[object]]:
    """Count municipalities sitting next to a published cut.

    Args:
        linhas: Baseline rows.

    Returns:
        One row per cut, with the count and the tolerance used.
    """
    cortes_pp = (8.0, 13.0, 15.0, 16.0, 25.0, 28.0)
    saida: list[list[object]] = []
    atras = [linha for linha in linhas if linha.dif_votos < 0]
    for corte in cortes_pp:
        quantidade = sum(1 for linha in atras if abs(abs(linha.dif_pp) - corte) <= 0.5)
        saida.append(["margem_pp", corte, 0.5, quantidade, len(atras)])
    for corte in (40_000, 50_000, 60_000, 100_000):
        quantidade = sum(1 for linha in linhas if abs(linha.aptos - corte) <= corte * 0.05)
        saida.append(["aptos", corte, 0.05, quantidade, len(linhas)])
    for corte in (15_000,):
        deficits = [abs(linha.dif_votos) for linha in atras]
        quantidade = sum(1 for valor in deficits if abs(valor - corte) <= corte * 0.05)
        saida.append(["deficit_votos", corte, 0.05, quantidade, len(atras)])
    return saida


def executar(raiz: Path, destino: Path) -> dict[str, float]:
    """Run the sweep and write tables and charts.

    Args:
        raiz: Repository root containing the official CSVs.
        destino: Directory that receives the new files only.

    Returns:
        Compact numeric summary used by the command-line printout.
    """
    destino.mkdir(parents=True, exist_ok=True)
    arquivos = listar_csvs(raiz)
    hashes_antes = {caminho.name: hash_arquivo(caminho) for caminho in arquivos}
    linhas = carregar_linhas(raiz)
    hashes_depois = {caminho.name: hash_arquivo(caminho) for caminho in arquivos}
    if hashes_antes != hashes_depois:
        raise RuntimeError("Official CSVs changed while the sweep was reading them.")
    regioes = agregar_regioes(linhas)
    cenarios = montar_cenarios()
    nomes = [linha.chave for linha in linhas]
    indices_base = [linha.indice for linha in linhas]
    chaves_base = [
        chave_fila(linha.papel, linha.indice, linha.dif_votos) for linha in linhas
    ]
    chaves_regiao_base = [
        chave_regiao(regiao.papel, regiao.agregado.deficit) for regiao in regioes
    ]
    nomes_regiao = [regiao.chave for regiao in regioes]
    atras = [indice for indice, linha in enumerate(linhas) if linha.dif_votos < 0]
    chaves_indice_base = [(-indices_base[indice],) for indice in atras]

    manteve_limiar = [0] * len(linhas)
    vistos_limiar = 0
    manteve_todos = [0] * len(linhas)
    vistos_todos = 0
    alternativas: list[set[str]] = [set() for _linha in linhas]
    parametros_mudam: list[set[str]] = [set() for _linha in linhas]
    indice_min = list(indices_base)
    indice_max = list(indices_base)
    posicao_max = [0.0] * len(linhas)
    manteve_regiao = [0] * len(regioes)
    vistos_regiao = 0
    alternativas_regiao: list[set[str]] = [set() for _regiao in regioes]

    linhas_cenario: list[list[object]] = []
    linhas_transicao: list[list[object]] = []
    pior_matriz: dict[tuple[str, str], int] = {}
    pior_pct = -1.0
    pior_titulo = "transicoes"
    pior_matriz_regiao: dict[tuple[str, str], int] = {}
    pior_pct_regiao = -1.0
    pior_titulo_regiao = "transicoes regionais"
    sensibilidade: dict[str, float] = {}
    spearman_por_parametro: dict[str, float] = {}

    for cenario in cenarios:
        if not cenario.aceito:
            linhas_cenario.append(_linha_cenario_rejeitado(cenario))
            continue
        papeis, indices = avaliar_municipios(linhas, cenario)
        chaves = [
            chave_fila(papeis[i], indices[i], linhas[i].dif_votos) for i in range(len(linhas))
        ]
        metricas = metricas_ranking(chaves_base, chaves, nomes)
        chaves_indice = [(-indices[indice],) for indice in atras]
        correlacao_indice = spearman(chaves_indice_base, chaves_indice)
        tau_indice = kendall_tau_b(chaves_indice_base, chaves_indice)
        mudancas = sum(1 for i, papel in enumerate(papeis) if papel != linhas[i].papel)
        pct = 100.0 * mudancas / float(len(linhas)) if linhas else 0.0
        transicoes: Counter[tuple[str, str]] = Counter()
        for i, papel in enumerate(papeis):
            transicoes[(linhas[i].papel, papel)] += 1
        for (origem, destino_papel), quantidade in sorted(transicoes.items()):
            if origem == destino_papel:
                continue
            linhas_transicao.append(
                [
                    cenario.familia,
                    cenario.parametro,
                    "{delta:.2f}".format(delta=cenario.fator_relativo),
                    origem,
                    destino_papel,
                    quantidade,
                ]
            )
        papeis_regiao = avaliar_regioes(regioes, cenario)
        chaves_regiao = [
            chave_regiao(papeis_regiao[i], regioes[i].agregado.deficit)
            for i in range(len(regioes))
        ]
        metricas_regiao = metricas_ranking(chaves_regiao_base, chaves_regiao, nomes_regiao)
        mudancas_regiao = sum(
            1 for i, papel in enumerate(papeis_regiao) if papel != regioes[i].papel
        )
        pct_regiao = 100.0 * mudancas_regiao / float(len(regioes)) if regioes else 0.0
        if cenario.familia == "limiar_regional":
            contagem_regional: Counter[tuple[str, str]] = Counter()
            for i, papel in enumerate(papeis_regiao):
                if papel != regioes[i].papel:
                    contagem_regional[(regioes[i].papel, papel)] += 1
            for (origem, destino_papel), quantidade in sorted(contagem_regional.items()):
                linhas_transicao.append(
                    [
                        cenario.familia,
                        cenario.parametro,
                        "{delta:.2f}".format(delta=cenario.fator_relativo),
                        origem,
                        destino_papel,
                        quantidade,
                    ]
                )
        dentro = {
            papel: spearman_no_papel(linhas, indices_base, indices, papel) for papel in PAPEIS
        }
        linhas_cenario.append(
            _linha_cenario(
                cenario,
                len(linhas),
                metricas,
                correlacao_indice,
                tau_indice,
                dentro,
                pct,
                len(regioes),
                metricas_regiao,
                pct_regiao,
            )
        )
        if cenario.familia == "limiar_municipal":
            vistos_limiar += 1
            vistos_todos += 1
            _acumular_municipio(
                linhas,
                papeis,
                indices,
                chaves_base,
                chaves,
                cenario.parametro,
                manteve_limiar,
                manteve_todos,
                alternativas,
                parametros_mudam,
                indice_min,
                indice_max,
                posicao_max,
            )
            anterior = sensibilidade.get(cenario.parametro, 0.0)
            if pct >= anterior:
                sensibilidade[cenario.parametro] = pct
                spearman_valor = metricas["spearman"]
                if spearman_valor == spearman_valor:
                    spearman_por_parametro[cenario.parametro] = spearman_valor
            if pct > pior_pct:
                pior_pct = pct
                pior_matriz = dict(transicoes)
                pior_titulo = "transicoes {param} x {delta:.2f}".format(
                    param=cenario.parametro,
                    delta=cenario.fator_relativo,
                )
        elif cenario.familia == "limiar_regional" and pct_regiao > pior_pct_regiao:
            pior_pct_regiao = pct_regiao
            contagem_regiao: Counter[tuple[str, str]] = Counter()
            for i, papel in enumerate(papeis_regiao):
                contagem_regiao[(regioes[i].papel, papel)] += 1
            pior_matriz_regiao = dict(contagem_regiao)
            pior_titulo_regiao = "transicoes regionais {param} x {delta:.2f}".format(
                param=cenario.parametro,
                delta=cenario.fator_relativo,
            )
        elif cenario.familia in ("fator_legado", "peso_abstencao"):
            vistos_todos += 1
            _acumular_municipio(
                linhas,
                papeis,
                indices,
                chaves_base,
                chaves,
                cenario.parametro,
                None,
                manteve_todos,
                alternativas,
                parametros_mudam,
                indice_min,
                indice_max,
                posicao_max,
            )
        elif cenario.familia == "limiar_regional":
            vistos_regiao += 1
            for i, papel in enumerate(papeis_regiao):
                if papel == regioes[i].papel:
                    manteve_regiao[i] += 1
                else:
                    alternativas_regiao[i].add(papel)

    _escrever_saidas(
        destino,
        raiz,
        arquivos,
        hashes_antes,
        linhas,
        regioes,
        linhas_cenario,
        linhas_transicao,
        manteve_limiar,
        vistos_limiar,
        manteve_todos,
        vistos_todos,
        alternativas,
        parametros_mudam,
        indice_min,
        indice_max,
        posicao_max,
        manteve_regiao,
        vistos_regiao,
        alternativas_regiao,
        sensibilidade,
        spearman_por_parametro,
        pior_matriz,
        pior_titulo,
        pior_matriz_regiao,
        pior_titulo_regiao,
        chaves_base,
        nomes,
    )
    estaveis = 0
    if vistos_limiar:
        estaveis = sum(1 for valor in manteve_limiar if valor == vistos_limiar)
    return {
        "municipios": float(len(linhas)),
        "regioes": float(len(regioes)),
        "cenarios": float(len(cenarios)),
        "estaveis_limiar": float(estaveis),
        "pior_pct": pior_pct,
    }


def _acumular_municipio(
    linhas: list[Linha],
    papeis: list[str],
    indices: list[float],
    chaves_base: list[tuple[float, ...]],
    chaves: list[tuple[float, ...]],
    parametro: str,
    manteve_limiar: list[int] | None,
    manteve_todos: list[int],
    alternativas: list[set[str]],
    parametros_mudam: list[set[str]],
    indice_min: list[float],
    indice_max: list[float],
    posicao_max: list[float],
) -> None:
    """Update per-municipality trackers for one accepted scenario."""
    postos_base = postos_medios(chaves_base)
    postos = postos_medios(chaves)
    for i, papel in enumerate(papeis):
        if papel == linhas[i].papel:
            manteve_todos[i] += 1
            if manteve_limiar is not None:
                manteve_limiar[i] += 1
        else:
            alternativas[i].add(papel)
            parametros_mudam[i].add(parametro)
        if indices[i] < indice_min[i]:
            indice_min[i] = indices[i]
        if indices[i] > indice_max[i]:
            indice_max[i] = indices[i]
        delta = abs(postos[i] - postos_base[i])
        if delta > posicao_max[i]:
            posicao_max[i] = delta


def _linha_cenario_rejeitado(cenario: Cenario) -> list[object]:
    """One CSV row for a perturbation that broke a constraint."""
    return [
        cenario.familia,
        cenario.parametro,
        "{delta:.2f}".format(delta=cenario.fator_relativo),
        cenario.valor,
        "nao",
        cenario.motivo,
    ]


def _linha_cenario(
    cenario: Cenario,
    n_municipios: int,
    metricas: dict[str, float],
    correlacao_indice: float | None,
    tau_indice: float | None,
    dentro: dict[str, float | None],
    pct: float,
    n_regioes: int,
    metricas_regiao: dict[str, float],
    pct_regiao: float,
) -> list[object]:
    """One CSV row for an accepted scenario."""
    linha: list[object] = [
        cenario.familia,
        cenario.parametro,
        "{delta:.2f}".format(delta=cenario.fator_relativo),
        cenario.valor,
        "sim",
        "",
        n_municipios,
        numero(metricas["spearman"]),
        numero(metricas["kendall"]),
        numero(correlacao_indice if correlacao_indice is not None else float("nan")),
        numero(tau_indice if tau_indice is not None else float("nan")),
        "{pct:.4f}".format(pct=pct),
        numero(metricas["delta_mediana"]),
        numero(metricas["delta_p90"]),
    ]
    for papel, valor in dentro.items():
        linha.append(numero(valor if valor is not None else float("nan")))
    for marca in ("5", "10", "20"):
        linha.append(numero(metricas["jaccard_empate_{marca}".format(marca=marca)]))
        linha.append(numero(metricas["jaccard_fixo_{marca}".format(marca=marca)]))
        linha.append(numero(metricas["sobreposicao_{marca}".format(marca=marca)]))
        linha.append(int(metricas["n_topo_base_{marca}".format(marca=marca)]))
        linha.append(int(metricas["n_topo_cenario_{marca}".format(marca=marca)]))
    linha.extend(
        [
            n_regioes,
            numero(metricas_regiao["spearman"]),
            numero(metricas_regiao["kendall"]),
            "{pct:.4f}".format(pct=pct_regiao),
        ]
    )
    return linha


def _cabecalho_cenario() -> list[str]:
    """Column names shared by accepted and rejected scenario rows."""
    cabecalho = [
        "familia",
        "parametro",
        "fator_relativo",
        "valor",
        "aceito",
        "motivo",
        "n_municipios",
        "spearman_fila",
        "kendall_fila",
        "spearman_indice_atras",
        "kendall_indice_atras",
        "pct_muda_papel",
        "delta_posicao_mediana",
        "delta_posicao_p90",
    ]
    for papel in PAPEIS:
        cabecalho.append("spearman_dentro_{nome}".format(nome=papel.replace(" ", "_")))
    for marca in ("5", "10", "20"):
        cabecalho.extend(
            [
                "jaccard_empate_{marca}".format(marca=marca),
                "jaccard_fixo_{marca}".format(marca=marca),
                "sobreposicao_{marca}".format(marca=marca),
                "n_topo_base_{marca}".format(marca=marca),
                "n_topo_cenario_{marca}".format(marca=marca),
            ]
        )
    cabecalho.extend(
        [
            "n_regioes",
            "spearman_regiao",
            "kendall_regiao",
            "pct_muda_regiao",
        ]
    )
    return cabecalho


def _escrever_saidas(
    destino: Path,
    raiz: Path,
    arquivos: list[Path],
    hashes: dict[str, str],
    linhas: list[Linha],
    regioes: list[RegiaoBase],
    linhas_cenario: list[list[object]],
    linhas_transicao: list[list[object]],
    manteve_limiar: list[int],
    vistos_limiar: int,
    manteve_todos: list[int],
    vistos_todos: int,
    alternativas: list[set[str]],
    parametros_mudam: list[set[str]],
    indice_min: list[float],
    indice_max: list[float],
    posicao_max: list[float],
    manteve_regiao: list[int],
    vistos_regiao: int,
    alternativas_regiao: list[set[str]],
    sensibilidade: dict[str, float],
    spearman_por_parametro: dict[str, float],
    pior_matriz: dict[tuple[str, str], int],
    pior_titulo: str,
    pior_matriz_regiao: dict[tuple[str, str], int],
    pior_titulo_regiao: str,
    chaves_base: list[tuple[float, ...]],
    nomes: list[str],
) -> None:
    """Write every table and chart. Does not touch the official CSVs."""
    referencia = [
        ["commit", commit_atual(raiz)],
        ["script", "scripts/analisar_robustez.py"],
        ["n_csv", str(len(arquivos))],
        ["n_municipios", str(len(linhas))],
        ["n_regioes", str(len(regioes))],
        [
            "indicadores",
            "competitividade;votos_validos;taxa_abstencao;participacao_conjunta;diferenca_absoluta;diferenca_pp;indice_legado;papel",
        ],
        [
            "nota",
            "fracao de manutencao nao e probabilidade; competitividade nao ordena a fila",
        ],
    ]
    for nome, digest in hashes.items():
        referencia.append(["sha256_{nome}".format(nome=nome), digest])
    for relativo in ("gerar_relatorio_estrategia.py", "scripts/analisar_robustez.py"):
        referencia.append(
            [
                "sha256_{nome}".format(nome=relativo.replace("/", "_")),
                hash_arquivo(raiz / relativo),
            ]
        )
    escrever_csv(destino / "referencia.csv", ["chave", "valor"], referencia)
    baseline = []
    for linha in linhas:
        competitividade = linha.competitividade
        baseline.append(
            [
                linha.uf,
                linha.nome,
                linha.regiao,
                linha.papel,
                "{indice:.4f}".format(indice=linha.indice),
                "" if competitividade is None else "{valor:.6f}".format(valor=competitividade),
                linha.validos,
                linha.dif_votos,
                "{pp:.4f}".format(pp=linha.dif_pp),
                linha.aptos,
                linha.abstencoes,
            ]
        )
    escrever_csv(
        destino / "baseline_municipios.csv",
        [
            "uf",
            "nome",
            "regiao",
            "papel",
            "indice_legado",
            "competitividade",
            "votos_validos",
            "diferenca_votos",
            "diferenca_pp",
            "aptos",
            "abstencoes",
        ],
        baseline,
    )
    baseline_regiao = []
    for regiao in regioes:
        baseline_regiao.append(
            [
                regiao.uf,
                regiao.nome,
                regiao.papel,
                regiao.agregado.municipios,
                regiao.agregado.dif_votos,
                "{pp:.4f}".format(pp=regiao.agregado.dif_pp),
                regiao.agregado.validos,
                ""
                if regiao.agregado.competitividade is None
                else "{valor:.6f}".format(valor=regiao.agregado.competitividade),
            ]
        )
    escrever_csv(
        destino / "baseline_regioes.csv",
        [
            "uf",
            "regiao",
            "papel",
            "municipios",
            "diferenca_votos",
            "diferenca_pp",
            "votos_validos",
            "competitividade",
        ],
        baseline_regiao,
    )
    cabecalho = _cabecalho_cenario()
    normalizadas = []
    for linha in linhas_cenario:
        if len(linha) < len(cabecalho):
            linha = linha + [""] * (len(cabecalho) - len(linha))
        normalizadas.append(linha)
    escrever_csv(destino / "cenarios.csv", cabecalho, normalizadas)
    escrever_csv(
        destino / "transicoes.csv",
        ["familia", "parametro", "fator_relativo", "de", "para", "quantidade"],
        linhas_transicao,
    )
    estabilidade = []
    for i, linha in enumerate(linhas):
        fracao_limiar = (
            manteve_limiar[i] / float(vistos_limiar) if vistos_limiar else float("nan")
        )
        fracao_todos = manteve_todos[i] / float(vistos_todos) if vistos_todos else float("nan")
        competitividade = linha.competitividade
        estabilidade.append(
            [
                linha.uf,
                linha.nome,
                linha.papel,
                numero(fracao_limiar),
                numero(fracao_todos),
                "|".join(sorted(alternativas[i])),
                "|".join(sorted(parametros_mudam[i])),
                "{valor:.4f}".format(valor=indice_min[i]),
                "{valor:.4f}".format(valor=indice_max[i]),
                "{valor:.4f}".format(valor=indice_max[i] - indice_min[i]),
                "{valor:.4f}".format(valor=posicao_max[i]),
                "" if competitividade is None else "{valor:.6f}".format(valor=competitividade),
                "0",
            ]
        )
    escrever_csv(
        destino / "estabilidade_municipio.csv",
        [
            "uf",
            "nome",
            "papel_original",
            "fracao_manutencao_limiar",
            "fracao_manutencao_todos_parametros",
            "categorias_alternativas",
            "parametros",
            "indice_min",
            "indice_max",
            "amplitude_indice",
            "variacao_posicao_max",
            "competitividade",
            "amplitude_competitividade",
        ],
        estabilidade,
    )
    estabilidade_regiao = []
    for i, regiao in enumerate(regioes):
        fracao = manteve_regiao[i] / float(vistos_regiao) if vistos_regiao else float("nan")
        estabilidade_regiao.append(
            [
                regiao.uf,
                regiao.nome,
                regiao.papel,
                numero(fracao),
                "|".join(sorted(alternativas_regiao[i])),
            ]
        )
    escrever_csv(
        destino / "estabilidade_regiao.csv",
        ["uf", "regiao", "papel_original", "fracao_manutencao", "categorias_alternativas"],
        estabilidade_regiao,
    )
    escrever_csv(
        destino / "descontinuidades.csv",
        ["regra", "ponto", "abaixo", "no_limite", "acima", "leitura"],
        casos_fronteira(),
    )
    escrever_csv(
        destino / "proximidade_limiares.csv",
        ["tipo", "corte", "tolerancia", "municipios", "denominador"],
        municipios_perto_de_limiar(linhas),
    )
    _escrever_correlacao_baseline(destino, linhas, chaves_base)
    _escrever_graficos(
        destino,
        sensibilidade,
        spearman_por_parametro,
        pior_matriz,
        pior_titulo,
        pior_matriz_regiao,
        pior_titulo_regiao,
        posicao_max,
        manteve_limiar,
        vistos_limiar,
    )


def _escrever_correlacao_baseline(
    destino: Path,
    linhas: list[Linha],
    chaves_base: list[tuple[float, ...]],
) -> None:
    """Compare descriptive rankings with the operational queue once.

    Disagreement here is a difference of question, not instability.
    """
    pares: list[list[object]] = []
    definidos = [i for i, linha in enumerate(linhas) if linha.competitividade is not None]
    excluidos = len(linhas) - len(definidos)
    if len(definidos) >= 2:
        fila = [chaves_base[i] for i in definidos]
        competitividade: list[tuple[float, ...]] = []
        for indice in definidos:
            valor = linhas[indice].competitividade
            competitividade.append((-(valor if valor is not None else 0.0),))
        spearman_c = spearman(fila, competitividade)
        kendall_c = kendall_tau_b(fila, competitividade)
        pares.append(
            [
                "fila_operacional",
                "competitividade",
                numero(spearman_c if spearman_c is not None else float("nan")),
                numero(kendall_c if kendall_c is not None else float("nan")),
                len(definidos),
                excluidos,
                "rankings diferentes; competitividade nao e prioridade",
            ]
        )
    atras = [i for i, linha in enumerate(linhas) if linha.dif_votos < 0]
    if len(atras) >= 2:
        fila_atras = [chaves_base[i] for i in atras]
        indice_atras = [(-linhas[i].indice,) for i in atras]
        spearman_i = spearman(fila_atras, indice_atras)
        kendall_i = kendall_tau_b(fila_atras, indice_atras)
        pares.append(
            [
                "fila_operacional_entre_quem_esta_atras",
                "indice_legado",
                numero(spearman_i if spearman_i is not None else float("nan")),
                numero(kendall_i if kendall_i is not None else float("nan")),
                len(atras),
                len(linhas) - len(atras),
                "a fila ainda ordena primeiro pelo papel",
            ]
        )
    todos = list(range(len(linhas)))
    deficit = [(-float(max(-linhas[i].dif_votos, 0)),) for i in todos]
    spearman_d = spearman(chaves_base, deficit)
    kendall_d = kendall_tau_b(chaves_base, deficit)
    pares.append(
        [
            "fila_operacional",
            "deficit_absoluto",
            numero(spearman_d if spearman_d is not None else float("nan")),
            numero(kendall_d if kendall_d is not None else float("nan")),
            len(linhas),
            0,
            "deficit nao aplica o desconto de margem nem o papel",
        ]
    )
    escrever_csv(
        destino / "correlacao_baseline.csv",
        ["ranking_a", "ranking_b", "spearman", "kendall", "n", "excluidos", "nota"],
        pares,
    )


def _escrever_graficos(
    destino: Path,
    sensibilidade: dict[str, float],
    spearman_por_parametro: dict[str, float],
    pior_matriz: dict[tuple[str, str], int],
    pior_titulo: str,
    pior_matriz_regiao: dict[tuple[str, str], int],
    pior_titulo_regiao: str,
    posicao_max: list[float],
    manteve_limiar: list[int],
    vistos_limiar: int,
) -> None:
    """Charts for the summary tables."""
    ordem = sorted(sensibilidade, key=lambda nome: sensibilidade[nome], reverse=True)
    svg_barras(
        destino / "sensibilidade_parametros.svg",
        "Maior percentual que muda de papel, um limiar por vez",
        ordem,
        [sensibilidade[nome] for nome in ordem],
    )
    ordem_corr = sorted(
        spearman_por_parametro,
        key=lambda nome: spearman_por_parametro[nome],
    )
    svg_barras(
        destino / "correlacao_fila.svg",
        "Spearman da fila no cenario em que o limiar mais troca de papel",
        ordem_corr,
        [spearman_por_parametro[nome] for nome in ordem_corr],
    )
    svg_heatmap(destino / "transicoes.svg", pior_titulo, pior_matriz)
    svg_heatmap(
        destino / "transicoes_regiao.svg",
        pior_titulo_regiao,
        pior_matriz_regiao,
        list(ORDEM_REGIAO.keys()),
    )
    bins = ((0, 0), (1, 5), (6, 20), (21, 50), (51, 200), (201, 10**9))
    rotulos = ["0", "1-5", "6-20", "21-50", "51-200", "201+"]
    contagens = []
    for inicio, fim in bins:
        if inicio == 0 and fim == 0:
            contagens.append(float(sum(1 for valor in posicao_max if valor == 0)))
        elif fim > 10**8:
            contagens.append(float(sum(1 for valor in posicao_max if valor >= inicio)))
        else:
            contagens.append(
                float(sum(1 for valor in posicao_max if inicio <= valor <= fim))
            )
    svg_barras(
        destino / "variacao_posicao.svg",
        "Municipios pela maior mudanca de posicao na fila",
        rotulos,
        contagens,
    )
    if vistos_limiar:
        fracoes = [valor / float(vistos_limiar) for valor in manteve_limiar]
    else:
        fracoes = []
    grupos = (
        ("1.00", lambda valor: valor == 1.0),
        ("0.95-0.99", lambda valor: 0.95 <= valor < 1.0),
        ("0.80-0.95", lambda valor: 0.80 <= valor < 0.95),
        ("abaixo de 0.80", lambda valor: valor < 0.80),
    )
    svg_barras(
        destino / "estabilidade_municipio.svg",
        "Municipios pela fracao de cenarios de limiar que mantem o papel",
        [rotulo for rotulo, _regra in grupos],
        [float(sum(1 for valor in fracoes if regra(valor))) for _rotulo, regra in grupos],
    )


def main() -> int:
    """Write resultados_robustez next to the repository root.

    Returns:
        Zero after the tables are written.
    """
    resumo = executar(RAIZ, RAIZ / "resultados_robustez")
    print(
        "municipalities={m:.0f} regions={r:.0f} scenarios={c:.0f} stable_threshold={s:.0f} worst_pct={p:.2f}".format(
            m=resumo["municipios"],
            r=resumo["regioes"],
            c=resumo["cenarios"],
            s=resumo["estaveis_limiar"],
            p=resumo["pior_pct"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
