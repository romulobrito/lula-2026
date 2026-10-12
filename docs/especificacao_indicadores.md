# Especificacao dos indicadores

Versao da especificacao: 2026-10-12.

Origem dos dados: arquivos `resumo_municipios_UF.csv` na raiz do repositorio. Cada linha e um municipio. As colunas usadas sao `APTOS`, `COMPARECIMENTO`, `ABSTENCOES`, `VOTOS VALIDOS`, `LULA` e `FLAVIO BOLSONARO`. Os CSV de entrada nao sao alterados pelo gerador. Nao ha, neste repositorio, o identificador da publicacao do TSE alem desses arquivos.

Os indicadores abaixo sao descritivos. Nenhum deles e uma previsao, nem uma ordem de prioridade operacional.

## Competitividade eleitoral

\[
C = 1 - \frac{|L - F|}{L + F}
\]

- \(L\): votos de Lula.
- \(F\): votos de Flavio.
- Dominio: \(L \ge 0\), \(F \ge 0\) e \(L + F > 0\). Fora disso o resultado e ausente (`None` no codigo, `n/d` no HTML).
- Unidade: adimensional, no intervalo \([0, 1]\).
- \(C = 1\): empate entre os dois.
- \(C\) proximo de 1: totais proximos.
- \(C\) proximo de 0: um dos dois concentra os votos dos dois.
- Propriedades verificadas: simetria em \(L\) e \(F\), independencia de escala, continuidade para \(L + F > 0\), determinismo.
- Hipotese: so entram os votos desses dois candidatos.
- Limitacao: \(C\) nao e a competitividade da eleicao inteira quando ha outros votos validos. Isso aparece em separado na participacao conjunta.

## Dimensao eleitoral

\[
E = V
\]

- \(V\): votos validos.
- Unidade: votos.
- Nao e combinada com \(C\).

## Taxa de abstencao

\[
A = \frac{N - Q}{N}
\]

- \(N\): eleitores aptos.
- \(Q\): comparecimento.
- Dominio: \(N > 0\) e \(0 \le Q \le N\). Fora disso o resultado e ausente.
- Unidade: taxa de 0 a 1.
- O relatorio tambem mostra a abstenção em percentual, \(100 \times\) abstenções / aptos. As duas contas coincidem quando abstenções = aptos − comparecimento.
- Hipotese: ausente nao tem preferencia conhecida.

## Participacao conjunta

\[
P = \frac{L + F}{V}
\]

- Dominio: \(V > 0\), \(L \ge 0\), \(F \ge 0\) e \(L + F \le V\). Fora disso o resultado e ausente.
- Unidade: taxa de 0 a 1.
- Mede a fatia dos votos validos que ficou com os dois candidatos analisados.

## Diferenca

\[
D = L - F
\]

\[
\Delta = 100 \times \frac{L - F}{V}
\]

- \(D\) e a diferenca absoluta, em votos. Negativa quando Lula esta atras.
- \(\Delta\) e a diferenca em pontos percentuais sobre todos os votos validos. Nao usa \(L + F\) como denominador.
- \(\Delta\) e ausente quando \(V \le 0\).

## Agregacao

Municipio e regiao usam as mesmas formulas, aplicadas aos totais somados. \(C\), \(A\) e \(P\) da regiao nao sao a media dos municipios. \(D\) da regiao e a soma dos \(D\) municipais.

## Indice legado

O indice legado permanece no relatorio e na ordenacao dentro de cada papel. A formula e o dominio dele estao em `docs/comparacao_metodologica.md`. Ele nao substitui e nao e substituido por \(C\).
