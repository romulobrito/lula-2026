"""Robustness metrics, rule boundaries, and untouched official CSVs."""

import hashlib
import unittest
from dataclasses import replace
from pathlib import Path

from gerar_relatorio_estrategia import (
    PESO_AUSENTE,
    ParametrosFator,
    ParametrosMunicipio,
    classificar_municipio,
    fator_proximidade,
    indice_atencao,
)
from scripts.analisar_robustez import (
    RAIZ,
    Linha,
    carregar_linhas,
    cenario_base,
    avaliar_municipios,
    jaccard,
    kendall_tau_b,
    listar_csvs,
    motivo_fator,
    motivo_municipio,
    spearman,
)

CONTAGEM_PAPEL = {
    "Presenca contida": 1592,
    "Consolidar": 1467,
    "Apoio local": 539,
    "Intensificar": 69,
    "Reduzir o buraco": 44,
}


class TestMetricas(unittest.TestCase):
    """Rank correlations and overlap on small, known lists."""

    def test_spearman_identico_e_invertido(self) -> None:
        identico = [(1.0,), (2.0,), (3.0,)]
        self.assertEqual(spearman(identico, identico), 1.0)
        self.assertEqual(spearman(identico, [(3.0,), (2.0,), (1.0,)]), -1.0)

    def test_kendall_com_empate_e_uma_troca(self) -> None:
        empatado = [(1.0,), (2.0,), (2.0,)]
        self.assertEqual(kendall_tau_b(empatado, empatado), 1.0)
        tau = kendall_tau_b([(1.0,), (2.0,), (3.0,)], [(1.0,), (3.0,), (2.0,)])
        self.assertIsNotNone(tau)
        if tau is not None:
            self.assertAlmostEqual(tau, 1.0 / 3.0)

    def test_kendall_indefinido_quando_tudo_empata(self) -> None:
        self.assertIsNone(kendall_tau_b([(1.0,), (1.0,)], [(1.0,), (2.0,)]))

    def test_jaccard(self) -> None:
        self.assertEqual(jaccard({"a", "b"}, {"a", "b"}), 1.0)
        self.assertAlmostEqual(jaccard({"a", "b"}, {"b", "c"}), 1.0 / 3.0)
        self.assertEqual(jaccard(set(), set()), 1.0)


class TestLimites(unittest.TestCase):
    """Jumps at a cut are the published rule, and invalid steps are refused."""

    def test_fator_na_fronteira(self) -> None:
        self.assertEqual(fator_proximidade(7.99), 1.0)
        self.assertEqual(fator_proximidade(8.0), 1.0)
        self.assertEqual(fator_proximidade(8.01), 0.65)
        self.assertEqual(fator_proximidade(15.0), 0.65)
        self.assertEqual(fator_proximidade(15.01), 0.35)
        self.assertEqual(fator_proximidade(25.0), 0.35)
        self.assertEqual(fator_proximidade(25.01), 0.15)

    def test_classificacao_na_fronteira(self) -> None:
        self.assertEqual(classificar_municipio(-8.0, -100, 50_000), "Intensificar")
        self.assertEqual(classificar_municipio(-8.01, -100, 50_000), "Apoio local")
        self.assertEqual(classificar_municipio(-8.0, -100, 49_999), "Apoio local")
        self.assertEqual(classificar_municipio(-28.0, -100, 80_000), "Presenca contida")
        self.assertEqual(classificar_municipio(-27.99, -100, 80_000), "Apoio local")
        self.assertEqual(classificar_municipio(-20.0, -15_000, 80_000), "Reduzir o buraco")
        self.assertEqual(classificar_municipio(-20.0, -14_999, 80_000), "Apoio local")
        self.assertEqual(classificar_municipio(-15.0, -100, 39_999), "Apoio local")
        self.assertEqual(classificar_municipio(-15.01, -100, 39_999), "Presenca contida")

    def test_parametro_invalido_e_recusado(self) -> None:
        with self.assertRaises(ValueError):
            classificar_municipio(0.0, 0, 10, replace(ParametrosMunicipio(), precedencia="outra"))
        self.assertNotEqual(motivo_fator(replace(ParametrosFator(), ate_8=1.2)), "")
        invertido = replace(ParametrosMunicipio(), pp_disputavel=12.0)
        self.assertNotEqual(motivo_municipio(invertido), "")

    def test_precedencia_nomeada(self) -> None:
        cidade_larga = classificar_municipio(-30.0, -1000, 200_000)
        antes = classificar_municipio(
            -30.0,
            -1000,
            200_000,
            replace(ParametrosMunicipio(), precedencia="presenca_antes"),
        )
        self.assertEqual(cidade_larga, "Presenca contida")
        self.assertEqual(antes, cidade_larga)
        perto = classificar_municipio(-4.0, -20_000, 80_000)
        com_deficit_antes = classificar_municipio(
            -4.0,
            -20_000,
            80_000,
            replace(ParametrosMunicipio(), precedencia="deficit_antes"),
        )
        self.assertEqual(perto, "Intensificar")
        self.assertEqual(com_deficit_antes, "Reduzir o buraco")


class TestReferencia(unittest.TestCase):
    """The published labels stay put, and the CSVs are only read."""

    def test_csv_oficial_nao_e_alterado(self) -> None:
        antes = {caminho.name: hashlib.sha256(caminho.read_bytes()).hexdigest() for caminho in listar_csvs(RAIZ)}
        linhas = carregar_linhas(RAIZ)
        depois = {caminho.name: hashlib.sha256(caminho.read_bytes()).hexdigest() for caminho in listar_csvs(RAIZ)}
        self.assertEqual(antes, depois)
        self.assertEqual(len(linhas), 3711)
        contagem: dict[str, int] = {}
        for linha in linhas:
            contagem[linha.papel] = contagem.get(linha.papel, 0) + 1
            repetido = classificar_municipio(linha.dif_pp, linha.dif_votos, linha.aptos)
            self.assertEqual(repetido, linha.papel)
            self.assertAlmostEqual(
                indice_atencao(linha.dif_pp, linha.dif_votos, linha.abstencoes, linha.papel),
                linha.indice,
            )
        self.assertEqual(contagem, CONTAGEM_PAPEL)

    def test_cenario_publicado_nao_move_ninguem(self) -> None:
        linhas = carregar_linhas(RAIZ)
        papeis, indices = avaliar_municipios(linhas, cenario_base())
        self.assertEqual(papeis, [linha.papel for linha in linhas])
        for indice, linha in enumerate(linhas):
            self.assertAlmostEqual(indices[indice], linha.indice)

    def test_curitiba_permanece_reduzir_o_buraco(self) -> None:
        linhas = carregar_linhas(RAIZ)
        curitiba = [linha for linha in linhas if linha.uf == "PR" and linha.nome == "CURITIBA"]
        self.assertEqual(len(curitiba), 1)
        self.assertEqual(curitiba[0].papel, "Reduzir o buraco")

    def test_competitividade_ausente_fica_fora_do_ranking(self) -> None:
        linha = Linha(
            uf="RJ",
            nome="SEM",
            regiao="Teste",
            locais=1,
            aptos=10,
            comparecimento=10,
            abstencoes=0,
            validos=10,
            lula=0,
            flavio=0,
            dif_pp=0.0,
            dif_votos=0,
            locais_lula=0,
            locais_flavio=0,
            locais_empate=1,
            papel="Apoio local",
            indice=0.0,
        )
        self.assertIsNone(linha.competitividade)
        definidos = [item for item in (linha,) if item.competitividade is not None]
        self.assertEqual(definidos, [])

    def test_peso_publicado(self) -> None:
        self.assertEqual(PESO_AUSENTE, 0.5)
        self.assertTrue(Path(RAIZ, "resumo_municipios_RJ.csv").is_file())


if __name__ == "__main__":
    unittest.main()
