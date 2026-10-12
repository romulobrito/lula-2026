# Lula 2026 — relatorio de estrategia municipal

Os relatorios publicados ficam em https://romulobrito.github.io/lula-2026/

Gera, para cada estado, um XLSX e um HTML com o papel de campanha de cada municipio, a partir de um CSV no padrao `resumo_municipios_UF.csv`. O HTML inclui um mapa dos municipios colorido pelo papel. A malha e a mesorregiao vem do IBGE e ficam em `cache/` depois do primeiro uso.

## Papéis

- **Intensificar**: Lula atras, margem curta e eleitorado grande.
- **Reduzir o buraco**: deficit absoluto grande, margem ainda abaixo de 28 pontos. A meta e diminuir a diferenca, nao virar o municipio.
- **Consolidar**: Lula ja tem mais votos que Flavio.
- **Apoio local**: nao entra nas faixas acima e nao justifica a estrutura principal.
- **Presenca contida**: margem larga demais para concentrar comicio e agenda.

O indice de atencao vale o deficit de votos vezes um fator de proximidade: 1,00 ate 8 pontos, 0,65 ate 15, 0,35 ate 25 e 0,15 acima disso. Quando Lula esta a frente, o indice e zero.

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
