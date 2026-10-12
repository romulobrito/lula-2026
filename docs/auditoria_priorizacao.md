# Auditoria da priorizacao

Data: 2026-10-12.

Funcoes inspecionadas em `gerar_relatorio_estrategia.py`: `fator_proximidade`, `indice_atencao`, `classificar_municipio`, `classificar_regiao` e `fila`.

## O que cada funcao faz

- `fator_proximidade` reduz o peso do deficit em degraus: 1,00 ate 8 p.p., 0,65 ate 15, 0,35 ate 25 e 0,15 acima disso.
- `indice_atencao` e o indice legado. Vale zero quando Lula nao esta atras. Nas faixas Intensificar e Reduzir o buraco soma metade das abstenções absolutas antes do desconto da margem.
- `classificar_municipio` atribui um papel operacional a partir de margem, deficit e aptos.
- `classificar_regiao` atribui um papel ao agregado.
- `fila` ordena primeiro pelo papel e, dentro do papel, pelo indice legado. Nao ordena por competitividade.

## Achados

1. Degraus arbitrarios. Em 8,00 p.p. o fator e 1,00; em 8,01 p.p. passa a 0,65. A funcao e descontinua. Nao ha justificativa demonstrada para os quatro patamares nem para o peso 0,5 da abstenção.
2. O indice legado depende do papel, e o papel depende dos mesmos limiares. Duas cidades quase iguais podem receber tratamento diferente da abstenção se uma cruzar a faixa de Presenca contida.
3. A fila coloca toda a categoria Intensificar antes de Reduzir o buraco, qualquer que seja o indice ou a competitividade. Isso e uma regra de ordenacao, nao uma propriedade dos votos.
4. Erro objetivo, corrigido: `classificar_regiao` rotulava como Consolidar uma regiao em que Lula estava atras, quando a margem absoluta ficava em ate 10 p.p. e nenhuma regra anterior se aplicava. Consolidar significa Lula a frente. Essa regiao passa a Seletiva.
5. Erro objetivo, corrigido: empate municipal (`dif_votos = 0`) caia em Intensificar quando a cidade era grande, porque a margem era zero. Empate nao e Lula atras. O municipio em empate fica em Apoio local. Empate regional fica Seletivo.
6. Intensificar, Reduzir o buraco e Presenca contida continuam dependendo de limiares fixos (8, 13, 16, 28 p.p.; 15 mil, 40 mil, 50 mil, 60 mil e 100 mil aptos ou votos). Sao parametros operacionais, nao estimativas.

## O que foi preservado

Os limiares e o indice legado nao foram substituidos. A fila operacional continua a mesma regra de categorias. A competitividade entrou como coluna propria e nao reordena essa fila.

Nao foi introduzido peso novo, modelo de aprendizado nem funcao de otimizacao.
