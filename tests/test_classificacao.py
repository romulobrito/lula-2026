"""Checks that legacy labels stay compatible with the results they describe."""

import unittest

from gerar_relatorio_estrategia import (
    Agregado,
    Municipio,
    Relatorio,
    classificar_municipio,
    classificar_regiao,
    fila,
    indice_atencao,
)


class TestClassificacaoMunicipal(unittest.TestCase):
    """Municipal roles keep their original meaning."""

    def test_lula_a_frente_consolida(self) -> None:
        self.assertEqual(classificar_municipio(4.0, 1000, 80_000), "Consolidar")

    def test_empate_nao_e_intensificar(self) -> None:
        self.assertEqual(classificar_municipio(0.0, 0, 200_000), "Apoio local")

    def test_margem_curta_em_cidade_grande_intensifica(self) -> None:
        self.assertEqual(classificar_municipio(-4.0, -1000, 80_000), "Intensificar")

    def test_margem_estrutural_e_presenca_contida(self) -> None:
        self.assertEqual(classificar_municipio(-30.0, -500, 10_000), "Presenca contida")


class TestClassificacaoRegional(unittest.TestCase):
    """A region where Lula is behind is not labeled as a lead."""

    def test_lula_a_frente(self) -> None:
        self.assertEqual(classificar_regiao(_regiao(600, 400, 1000)), "Consolidar")

    def test_empate_e_seletivo(self) -> None:
        self.assertEqual(classificar_regiao(_regiao(500, 500, 1000)), "Seletivo")

    def test_atras_curto_sem_volume_nao_consolida(self) -> None:
        regiao = _regiao(400, 480, 1000)
        self.assertLess(regiao.dif_pp, 0)
        self.assertAlmostEqual(abs(regiao.dif_pp), 8.0)
        self.assertEqual(classificar_regiao(regiao), "Seletivo")

    def test_margem_larga_e_presenca_contida(self) -> None:
        self.assertEqual(classificar_regiao(_regiao(200, 800, 1000)), "Presenca contida")


class TestIndiceLegado(unittest.TestCase):
    """The legacy score stays defined and is not competitiveness."""

    def test_lula_a_frente_zera_o_indice(self) -> None:
        self.assertEqual(indice_atencao(4.0, 1000, 500, "Consolidar"), 0.0)

    def test_abstencao_entra_so_nas_faixas_previstas(self) -> None:
        com_ausentes = indice_atencao(-4.0, -1000, 500, "Intensificar")
        sem_ausentes = indice_atencao(-4.0, -1000, 0, "Intensificar")
        presenca = indice_atencao(-30.0, -1000, 500, "Presenca contida")
        presenca_sem = indice_atencao(-30.0, -1000, 0, "Presenca contida")
        self.assertGreater(com_ausentes, sem_ausentes)
        self.assertEqual(presenca, presenca_sem)

    def test_fila_nao_ordena_por_competitividade(self) -> None:
        perto = _municipio("Perto", "Reduzir o buraco", 1.0, 10, 11, 30)
        longe = _municipio("Longe", "Intensificar", 1.0, 10, 90, 100)
        relatorio = Relatorio(
            uf="RJ",
            origem="teste",
            gerado_em="2026-10-12",
            municipios=(perto, longe),
            estado=_regiao(100, 100, 200),
            regioes=(),
            avisos=(),
            fonte_regiao="teste",
        )
        ordem = [item.nome for item in fila(relatorio)]
        self.assertEqual(ordem, ["Longe", "Perto"])
        self.assertGreater(perto.competitividade or 0.0, longe.competitividade or 0.0)


def _regiao(lula: int, flavio: int, validos: int) -> Agregado:
    """Build a region whose derived gap comes only from these vote totals."""
    return Agregado(
        nome="Teste",
        municipios=1,
        locais=1,
        aptos=validos,
        comparecimento=validos,
        abstencoes=0,
        validos=validos,
        lula=lula,
        flavio=flavio,
        locais_lula=0,
        locais_flavio=1,
        locais_empate=0,
    )


def _municipio(
    nome: str,
    papel: str,
    indice: float,
    lula: int,
    flavio: int,
    validos: int,
) -> Municipio:
    """Build a municipality with an explicit legacy role and score."""
    return Municipio(
        uf="RJ",
        nome=nome,
        regiao="Teste",
        papel=papel,
        indice=indice,
        locais=1,
        aptos=validos,
        comparecimento=validos,
        abstencoes=0,
        abstencao_pct=0.0,
        validos=validos,
        lula=lula,
        lula_pct=100.0 * lula / validos,
        flavio=flavio,
        flavio_pct=100.0 * flavio / validos,
        dif_pp=100.0 * (lula - flavio) / validos,
        dif_votos=lula - flavio,
        locais_lula=0,
        locais_flavio=1,
        locais_empate=0,
        origem="teste",
    )


if __name__ == "__main__":
    unittest.main()
