# Robustez das classificacoes

A referencia e a classificacao publicada depois da revisao dos indicadores. Nos 3.711 municipios dos dez estados, os papeis de partida sao 1.592 Presenca contida, 1.467 Consolidar, 539 Apoio local, 69 Intensificar e 44 Reduzir o buraco.

A manutencao abaixo e a fracao dos cenarios de limiar municipal aceitos em que o municipio conserva o papel. Nao e probabilidade.

## Quem conserva o papel

Dos 3.711 municipios, 3.321 (89,5%) ficam no mesmo papel em todos os cenarios de limiar aceitos, de ±5% a ±20%, um parametro por vez. Outros 283 conservam o papel em pelo menos 95% desses cenarios, e 107 ficam entre 80% e 95%. Nenhum fica abaixo de 80%.

Os 390 que mudam em algum cenario saem sobretudo de Presenca contida (183) e de Apoio local (162). Intensificar muda em 13 municipios e Reduzir o buraco em 32. Consolidar nao muda: a regra de Lula a frente vem antes dos limiares e nenhum cenario desta grade a ultrapassa.

O parametro que mais aparece nessas trocas e a margem da cidade pequena (230 municipios em algum cenario), depois a margem estrutural de 28 p.p. (95).

## O que isso significa para a fila

A fila operacional continua ordenando primeiro pelo papel e so depois pelo indice legado. Por isso um municipio que cruza de Presenca contida para Reduzir o buraco anda centenas de posicoes, mesmo quando poucos municipios mudam. A maior variacao de posicao observada na grade chega a 2.112 lugares. A mediana, no cenario municipal que mais troca papel, e de 9 lugares.

Os primeiros 5%, 10% e 20% da fila ficam intactos quando a troca e entre Apoio local e Presenca contida. Eles se alteram quando a margem estrutural sobe e cidades de deficit grande deixam Presenca contida para entrar em Reduzir o buraco. Nesse cenario (±20% sobre 28 p.p.), o Jaccard dos primeiros 5% e 0,676, com Spearman da fila ainda em 0,981.

Dentro de cada papel, o indice legado quase nao troca de ordem quando so o limiar de classificacao se move. A ordem interna de Consolidar nao e o indice legado: esse indice vale zero sempre que Lula esta a frente, e a fila usa a vantagem em votos, que esta grade nao altera.

## Indicadores que nao dependem do limiar

Competitividade, votos validos, diferenca absoluta e taxa de abstencao permanecem iguais em todos os cenarios. A amplitude da competitividade, municipio a municipio, e zero. A discordancia entre a fila e a competitividade (Spearman 0,002) ja existe no baseline. Continua valida a leitura de que competitividade nao e prioridade operacional.

A correlacao entre a fila e o deficit absoluto e negativa (−0,585). O deficit colocaria primeiro os maiores buracos. A fila publicada coloca primeiro a margem curta em eleitorado grande, e deixa por ultimo a margem larga, mesmo quando o buraco e grande. O sinal negativo e a regra de ordenacao, nao uma falha da conta.

Entre os 2.243 municipios em que Lula esta atras, a fila e o indice legado concordam de forma parcial (Spearman 0,609), porque o papel ainda vem antes do indice.

## O que permanece e o que depende do corte

Permanece, dentro das perturbacoes testadas:

- o papel de quem ja esta a frente;
- a competitividade e os demais indicadores sem parametro;
- a quase totalidade de Intensificar e de Reduzir o buraco;
- a ordem geral da fila, com Spearman acima de 0,98 nos cenarios de um limiar so.

Depende do corte escolhido:

- a fronteira entre Apoio local e Presenca contida nas cidades pequenas, em torno de 15 p.p. e de 40 mil aptos;
- a fronteira de 28 p.p., que decide se um deficit grande continua em Presenca contida ou passa a Reduzir o buraco e, com isso, sobe na fila;
- nas regioes, o mesmo corte de 28 p.p.: ±20% troca 11 das 86 regioes.

A ordem alternativa que consulta o deficit antes das outras faixas nao e uma perturbacao pequena. Ela reclassifica 146 municipios, a maior parte de Presenca contida para Reduzir o buraco. A ordem publicada nao faz isso.

## O que esta fora desta etapa

Nada aqui estima a chance de um papel estar "certo" numa eleicao futura. Nao houve comparacao com resultado historico independente. Nao houve ajuste de peso para aproximar a fila de um ranking desejado. Os numeros desta pagina estao reproduzidos em `resultados_robustez/`.
