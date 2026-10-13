# Lula 2026 — relatorio de estrategia municipal

Os relatorios publicados ficam em https://romulobrito.github.io/lula-2026/

Gera, para cada estado, um XLSX e um HTML com o papel de campanha de cada municipio, a partir de um CSV no padrao `resumo_municipios_UF.csv`. O HTML inclui um mapa dos municipios colorido pelo papel. A malha e a mesorregiao vem do IBGE e ficam em `cache/` depois do primeiro uso.

## Papéis

- **Intensificar**: Lula atras, margem curta e eleitorado grande.
- **Reduzir o buraco**: deficit absoluto grande, margem ainda abaixo de 28 pontos. A meta e diminuir a diferenca, nao virar o municipio.
- **Consolidar**: Lula ja tem mais votos que Flavio.
- **Apoio local**: nao entra nas faixas acima e nao justifica a estrutura principal.
- **Presenca contida**: margem larga demais para concentrar comicio e agenda.

Indicadores descritivos, calculados em separado e sem um indice unico novo:

- Competitividade = 1 - |Lula - Flavio| / (Lula + Flavio). Vale 1 no empate entre os dois. Nao usa os outros votos validos e nao ordena a fila.
- Dimensao eleitoral = votos validos.
- Taxa de abstencao = (aptos - comparecimento) / aptos.
- Participacao conjunta = (Lula + Flavio) / votos validos.
- Diferenca absoluta = Lula - Flavio. A diferenca em pontos percentuais usa os votos validos.

O indice legado, em Intensificar e Reduzir o buraco, soma o deficit de votos com metade das abstencoes e multiplica pelo fator de proximidade: 1,00 ate 8 pontos, 0,65 ate 15, 0,35 ate 25 e 0,15 acima disso. Em Presenca contida a abstencao nao sobe o municipio. Quando Lula esta a frente, o indice e zero. Esse indice nao e competitividade. Os pesos nao tem justificativa demonstrada.

A especificacao, a auditoria e a comparacao estao em `docs/especificacao_indicadores.md`, `docs/auditoria_priorizacao.md` e `docs/comparacao_metodologica.md`.

A sensibilidade dos limiares esta em `docs/analise_sensibilidade.md` e `docs/robustez_classificacoes.md`. O comando `python scripts/analisar_robustez.py` so le os CSV e grava tabelas em `resultados_robustez/`. Ele nao recalibra peso e nao reescreve os relatorios publicados.

A tabela de regioes cruza tres sinais com o deficit: se as abstencoes cobrem o buraco, a parcela de locais em que Lula fica a frente, e brancos e nulos (comparecimento menos votos validos).

## Requisitos

Python 3.11 ou superior. A unica dependencia externa e o openpyxl.

```bash
python -m pip install -r requirements.txt
```

## Uso

```bash
python gerar_relatorio_estrategia.py resumo_municipios_RJ.csv resumo_municipios_SP.csv resumo_municipios_MG.csv
```

Opcoes:

- `--saida DIR` — diretorio dos arquivos gerados. Padrao: `./saida`.
- `--regioes ARQUIVO.csv` — mapa opcional com as colunas `MUNICIPIO` e `REGIAO`. `UF` e opcional e restringe a linha a um estado.

Cada estado gera `estrategia_UF.xlsx`, `estrategia_UF.html` e `estrategia_UF_mapa.svg`. O Rio de Janeiro usa macrorregioes de campanha embutidas. Nos demais estados o agrupamento usa a mesorregiao do IBGE, salvo quando `--regioes` cobre o municipio.

O script precisa de rede na primeira execucao de cada estado, para baixar a malha municipal do IBGE. As execucoes seguintes leem `cache/`.
