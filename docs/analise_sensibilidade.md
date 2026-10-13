# Analise de sensibilidade

Data da varredura: 2026-10-12. Commit de referencia registrado em `resultados_robustez/referencia.csv` (`69a50a5`), mais o hash dos dez CSV oficiais e dos arquivos `gerar_relatorio_estrategia.py` e `scripts/analisar_robustez.py` usados na conta. A varredura leu 3.711 municipios em 10 estados e 86 regioes. Nao alterou os CSV nem os relatorios HTML.

## O que foi variado

Um parametro de cada vez, em ±5%, ±10% e ±20%, com os demais no valor publicado. Contagens de aptos e de votos foram arredondadas para inteiro. Dez cenarios foram descartados porque inverteriam uma faixa ou tirariam um fator do intervalo (0, 1]. O motivo fica na coluna `motivo` de `resultados_robustez/cenarios.csv`. Nao houve valor substituto.

Dois cenarios discretos, separados da grade percentual, trocam a ordem das regras municipais: Presenca contida de 28 p.p. antes de Intensificar; deficit de 15 mil votos antes das faixas de Intensificar.

A competitividade, os votos validos, a diferenca e a taxa de abstencao nao entram nessa grade. Nao tem parametro livre. No baseline, a correlacao de Spearman entre a fila operacional e a competitividade e 0,002 (Kendall −0,003), em 3.711 municipios. Isso descreve perguntas diferentes. Nao e instabilidade.

## O que quase nao se move

Com os parametros publicados, a fila reproduz a si mesma: Spearman e Kendall iguais a 1, e nenhum municipio muda de papel.

O peso 0,5 da abstencao e os degraus do fator de proximidade nao mudam papel. O efeito aparece dentro do papel. O caso mais sensivel do fator e o corte de 15 p.p. reduzido a 12: a fila inteira continua com Spearman 0,9999, e a ordem interna de Intensificar cai a 0,962. O indice legado de cidades grandes varia muito em valor absoluto (Rio de Janeiro e Belo Horizonte, nas faixas em que a abstencao entra na conta) sem trocar o papel.

Dois limiares municipais nao trocaram ninguem nesta base, nem em ±20%: 13 p.p. com 100 mil aptos. A faixa existe na regra e nao encontra municipio adicional nesses dez estados dentro desse intervalo.

O limiar regional de 10 p.p., que depois da revisao anterior cai nos dois ramos em Seletivo, tambem nao muda nenhum rotulo.

## O que mais troca o papel municipal

| Parametro | Maior troca de papel | Spearman minimo da fila |
| --- | --- | --- |
| Margem da cidade pequena (15 p.p.) | 3,15% em 12 p.p. | 0,981 |
| Margem estrutural (28 p.p.) | 1,40% em 33,6 p.p. | 0,981 |
| Aptos da cidade pequena (40 mil) | 0,62% | 0,999 |
| Margem disputavel (16 p.p.) | 0,43% | 0,997 |
| Deficit de 15 mil votos | 0,27% | 0,997 |

A maior troca percentual, 117 municipios, e Apoio local para Presenca contida quando o corte de 15 p.p. da cidade pequena cai para 12. O topo da fila nao se mexe nesse cenario: Jaccard dos primeiros 5%, 10% e 20% permanece 1. A mediana do deslocamento de posicao e 9 lugares; o percentil 90 e 115.

O topo se mexe quando o corte estrutural sobe de 28 para 33,6 p.p. So 1,40% dos municipios mudam de papel (36 saem de Presenca contida para Reduzir o buraco e 16 para Apoio local), mas quem entra em Reduzir o buraco salta para o inicio da fila, porque a fila ordena primeiro pelo papel. O Jaccard dos primeiros 5% cai para 0,676. O Spearman da fila inteira continua em 0,981. A correlacao da lista inteira esconde o salto de categoria.

## Regioes

Ha 86 regioes. Baixar o corte estrutural de 28 para 22,4 p.p. troca 11 delas (12,8%): 8 deixam Reduzir o buraco e 3 deixam Seletivo, todas para Presenca contida. Um ponto percentual regional e uma regiao inteira, entao a porcentagem e grosseira. Cinquenta e sete regioes mantem o papel em todos os cenarios regionais aceitos.

## Ordem das regras

Colocar Presenca contida de 28 p.p. antes de Intensificar nao muda nenhum municipio. As faixas nao se cruzam: Intensificar exige margem de no maximo 16 p.p. e Presenca estrutural exige 28 ou mais.

Colocar o deficit de 15 mil votos antes das outras faixas muda 3,93% dos municipios (Spearman da fila 0,937): 124 saem de Presenca contida para Reduzir o buraco e 22 saem de Intensificar para Reduzir o buraco. Isso e outra regra de decisao, nao uma perturbacao pequena do procedimento publicado.

## Descontinuidades

O fator de proximidade vale 1,00 em 8,00 p.p. e 0,65 em 8,01. O mesmo salto existe em 15 e em 25 p.p. Sao degraus da regra, nao erro de conta. A classificacao tambem muda no inteiro ao lado do corte: 8,00 p.p. com 50 mil aptos e Intensificar; 8,01 p.p. com os mesmos aptos e Apoio local, porque as faixas seguintes pedem 60 mil ou 100 mil aptos. A tabela esta em `resultados_robustez/descontinuidades.csv`.

Entre quem esta atras (2.243 municipios), 66 ficam a ate 0,5 p.p. do corte de 28, e 48 a ate 0,5 p.p. do corte de 8. Sao os municipios em que um pequeno movimento do limiar pode trocar o papel. A lista esta em `resultados_robustez/proximidade_limiares.csv`.

## Limites desta leitura

A grade e um parametro por vez. Um choque simultaneo de varios limiares nao foi calculado. Os dez estados nao sao o pais. A fracao de manutencao nao e probabilidade de acerto eleitoral. Estabilidade tambem nao compara a classificacao com uma eleicao anterior. Os pesos nao foram ajustados para produzir uma ordem desejada.
