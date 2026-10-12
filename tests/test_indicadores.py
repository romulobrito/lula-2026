"""Mathematical checks for the separated electoral indicators."""

import unittest

from gerar_relatorio_estrategia import (
    Agregado,
    agregar,
    competitividade_eleitoral,
    diferenca_absoluta,
    diferenca_percentual,
    participacao_conjunta,
    taxa_abstencao,
)


class TestCompetitividade(unittest.TestCase):
    """Competitiveness depends on the Lula/Flavio ratio, not on the scale."""

    def test_empate(self) -> None:
        self.assertEqual(competitividade_eleitoral(100, 100), 1.0)

    def test_extremos(self) -> None:
        self.assertEqual(competitividade_eleitoral(0, 100_000), 0.0)
        self.assertEqual(competitividade_eleitoral(100_000, 0), 0.0)

    def test_mesma_proporcao_em_escalas_diferentes(self) -> None:
        grande = competitividade_eleitoral(45_000, 55_000)
        pequeno = competitividade_eleitoral(4_500, 5_500)
        self.assertAlmostEqual(grande, 0.90)
        self.assertAlmostEqual(pequeno, 0.90)
        self.assertAlmostEqual(grande, pequeno)

    def test_simetria_entre_candidatos(self) -> None:
        self.assertEqual(
            competitividade_eleitoral(45_000, 55_000),
            competitividade_eleitoral(55_000, 45_000),
        )

    def test_limites(self) -> None:
        valor = competitividade_eleitoral(1, 1_000_000)
        self.assertIsNotNone(valor)
        if valor is not None:
            self.assertGreaterEqual(valor, 0.0)
            self.assertLessEqual(valor, 1.0)

    def test_soma_zero_e_contagem_negativa(self) -> None:
        self.assertIsNone(competitividade_eleitoral(0, 0))
        self.assertIsNone(competitividade_eleitoral(-1, 10))
        self.assertIsNone(competitividade_eleitoral(10, -1))


class TestIndicadoresSeparados(unittest.TestCase):
    """Each indicator keeps its own domain and does not absorb the others."""

    def test_abstencao(self) -> None:
        self.assertAlmostEqual(taxa_abstencao(100, 80), 0.20)
        self.assertIsNone(taxa_abstencao(0, 0))
        self.assertIsNone(taxa_abstencao(100, 120))
        self.assertIsNone(taxa_abstencao(-1, 0))

    def test_participacao_conjunta(self) -> None:
        self.assertAlmostEqual(participacao_conjunta(40, 40, 100), 0.80)
        self.assertIsNone(participacao_conjunta(40, 40, 0))
        self.assertIsNone(participacao_conjunta(70, 40, 100))

    def test_diferenca_absoluta_e_percentual(self) -> None:
        self.assertEqual(diferenca_absoluta(45_000, 55_000), -10_000)
        self.assertAlmostEqual(
            diferenca_percentual(45_000, 55_000, 200_000),
            -5.0,
        )
        self.assertIsNone(diferenca_percentual(45_000, 55_000, 0))

    def test_competitividade_nao_equivale_a_pontos_quando_ha_outros_votos(self) -> None:
        competitividade = competitividade_eleitoral(45_000, 55_000)
        pontos = diferenca_percentual(45_000, 55_000, 200_000)
        self.assertAlmostEqual(competitividade, 0.90)
        self.assertAlmostEqual(pontos, -5.0)


class TestAgregacao(unittest.TestCase):
    """Regional indicators use summed votes, not the mean of municipal rates."""

    def test_diferenca_e_competitividade_regionais(self) -> None:
        primeiro = _municipio("A", 10, 30, 50)
        segundo = _municipio("B", 40, 20, 70)
        regiao = agregar("Regiao", [primeiro, segundo])
        self.assertEqual(regiao.dif_votos, (10 - 30) + (40 - 20))
        self.assertAlmostEqual(regiao.competitividade, 1.0)
        media = (
            (primeiro.competitividade or 0.0) + (segundo.competitividade or 0.0)
        ) / 2.0
        self.assertNotAlmostEqual(regiao.competitividade, media)
        self.assertEqual(regiao.lula + regiao.flavio, 100)
        self.assertEqual(regiao.validos, 120)


def _municipio(nome: str, lula: int, flavio: int, validos: int):
    """Build the smallest municipality the aggregator accepts."""
    from gerar_relatorio_estrategia import Municipio

    return Municipio(
        uf="RJ",
        nome=nome,
        regiao="Teste",
        papel="Apoio local",
        indice=0.0,
        locais=1,
        aptos=validos + 10,
        comparecimento=validos + 5,
        abstencoes=5,
        abstencao_pct=1.0,
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


class TestAgregadoDireto(unittest.TestCase):
    """A hand-built aggregate matches the same formulas as a municipality."""

    def test_formulas_no_agregado(self) -> None:
        agregado = Agregado(
            nome="Teste",
            municipios=1,
            locais=1,
            aptos=100,
            comparecimento=80,
            abstencoes=20,
            validos=70,
            lula=30,
            flavio=30,
            locais_lula=1,
            locais_flavio=0,
            locais_empate=0,
        )
        self.assertEqual(agregado.competitividade, 1.0)
        self.assertAlmostEqual(agregado.taxa_abstencao, 0.20)
        self.assertAlmostEqual(agregado.participacao_conjunta, 60 / 70)


if __name__ == "__main__":
    unittest.main()
